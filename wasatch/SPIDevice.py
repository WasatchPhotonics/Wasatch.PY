import re
import os
import usb
import time
import json
import struct
import logging
import datetime
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

log = logging.getLogger(__name__)

class SPIDevice(InterfaceDevice):
    """
    @todo that we still need to add:

    CRC validation
    set_gain_db
    set_pixel_mode (i.e. detector resolution)
    set_start_line
    set_stop_line
    set_start_column
    set_stop_column
    set_trigger_mode (not currently documented in ENG-0150)

    This implements SPI communication via the FT232H usb converter.     

    @todo convert the different asserts to SpectrometerResponse returns
    ##########################################################################
    This class adopts the external device interface structure
    This involves receiving a request through the handle_request function
    A request is processed based on the key in the request
    The processing function passes the commands to the requested device
    Once it receives a response from the connected device it then passes that
    back up the chain
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
    ############################################################################
    """

    INTEGRATION_ADDRESS = 0x11
    GAIN_DB_ADDRESS = 0x14
    CRC_8_TABLE = [
      0, 94,188,226, 97, 63,221,131,194,156,126, 32,163,253, 31, 65,
    157,195, 33,127,252,162, 64, 30, 95,  1,227,189, 62, 96,130,220,
     35,125,159,193, 66, 28,254,160,225,191, 93,  3,128,222, 60, 98,
    190,224,  2, 92,223,129, 99, 61,124, 34,192,158, 29, 67,161,255,
     70, 24,250,164, 39,121,155,197,132,218, 56,102,229,187, 89,  7,
    219,133,103, 57,186,228,  6, 88, 25, 71,165,251,120, 38,196,154,
    101, 59,217,135,  4, 90,184,230,167,249, 27, 69,198,152,122, 36,
    248,166, 68, 26,153,199, 37,123, 58,100,134,216, 91,  5,231,185,
    140,210, 48,110,237,179, 81, 15, 78, 16,242,172, 47,113,147,205,
     17, 79,173,243,112, 46,204,146,211,141,111, 49,178,236, 14, 80,
    175,241, 19, 77,206,144,114, 44,109, 51,209,143, 12, 82,176,238,
     50,108,142,208, 83, 13,239,177,240,174, 76, 18,145,207, 45,115,
    202,148,118, 40,171,245, 23, 73,  8, 86,180,234,105, 55,213,139,
     87,  9,235,181, 54,104,138,212,149,203, 41,119,244,170, 72, 22,
    233,183, 85, 11,136,214, 52,106, 43,117,151,201, 74, 20,246,168,
    116, 42,200,150, 21, 75,169,247,182,232, 10, 84,215,137,107, 53
    ]


    def __init__(self, device_id: DeviceID, message_queue: Queue) -> None:
        # if passed a string representation of a DeviceID, deserialize it
        super().__init__()
        try:
            import board
            import time
            import board
            import digitalio
            import busio
        except Exception as e:
            log.error(f"Problem importing board for SPI device of {e}")

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
        self.SPI  = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)

        # Initialize D5 as the ready signal
        self.ready = digitalio.DigitalInOut(board.D5)
        self.ready.direction = digitalio.Direction.INPUT

        # Initialize D6 as the trigger
        self.trigger = digitalio.DigitalInOut(board.D6)
        self.trigger.direction = digitalio.Direction.OUTPUT
        self.trigger.value = False

        # Take control of the SPI Bus
        while not self.SPI.try_lock():
            pass

        # Configure the SPI bus
        self.SPI.configure(baudrate=20000000, phase=0, polarity=0, bits=8)
        self.process_f = self._init_process_funcs()

    def connect(self) -> SpectrometerResponse:
        eeprom_pages = []
        for i in range(EEPROM.MAX_PAGES):
            response = bytearray(2)
            while self.ready.value:
                self.SPI.readinto(response,0,2)
            page = self._EEPROMReadPage(i)
            eeprom_pages.append(page)
        self.settings.eeprom.parse(eeprom_pages)
        self.settings.update_wavecal()
        self.settings.update_raman_intensity_factors()
        self.settings.state.integration_time_ms = 10
        log.info("SPI connect done, returning True")
        return SpectrometerResponse(True)

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
            reading.spectrum            = self._Acquire()
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

    def write_eeprom(self) -> SpectrometerResponse:
        try:
            self.settings.eeprom.generate_write_buffers()
        except:
            log.critical("failed to render EEPROM write buffers", exc_info=1)
            #self.message_queue("marquee_error", "Failed to write EEPROM")
            return False

        for page in range(EEPROM.MAX_PAGES):
            time.sleep(0.1) # for now a hard coded delay for the eeprom
            self._EEPROMWritePage(page,self.settings.eeprom.write_buffers[page])

        #self.message_queue("marquee_info", "EEPROM successfully updated")
        return SpectrometerResponse(True)

    def _EEPROMReadPage(self, page: int) -> list[bytes]:
        while True:
            EEPROMPage  = bytearray(75)
            command     = bytearray(7)
            command     = [0x3C, 0x00, 0x02, 0xB0, (0x40 + page), 0xFF, 0x3E]
            self.SPI.write(command, 0, 7)
            self._SPIBusy()
            command = [0x3C, 0x00, 0x01, 0x31, 0xFF, 0x3E]
            self.SPI.write_readinto(command, EEPROMPage)
            log.debug(f"raw eeprom dump is {EEPROMPage}")
            try:
                EEPROMPage = EEPROMPage[EEPROMPage.index(b'<')+4:len(EEPROMPage)-1]
            except:
                log.error(f"SPI EEPROM read got a page without start byte, trying to re-read. Page was {EEPROMPage}")
                continue
            if EEPROMPage[3] == 0x3:
                continue
            else:
                log.debug(f"sliced page value is {EEPROMPage}")
                break
        self._SPIBusy()
        log.debug(f"for page {page} got values {EEPROMPage}")
        return EEPROMPage

    def set_integration_time_ms(self, value: int) -> SpectrometerResponse:
        while self.acquiring:
            time.sleep(0.01)
            continue
        self._SPIWrite(value, self.INTEGRATION_ADDRESS)
        self.settings.state.integration_time_ms = value
        return SpectrometerResponse()

    def set_gain(self, value: int) -> SpectrometerResponse:
        if not isinstance(value,int):
            value = int(value)
        while self.acquiring:
            time.sleep(0.01)
            continue
        self._SPIWrite(value, self.GAIN_DB_ADDRESS)
        self.settings.state.gain_DB = value
        return SpectrometerResponse()

    def _SPIWrite(self, value: int, address: int) -> None:
        command = bytearray(8)
        # Convert the int into bytes.
        txData = bytearray(2)
        txData[1]   = value >> 8
        txData[0]   = value - (txData[1] << 8)
        # A write command consists of opening and closing delimeters, the payload size which is data + 1 (for the command byte),
        # the command/address with the MSB set for a write operation, the payload data, and the CRC. This function does not 
        # caluculate the CRC nor read back the status.
        # Refer to ENG-150 for additional information
        command = [0x3C, 0x00, 0x03, (address+0x80), txData[0], txData[1], 0xFF, 0x3E]
        self.SPI.write(command, 0, 8)
        time.sleep(0.01)

    def _SPIBusy(self):
        command  = bytearray(5)
        response = bytearray(19)
        response = [0x3]*19
        while response == [0x3]*19:
            command = [0x3C, 0x00, 0x01, 0x10, 0x3E] # check for the FPGA Rev num
            self.SPI.write_readinto(command, response)

    def _EEPROMWritePage(self, page: int, write_array: list[bytes]) -> None:
        log.debug(f"attempting to write eeprom page {page}")
        read_cmd = bytearray(8)
        command     = bytearray(7)
        read_cmd = [0x3,0x3,0x3,0x3,0x3,0x3] # hard code to all 0x3 so it checks at least once

        self._SPIBusy()
        EEPROMWrCmd = bytearray(70)
        EEPROMWrCmd[0:3] = [0x3C, 0x00, 0x41, 0xB1]
        try:
            for x in range(0, 64):
                log.info(f"spi writing to page {page} with value {write_array[x]}")
                EEPROMWrCmd[x+4] = write_array[x]
        except Exception as e:
            log.error(f"spi failed to write value of {write_array[x]} to page {page}. had exception {e}")
            raise e

        EEPROMWrCmd[68] = 0xFF
        EEPROMWrCmd[69] = 0x3E
        self.SPI.write(EEPROMWrCmd, 0, 70)
        time.sleep(0.1)
        command = [0x3C, 0x00, 0x02, 0xB0, (0x80 + page), 0xFF, 0x3E]
        self.SPI.write(command, 0, 7)
        time.sleep(0.1)

    def change_setting(self,setting,value):
        log.info(f"spi being told to change setting {setting} to {value}")
        f = self.lambdas.get(setting,None)
        if f is not None:
            f(value)
        return True

    def _Acquire(self) -> list[int]:
        if self.disconnect:
            return False
        self.acquiring = True
        log.debug("calling acquire")
        SPIBuf  = bytearray(2)
        spectra = []
        # Send and acquire trigger
        self.trigger.value = True

        # Wait until the data is ready
        log.debug("waiting for data to be ready")
        while not self.ready.value:
            #log.debug("spi waiting")
            pass
        log.debug("data is ready")

        # Release the trigger
        self.trigger.value = False

        # Read in the spectra
        log.debug("reading spectra pixels")
        while self.ready.value:
            self.SPI.readinto(SPIBuf, 0, 2)
            pixel = (SPIBuf[0] << 8) + SPIBuf[1]
            spectra.append(pixel)

        log.debug(f"returning spectra of length ({len(spectra)})")
        self.acquiring = False
        return spectra

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
        process_f["write_eeprom"]                       = lambda x: self.write_eeprom()
        process_f["replace_eeprom"]                     = lambda x: self.write_eeprom()
        process_f["integration_time_ms"]                = lambda x: self.set_integration_time_ms(x)

        return process_f 