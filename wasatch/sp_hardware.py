#!/usr/bin/env python2
""" Interface wrapper around the libusb drivers to show stroker protocol
    communication for devices from Wasatch Photonics. Stroker in this case is an 
    homage to automotive performance: https://en.wikipedia.org/wiki/Stroker_kit """

import usb
import math
import struct
import logging

from . import common

log = logging.getLogger(__name__)

USB_TIMEOUT=60000

################################################################################
#                                                                              #
#                                  ListDevices                                 #
#                                                                              #
################################################################################

class ListDevices(object):
    """ Create a list of vendor id, product id pairs of any device on the bus 
        with the 0x24AA VID. Explicitly reject the newer feature identification 
        devices. """
    def __init__(self):
        log.debug("init")

    def get_all(self, vid=0x24aa):
        """ Return the full list of devices that match the vendor id. Explicitly 
            reject the feature identification codes. """
        list_devices = []

        for bus in usb.busses():
            for device in bus.devices:
                single = self.device_match(device, vid)
                if single is not None:
                    list_devices.append(single)

        return list_devices

    def device_match(self, device, vid):
        """ Match vendor id and rejectable feature identification devices. """
        if device.idVendor != vid:
            return None

        if device.idProduct == 0x1000 or \
           device.idProduct == 0x2000 or \
           device.idProduct == 0x3000 or \
           device.idProduct == 0x4000:
               return None

        if len(str(device.idProduct)) == 2:
            format_pid = "0x00%x" % device.idProduct
        else:
            format_pid = "0x000%x" % device.idProduct

        single = (hex(device.idVendor), format_pid)
        return single

