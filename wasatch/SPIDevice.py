import re
import os
import usb
import time
import logging
import datetime
import threading

from queue import Queue
from typing import Callable, Any

# Needed for Mac side
# Device Finder should already have done this
# For thoroughness though doing here anyway
# This is required for finding the usb <-> serial board
import usb.core
usb.core.find()

from .SpectrometerSettings        import SpectrometerSettings
from .SpectrometerState           import SpectrometerState
from .SpectrometerResponse        import SpectrometerResponse
from .SpectrometerRequest         import SpectrometerRequest
from .SpectrometerResponse        import ErrorLevel
from .ControlObject               import ControlObject
from .DeviceID                    import DeviceID
from .InterfaceDevice             import InterfaceDevice
from .Reading                     import Reading
from .EEPROM                      import EEPROM

import crcmod.predefined

log = logging.getLogger(__name__)

class CommandTuple:
    def __init__(self, address, value, write_len, name):
        self.address   = address
        self.value     = value
        self.write_len = write_len
        self.name      = name

    def __str__(self):
        return f"CommandTuple <name {self.name}, address 0x{self.address:02x}, value {self.value}, write_len {self.write_len}>"

class SPIDevice(InterfaceDevice):
    """
    This implements SPI communication via the FT232H usb converter.     

    This class adopts the external device interface structure.
    This involves receiving a request through the handle_request function.
    A request is processed based on the key in the request.
    The processing function passes the commands to the requested device.
    Once it receives a response from the connected device it then passes that
    back up the chain.

    @verbatim
                               Enlighten Request
                                       |
                                handle_requests
                                       |
                                 ------------
                                /   /  |  \  \ 
             { get_laser status, acquire, set_laser_watchdog, etc....}
                                \   \  |  /  /
                                 ------------
                                       |
                         {self.driver.some_spi_call}
    @endverbatim

    Updates in this version:

    - imported latest updates from spi_console.py
    - 24-bit integration time
    - floating-point gain
    - stop "searching" for start bytes in SPI stream
    - populate and validate CRC
    """

    READ_RESPONSE_OVERHEAD  = 5 # <, LEN_MSB, LEN_LSB, CRC, >  # does NOT include ADDR
    WRITE_RESPONSE_OVERHEAD = 2 # <, >
    READY_POLL_LEN = 2          # 1 seems to work
    START = 0x3c                # <
    END   = 0x3e                # >
    WRITE = 0x80                # bit changing opcodes from 'getter' to 'setter'
    CRC   = 0xff                # for readability

    lock = threading.Lock()

    def __init__(self, device_id: DeviceID, message_queue: Queue) -> None:
        super().__init__()

        ########################################################################
        # Attempt to import dependencies (connects to FT232H)
        ########################################################################

        import_successful = False
        try:
            os.environ["BLINKA_FT232H"] = "1"
            import board
            import digitalio
            import busio
            import_successful = True
        except RuntimeError as ex:
            log.error("No FT232H connected.", exc_info=1)
            if platform.system() == "Windows":
                log.error("Ensure you've followed the Zadig process in README_SPI.md")
        except ValueError as ex:
            log.error("If you are receiving 'no backend available' errors, try the following:")
            log.error("MacOS:  $ export DYLD_LIBRARY_PATH=/usr/local/lib")
            log.error("Linux:  $ export LD_LIBRARY_PATH=/usr/local/lib")
        except FtdiError as ex:
            log.error("No FT232H connected.", exc_info=1)
            if platform.system() == "Windows":
                log.error("Ensure you've followed the Zadig process in README_SPI.md")
            log.error("SPIDevice is not usable")
        if not import_successful:
            return

        self.crc8 = crcmod.predefined.mkPredefinedCrcFun('crc-8-maxim')

        ########################################################################
        # Proceed with initialization
        ########################################################################

        # if passed a string representation of a DeviceID, deserialize it
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.device_id      = device_id
        self.message_queue  = message_queue

        self.connected = False
        self.disconnect = False
        self.acquiring = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. Although a logical queue, has nothing to do with multiprocessing.
        self.command_queue = []

        self.immediate_mode = False

        self.settings = SpectrometerSettings(self.device_id)
        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0
        self.take_one               = False
        self.failure_count          = 0

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0
        self.lambdas = None
        self.spec_index = 0 
        self._scan_averaging = 1
        self.dark = None
        self.boxcar_half_width = 0

        # Initialize the SPI bus on the FT232H
        self.SPI = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)

        # Initialize D5 as the ready signal (allow override)
        self.ready = digitalio.DigitalInOut(getattr(board, os.getenv("SPI_PIN_READY", default="D5")))
        self.ready.direction = digitalio.Direction.INPUT

        # Initialize D6 as the trigger (allow override)
        self.trigger = digitalio.DigitalInOut(getattr(board, os.getenv("SPI_PIN_TRIGGER", default="D6")))
        self.trigger.direction = digitalio.Direction.OUTPUT
        self.trigger.value = False

        # Take control of the SPI Bus
        while not self.SPI.try_lock():
            pass

        # Configure the SPI bus (allow override)
        self.baud_mhz = int(os.getenv("SPI_BAUD_MHZ", default="10")) 
        log.debug(f"using baud rate {self.baud_mhz}MHz")
        self.SPI.configure(baudrate=self.baud_mhz * 1e6, phase=0, polarity=0, bits=8)

        # for kicks, let block size be overridden by the environment
        self.block_size = int(os.getenv("SPI_BLOCK_SIZE", default="256"))
        log.debug(f"using SPI block size {self.block_size}")

        self.process_f = self._init_process_funcs()

        ########################################################################
        # Store SPI command table in a lookup
        ########################################################################

        self.cmds = {}
        for  addr, value, _len, name in [
           # ---- ------ ----- -----------
            (0x11,    3,     4, "Integration Time"),
            (0x13,    0,     3, "Black Level"),
            (0x14,   24,     3, "Gain dB"),
            (0x2B,    3,     2, "Pixel Mode"),
            (0x50,  250,     2, "Start Line 0"),     
            (0x51,  750,     2, "Stop Line 0"),     
            (0x52,   12,     3, "Start Column 0"),     
            (0x53, 1932,     3, "Stop Column 0"),
            (0x54,    0,     3, "Start Line 1"),
            (0x55,    0,     3, "Stop Line 1"),
            (0x56,    0,     3, "Start Column 1"),
            (0x57,    0,     3, "Stop Column 1"),
            (0x58,    0,     3, "Desmile") ]:
            self.cmds[name] = CommandTuple(addr, value, _len, name)

        # patch gain
        cmd = self.cmds["Gain dB"]
        cmd.value = self.gain_to_ff(cmd.value)

    def connect(self) -> SpectrometerResponse:
        self.init_eeprom()

        # initialize all settings -- this ensures no setting is uninitialized
        for key in self.cmds:
            cmd = self.cmds[key]
            log.debug(f"initializing {cmd}")
            self.send_command(cmd)

        self.settings.state.integration_time_ms = self.settings.eeprom.startup_integration_time_ms
        self.settings.state.gain_db = self.settings.eeprom.detector_gain

        log.info("SPI connect done, returning True")
        return SpectrometerResponse(True)

    def init_eeprom(self):
        eeprom = self.settings.eeprom

        pages = []
        for i in range(EEPROM.MAX_PAGES):
            self.flush_input_buffer()
            pages.append(self.read_page(i))
        eeprom.parse(pages)

        self.settings.update_wavecal()
        self.settings.update_raman_intensity_factors()

        # copy startup values from EEPROM into our local command table so they 
        # get initialized properly

        self.cmds["Integration Time"].value = eeprom.startup_integration_time_ms
        self.cmds["Gain dB"         ].value = self.gain_to_ff(eeprom.detector_gain)
        self.cmds["Start Line 0"    ].value = eeprom.roi_vertical_region_1_start
        self.cmds["Stop Line 0"     ].value = eeprom.roi_vertical_region_1_end
        self.cmds["Start Column 0"  ].value = eeprom.roi_horizontal_start
        self.cmds["Stop Column 0"   ].value = eeprom.roi_horizontal_end

    def disconnect(self) -> SpectrometerResponse:
        self.disconnect = True
        return SpectrometerResponse(True)

    def acquire_data(self) -> SpectrometerResponse:
        log.debug("spi starts reading")
        if self.disconnect:
            log.debug("disconnecting, returning False for the spectrum")
            return False
        averaging_enabled = (self.settings.state.scans_to_average > 1)
        reading = Reading(self.device_id)

        try:
            reading.integration_time_ms = self.settings.state.integration_time_ms
            reading.laser_power_perc    = self.settings.state.laser_power_perc
            reading.laser_power_mW      = self.settings.state.laser_power_mW
            reading.laser_enabled       = self.settings.state.laser_enabled
            reading.spectrum            = self.get_spectrum() 
            if reading.spectrum == False:
                return False
        except usb.USBError:
            self.failure_count += 1
            log.error(f"SPI Device: encountered USB error in reading for device {self.device}")

        if not reading.failure:
            if averaging_enabled:
                if self.sum_count == 0:
                    self.summed_spectra = [float(i) for i in reading.spectrum]
                else:
                    log.debug("device.take_one_averaged_reading: summing spectra")
                    for i in range(len(self.summed_spectra)):
                        self.summed_spectra[i] += reading.spectrum[i]
                self.sum_count += 1
                log.debug("device.take_one_averaged_reading: summed_spectra : %s ...", self.summed_spectra[0:9])

        if self.settings.eeprom.bin_2x2:
            # perform the 2x2 bin software side
            next_idx_values = reading.spectrum[1:]
            # average all except the last value, which is just appended as is
            binned = [(value + next_value)/2 for value, next_value in zip(reading.spectrum[:-1], next_idx_values)]
            binned.append(reading.spectrum[-1])
            reading.spectrum = binned
                

        self.session_reading_count += 1
        reading.session_count = self.session_reading_count
        reading.sum_count = self.sum_count

        if averaging_enabled:
            if self.sum_count >= self.settings.state.scans_to_average:
                reading.spectrum = [ x / self.sum_count for x in self.summed_spectra ]
                log.debug("spi device acquire data: averaged_spectrum : %s ...", reading.spectrum[0:9])
                reading.averaged = True

                # reset for next average
                self.summed_spectra = None
                self.sum_count = 0
        else:
            # if averaging isn't enabled...then a single reading is the
            # "averaged" final measurement (check reading.sum_count to confirm)
            reading.averaged = True

        return SpectrometerResponse(reading)

    def set_integration_time_ms(self, value: int) -> SpectrometerResponse:
        cmd = self.cmds["Integration Time"]
        cmd.value = value
        self.send_command(cmd)
        self.settings.state.integration_time_ms = value
        return SpectrometerResponse()

    def set_gain(self, value: float) -> SpectrometerResponse:
        cmd = self.cmds["Gain dB"]
        cmd.value = self.gain_to_ff(value)
        self.send_command(cmd)
        self.settings.state.gain_db = value
        return SpectrometerResponse()

    def change_setting(self,setting,value):
        log.info(f"spi being told to change setting {setting} to {value}")
        f = self.lambdas.get(setting,None)
        if f is not None:
            f(value)
        return True

    def _init_process_funcs(self) -> dict[str, Callable[..., Any]]:
        process_f = {}

        process_f["connect"] = self.connect
        process_f["disconnect"] = self.disconnect
        process_f["acquire_data"] = self.acquire_data
        process_f["set_integration_time_ms"] = self.set_integration_time_ms
        process_f["detector_gain"] = self.set_gain
        ##################################################################
        # What follows is the old init-lambdas that are squashed into process_f
        # Long term, the upstream requests should be changed to match the new format
        # This is an easy fix for the time being to make things behave
        ##################################################################
       #process_f["write_eeprom"]                       = lambda x: self.write_eeprom()
       #process_f["replace_eeprom"]                     = lambda x: self.write_eeprom()
        process_f["integration_time_ms"]                = lambda x: self.set_integration_time_ms(x)

        return process_f 

    ############################################################################
    #                                                                          #
    #                               Utility Methods                            #
    #                                                                          #
    ############################################################################

    ## format a list or bytearray as "[ 0x00, 0x0a, 0xff ]"
    def to_hex(self, values):
        return "[ " + ", ".join([ f"0x{v:02x}" for v in values ]) + " ]"

    ## confirm the received CRC matches our computed CRC for the list or bytearray "data"
    def check_crc(self, crc_received, data):
        crc_computed = self.crc8(data)
        if crc_computed != crc_received:
            print(f"\nERROR *** CRC mismatch: received 0x{crc_received:02x}, computed 0x{crc_computed:02x}\n")

    ## given a list or bytearray of data elements, return the checksum
    def compute_crc(self, data):
        return self.crc8(bytearray(data))

    ##
    # given a formatted SPI command of the form [START, L0, L1, ADDR, ...DATA..., CRC, END],
    # return command with CRC replaced with the computed checksum of [L0..DATA] as bytearray
    def fix_crc(self, cmd):
        if cmd is None or len(cmd) < 6 or cmd[0] != self.START or cmd[-1] != self.END:
            log.error(f"fix_crc expects well-formatted SPI 'write' command: {cmd}")
            return

        index = len(cmd) - 2
        checksum = self.compute_crc(bytearray(cmd[1:index]))
        result = cmd[:index]
        result.extend([checksum, cmd[-1]])
        # log.debug(f"fix_crc: cmd {self.to_hex(cmd)} -> result {self.to_hex(result)}")
        return bytearray(result)

    ## @see ENG-0150-C section 3.2, "Configuration Set Response Packet"
    def errorcode_to_string(self, code) -> str:
        if   code == 0: return "SUCCESS"
        elif code == 1: return "ERROR_LENGTH"
        elif code == 2: return "ERROR_CRC"
        elif code == 3: return "ERROR_UNRECOGNIZED_COMMAND"
        else          : return "ERROR_UNDEFINED"

    ## @param response (Input): the last 3 bytes of the device's response to a SPI write command
    def validate_write_response(self, response) -> str:
        if len(response) != 3:
            return f"invalid response length: {response}"
        if response[0] != self.START:
            return f"invalid response START marker: {response}"
        if response[2] != self.END:
            return f"invalid response END marker: {response}"
        return self.errorcode_to_string(response[1])

    ##
    # Given an existing list or bytearray, copy the contents into a new bytearray of
    # the specified size. This is used to generate the "command" argument of a
    # SPI.write_readinto(cmd, response) call, as both buffers are expected to be of
    # the same size.
    #
    # @see https://docs.circuitpython.org/en/latest/shared-bindings/busio/#busio.SPI.write_readinto
    def buffer_bytearray(self, orig, size):
        new = bytearray(size)
        new[:len(orig)] = orig[:]
        return new

    ##
    # Given an unbuffered "read" command (just the bytes we wanted to write, without
    # trailing zeros for the read response), and the complete (buffered) response
    # read back (including leading junk from the command/write phase), parse out the
    # actual response data and validate checksum.
    #
    # @para Example (reading FPGA version number)
    # @verbatim
    #              offset:    0     1     2     3     4     5     6     7     8     9    10    11    12    13    14    15    16    17
    #         explanation:    <    (_length_)  ADDR   >     <    (_length_)  ADDR  '0'   '2'   '.'   '1'   '.'   '2'   '3'   CRC    >
    #  unbuffered_command: [ 0x3c, 0x00, 0x01, 0x10, 0x3e ]
    #    buffered_command: [ 0x3c, 0x00, 0x01, 0x10, 0x3e, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 ]
    #   buffered_response: [  ?? ,  ?? ,  ?? ,  ?? ,  ?? , 0x3c, 0x00, 0x08, 0x10, 0x30, 0x32, 0x2e, 0x31, 0x2e, 0x32, 0x33, 0x83, 0x3e ]
    # unbuffered_response:                               [ 0x3c, 0x00, 0x08, 0x10, 0x30, 0x32, 0x2e, 0x31, 0x2e, 0x32, 0x33, 0x83, 0x3e ]
    #            crc_data:                                     [ 0x00, 0x08, 0x10, 0x30, 0x32, 0x2e, 0x31, 0x2e, 0x32, 0x33 ]
    #       response_data:                                                       [ 0x30, 0x32, 0x2e, 0x31, 0x2e, 0x32, 0x33 ]
    # @endverbatim
    #
    # @returns array of response payload bytes (everything after ADDR but before CRC)
    # @note only used for SPI "read" commands ("write" commands are much simpler)
    def decode_read_response(self, unbuffered_cmd, buffered_response, name=None, missing_echo_len=0):
        cmd_len = len(unbuffered_cmd)
        unbuffered_response = buffered_response[len(unbuffered_cmd) - missing_echo_len:]
        response_data_len = (unbuffered_response[1] << 8) | unbuffered_response[2]
        response_data = unbuffered_response[4 : 4 + response_data_len - 1]
        crc_received = unbuffered_response[-2]
        crc_data = unbuffered_response[1 : -2]
        self.check_crc(crc_received, crc_data)

        if True:
            log.debug(f"decode_read_response({name}, missing={missing_echo_len}):")
            log.debug(f"  unbuffered_cmd:      {self.to_hex(unbuffered_cmd)}")
            log.debug(f"  buffered_response:   {self.to_hex(buffered_response)}")
            log.debug(f"  cmd_len:             {cmd_len}")
            log.debug(f"  unbuffered_response: {self.to_hex(unbuffered_response)}")
            log.debug(f"  response_data_len:   {response_data_len}")
            log.debug(f"  response_data:       {self.to_hex(response_data)}")
            log.debug(f"  crc_received:        {hex(crc_received)}")
            log.debug(f"  crc_data:            {self.to_hex(crc_data)}")

        return response_data

    ## @returns response payload as string
    def decode_read_response_str(self, unbuffered_cmd, buffered_response, name=None, missing_echo_len=0) -> str:
        return self.decode_read_response(unbuffered_cmd, buffered_response, name, missing_echo_len).decode()

    ## @returns little-endian response payload as uint16
    def decode_read_response_int(self, unbuffered_cmd, buffered_response, name=None, missing_echo_len=0) -> int:
        response_data = self.decode_read_response(unbuffered_cmd, buffered_response, name, missing_echo_len)
        result = (response_data[1] << 8) | response_data[0]
        log.debug(f"  result:              {result}")
        return result

    def decode_write_response_UNUSED(self, unbuffered_cmd, buffered_response, name=None, missing_echo_len=0):
        cmd_len = len(unbuffered_cmd)
        unbuffered_response = buffered_response[len(unbuffered_cmd) - missing_echo_len:]
        response_data_len = (unbuffered_response[1] << 8) | unbuffered_response[2]
        response_data = unbuffered_response[4 : 4 + response_data_len - 1]
        crc_received = unbuffered_response[-2]
        crc_data = unbuffered_response[1 : -2]
        self.chec_crc(crc_received, crc_data)

        if True:
            log.debug(f"decode_read_response({name}, missing={missing_echo_len}):")
            log.debug(f"  unbuffered_cmd:      {self.to_hex(unbuffered_cmd)}")
            log.debug(f"  buffered_response:   {self.to_hex(buffered_response)}")
            log.debug(f"  cmd_len:             {cmd_len}")
            log.debug(f"  unbuffered_response: {self.to_hex(unbuffered_response)}")
            log.debug(f"  response_data_len:   {response_data_len}")
            log.debug(f"  response_data:       {self.to_hex(response_data)}")
            log.debug(f"  crc_received:        {hex(crc_received)}")
            log.debug(f"  crc_data:            {self.to_hex(crc_data)}")

        return response_data

    def send_command(self, cmd):
        txData = []
        txData      .append( cmd.value        & 0xff) # LSB
        if cmd.write_len > 2:
            txData  .append((cmd.value >>  8) & 0xff)
        if cmd.write_len > 3:
            txData  .append((cmd.value >> 16) & 0xff) # MSB

        unbuffered_cmd = [self.START, 0x00, cmd.write_len, cmd.address | self.WRITE]
        unbuffered_cmd.extend(txData)
        unbuffered_cmd.extend([ self.compute_crc(unbuffered_cmd[1:]), self.END])

        # MZ: the -1 at the end was added as a kludge, because otherwise we find
        #     a redundant '>' in the last byte.  This seems a bug, due to the
        #     fact that only 7 of the 8 unbuffered_cmd bytes are echoed back into
        #     the read buffer.
        buffered_response = bytearray(len(unbuffered_cmd) + self.WRITE_RESPONSE_OVERHEAD + 1 - 1)
        buffered_cmd = self.buffer_bytearray(unbuffered_cmd, len(buffered_response))

        with self.lock:
            self.flush_input_buffer()
            self.SPI.write_readinto(buffered_cmd, buffered_response)

        error_msg = self.validate_write_response(buffered_response[-3:])
        log.debug(f"send_command[{cmd.name}]: {self.to_hex(buffered_cmd)} -> {self.to_hex(buffered_response)} ({error_msg})")

    def flush_input_buffer(self):
        count = 0
        junk = bytearray(self.READY_POLL_LEN)
        while self.ready.value:
            self.SPI.readinto(junk)
            count += 1
        if count > 0:
            log.debug(f"flushed {count} bytes from input buffer")

    def waitForDataReady(self):
        while not self.ready.value:
            pass

    ##
    # Convert a (potentially) floating-point value into the big-endian 16-bit "Funky
    # Float" used for detector gain in the FPGA on both Hamamatsu and IMX sensors.
    #
    # @see https://wasatchphotonics.com/api/Wasatch.NET/class_wasatch_n_e_t_1_1_funky_float.html
    def gain_to_ff(self, gain):
        msb = int(round(gain, 5)) & 0xff
        lsb = int((gain - msb) * 256) & 0xff
        raw = (msb << 8) | lsb
        log.debug(f"gain_to_ff: {gain:0.3f} -> dec {raw} (0x{raw:04x})")
        return raw

    def read_page(self, page: int) -> list[bytes]:
        with self.lock:
            # send 0xb0 command to tell FPGA to load EEPROM page into FPGA buffer
            unbuffered_cmd = self.fix_crc([self.START, 0, 2, 0xB0, 0x40 + page, self.CRC, self.END])
            buffered_response = bytearray(len(unbuffered_cmd) + self.READ_RESPONSE_OVERHEAD + 1)
            buffered_cmd = self.buffer_bytearray(unbuffered_cmd, len(buffered_response))
            self.SPI.write_readinto(buffered_cmd, buffered_response)
            log.debug(f">> read_page: {self.to_hex(buffered_cmd)} -> {self.to_hex(buffered_response)}")

            # MZ: API says "wait for SPEC_BUSY to be deasserted...why aren't we doing that?
            self.sleep_ms(10) # empirically determined 10ms delay

            # send 0x31 command to read the buffered page from the FPGA
            unbuffered_cmd = self.fix_crc([self.START, 0, 65, 0x31, self.CRC, self.END])
            buffered_response = bytearray(len(unbuffered_cmd) + self.READ_RESPONSE_OVERHEAD + 64) # MZ: including kludged -1
            buffered_cmd = self.buffer_bytearray(unbuffered_cmd, len(buffered_response))
            self.SPI.write_readinto(buffered_cmd, buffered_response)

        buf = self.decode_read_response(unbuffered_cmd, buffered_response, "read_eeprom_page", missing_echo_len=1)
        log.debug(f"decoded {len(buf)} values from EEPROM")
        return buf

    def sleep_ms(self, ms):
        ms = int(round(ms))
        sec = ms / 1000.0
        log.debug(f"sleeping {ms} ms")
        time.sleep(sec)

    def get_spectrum(self) -> list[int]:
        with self.lock:

            ####################################################################
            # Trigger Acquisition
            ####################################################################

            if self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_EXTERNAL:
                log.debug("waiting on external trigger...")
                self.waitForDataReady()
            else:
                # send trigger via the FT232H
                self.trigger.value = True
                self.waitForDataReady()
                self.trigger.value = False

            ####################################################################
            # Read the spectrum (MZ: big-endian, seriously?)
            ####################################################################

            spectrum = []
            pixels = self.settings.pixels()
            bytes_remaining = pixels * 2

            log.debug(f"getSpectrum: reading spectrum of {pixels} pixels")
            raw = []
            while self.ready.value:
                if bytes_remaining > 0:
                    bytes_this_read = min(self.block_size, bytes_remaining)

                    log.debug(f"getSpectrum: reading block of {bytes_this_read} bytes")
                    buf = bytearray(bytes_this_read)

                    # there is latency associated with this call, so call it as
                    # few times as possible (with the largest possible block size)
                    self.SPI.readinto(buf)

                    log.debug(f"getSpectrum: read block of {len(buf)} bytes")
                    raw.extend(list(buf))

                    bytes_remaining -= len(buf)

        ########################################################################
        # post-process spectrum
        ########################################################################

        # demarshall big-endian
        for i in range(0, len(raw)-1, 2):
            spectrum.append((raw[i] << 8) | raw[i+1])
        log.debug(f"getSpectrum: {len(spectrum)} pixels read")

        return spectrum
