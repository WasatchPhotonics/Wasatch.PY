import usb
import math
import struct
import logging

from SpectrometerSettings import SpectrometerSettings

log = logging.getLogger(__name__)

USB_TIMEOUT_MS = 60000

##
# Older Wasatch Photonics spectrometers (before 2014 or so) used a protocol
# called "Stroker Protocol" (SP).  They had a variety of USB PIDs starting from 
# 0x0001 and climbing into the low 2-digits.  These spectrometers had no EEPROM
# or any way to store configuration data on-board.  No StrokerProtocol devices
# are still being manufactured or sold, but cursory support for them is maintained
# through applications such as ENLIGHTEN.  
#
# As a historical note, the term "stroker" in this context was an homage to 
# automotive performance: https://en.wikipedia.org/wiki/Stroker_kit
#
class StrokerProtocolDevice(object):

    def __init__(self, vid="0x24aa", pid="0x0001"):
        log.debug("init %s", pid)
        self.vid = int(vid, 16)
        self.pid = int(pid, 16)

        self.device = None

        self.laser_temperature_invalid = False
        self.detector_temperature_invalid = False

        self.settings = SpectrometerSettings()

        # apply StrokerProtocol defaults
        self.settings.eeprom.model = "Stroker Protocol Device"
        self.settings.eeprom.wavelength_coeffs = [ 0, 1, 0, 0 ]

        self.detector_tec_setpoint_has_been_set = False
        self.secondary_adc_enabled = False
        self.invert_x_axis = False

        # Defaults from Original (Stroker-era) settings. These are known
        # to set ccd setpoints effectively for Stroker 785-class units.
        self.settings.eeprom.degC_to_dac_coeffs = [ 3566.62, -143.543, -0.324723 ]

        self.last_applied_laser_power = 0
        self.next_applied_laser_power = 100

    ## Attempt to connect to the specified device. Log any failures and
    #  return False if there is a problem, otherwise return True. 
    def connect(self):
        try:
            device = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        except Exception as exc:
            log.critical("Exception in find: %s", exc)
            log.info("Is the device available with libusb?")
            raise

        if device is None:
            log.critical("Can't find: %s, %s" % (self.vid, self.pid))
            return False

        log.debug("Attempt to set configuration")
        try:
            result = device.set_configuration(1)
        except Exception as exc:
            log.warn("Hardware Failure in setConfiguration %s", exc)
            raise

        try:
            result = usb.util.claim_interface(device, 0)
        except Exception as exc:
            log.warn("Hardware Failure in claimInterface: %s", exc)
            raise

        self.device = device

        log.debug("reading serial number")
        self.load_serial_number()
        log.debug("serial_number = %s", self.settings.eeprom.serial_number)

        log.debug("getting pixels")
        self.get_sensor_line_length()

        return True

    ## Function stub for historical matching of expected explicit connect
    #  and disconnect.
    def disconnect(self):
        log.critical("USB release interface")
        try:
            result = usb.util.release_interface(self.device, 0)
        except Exception as exc:
            log.warn("Hardware Failure in release interface: %s", exc)
            raise

        return True

    def send_code(self, bRequest, wValue=0, wIndex=0, data_or_wLength="", label=""):
        prefix = "" if not label else ("%s: " % label)
        result = None
        log.debug("%ssend_code: request 0x%02x value 0x%04x index 0x%04x data/len %s",
            prefix, bRequest, wValue, wIndex, data_or_wLength)
        try:
            result = self.device.ctrl_transfer(0x40,     # HOST_TO_DEVICE
                                               bRequest,
                                               wValue,
                                               wIndex,
                                               data_or_wLength) # add TIMEOUT_MS parameter?
        except Exception as exc:
            log.critical("Hardware Failure SP Send Code Problem with ctrl transfer", exc_info=1)
            #self.schedule_disconnect()

        log.debug("%sSend Raw result: [%s]", prefix, result)
        log.debug("%ssend_code: request 0x%02x value 0x%04x index 0x%04x data/len %s: result %s",
            prefix, bRequest, wValue, wIndex, data_or_wLength, result)
        return result

    def get_code(self, bmRequest, wValue=0, wLength=64):
        bmRequestType = 0xC0 # device to host
        wIndex = 0           # current specification has all index 0

        result = None
        try:
            result = self.device.ctrl_transfer(bmRequestType,
                                               bmRequest,
                                               wValue,
                                               wIndex,
                                               wLength)
        except Exception as exc:
            log.critical("Hardware Problem with get ctrl transfer: %s", exc)
            raise

        log.debug("Raw result: [%s]", result)
        return result

    ## Return the serial number portion of the USB descriptor. 
    def load_serial_number(self):
        if self.settings.eeprom.serial_number is not None:
            return self.settings.eeprom.serial_number

        # Older units support the 256 langid. Newer units require none.
        try:
            self.settings.eeprom.kerial_number = usb.util.get_string(self.device, self.device.iSerialNumber, 256)
        except Exception as exc:
            log.info("Read new langid 256 serial: %s", exc)
            try:
                self.settings.eeprom.serial_number = usb.util.get_string(self.device, self.device.iSerialNumber)
            except Exception as exc:
                log.critical("Failure to read langid none serial: %s", exc)
                raise

    ## Read the integration time stored on the device. 
    def get_integration_time(self):
        result = self.get_code(0xBF)

        curr_time = (result[2] * 0x10000) + (result[1] * 0x100) + result[0]

        # MZ: should we be multiplying by 10 on PID 0x0001?

        log.debug("Integration time: %s", curr_time)
        return curr_time

    ## Read the device stored gain.  Convert from binary wasatch format.
    #  - 1st byte is binary encoded: 0 = 1/2, 1 = 1/4, 2 = 1/8 etc
    #  - 2nd byte is the part to the left of the decimal
    #  So 231 is 1e7 is 1.90234375
    def get_detector_gain(self):
        result = self.get_code(0xc5)

        lsb = result[0] # LSB-MSB
        msb = result[1]

        gain = msb + lsb / 256.0
        log.debug("detector_gain is: %f (msb %d, lsb %d)" % (gain, msb, lsb))
        self.settings.eeprom.detector_gain = gain

        return gain

    ## Given a string of 1's and 0's, look through each ordinal position
    #  and return a 1 if it has a one. Otherwise return zero.
    def bit_from_string(self, string, index):
        i, j = divmod(index, 8)
        return ord(string[i]) & (1 << j)

    ## 0xC0 is not to be confused with the device to host specification in
    #  the control message. This is a vendor defined opcode for returning
    #  the software information. Result is Major version, hyphen, minor
    #  version.
    def get_microcontroller_firmware_version(self):
        result = self.get_code(0xC0)
        sw_code = "%d-%d" % (result[0], result[1])
        self.settings.microcontroller_firmware_version = sw_code
        return sw_code

    ## The version of the FPGA code read from the device. First three bytes
    #  plus a hyphen is the major version, then last three bytes is the
    #  minor.
    def get_fpga_firmware_version(self):
        result = self.get_code(0xB4)

        chr_fpga_suffix = "%s%s%s" \
                          % (chr(result[4]), chr(result[5]),
                             chr(result[6]))

        chr_fpga_prefix = "%s%s%s%s" \
                          % (chr(result[0]), chr(result[1]),
                             chr(result[2]), chr(result[3]))

        version = "%s%s" % (chr_fpga_prefix, chr_fpga_suffix)
        self.settings.fpga_firmware_version = version
        return version

    ## Issue the "acquire" control message, then immediately read back from
    #  the bulk endpoint.
    def get_line(self):
        # Apparently 0x0009 class devices (ARM Board), will report an
        # errno None, code 110 on sending this.
        result = self.send_code(0xAD, label="CCD_ACQUIRE")

        line_buffer = 2048 # 1024 16bit pixels
        if self.pid == 0x2000:
            line_buffer = 1024 # 512 16bit pixels

        data = self.device.read(0x82, line_buffer, timeout=USB_TIMEOUT_MS)
        log.debug("Raw data: %s", data[0:9])

        # Append the 2048 pixel data for just MTI produt id 0x0001
        if self.pid == 0x0001:
            data.extend(self.read_second_half())

        try:
            spectrum = [i + 256 * j for i, j in zip(data[::2], data[1::2])]
        except Exception as exc:
            log.critical("Failure in data unpack: %s", exc)
            raise

        # if we're in area scan mode, use first pixel as row index (leave pixel in spectrum)
        area_scan_row_count = -1
        if self.settings.state.area_scan_enabled:
            area_scan_row_count = spectrum[0]

        if len(spectrum) != self.settings.pixels():
            log.critical("Read read %d pixels (expected %d)", len(spectrum), self.settings.pixels())
            return None

        return (spectrum, area_scan_row_count)

    ## Read from endpoint 86 of the 2048-pixel Hamamatsu detector in MTI units.
    def read_second_half(self):
        log.debug("Also read off end point 86")
        return self.device.read(0x86, 2048, timeout=1000)

    ## Mustard Tree PID=0x0001 class devices have a hard-coded integration time
    #  resolution of 10ms; e.g. if you set a value of 500, you will get a 5
    #  second integration.
    def set_integration_time(self, value):
        self.settings.state.integration_time_ms = value

        if self.pid == 1:
            log.debug("scaled Mustard Tree integration time by 0.1")
            value = int(value / 10)

        return self.send_code(0xB2, value, label="SET_INTEGRATION_TIME")

    # ##########################################################################
    # Temperature
    # ##########################################################################

    def select_adc(self, n):
        log.error("StrokerProtocol: select_adc not implemented")

    def get_secondary_adc_calibrated(self, raw=None):
        log.error("StrokerProtocol: get_secondary_adc_calibrated not implemented")
        return 0

    def get_secondary_adc_raw(self):
        log.error("StrokerProtocol: get_secondary_adc_raw not implemented")
        return 0

    def get_laser_temperature_raw(self):
        result = self.get_code(0xd5)
        if not result:
            raise Exception("Unable to read raw laser temperature")
        return result[0] + result[1] << 8

    ## Read the Analog to Digital conversion value from the device.  Apply
    #  formula to convert AD value to temperature, return raw temperature
    #  value.
    def get_laser_temperature_degC(self, raw=None):

        if raw is None:
            raw = get_laser_temperature_raw()

        try:
            if raw is None:
                if not self.laser_temperature_invalid:
                    log.critical("Hardware Laser temperature invalid")
                self.laser_temperature_invalid = True
                return 0

        except TypeError:
            if not self.laser_temperature_invalid:
                log.critical("Laser temperature invalid (TypeError)")
            self.laser_temperature_invalid = True
            raise

        except IndexError:
            if not self.laser_temperature_invalid:
                log.critical("Laser temperature invalid (IndexError)")
            self.laser_temperature_invalid = True
            raise

        try:
            voltage    = 2.5 * raw / 4096;
            resistance = 21450.0 * voltage / (2.5 - voltage);
            if resistance <= 0:
                log.debug("get_laser_temperature_degC: invalid resistance")
                self.laser_temperature_invalid = True
                return -99

            logVal     = math.log(resistance / 10000);
            insideMain = logVal + 3977.0 / (25 + 273.0);
            degC       = 3977.0 / insideMain - 273.0;

        except ValueError as exc:
            if not self.laser_temperature_invalid:
                log.critical("Hardware Laser temp invalid (math domain error)")
                log.critical("exception: %s", exc)
            self.laser_temperature_invalid = True
            raise

        except Exception as exc:
            log.critical("Failure processing laser temperature: %s", exc)
            raise

        log.debug("Laser temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        return degC

    def get_detector_temperature_raw(self):
        result = self.get_code(0xd7)
        if not result:
            raise Exception("Unable to read detector temperature")
        return result[1] + result[0] << 8

    ## Read the Analog to Digital conversion value from the device.
    #  Apply formula to convert AD value to temperature, return raw
    #  temperature value. (Stroker Protocol units had no EEPROM) 
    def get_detector_temperature_degC(self, raw=0):
        if raw == 0:
            raw = self.get_detector_temperature_raw()
        raw = float(raw)

        try:
            voltage    = 1.5 * raw / 4096.0
            resistance = 10000 * voltage / (2 - voltage)
            if resistance <= 0:
                log.error("get_detector_temperature_degC: invalid resistance")
                self.detector_temperature_invalid = True
                return -99

            logVal     = math.log(resistance / 10000)
            insideMain = logVal + 3977.0 / (25 + 273.0)
            degC       = 3977.0 / insideMain - 273.0

        except ValueError as exc:
            if not self.detector_temperature_invalid:
                log.critical("Hardware CCD temp invalid (math domain error)")

            self.detector_temperature_invalid = True
            raise

        except Exception as exc:
            log.critical("Failure processing ccd temperature: %s", exc)
            raise

        return degC

    ## Read the laser enable status from the device. 
    def get_laser_enable(self):
        result = self.get_code(0xE2)
        enabled = result[0] != 0
        self.settings.state.laser_enabled = enabled
        return enabled

    def set_laser_enable(self, flag):
        self.settings.state.laser_enabled = flag
        value = 1 if flag else 0
        log.debug("Send laser enable: %s", value)

        if not flag:
            self.last_applied_laser_power = 0
        else:
            self.last_applied_laser_power = self.next_applied_laser_power
        return self.send_code(0xBE, value, label="SET_LASER_ENABLE")

    def set_detector_tec_enable(self, flag):
        if not self.detector_tec_setpoint_has_been_set:
            log.debug("defaulting TEC setpoint to min %d", self.settings.eeprom.min_temp_degC)
            self.set_tec_setpoint_degC(self.settings.eeprom.min_temp_degC)

        self.settings.state.tec_enabled = flag
        log.debug("CCD TEC enable: %s", flag)
        result = self.send_code(0xD6, 1 if flag else 0, label="SET_TEC_ENABLE")

    ## Reorder, pad and decode the eeprom data to produce a string
    #  representation of the value stored in the device memory.
    def decode_eeprom(self, raw_data, width, offset=0):
        # Take N width slice of the data starting from the offset
        top_slice = raw_data[offset:offset+width]

        # Legacy comment: Unpack as an integer, always returns a tuple
        # MZ: "d" means a double, not integer:
        #     https://docs.python.org/2/library/struct.html#format-characters
        unpacked = struct.unpack("d", top_slice)

        log.debug("Unpacked str: %s ", unpacked)
        return str(unpacked[0])

    ## Attempt to set the CCD cooler setpoint. Verify that it is within an
    #  acceptable range. Ideally this is to prevent condensation and other
    #  issues. This value is a default and is hugely dependent on the
    #  environmental conditions. 
    def set_tec_setpoint_degC(self, degC):
        ok_range = "%s, %s" % (self.settings.eeprom.min_temp_degC,
                               self.settings.eeprom.max_temp_degC)

        if degC < self.settings.eeprom.min_temp_degC:
            log.critical("TEC setpoint MIN out of range (%s)", ok_range)
            return False

        if degC > self.settings.eeprom.max_temp_degC:
            log.critical("TEC setpoint MAX out of range (%s)", ok_range)
            return False

        raw = int(self.settings.eeprom.degC_to_dac_coeffs[0]
                + self.settings.eeprom.degC_to_dac_coeffs[1] * degC
                + self.settings.eeprom.degC_to_dac_coeffs[2] * degC * degC)
        log.debug("degC-to-DAC coeffs: %s", self.settings.eeprom.degC_to_dac_coeffs)

        raw = max(0, min(4095, raw))

        self.settings.state.tec_setpoint_degC = degC

        log.debug("calling SET_TEC_SETPOINT with %s degC (raw 0x%04x, %d dec)", degC, raw, raw)
        self.send_code(0xd8, raw, label="SET_TEC_SETPOINT")
        self.detector_tec_setpoint_has_been_set = True
        return True

    def get_laser_temperature_setpoint_raw(self):
        result = self.get_code(0xe8)
        return result[0]

    def get_sensor_line_length(self):
        if self.pid == 0x0001: # MTI Stroker Protocol devices with 2048 pixels
            pixels = 2048
        elif self.pid == 0x2000:
            pixels = 512 # are there any Stroker Protocol InGaAs?
        else:
            pixels = 1024

        log.debug("sp.get_sensor_line_length: pixels = %d", pixels)
        self.settings.eeprom.active_pixels_horizontal = pixels

        return pixels

    def set_laser_temperature_setpoint_raw(self, value):
        log.debug("Send laser temperature setpoint raw: %d", value)
        return self.send_code(0xe7, value, label="SET_LASER_SETPOINT")

    ## Laser power is determined by a combination of the pulse width,
    #  period and modulation being enabled. There are many combinations of
    #  these values that will produce a given percentage of the total laser
    #  power through pulse width modulation. There is no 'get laser power'
    #  control message on the device. 
    def set_laser_power_perc(self, value=100):
        # round to an int between 0-100
        value = min(100, max(0, int(round(value))))

        self.next_applied_laser_power = value
        self.settings.state.laser_power = value

        # Turn off modulation at full laser power, exit
        if value == 100:
            log.info("Turning off laser modulation (full power)")
            result = self.send_code(0xBD, 0, label="SET_LASER_MODULATION")
            return result

        log.error("StrokerProtocolDevice.set_laser_power_perc: disabled until properly tested")
        return

        # Change the pulse period to 100 us
        result = self.send_code(0xC7, 100, label="SET_LASER_PULSE_PERIOD")

        if result == None:
            log.critical("Hardware Failure to send laser mod. pulse period")
            return False

        # Set the pulse width to the 0-100 percentage of power
        result = self.send_code(0xDB, value, label="SET_LASER_PULSE_WIDTH")

        if result == None:
            log.critical("Hardware Failure to send pulse width")
            return False

        # Enable modulation (MZ: C7 is "PULSE_PERIOD" above...?)
        result = self.send_code(0xC7, 1, label="SET_LASER_SOMETHING")
        if result == None:
            log.critical("Hardware Failure to send laser modulation")
            return False

        log.info("Laser power set to: %s", value)

        return result

    ## Perform the specified setting such as physically writing the laser
    #  on, changing the integration time, turning the cooler on etc. 
    def write_setting(self, record):
        log.debug("sp.write_setting: %s -> %s", record.setting, record.value)

        if record.setting == "laser_enable":
            self.set_laser_enable(True if record.value else False)

        elif record.setting == "integration_time_ms":
            self.set_integration_time(int(record.value))

        elif record.setting == "detector_tec_setpoint_degC":
            self.set_tec_setpoint_degC(int(record.value))

        elif record.setting == "degC_to_dac_coeffs":
            self.settings.eeprom.degC_to_dac_coeffs = record.value

        elif record.setting == "detector_tec_enable":
            self.set_detector_tec_enable(True if record.value else False)

        elif record.setting == "laser_power_perc":
            self.set_laser_power_perc(record.value)

        elif record.setting == "laser_temperature_setpoint_raw":
            self.set_laser_temperature_setpoint_raw(int(record.value))

        elif record.setting == "scans_to_average":
            self.settings.state.scans_to_average = int(record.value)

        elif record.setting == "bad_pixel_mode":
            self.settings.state.bad_pixel_mode = int(record.value)

        elif record.setting == "log_level":
            self.set_log_level(record.value)

        else:
            log.critical("Unknown setting: %s", record.setting)
            return False

        return True

    def set_log_level(self, s):
        lvl = logging.DEBUG if s == "DEBUG" else logging.INFO
        log.info("sp.set_log_level: setting to %s", lvl)
        logging.getLogger().setLevel(lvl)