################################################################################
#                                                                              #
#                            StrokerProtocolDevice                             #
#                                                                              #
################################################################################

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

        self.laser_temperature_invalid = 0
        self.detector_temperature_invalid = 0

        self.max_integration = 10000 # ms
        self.min_integration = 1     # ms

        self.integration = self.min_integration
        self.laser_status = 0
        self.detector_tec_setpoint_degC = 15.0
        self.detector_tec_enable = 0
        self.detector_tec_setpoint_has_been_set = False
        self.ccd_adc_setpoint = 2047 # Midway of a 12bit ADC # MZ: used ever?

        # Defaults from Original (stroker-era) settings. These are known
        # to set ccd setpoints effectively for stroker 785 class units.
        self.original_degC_to_dac_coeff_0 = 3566.62
        self.original_degC_to_dac_coeff_1 = -143.543
        self.original_degC_to_dac_coeff_2 =   -0.324723

        self.degC_to_dac_coeff_0 = self.original_degC_to_dac_coeff_0
        self.degC_to_dac_coeff_1 = self.original_degC_to_dac_coeff_1
        self.degC_to_dac_coeff_2 = self.original_degC_to_dac_coeff_2

        self.scans_to_average = 1
        self.bad_pixel_mode = common.bad_pixel_mode_average

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
        """ Perform the control message transfer required to send a value to the 
            device, return the extracted value. """
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
        """ Use the StrokerProtocol (sp), and perform the control message 
            transfer required to get a setting from the device. """
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

    def get_model_number(self):
        # MZ: make this better
        return "Stroker Protocol Device"

    def get_serial_number(self):
        """ Return the serial number portion of the USB descriptor. """
        serial = "Unavailable"

        # Older units support the 256 langid. Newer units require none.
        try:
            serial = usb.util.get_string(self.device,
                                         self.device.iSerialNumber, 256)
        except Exception as exc:
            log.info("Read new langid 256 serial: %s", exc)

        try:
            serial = usb.util.get_string(self.device,
                                         self.device.iSerialNumber)
        except Exception as exc:
            log.critical("Failure to read langid none serial: %s", exc)
            raise

        return serial

    def get_integration_time(self):
        """ Read the integration time stored on the device. """
        result = self.get_code(0xBF)

        curr_time = (result[2] * 0x10000) + (result[1] * 0x100) + result[0]

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

        log.debug("Raw gain [sp] is: %s", result)

        return gain

    def bit_from_string(self, string, index):
        """ Given a string of 1's and 0's, look through each ordinal position
            and return a 1 if it has a one. Otherwise return zero. """
        i, j = divmod(index, 8)

        # Uncomment this if you want the high-order bit first
        #j = 8 - j

        if ord(string[i]) & (1 << j):
            return 1

        return 0

    def get_standard_software_code(self):
        """ 0xC0 is not to be confused with the device to host specification in
            the control message. This is a vendor defined opcode for returning 
            the software information. Result is Major version, hyphen, minor
            version. """
        result = self.get_code(0xC0)
        sw_code = "%d-%d" % (result[0], result[1])
        return sw_code

    def get_fpga_revision(self):
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

        return "%s%s" % (chr_fpga_prefix, chr_fpga_suffix)

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

        data = self.device.read(0x82, line_buffer, timeout=USB_TIMEOUT)
        log.debug("Raw data: %s", data[0:9])

        try:
            data = [i + 256 * j for i, j in zip(data[::2], data[1::2])]
        except Exception as exc:
            log.critical("Failure in data unpack: %s", exc)
            raise

        log.debug("DONE   get line")
        # Append the 2048 pixel data for just MTI produt id (1)
        if self.pid == 1:
            second_half = self.read_second_half()
            data.extend(second_half)

        return data

    def read_second_half(self):
        """ Read from end point 86 of the ancient-er 2048 pixel hamamatsu 
            detector in MTI units. """
        log.debug("Also read off end point 86")
        data = self.device.read(0x86, 2048, timeout=1000)
        try:
            data = [i + 256 * j for i, j in zip(data[::2], data[1::2])]

        except Exception as exc:
            log.critical("Failure in data unpack: %s", exc)
            data = None

        return data

    def set_integration_time(self, int_time):
        """ Send the updated integration time in a control message to the device. """

        log.debug("Send integration time: %s", int_time)

        # Welcome back MTI - we did not miss you. PID=0x0001 class
        # devices only support integration time measured in units of
        # 10msec. If you send in 500msec, you will get a 5 second
        # integration. Search the through Dash code base to see why we
        # never, ever want to do this.
        if self.pid == 1:
            int_time = int(int_time / 10)

        result = self.send_code(0xB2, int_time)
        return result

    ############################################################################
    # Temperature
    ############################################################################

    def get_laser_temperature_raw(self):
        result = self.get_code(0xd5)
        if not result:
            raise Exception("Unable to read raw laser temperature")
        return result[0] + result[1] << 8

    def get_laser_temperature_degC(self, raw=0):
        """ Read the Analog to Digital conversion value from the device.  Apply 
            formula to convert AD value to temperature, return raw temperature 
            value. """

        if raw == 0:
            raw = get_laser_temperature_raw()

        try:
            if raw == 0:
                if self.laser_temperature_invalid == 0:
                    log.critical("Hardware Laser temperature invalid (0)")
                self.laser_temperature_invalid = 1
                return 0

        except TypeError:
            if self.laser_temperature_invalid == 0:
                log.critical("Laser temperature invalid (TypeError)")
            self.laser_temperature_invalid = 1
            raise

        except IndexError:
            if self.laser_temperature_invalid == 0:
                log.critical("Laser temperature invalid (IndexError)")
            self.laser_temperature_invalid = 1
            raise

        try:
            voltage    = 2.5 * raw / 4096;
            resistance = 21450.0 * voltage / (2.5 - voltage);
            logVal     = math.log(resistance / 10000);
            insideMain = logVal + 3977.0 / (25 + 273.0);
            degC       = 3977.0 / insideMain - 273.0;

        except ValueError as exc:
            if self.laser_temperature_invalid == 0:
                log.critical("Hardware Laser temp invalid (math domain error)")
                log.critical("exception: %s", exc)
            self.laser_temperature_invalid = 1
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
            raw = float(self.get_detector_temperature_raw())
    
        try:
            voltage    = 1.5 * raw / 4096.0
            resistance = 10000 * voltage / (2 - voltage)
            logVal     = math.log(resistance / 10000)
            insideMain = logVal + 3977.0 / (25 + 273.0)
            degC       = 3977.0 / insideMain - 273.0
    
        except ValueError as exc:
            if self.detector_temperature_invalid == 0:
                log.critical("Hardware CCD temp invalid (math domain error)")
    
            self.detector_temperature_invalid = 1
            raise
    
        except Exception as exc:
            log.critical("Failure processing ccd temperature: %s", exc)
            raise
    
        return degC

    def get_laser_enable(self):
        """ Read the laser enable status from the device. """
        result = self.get_code(0xE2)
        return result[0]

    def set_laser_enable(self, flag=0):
        value = 1 if flag else 0
        log.debug("Send laser enable: %s", value)
        return self.send_code(0xBE, value)

    def set_detector_tec_enable(self, value=0):
        """ Write one for enable, zero for disable of the ccd tec cooler. """
        if not self.detector_tec_setpoint_has_been_set:
            log.debug("defaulting TEC setpoint to min", self.tmin)
            self.set_detector_tec_setpoint_degC(self.tmin)

        log.debug("Send CCD TEC enable: %s", value)
        result = self.send_code(0xD6, value)

    def get_calibration_coeffs(self):
        """ Read the calibration coefficients from the on-board EEPROM. """

        eeprom_data = self.get_code(0xA2)
        log.debug("Full eeprom dump: %s", eeprom_data)

        c0 = self.decode_eeprom(eeprom_data, width=8, offset=0)
        c1 = self.decode_eeprom(eeprom_data, width=8, offset=8)
        c2 = self.decode_eeprom(eeprom_data, width=8, offset=16)
        c3 = self.decode_eeprom(eeprom_data, width=8, offset=24)

        log.debug("Coeffs: %s, %s, %s, %s" % (c0, c1, c2, c3))
        return [c0, c1, c2, c3]

    def decode_eeprom(self, raw_data, width, offset=0):
        """ Reorder, pad and decode the eeprom data to produce a string 
            representation of the value stored in the device memory. """
        # Take N width slice of the data starting from the offset
        top_slice = raw_data[offset:offset+width]

        # Unpack as an integer, always returns a tuple
        unpacked = struct.unpack("d", top_slice)

        log.debug("Unpacked str: %s ", unpacked)
        return str(unpacked[0])

    def set_detector_tec_setpoint_degC(self, degC):
        """ Attempt to set the CCD cooler setpoint. Verify that it is within an 
            acceptable range. Ideally this is to prevent condensation and other 
            issues. This value is a default and is hugely dependent on the 
            environmental conditions. """

        setpoint_min_degC = 10
        setpoint_max_degC = 20
        ok_range = "%s, %s" % (setpoint_min_degC, setpoint_max_degC)

        if degC < setpoint_min_degC:
            log.critical("TEC setpoint MIN out of range (%s)", ok_range)
            return False

        if degC > setpoint_max_degC:
            log.critical("TEC setpoint MAX out of range (%s)", ok_range)
            return False

        raw = int(self.degC_to_dac_coeff_0 
                + self.degC_to_dac_coeff_1 * degC 
                + self.degC_to_dac_coeff_2 * degC * degC)

        if raw < 0:
            raw = 0
        elif raw > 4095:
            raw = 4095

        log.debug("Setting TEC setpoint to: %s (deg C) (%d raw)", degC, raw)
        result = self.send_code(0xd8, raw)
        self.detector_tec_setpoint_has_been_set = True
        return True

    def set_laser_power_perc(self, value=100):
        """ Laser power is determined by a combination of the pulse width, 
            period and modulation being enabled. There are many combinations of 
            these values that will produce a given percentage of the total laser 
            power through pulse width modulation. There is no 'get laser power' 
            control message on the device. """

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

    def write_setting(self, record):
        """ Perform the specified setting such as physically writing the laser 
            on, changing the integration time, turning the cooler on etc. """

        if record.setting == "laser_enable":
            self.set_laser_enable(record.value)
            self.laser_status = record.value

        elif record.setting == "integration":
            self.integration = int(record.value)
            self.set_integration_time(self.integration)

            log.debug("Set integration to: %s", self.integration)

        elif record.setting == "detector_tec_setpoint_degC":
            self.detector_tec_setpoint_degC = int(record.value)
            self.set_detector_tec_setpoint_degC(self.detector_tec_setpoint_degC)

        elif record.setting == "degC_to_dac_coeffs":
            self.set_degC_to_dac_coeffs(record.value)

        elif record.setting == "detector_tec_enable":
            self.detector_tec_enable = int(record.value)
            self.set_detector_tec_enable(self.detector_tec_enable)

        elif record.setting == "laser_power_perc":
            self.laser_power_perc = int(record.value)
            self.set_laser_power_perc(self.laser_power_perc)

        elif record.setting == "scans_to_average":
            self.scans_to_average = int(record.value)

        elif record.setting == "bad_pixel_mode":
            self.bad_pixel_mode = int(record.value)

        else:
            log.critical("Unknown setting: %s", record.setting)
            return False

        return True

    def set_degC_to_dac_coeffs(self, coeffs):
        degC_to_dac_coeff_0 = self.original_degC_to_dac_coeff_0
        degC_to_dac_coeff_1 = self.original_degC_to_dac_coeff_1
        degC_to_dac_coeff_2 = self.original_degC_to_dac_coeff_2

        try:
            (degC_to_dac_coeff_0, degC_to_dac_coeff_1, degC_to_dac_coeff_2) = coeffs.split(" ")
        except Exception as exc:
            log.critical("TEC Coeffs split failiure: %s", exc)
            log.critical("Setting original class coeffs")

        self.degC_to_dac_coeff_0 = float(degC_to_dac_coeff_0)
        self.degC_to_dac_coeff_1 = float(degC_to_dac_coeff_1)
        self.degC_to_dac_coeff_2 = float(degC_to_dac_coeff_2)
        log.info("Succesfully changed DegC-to-DAC setpoint coefficients")

