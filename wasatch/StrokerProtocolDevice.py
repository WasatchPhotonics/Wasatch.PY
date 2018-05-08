""" Interface wrapper around the libusb drivers to show stroker protocol
    communication for devices from Wasatch Photonics. Stroker in this case is an
    homage to automotive performance: https://en.wikipedia.org/wiki/Stroker_kit """

import usb
import math
import struct
import logging

from SpectrometerSettings import SpectrometerSettings

log = logging.getLogger(__name__)

USB_TIMEOUT_MS = 60000

class StrokerProtocolDevice(object):
    """ Provide function wrappers for all of the common tasks associated with
        stroker control. This includes control messages to pass settings back
        and forth, as well as bulk transfers to get lines of data from the
        device. """

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

        self.detector_tec_setpoint_has_been_set = False
        self.secondary_adc_enabled = False
        self.invert_x_axis = False

        # Defaults from Original (Stroker-era) settings. These are known
        # to set ccd setpoints effectively for Stroker 785-class units.
        self.settings.eeprom.degC_to_dac_coeffs = [ 3566.62, -143.543, -0.324723 ]

        self.last_applied_laser_power = 0
        self.next_applied_laser_power = 100

    def connect(self):
        """ Attempt to connect to the specified device. Log any failures and
            return False if there is a problem, otherwise return True. """

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

    def disconnect(self):
        """ Function stub for historical matching of expected explicit connect
            and disconnect. """
        log.critical("USB release interface")
        try:
            result = usb.util.release_interface(self.device, 0)
        except Exception as exc:
            log.warn("Hardware Failure in release interface: %s", exc)
            raise

        return True

    def send_code(self, FID_bmRequest, FID_wValue=0):
        FID_bmRequestType = 0x40 # host to device
        FID_wIndex = 0           # current specification has all index 0
        FID_wLength = ""

        result = None
        try:
            result = self.device.ctrl_transfer(FID_bmRequestType,
                                               FID_bmRequest,
                                               FID_wValue,
                                               FID_wIndex,
                                               FID_wLength)
        except Exception as exc:
            log.critical("Hardware Problem with send ctrl transfer: %s", exc)
            #raise

        log.debug("Raw result: [%s]", result)
        return result

    def get_code(self, FID_bmRequest, FID_wValue=0, FID_wLength=64):
        FID_bmRequestType = 0xC0 # device to host
        FID_wIndex = 0           # current specification has all index 0

        result = None
        try:
            result = self.device.ctrl_transfer(FID_bmRequestType,
                                               FID_bmRequest,
                                               FID_wValue,
                                               FID_wIndex,
                                               FID_wLength)
        except Exception as exc:
            log.critical("Hardware Problem with get ctrl transfer: %s", exc)
            raise

        log.debug("Raw result: [%s]", result)
        return result

    def load_serial_number(self):
        """ Return the serial number portion of the USB descriptor. """
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

    def get_integration_time(self):
        """ Read the integration time stored on the device. """
        result = self.get_code(0xBF)

        curr_time = (result[2] * 0x10000) + (result[1] * 0x100) + result[0]

        # MZ: should we be multiplying by 10 on PID 0x0001?

        log.debug("Integration time: %s", curr_time)
        return curr_time

    def get_ccd_gain(self):
        """ Read the device stored gain.  Convert from binary wasatch format.
                1st byte is binary encoded: 0 = 1/2, 1 = 1/4, 2 = 1/8 etc
                2nd byte is the part to the left of the decimal
            So 231 is 1e7 is 1.90234375 """
        result = self.get_code(0xC5)
        gain = result[1]
        start_byte = str(result[0])
        for i in range(8):
            bit_val = self.bit_from_string(start_byte, i)
            if   bit_val == 1 and i == 0: gain = gain + 0.5
            elif bit_val == 1 and i == 1: gain = gain + 0.25
            elif bit_val == 1 and i == 2: gain = gain + 0.125
            elif bit_val == 1 and i == 3: gain = gain + 0.0625
            elif bit_val == 1 and i == 4: gain = gain + 0.03125
            elif bit_val == 1 and i == 5: gain = gain + 0.015625
            elif bit_val == 1 and i == 6: gain = gain + 0.0078125
            elif bit_val == 1 and i == 7: gain = gain + 0.00390625

        self.settings.state.ccd_gain = gain

        log.debug("Gain is: %s raw (%.2f)", result, gain)
        return gain

    def bit_from_string(self, string, index):
        """ Given a string of 1's and 0's, look through each ordinal position
            and return a 1 if it has a one. Otherwise return zero. """
        i, j = divmod(index, 8)
        return ord(string[i]) & (1 << j)

    def get_microcontroller_firmware_version(self):
        """ 0xC0 is not to be confused with the device to host specification in
            the control message. This is a vendor defined opcode for returning
            the software information. Result is Major version, hyphen, minor
            version. """
        result = self.get_code(0xC0)
        sw_code = "%d-%d" % (result[0], result[1])
        self.settings.microcontroller_firmware_version = sw_code
        return sw_code

    def get_fpga_firmware_version(self):
        """ The version of the FPGA code read from the device. First three bytes
            plus a hyphen is the major version, then last three bytes is the
            minor. """
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

    def get_line(self):
        """ Issue the "acquire" control message, then immediately read back from
            the bulk endpoint. """
        log.debug("INSIDE get line")

        # Apparently 0x0009 class devices (ARM Board), will report an
        # errno None, code 110 on sending this.
        result = self.send_code(0xAD)

        line_buffer = 2048 # 1024 16bit pixels
        if self.pid == 0x2000:
            line_buffer = 1024 # 512 16bit pixels

        data = self.device.read(0x82, line_buffer, timeout=USB_TIMEOUT_MS)
        log.debug("Raw data: %s", data[0:9])

        try:
            data = [i + 256 * j for i, j in zip(data[::2], data[1::2])]
        except Exception as exc:
            log.critical("Failure in data unpack: %s", exc)
            raise

        log.debug("DONE   get line")

        # Append the 2048 pixel data for just MTI produt id 0x0001
        if self.pid == 0x0001:
            second_half = self.read_second_half()
            data.extend(second_half)

        return data

    def read_second_half(self):
        """ Read from endpoint 86 of the 2048-pixel Hamamatsu detector in MTI units. """
        log.debug("Also read off end point 86")
        data = self.device.read(0x86, 2048, timeout=1000)
        try:
            data = [i + 256 * j for i, j in zip(data[::2], data[1::2])]

        except Exception as exc:
            log.critical("Failure in data unpack: %s", exc)
            data = None

        return data

    def set_integration_time(self, value):
        # Mustard Tree PID=0x0001 class devices have a hard-coded integration time
        # resolution of 10ms; e.g. if you set a value of 500, you will get a 5
        # second integration.
        self.settings.state.integration_time_ms = value

        if self.pid == 1:
            log.debug("scaled Mustard Tree integration time by 0.1")
            value = int(value / 10)

        return self.send_code(0xB2, value)

    ############################################################################
    # Temperature
    ############################################################################

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

    def get_laser_temperature_degC(self, raw=None):
        """ Read the Analog to Digital conversion value from the device.  Apply
            formula to convert AD value to temperature, return raw temperature
            value. """

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

    def get_detector_temperature_degC(self, raw=0):
        """ Read the Analog to Digital conversion value from the device.
            Apply formula to convert AD value to temperature, return raw
            temperature value. (Stroker Protocol units had no EEPROM) """

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

    def get_laser_enable(self):
        """ Read the laser enable status from the device. """
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
        return self.send_code(0xBE, value)

    def set_detector_tec_enable(self, flag):
        if not self.detector_tec_setpoint_has_been_set:
            log.debug("defaulting TEC setpoint to min %d", self.settings.eeprom.min_temp_degC)
            self.set_tec_setpoint_degC(self.settings.eeprom.min_temp_degC)

        self.settings.state.tec_enabled = flag
        log.debug("CCD TEC enable: %s", flag)
        result = self.send_code(0xD6, 1 if flag else 0)

    # MZ: I don't know if this is ever used.  I'm confused...I thought
    # StrokerProtocol devices didn't have an EEPROM "by definition".
    def get_calibration_coeffs(self):
        """ Read the calibration coefficients from the on-board EEPROM. """

        eeprom_data = self.get_code(0xA2)
        log.debug("Full eeprom dump: %s", eeprom_data)

        c0 = self.decode_eeprom(eeprom_data, width=8, offset=0)
        c1 = self.decode_eeprom(eeprom_data, width=8, offset=8)
        c2 = self.decode_eeprom(eeprom_data, width=8, offset=16)
        c3 = self.decode_eeprom(eeprom_data, width=8, offset=24)

        log.debug("Coeffs: %s, %s, %s, %s" % (c0, c1, c2, c3))
        self.settings.eeprom.wavelength_coeffs = [c0, c1, c2, c3]
        return self.settings.eeprom.wavelength_coeffs

    def decode_eeprom(self, raw_data, width, offset=0):
        """ Reorder, pad and decode the eeprom data to produce a string
            representation of the value stored in the device memory. """
        # Take N width slice of the data starting from the offset
        top_slice = raw_data[offset:offset+width]

        # Legacy comment: Unpack as an integer, always returns a tuple
        # MZ: "d" means a double, not integer:
        #     https://docs.python.org/2/library/struct.html#format-characters
        unpacked = struct.unpack("d", top_slice)

        log.debug("Unpacked str: %s ", unpacked)
        return str(unpacked[0])

    def set_tec_setpoint_degC(self, degC):
        """ Attempt to set the CCD cooler setpoint. Verify that it is within an
            acceptable range. Ideally this is to prevent condensation and other
            issues. This value is a default and is hugely dependent on the
            environmental conditions. """

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

        raw = max(0, min(4095, raw))

        self.settings.state.tec_setpoint_degC = degC

        log.debug("Setting TEC setpoint to: %s degC (%d raw)", degC, raw)
        self.send_code(0xd8, raw)
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
        return self.send_code(0xe7, value)

    def set_laser_power_perc(self, value=100):
        """ Laser power is determined by a combination of the pulse width,
            period and modulation being enabled. There are many combinations of
            these values that will produce a given percentage of the total laser
            power through pulse width modulation. There is no 'get laser power'
            control message on the device. """

        self.next_applied_laser_power = value
        self.settings.state.laser_power_perc = value

        # Turn off modulation at full laser power, exit
        if value == 100:
            log.info("Turning off laser modulation (full power)")
            result = self.send_code(0xBD, 0)
            return result

        # Change the pulse period to 100 us
        result = self.send_code(0xC7, 100)

        if result == None:
            log.critical("Hardware Failure to send laser mod. pulse period")
            return False

        # Set the pulse width to the 0-100 percentage of power
        result = self.send_code(0xDB, value)

        if result == None:
            log.critical("Hardware Failure to send pulse width")
            return False

        # Enable modulation
        result = self.send_code(0xC7, 1)
        if result == None:
            log.critical("Hardware Failure to send laser modulation")
            return False

        log.info("Laser power set to: %s", value)

        return result

    # implemented subset of WasatchDeviceWrapper.DEVICE_CONTROL_COMMANDS
    def write_setting(self, record):
        """ Perform the specified setting such as physically writing the laser
            on, changing the integration time, turning the cooler on etc. """

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
            self.set_laser_power_perc(int(record.value))

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
        log.info("fid.set_log_level: setting to %s", lvl)
        logging.getLogger().setLevel(lvl)
