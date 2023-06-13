import re
import os
import usb
import time
import logging
import datetime
import threading
from queue import Queue
import usb.core
usb.core.find()
from .SpectrometerSettings import SpectrometerSettings
from .SpectrometerState import SpectrometerState
from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest import SpectrometerRequest
from .SpectrometerResponse import ErrorLevel
from .ControlObject import ControlObject
from .DeviceID import DeviceID
from .InterfaceDevice import InterfaceDevice
from .Reading import Reading
from .EEPROM import EEPROM
import crcmod.predefined
log = logging.getLogger(__name__)


class CommandTuple:

    def __init__(self, address, value, write_len, name):
        self.address = address
        self.value = value
        self.write_len = write_len
        self.name = name

    def __str__(self):
        return (
            f'CommandTuple <name {self.name}, address 0x{self.address:02x}, value {self.value}, write_len {self.write_len}>'
            )


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

                                /   /  |  \\  
             { get_laser status, acquire, set_laser_watchdog, etc....}

                                \\   \\  |  /  /

                                 ------------

                                       |

                         {self.driver.some_spi_call}

    @endverbatim



    @see https://github.com/WasatchPhotonics/Python-USB-WP-Raman-Examples/blob/master/SPI/spi_console.py

    """
    READ_RESPONSE_OVERHEAD = 5
    WRITE_RESPONSE_OVERHEAD = 2
    READY_POLL_LEN = 2
    START = 60
    END = 62
    WRITE = 128
    CRC = 255
    lock = threading.Lock()

    def __init__(self, device_id, message_queue):
        super().__init__()
        import_successful = False
        try:
            os.environ['BLINKA_FT232H'] = '1'
            import board
            import digitalio
            import busio
            import_successful = True
        except RuntimeError as ex:
            log.error('No FT232H connected.', exc_info=1)
            if platform.system() == 'Windows':
                log.error(
                    "Ensure you've followed the Zadig process in README_SPI.md"
                    )
        except ValueError as ex:
            log.error(
                "If you are receiving 'no backend available' errors, try the following:"
                )
            log.error('MacOS:  $ export DYLD_LIBRARY_PATH=/usr/local/lib')
            log.error('Linux:  $ export LD_LIBRARY_PATH=/usr/local/lib')
        except FtdiError as ex:
            log.error('No FT232H connected.', exc_info=1)
            if platform.system() == 'Windows':
                log.error(
                    "Ensure you've followed the Zadig process in README_SPI.md"
                    )
            log.error('SPIDevice is not usable')
        if not import_successful:
            return
        self.crc8 = crcmod.predefined.mkPredefinedCrcFun('crc-8-maxim')
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)
        self.device_id = device_id
        self.message_queue = message_queue
        self.connected = False
        self.disconnect = False
        self.acquiring = False
        self.command_queue = []
        self.immediate_mode = False
        self.settings = SpectrometerSettings(self.device_id)
        self.summed_spectra = None
        self.sum_count = 0
        self.session_reading_count = 0
        self.take_one = False
        self.failure_count = 0
        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0
        self.lambdas = None
        self.spec_index = 0
        self._scan_averaging = 1
        self.dark = None
        self.boxcar_half_width = 0
        self.SPI = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)
        self.ready = digitalio.DigitalInOut(getattr(board, os.getenv(
            'SPI_PIN_READY', default='D5')))
        self.ready.direction = digitalio.Direction.INPUT
        self.trigger = digitalio.DigitalInOut(getattr(board, os.getenv(
            'SPI_PIN_TRIGGER', default='D6')))
        self.trigger.direction = digitalio.Direction.OUTPUT
        self.trigger.value = False
        while not self.SPI.try_lock():
            pass
        self.baud_mhz = int(os.getenv('SPI_BAUD_MHZ', default='10'))
        log.debug(f'using baud rate {self.baud_mhz}MHz')
        self.SPI.configure(baudrate=self.baud_mhz * 1000000.0, phase=0,
            polarity=0, bits=8)
        self.block_size = int(os.getenv('SPI_BLOCK_SIZE', default='256'))
        log.debug(f'using SPI block size {self.block_size}')
        self.process_f = self._init_process_funcs()
        self.cmds = {}
        for addr, value, len_, name in [(17, 3, 4, 'Integration Time'), (19,
            0, 3, 'Black Level'), (20, 24, 3, 'Gain dB'), (43, 3, 2,
            'Pixel Mode'), (80, 250, 3, 'Start Line 0'), (81, 750, 3,
            'Stop Line 0'), (82, 12, 3, 'Start Column 0'), (83, 1932, 3,
            'Stop Column 0'), (84, 0, 3, 'Start Line 1'), (85, 0, 3,
            'Stop Line 1'), (86, 0, 3, 'Start Column 1'), (87, 0, 3,
            'Stop Column 1'), (88, 0, 3, 'Desmile')]:
            self.cmds[name] = CommandTuple(addr, value, len_, name)
        cmd = self.cmds['Gain dB']
        cmd.value = self.gain_to_ff(cmd.value)

    def connect(self):
        log.debug('initializing EEPROM')
        if not self.init_eeprom():
            log.critical('failed to initialize EEPROM, giving up')
            return SpectrometerResponse(False, error_msg=
                'EEPROM initialization failure', error_lvl=ErrorLevel.high,
                poison_pill=True)
        log.debug('initializing all commands')
        for key in self.cmds:
            cmd = self.cmds[key]
            log.debug(f'initializing {cmd}')
            self.send_command(cmd)
        self.settings.state.integration_time_ms = (self.settings.eeprom.
            startup_integration_time_ms)
        self.settings.state.gain_db = self.settings.eeprom.detector_gain
        log.info('SPI connect done, returning True')
        return SpectrometerResponse(True)

    def init_eeprom(self):
        eeprom = self.settings.eeprom
        pages = []
        for i in range(EEPROM.MAX_PAGES):
            log.debug(f'flushing buffer before page {i}')
            if not self.flush_input_buffer():
                log.error('unable to read EEPROM')
                return False
            pages.append(self.read_page(i))
        if not eeprom.parse(pages):
            log.error(f'failed to parse EEPROM')
            return False
        self.settings.update_wavecal()
        self.settings.update_raman_intensity_factors()
        self.cmds['Integration Time'
            ].value = eeprom.startup_integration_time_ms
        self.cmds['Gain dB'].value = self.gain_to_ff(eeprom.detector_gain)
        self.cmds['Start Line 0'].value = eeprom.roi_vertical_region_1_start
        self.cmds['Stop Line 0'].value = eeprom.roi_vertical_region_1_end
        self.cmds['Start Column 0'].value = eeprom.roi_horizontal_start
        self.cmds['Stop Column 0'].value = eeprom.roi_horizontal_end
        return True

    def disconnect(self):
        self.disconnect = True
        return SpectrometerResponse(True)

    def acquire_data(self):
        log.debug('spi starts reading')
        if self.disconnect:
            log.debug('disconnecting, returning False for the spectrum')
            return False
        averaging_enabled = self.settings.state.scans_to_average > 1
        reading = Reading(self.device_id)
        try:
            reading.integration_time_ms = (self.settings.state.
                integration_time_ms)
            reading.laser_power_perc = self.settings.state.laser_power_perc
            reading.laser_power_mW = self.settings.state.laser_power_mW
            reading.laser_enabled = self.settings.state.laser_enabled
            reading.spectrum = self.get_spectrum()
            if reading.spectrum == False:
                return False
        except usb.USBError:
            self.failure_count += 1
            log.error(
                f'SPI Device: encountered USB error in reading for device {self.device}'
                )
        if not reading.failure:
            if averaging_enabled:
                if self.sum_count == 0:
                    self.summed_spectra = [float(i) for i in reading.spectrum]
                else:
                    log.debug(
                        'device.take_one_averaged_reading: summing spectra')
                    for i in range(len(self.summed_spectra)):
                        self.summed_spectra[i] += reading.spectrum[i]
                self.sum_count += 1
                log.debug(
                    'device.take_one_averaged_reading: summed_spectra : %s ...'
                    , self.summed_spectra[0:9])
        if self.settings.eeprom.bin_2x2:
            next_idx_values = reading.spectrum[1:]
            binned = [((value + next_value) / 2) for value, next_value in
                zip(reading.spectrum[:-1], next_idx_values)]
            binned.append(reading.spectrum[-1])
            reading.spectrum = binned
        self.session_reading_count += 1
        reading.session_count = self.session_reading_count
        reading.sum_count = self.sum_count
        if averaging_enabled:
            if self.sum_count >= self.settings.state.scans_to_average:
                reading.spectrum = [(x / self.sum_count) for x in self.
                    summed_spectra]
                log.debug('spi device acquire data: averaged_spectrum : %s ...'
                    , reading.spectrum[0:9])
                reading.averaged = True
                self.summed_spectra = None
                self.sum_count = 0
        else:
            reading.averaged = True
        return SpectrometerResponse(reading)

    def set_integration_time_ms(self, value):
        cmd = self.cmds['Integration Time']
        cmd.value = value
        self.send_command(cmd)
        self.settings.state.integration_time_ms = value
        return SpectrometerResponse()

    def set_gain(self, value):
        cmd = self.cmds['Gain dB']
        cmd.value = self.gain_to_ff(value)
        self.send_command(cmd)
        self.settings.state.gain_db = value
        return SpectrometerResponse()

    def change_setting(self, setting, value):
        log.info(f'spi being told to change setting {setting} to {value}')
        f = self.lambdas.get(setting, None)
        if f is not None:
            f(value)
        return True

    def _init_process_funcs(self):
        process_f = {}
        process_f['connect'] = self.connect
        process_f['disconnect'] = self.disconnect
        process_f['acquire_data'] = self.acquire_data
        process_f['set_integration_time_ms'] = self.set_integration_time_ms
        process_f['detector_gain'] = self.set_gain
        process_f['integration_time_ms'
            ] = lambda x: self.set_integration_time_ms(x)
        return process_f

    def to_hex(self, values):
        return '[ ' + ', '.join([f'0x{v:02x}' for v in values]) + ' ]'

    def check_crc(self, crc_received, data):
        crc_computed = self.crc8(data)
        if crc_computed != crc_received:
            log.error(
                f'CRC mismatch: received 0x{crc_received:02x}, computed 0x{crc_computed:02x}'
                )

    def compute_crc(self, data):
        return self.crc8(bytearray(data))

    def fix_crc(self, cmd):
        if cmd is None or len(cmd) < 6 or cmd[0] != self.START or cmd[-1
            ] != self.END:
            log.error(
                f"fix_crc expects well-formatted SPI 'write' command: {cmd}")
            return
        index = len(cmd) - 2
        checksum = self.compute_crc(bytearray(cmd[1:index]))
        result = cmd[:index]
        result.extend([checksum, cmd[-1]])
        return bytearray(result)

    def errorcode_to_string(self, code):
        if code == 0:
            return 'SUCCESS'
        elif code == 1:
            return 'ERROR_LENGTH'
        elif code == 2:
            return 'ERROR_CRC'
        elif code == 3:
            return 'ERROR_UNRECOGNIZED_COMMAND'
        else:
            return 'ERROR_UNDEFINED'

    def validate_write_response(self, response):
        if len(response) != 3:
            return f'invalid response length: {response}'
        if response[0] != self.START:
            return f'invalid response START marker: {response}'
        if response[2] != self.END:
            return f'invalid response END marker: {response}'
        return self.errorcode_to_string(response[1])

    def buffer_bytearray(self, orig, size):
        new = bytearray(size)
        new[:len(orig)] = orig[:]
        return new

    def decode_read_response(self, unbuffered_cmd, buffered_response, name=
        None, missing_echo_len=0):
        cmd_len = len(unbuffered_cmd)
        unbuffered_response = buffered_response[len(unbuffered_cmd) -
            missing_echo_len:]
        response_data_len = unbuffered_response[1] << 8 | unbuffered_response[2
            ]
        response_data = unbuffered_response[4:4 + response_data_len - 1]
        crc_received = unbuffered_response[-2]
        crc_data = unbuffered_response[1:-2]
        self.check_crc(crc_received, crc_data)
        if True:
            log.debug(
                f'decode_read_response({name}, missing={missing_echo_len}):')
            log.debug(f'  unbuffered_cmd:      {self.to_hex(unbuffered_cmd)}')
            log.debug(
                f'  buffered_response:   {self.to_hex(buffered_response)}')
            log.debug(f'  cmd_len:             {cmd_len}')
            log.debug(
                f'  unbuffered_response: {self.to_hex(unbuffered_response)}')
            log.debug(f'  response_data_len:   {response_data_len}')
            log.debug(f'  response_data:       {self.to_hex(response_data)}')
            log.debug(f'  crc_received:        {hex(crc_received)}')
            log.debug(f'  crc_data:            {self.to_hex(crc_data)}')
        return response_data

    def send_command(self, cmd):
        txData = []
        txData.append(cmd.value & 255)
        if cmd.write_len > 2:
            txData.append(cmd.value >> 8 & 255)
        if cmd.write_len > 3:
            txData.append(cmd.value >> 16 & 255)
        unbuffered_cmd = [self.START, 0, cmd.write_len, cmd.address | self.
            WRITE]
        unbuffered_cmd.extend(txData)
        unbuffered_cmd.extend([self.compute_crc(unbuffered_cmd[1:]), self.END])
        buffered_response = bytearray(len(unbuffered_cmd) + self.
            WRITE_RESPONSE_OVERHEAD + 1 - 1)
        buffered_cmd = self.buffer_bytearray(unbuffered_cmd, len(
            buffered_response))
        with self.lock:
            if not self.flush_input_buffer():
                log.critical(f'failed to send command {cmd}')
                return
            self.SPI.write_readinto(buffered_cmd, buffered_response)
        error_msg = self.validate_write_response(buffered_response[-3:])
        log.debug(
            f'send_command[{cmd.name}]: {self.to_hex(buffered_cmd)} -> {self.to_hex(buffered_response)} ({error_msg})'
            )

    def flush_input_buffer(self):
        count = 0
        junk = bytearray(self.READY_POLL_LEN)
        MAX_READ = 4096
        try:
            while self.ready.value:
                self.SPI.readinto(junk)
                count += self.READY_POLL_LEN
                if count > MAX_READ:
                    log.critical(
                        f'flush_input_buffer: giving up after flushing {count} bytes'
                        )
                    return False
        except OSError:
            log.critical('flush_input_buffer: OSError', exc_info=1)
            return False
        except pyftdi.ftdi.FtdiError:
            log.critical('flush_input_buffer: FtdiError', exc_info=1)
            return False
        if count > 0:
            log.debug(f'flushed {count} bytes from input buffer')
        return True

    def wait_for_data_ready(self):
        while not self.ready.value:
            pass

    def gain_to_ff(self, gain):
        msb = int(round(gain, 5)) & 255
        lsb = int((gain - msb) * 256) & 255
        raw = msb << 8 | lsb
        log.debug(f'gain_to_ff: {gain:0.3f} -> dec {raw} (0x{raw:04x})')
        return raw

    def read_page(self, page):
        with self.lock:
            unbuffered_cmd = self.fix_crc([self.START, 0, 2, 176, 64 + page,
                self.CRC, self.END])
            buffered_response = bytearray(len(unbuffered_cmd) + self.
                READ_RESPONSE_OVERHEAD + 1)
            buffered_cmd = self.buffer_bytearray(unbuffered_cmd, len(
                buffered_response))
            self.SPI.write_readinto(buffered_cmd, buffered_response)
            log.debug(
                f'>> read_page: {self.to_hex(buffered_cmd)} -> {self.to_hex(buffered_response)}'
                )
            time.sleep(0.01)
            unbuffered_cmd = self.fix_crc([self.START, 0, 65, 49, self.CRC,
                self.END])
            buffered_response = bytearray(len(unbuffered_cmd) + self.
                READ_RESPONSE_OVERHEAD + 64)
            buffered_cmd = self.buffer_bytearray(unbuffered_cmd, len(
                buffered_response))
            self.SPI.write_readinto(buffered_cmd, buffered_response)
        buf = self.decode_read_response(unbuffered_cmd, buffered_response,
            'read_eeprom_page', missing_echo_len=1)
        log.debug(f'decoded {len(buf)} values from EEPROM')
        return buf

    def get_spectrum(self):
        with self.lock:
            if (self.settings.state.trigger_source == SpectrometerState.
                TRIGGER_SOURCE_EXTERNAL):
                log.debug('waiting on external trigger...')
                self.wait_for_data_ready()
            else:
                self.trigger.value = True
                self.wait_for_data_ready()
                self.trigger.value = False
            spectrum = []
            pixels = self.settings.pixels()
            bytes_remaining = pixels * 2
            log.debug(f'get_spectrum: reading spectrum of {pixels} pixels')
            raw = []
            while self.ready.value:
                if bytes_remaining > 0:
                    bytes_this_read = min(self.block_size, bytes_remaining)
                    buf = bytearray(bytes_this_read)
                    self.SPI.readinto(buf)
                    raw.extend(list(buf))
                    bytes_remaining -= len(buf)
        for i in range(0, len(raw) - 1, 2):
            spectrum.append(raw[i] << 8 | raw[i + 1])
        log.debug(
            f'get_spectrum: {len(spectrum)} pixels read ({spectrum[:3]} .. {spectrum[-3:]})'
            )
        return spectrum
