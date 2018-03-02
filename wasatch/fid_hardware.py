""" Interface wrapper around libusb and cypress drivers to show devices 
    compliant with the Wasatch Feature Identification Device (FID) protocol.

    TODO: 
        - split into single-class files
"""

import datetime
import logging
import struct
import math
import usb
import usb.core
import usb.util

from time import sleep
from random import randint

from . import common

log = logging.getLogger(__name__)

USB_TIMEOUT=60000

################################################################################
#                                                                              #
#                                 ListDevices                                  #
#                                                                              #
################################################################################

class ListDevices(object):
    def __init__(self):
        log.debug("init")

    def get_all(self, vid=0x24aa):
        """ Return the full list of devices that match the vendor id. """
        list_devices = []
        for bus in usb.busses():
            for device in bus.devices:
                single = self.device_match(device, vid)
                if single is not None:
                    list_devices.append(single)
        return list_devices

    def device_match(self, device, vid):
        """ Match vendor id and reject all non-feature identification devices. """
        if device.idVendor != vid:
            return None

        if device.idProduct != 0x1000 and \
           device.idProduct != 0x2000 and \
           device.idProduct != 0x3000 and \
           device.idProduct != 0x4000:
               return None

        single = (hex(device.idVendor), hex(device.idProduct))
        return single

################################################################################
#                                                                              #
#                           FeatureIdentificationDevice                        #
#                                                                              #
################################################################################

class FeatureIdentificationDevice(object):

    ############################################################################
    # Lifecycle
    ############################################################################

    def __init__(self, vid="0x24aa", pid="0x1000", bus_order=0):

        log.debug("init %s", pid)
        self.vid = int(vid, 16)
        self.pid = int(pid, 16)
        self.bus_order = bus_order

        self.device = None

        self.last_usb_timestamp = None

        self.laser_temperature_invalid = 0
        self.ccd_temperature_invalid = 0

        self.max_integration = 10000 # ms
        self.min_integration = 1     # ms

        self.integration = self.min_integration

        self.laser_status = 0
        self.laser_power_perc = 100
        self.laser_temperature_setpoint_raw = 0
        self.detector_tec_setpoint_degC = 15.0 # MZ: hardcode
        self.detector_tec_enable = 0
        self.detector_tec_setpoint_has_been_set = False
        self.ccd_gain = 1.9 # See notes in control.py # MZ: hardcode
        self.ccd_offset = 0

        # Defaults from Original (stroker-era) settings. These are known
        # to set ccd setpoints effectively for stroker 785 class units.
        self.original_degC_to_dac_coeff_0 = 3566.62 # MZ: hardcode
        self.original_degC_to_dac_coeff_1 = -143.543
        self.original_degC_to_dac_coeff_2 = -0.324723

        self.degC_to_dac_coeff_0 = self.original_degC_to_dac_coeff_0
        self.degC_to_dac_coeff_1 = self.original_degC_to_dac_coeff_1
        self.degC_to_dac_coeff_2 = self.original_degC_to_dac_coeff_2

        self.ccd_trigger = 0  # 0 internal, 1 external
        self.scans_to_average = 1
        self.bad_pixel_mode = common.bad_pixel_mode_average

        self.eeprom_pages = {}

    def connect(self):
        """ Attempt to connect to the specified device. Log any failures and
            return False if there is a problem, otherwise return True. If
            you try and connect to them in order, iterating on a failure, it
            will cause them to drop from the other Enlighten instance. """

        # MZ: this causes a problem in non-blocking mode (WasatchDeviceWrapper) on MacOS
        devices = usb.core.find(find_all=True, idVendor=self.vid, idProduct=self.pid) 

        dev_list = list(devices)

        if self.bus_order != 0:
            log.warn("Non standard bus order: %s", self.bus_order)

        device = dev_list[self.bus_order]

        if device is None:
            log.critical("Can't find: %s, %s", self.vid, self.pid)
            return False

        try:
            result = device.set_configuration(1)
        except Exception as exc:
            log.warn("Hardware Failure in setConfiguration", exc_info=1)
            raise

        try:
            result = usb.util.claim_interface(device, 0)
        except Exception as exc:
            log.warn("Hardware Failure in claimInterface", exc_info=1)
            raise

        self.device = device

        self.min_usb_interval_ms = 0
        self.max_usb_interval_ms = 0

        ########################################################################
        # PID-specific settings
        ########################################################################

        if self.pid == 0x4000:
            self.min_usb_interval_ms = 10
            self.max_usb_interval_ms = 10

        # overridden by EEPROM
        if self.pid == 0x2000:
            self.pixels = 512
        else:
            self.pixels = 1024

        self.read_eeprom()

        return True

    def disconnect(self):
        log.critical("Try release interface")
        try:
            result = usb.util.release_interface(self.device, 0)
        except Exception as exc:
            log.warn("Failure in release interface", exc_info=1)
            raise
        return True

    ############################################################################
    # Utility Methods
    ############################################################################

    def wait_for_usb_available(self):
        if self.max_usb_interval_ms > 0:
            if self.last_usb_timestamp is not None:
                delay_ms = randint(self.min_usb_interval_ms, self.max_usb_interval_ms)
                next_usb_timestamp = self.last_usb_timestamp + datetime.timedelta(milliseconds=delay_ms)
                if datetime.datetime.now() < next_usb_timestamp:
                    log.debug("fid_hardware: sleeping to enforce %d ms USB interval", delay_ms)
                    while datetime.datetime.now() < next_usb_timestamp:
                        sleep(0.001) # 1ms
            self.last_usb_timestamp = datetime.datetime.now()

    def send_code(self, FID_bmRequest, FID_wValue=0, FID_wIndex=0, FID_data_or_wLength=""):
        """ Perform the control message transfer, return the extracted value. 
            Yes, the USB spec really does say data_or_length """

        FID_bmRequestType = 0x40 # host to device

        result = None
        log.debug("send_code: request 0x%02x value 0x%04x index 0x%04x data/len %s", 
            FID_bmRequest, FID_wValue, FID_wIndex, FID_data_or_wLength)
        try:
            self.wait_for_usb_available()

            result = self.device.ctrl_transfer(FID_bmRequestType,
                                               FID_bmRequest,
                                               FID_wValue,
                                               FID_wIndex,
                                               FID_data_or_wLength)
        except Exception as exc:
            log.critical("Hardware Failure FID Send Code Problem with ctrl transfer", exc_info=1)

        log.debug("Send Raw result: [%s]", result)
        log.debug("send_code: request 0x%02x value 0x%04x index 0x%04x data/len %s: result %s", 
            FID_bmRequest, FID_wValue, FID_wIndex, FID_data_or_wLength, result)
        return result

    def get_code(self, FID_bmRequest, FID_wValue=0, FID_wLength=64, FID_wIndex=0):
        """ Perform the control message transfer, return the extracted value """
        FID_bmRequestType = 0xC0 # device to host

        result = None
        try:
            self.wait_for_usb_available()

            result = self.device.ctrl_transfer(FID_bmRequestType,
                                               FID_bmRequest,
                                               FID_wValue,
                                               FID_wIndex,
                                               FID_wLength)
        except Exception as exc:
            log.critical("Hardware Failure Get Code Problem with ctrl transfer", exc_info=1)
            raise

        log.debug("get_code: request 0x%02x value 0x%04x index 0x%04x = [%s]", 
            FID_bmRequest, FID_wValue, FID_wIndex, result)
        return result

    def get_upper_code(self, FID_wValue, FID_wIndex=0):
        """ Convenience function to wrap "upper area" bmRequest feature
            identification code around the standard get code command. """
        return self.get_code(FID_bmRequest=0xFF,
                             FID_wValue=FID_wValue,
                             FID_wIndex=FID_wIndex)

    def get_eeprom_unpack(self, address, data_type="s"):
        page       = address[0]
        start_byte = address[1]
        length     = address[2]
        end_byte   = start_byte + length

        # MZ: if we write to the EEPROM, flush eeprom_pages
        if not page in self.eeprom_pages:
            log.info("reading EEPROM page %d", page)
            self.eeprom_pages[page] = self.get_upper_code(0x01, FID_wIndex=page)
        result = self.eeprom_pages[page]

        if data_type == "s":
            unpack_result = ""
            for letter in result[start_byte:end_byte]:
                if letter != 0:
                    unpack_result += chr(letter)
        else:
            unpack_result = struct.unpack(data_type, result[start_byte:end_byte])[0]

        log.debug("Unpacked [%s]: %s", data_type, unpack_result)

        return unpack_result

    ############################################################################
    # initialization
    ############################################################################

    def read_eeprom(self):
        # NJH 2017-04-11 13:53 Some ARM units may use doubles instead of floats
        self.wavelength_coeff_0  = self.get_eeprom_unpack((1,  0,  4), "f")
        self.wavelength_coeff_1  = self.get_eeprom_unpack((1,  4,  4), "f")
        self.wavelength_coeff_2  = self.get_eeprom_unpack((1,  8,  4), "f")
        self.wavelength_coeff_3  = self.get_eeprom_unpack((1, 12,  4), "f")
        self.calibration_date    = self.get_eeprom_unpack((1, 48, 12), "s")
        self.calibrated_by       = self.get_eeprom_unpack((1, 60,  3), "s")
        self.excitation          = self.get_eeprom_unpack((0, 39,  2), "h")
        self.slit_size           = self.get_eeprom_unpack((0, 41,  2), "h")
        self.degC_to_dac_coeff_0 = self.get_eeprom_unpack((1, 16,  4), "f")
        self.degC_to_dac_coeff_1 = self.get_eeprom_unpack((1, 20,  4), "f")
        self.degC_to_dac_coeff_2 = self.get_eeprom_unpack((1, 24,  4), "f")
        self.adc_to_degC_coeff_0 = self.get_eeprom_unpack((1, 32,  4), "f")
        self.adc_to_degC_coeff_1 = self.get_eeprom_unpack((1, 36,  4), "f")
        self.adc_to_degC_coeff_2 = self.get_eeprom_unpack((1, 40,  4), "f")
        self.tmax                = self.get_eeprom_unpack((1, 28,  2), "h")
        self.tmin                = self.get_eeprom_unpack((1, 30,  2), "h")
        self.tec_r298            = self.get_eeprom_unpack((1, 44,  2), "h")
        self.tec_beta            = self.get_eeprom_unpack((1, 46,  2), "h")
        self.detector            = self.get_eeprom_unpack((2,  0, 16), "s")
        self.pixels              = self.get_eeprom_unpack((2, 16,  2), "h")
        self.pixel_height        = self.get_eeprom_unpack((2, 19,  2), "h") # MZ: skipped 18
        self.min_integration     = self.get_eeprom_unpack((2, 21,  2), "H")
        self.max_integration     = self.get_eeprom_unpack((2, 23,  2), "H") # MZ: these were signed before
        self.bad_pixels          = self.populate_bad_pixels()

        log.info("EEPROM settings:")
        log.info("  Wavecal coeff0:   %s", self.wavelength_coeff_0)
        log.info("  Wavecal coeff1:   %s", self.wavelength_coeff_1)
        log.info("  Wavecal coeff2:   %s", self.wavelength_coeff_2)
        log.info("  Wavecal coeff3:   %s", self.wavelength_coeff_3)
        log.info("  Calibration date: %s", self.calibration_date)
        log.info("  Calibrated by:    %s", self.calibrated_by)
        log.info("  Excitation (nm):  %s", self.excitation)
        log.info("  Slit size (um):   %s", self.slit_size)
        log.info("  degCToDAC coeff0: %s", self.degC_to_dac_coeff_0)
        log.info("  degCToDAC coeff1: %s", self.degC_to_dac_coeff_1)
        log.info("  degCToDAC coeff2: %s", self.degC_to_dac_coeff_2)
        log.info("  adcToDegC coeff0: %s", self.adc_to_degC_coeff_0)
        log.info("  adcToDegC coeff1: %s", self.adc_to_degC_coeff_1)
        log.info("  adcToDegC coeff2: %s", self.adc_to_degC_coeff_2)
        log.info("  Det temp min:     %s", self.tmin)
        log.info("  Det temp max:     %s", self.tmax)
        log.info("  TEC R298:         %s", self.tec_r298)
        log.info("  TEC beta:         %s", self.tec_beta)
        log.info("  Detector name:    %s", self.detector)
        log.info("  Pixels:           %d", self.pixels)
        log.info("  Pixel height:     %d", self.pixel_height)
        log.info("  Min integration:  %d", self.min_integration)
        log.info("  Max integration:  %d", self.max_integration)
        log.info("  Bad Pixels:       %s", self.bad_pixels)

    def populate_bad_pixels(self):
        """ Read list from EEPROM, de-dupe and sort """
        bad = []
        for count in range(15):
            pixel = self.get_eeprom_unpack((5, count * 2, 2), "h")
            if str(pixel) != "-1":
                if pixel not in bad:
                    bad.append(pixel)
        bad.sort()
        return bad

    ############################################################################
    # Accessors
    ############################################################################

    def get_model_number(self):
        result = self.get_upper_code(0x01)
        model_number = ""
        for letter in result[0:15]:
            if letter != 0:
                model_number += chr(letter)

        return model_number

    # MZ: I don't think this method is ever called, and I'm not sure it would 
    #     work if it was called (min/max not part of tec_cal)
    # def set_calibration(self, wvl_cal, tec_cal, las_cal):
    #     """ Write the expected precision value for the given coefficients to the 
    #         device eeprom. The entire page must be written at once, so make sure 
    #         all of the values are populated. """
    #
    #        # MZ: not sure how to read 4d8f here; all 12 values are floats
    #        packed = struct.pack("4d8f",
    #                             float(wvl_cal[0]),
    #                             float(wvl_cal[1]), 
    #                             float(wvl_cal[2]), 
    #                             float(wvl_cal[3]),
    #                             float(tec_cal[0]), 
    #                             float(tec_cal[1]),
    #                             float(tec_cal[2]),
    #                             float(tec_cal[3]), # max MZ: what?
    #                             float(tec_cal[4]), # min MZ: what?
    #                             float(las_cal[0]), 
    #                             float(las_cal[1]),
    #                             float(0))          # placeholder
    #
    #        log.debug("packed: %s" % packed)
    #
    #        self.send_code(FID_bmRequest=0xFF,
    #                       FID_wValue=0x02, # Set eeprom code
    #                       FID_wIndex=0x01, # second page
    #                       FID_data_or_wLength=packed)

    def get_serial_number(self):
        """ Return the serial number portion of the model description. """
        FID_bmRequest = 0xFF  # upper area, content is wValue
        result = self.get_upper_code(0x01)
        serial_number = ""
        for letter in result[16:31]:
            serial_number += chr(letter)

        serial_number = serial_number.replace("\x00", "")
        return serial_number

    def get_integration_time(self):
        """ Read the integration time stored on the device. """
        result = self.get_code(0xBF)
        curr_time = (result[2] * 0x10000) + (result[1] * 0x100) + result[0]
        log.debug("fid_hardware.get_integration_time: read %d ms", curr_time)
        return curr_time

    def set_ccd_offset(self, value):
        word = int(value) & 0xffff
        log.debug("set ccd offset: 0x%04x", word)
        return self.send_code(0xb6, word)

    def get_ccd_gain(self):
        """ Read the device stored gain.  Convert from binary wasatch format.
            1st byte is binary encoded: 0 = 1/2, 1 = 1/4, 2 = 1/8 etc.  
            2nd byte is the part to the left of the decimal
            On both sides, expanded exponents (fractional or otherwise) are summed.
            E.g., 231 dec == 0x01e7 == 1.90234375
        """
        result = self.get_code(0xC5)

        msb = result[1]
        lsb = result[0]

        gain = msb + lsb / 256.0
        log.debug("Gain is: %f (msb %d, lsb %d)" % (gain, msb, lsb))

        return gain

    def set_ccd_gain(self, gain_val):
        """ Re-implementation for required gain settings with S10141
            sensor. These comments are from the C DLL for the SDK - also see
            control.py for details.

            // 201205171534 nharrington
            // Are you getting strange results even though you write what
            // appears to be the correct 2-byte integer data (first byte being 
            // the binary encoding?) It looks like the value gets sent to the 
            // device correctly, but is stored incorrectly (maybe).

            For example, if you run 'get_ccd_gain' on the device:
            C-00130   gain is 1.421875  1064  G9214
            WP-00108  gain is 1.296875  830-C S10141
            WP-00132  gain is 1.296875  638-R S11511
            WP-00134  gain is 1.296875  638-A S11511
            WP-00222  gain is 1.296875  VIS   S11511

            In practice, what this means is you will pass 1.9 as the gain
            setting into this function. It will transform it to the value 487 
            according to the shifted gain algorithm below. The CCD will change 
            dynamic range. Reading back the gain will still say 1.296875. This 
            has been tested with WP-00108 on 20170602

            If you write 1.9 to C-00130, you get 1.296875 back, which seems to 
            imply that only the default gain is set differently with the G9214 
            sensor.

            To see more confusion: Start: WP-00154
            Get gain value: 1.296875
            Start enlighten, set gain to 3.0
            Get gain again: 1.296875
            Why does it not change?
        """
        msb = int(gain_val)
        lsb = int((gain_val - msb) * 256)
        shifted_gain = (msb << 8) + lsb

        log.debug("Send CCD Gain: %s", shifted_gain)
        result = self.send_code(0xb7, shifted_gain)

    def get_sensor_line_length(self):
        """ The line length is encoded as a LSB-MSB ushort, such that 0x0004 =
            1024 pixels """
        result = self.get_upper_code(0x03)
        return result[0] + result[1] << 8

    def get_laser_availability(self):
        result = self.get_upper_code(0x08)
        return result[0]

    def get_standard_software_code(self):
        """ Get microcontroller firmware version """
        result = self.get_code(0xc0)
        return "%d.%d.%d.%d" % (result[3], result[2], result[1], result[0])

    # MZ: this could be simplified considerably (just return the chars in order)
    def get_fpga_revision(self):
        """ Get FPGA firmware version """
        result = self.get_code(0xb4)
        s = ""
        for i in range(len(result)):
            s += chr(result[i])
        return s

    def get_line(self):
        """ getSpectrum: send "acquire", then immediately read the bulk endpoint. """

        # Only send the CMD_GET_IMAGE (internal trigger) if external
        # trigger is disabled
        log.debug("get_line: requesting spectrum")
        if self.ccd_trigger == 0:
            result = self.send_code(0xad, FID_data_or_wLength="00000000")

        # regardless of pixel count, assume uint16
        line_buffer = self.pixels * 2 
        log.debug("waiting for %d bytes", line_buffer)

        self.wait_for_usb_available()

        # MZ: make constants for endpoints
        data = self.device.read(0x82, line_buffer, timeout=USB_TIMEOUT)
        #log.debug("get_line: %s ...", data[0:9])
        log.debug("get_line: %s", data)

        try:
            # MZ: there is such a thing as "too Pythonic"
            data = [i + 256 * j for i, j in zip(data[::2], data[1::2])]
        except Exception as exc:
            log.critical("Failure in data unpack", exc_info=1)
            raise

        return data

    def set_integration_time(self, int_time):
        """ Send the updated integration time in a control message to the device. """

        if int_time < self.min_integration or int_time > self.max_integration:
            log.error("fid.set_integration_time: %d ms outside range (%d ms, %d ms)" %
                (int_time, self.min_integration, self.max_integration))
            return

        log.debug("Send integration time: %s", int_time)
        result = self.send_code(0xB2, int_time)
        return result

    ############################################################################
    # Temperature
    ############################################################################

    def get_laser_temperature_raw(self):
        result = self.get_code(0xd5)
        if not result:
            raise Exception("Unable to read laser temperature")
        return result[0] + (result[1] << 8)

    def get_laser_temperature_degC(self, raw=-1):
        """ reminder, laser doesn't use EEPROM coeffs at all """
        if raw < 0:
            raw = self.get_laser_temperature_raw()

        if raw > 0xfff:
            log.error("get_laser_temperature_degC: read raw value 0x%04x (greater than 12 bit)", raw)
            return -99

        # can't take log of zero
        if raw == 0:
            return 0

        degC = -99
        try:
            voltage    = 2.5 * raw / 4096.0;
            resistance = 21450.0 * voltage / (2.5 - voltage);

            if resistance < 0:
                log.error("get_laser_temperature_degC: can't compute degC: raw = 0x%04x, voltage = %f, resistance = %f", raw, voltage, resistance)
                return -99

            logVal     = math.log(resistance / 10000.0);
            insideMain = logVal + 3977.0 / (25 + 273.0);
            degC       = 3977.0 / insideMain - 273.0;

            log.debug("Laser temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        except:
            log.error("exception computing laser temperature", exc_info=1)

        return degC

    def get_detector_temperature_raw(self):
        result = self.get_code(0xd7)
        if not result:
            raise Exception("Unable to read detector temperature")
        return result[1] + (result[0] << 8)

    def get_detector_temperature_degC(self, raw=-1):
        if raw < 0:
            raw = self.get_detector_temperature_raw()

        degC = self.adc_to_degC_coeff_0             \
             + self.adc_to_degC_coeff_1 * raw       \
             + self.adc_to_degC_coeff_2 * raw * raw
        log.debug("Detector temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        return degC

    # def get_ccd_temperature(self):
    #     """ Read the Analog to Digital conversion value from the device.  Apply 
    #         formula to convert AD value to temperature, return raw temperature 
    #         value. """
    #
    #     result = self.get_code(0xd7)
    #
    #     # Swap endianness of raw ADC value
    #     adc_value = result[1] + (result[0] << 8)
    #
    #     log.debug("get_ccd_temperature: adc_value = 0x%04x", adc_value)
    #     if adc_value == 0:
    #         return 0
    #
    #     # MZ: should we change this?
    #     try:
    #         # scale 12-bit value to 1.5v
    #         voltage    = 1.5 * float(adc_value) / 4096.0
    #
    #         # Convert to resistance
    #         resistance = 10000 * voltage
    #         resistance = resistance / (2 - voltage)
    #
    #         # Find the log of the resistance with a 10kOHM resistor
    #         logVal     = math.log( resistance / 10000 )
    #         insideMain = float(logVal + ( 3977.0 / (25 + 273.0) ))
    #         tempc      = float( (3977.0 / insideMain) -273.0 )
    #         result     = tempc
    #
    #     except ValueError as exc:
    #         if self.ccd_temperature_invalid == 0:
    #             log.critical("Hardware Failure CCD temp invalid (math domain error)", exc_info=1)
    #         self.ccd_temperature_invalid = 1
    #         raise
    #
    #     except Exception as exc:
    #         log.critical("Failure processing ccd temperature", exc_info=1)
    #         raise
    #
    #     return result

    def set_detector_tec_setpoint_degC(self, degC):
        """ Attempt to set the CCD cooler setpoint. Verify that it is within an 
            acceptable range. Ideally this is to prevent condensation and other 
            issues. This value is a default and is hugely dependent on the 
            environmental conditions. """

        if degC < self.tmin:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f below min %f", degC, self.tmin)
            return False

        if degC > self.tmax:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f exceeds max %f", degC, self.tmax)
            return False

        raw = int(self.degC_to_dac_coeff_0 + self.degC_to_dac_coeff_1 * degC + self.degC_to_dac_coeff_2 * degC * degC)

        # constrain to 12-bit DAC
        if (raw < 0):
            raw = 0
        if (raw > 0xfff):
            raw = 0xfff

        log.info("Set CCD TEC Setpoint: %.2f deg C (raw ADC 0x%04x)", degC, raw)
        result = self.send_code(0xD8, raw)
        self.detector_tec_setpoint_has_been_set = True
        return True

    def set_detector_tec_enable(self, flag=0):
        value = 1 if flag else 0

        if not self.detector_tec_setpoint_has_been_set:
            log.debug("defaulting TEC setpoint to min %s", self.tmin)
            self.set_detector_tec_setpoint_degC(self.tmin)

        log.debug("Send CCD TEC enable: %s", value)
        result = self.send_code(0xd6, value)

    def set_ccd_trigger(self, flag=0):
        # Don't send the opcode on ARM. See issue #2 on WasatchUSB project
        if self.pid != 0x2000:
            msb = 0
            lsb = 1 if flag else 0
            buf = 8 * [0]
            bytes_written = self.send_code(0xd2, lsb, msb, buf)

    def set_high_gain_mode_enable(self, flag=0):
        # CF_SELECT is configured using bit 2 of the FPGA configuration register 
        # 0x12.  This bit can be set using vendor commands 0xEB to SET and 0xEC 
        # to GET.  Note that the set command is expecting a 5-byte unsigned 
        # value, the highest byte of which we pass as part of an 8-byte buffer.
        # Not sure why.
        log.debug("Set high gain mode: %s", flag)

        msb = 0
        lsb = 1 if flag else 0
        buf = 8 * [0]
        bytes_written = self.send_code(0xeb, lsb, msb, buf)

    def set_laser_enable(self, flag=0):
        value = 1 if flag else 0
        log.debug("Send laser enable: %d", value)
        result = self.send_code(0xbe, value)
        return result

    def get_laser_temperature_setpoint_raw(self):
        result = self.get_code(0xe8)
        return result[0]

    def set_laser_temperature_setpoint_raw(self, value):
        log.debug("Send laser temperature setpoint raw: %d", value)
        return self.send_code(0xe7, value)

    def set_laser_power_perc(self, value=100):
        """ Laser power is determined by a combination of the pulse width, 
            period and modulation being enabled. There are many combinations of 
            these values that will produce a given percentage of the total laser 
            power through pulse width modulation. There is no 'get laser power' 
            control message on the device.

            Some of the goals of Enlighten are for it to be stable, and a reason 
            we have sales. During spectrometer builds, it was discovered that 
            the laser power settings were not implemented. During the 
            implementation process, it was discovered that the laser modulation, 
            pulse period and pulse width commands do not conform to 
            specification. Where you can set integration time 100 ms with the 
            command:

            device.ctrl_transfer(FID_bmRequestType=device to host,
                                 FID_bmRequest=0xDB,
                                 FID_wValue=100,
                                 FID_wIndex=0,
                                 FID_data_or_wLength=0)

            The laser pulse period must be set where the wValue and 
            data_or_wLength parameters are equal. So if you wanted a pulse
            period of 100, you must specify the value in both places:

            ...
                                 FID_wValue=100,
                                 FID_data_or_wLength=100)
            ...

            This in turn implies that the legacy firmware has a long masked
            issue when reading the value to update from the data_or_wLength
            parameter instead of the wValue field. This is only accurate for the 
            laser modulation related functions.

            This is backed up by the Dash v3 StrokerControl DLL implementation. 
            It was discovered that the StrokerControl DLL sets the wValue and 
            data or wLength parameters to the same value at every control 
            message write.

            The exciting takeaway here is that Enlighten is stable enough.
            Turning the laser on with the data or wLength parameter not set
            correctly will cause a hardware failure and complete device lockup 
            requiring a power cycle.

            fid_hardware:
                CRITICAL Hardware Failure FID Send Code Problem with
                         ctrl transfer: [Errno None] 11

            Unlike Dash which may lockup and require killing the application, 
            Enlighten does not lock up. The Enlighten code base has now been 
            used to unmask an issue that has been lurking with our legacy 
            firmware for close to 6 years. We've detected this out of 
            specification area of the code before it can adversely impact a 
            customer. """

        # don't want anything weird when passing over USB
        value = int(value)

        # Turn off modulation at full laser power, exit
        if value >= 100 or value < 0:
            log.info("Turning off laser modulation (full power)")
            result = self.send_code(0xBD, 0)
            return result

        # Change the pulse period to 100 us
        result = self.send_code(FID_bmRequest=0xc7, 
                                FID_wValue=100,
                                FID_wIndex=0,
                                FID_data_or_wLength=100)
        if result == None:
            log.critical("Hardware Failure to send laser mod. pulse period")
            return False

        # Set the pulse width to the 0-100 percentage of power
        result = self.send_code(FID_bmRequest=0xdb, 
                                FID_wValue=value,
                                FID_wIndex=0,
                                FID_data_or_wLength=value)
        if result == None:
            log.critical("Hardware Failure to send pulse width")
            return False

        # Enable modulation
        #
        # result = self.send_code(0xBD, 1)
        #
        # This will result in a control message failure. Only for the
        # laser modulation functions. A data length must be specified to
        # prevent the failure. Also present in the get_line control
        # message. Only with libusb; the original Cypress drivers do not
        # have this requirement.

        result = self.send_code(FID_bmRequest=0xbd, 
                                FID_wValue=1,
                                FID_wIndex=0,
                                FID_data_or_wLength="00000000")

        if result == None:
            log.critical("Hardware Failure to send laser modulation")
            return False

        log.info("Laser power set to: %s", value)
        return result

    def set_wavecal_coeffs(self, coeffs):
        try:
            (c0, c1, c2, c3) = coeffs.split(" ")
        except Exception as exc:
            log.critical("Wavecal coeffs split failiure", exc_info=1)
            return

        self.wavelength_coeff_0 = c0
        self.wavelength_coeff_1 = c1
        self.wavelength_coeff_2 = c2
        self.wavelength_coeff_3 = c3

        log.debug("updated wavecal coeffs")

    def set_degC_to_dac_coeffs(self, coeffs):
        """ Temporary solution for modifying the CCD TEC setpoint calibration 
            coefficients. These are used as part of a 2nd-order polynomial for 
            transforming the setpoint temperature into a DAC value. Expects a 
            great deal of accuracy on part of the user, otherwise sets default. """

        try:
            (c0, c1, c2) = coeffs.split(" ")
        except Exception as exc:
            log.critical("TEC Coeffs split failiure", exc_info=1)
            return

        self.degC_to_dac_coeff_0 = float(c0)
        self.degC_to_dac_coeff_1 = float(c1)
        self.degC_to_dac_coeff_2 = float(c2)

        log.info("Successfully changed CCD TEC setpoint coefficients")

    def get_ccd_trigger(self):
        """ Read the trigger source setting from the device. 0=internal,
            1=external. Use caution when interpreting the larger behavior of
            the device as ARM and FX2 implementations differ as of 2017-08-02 """

        result = self.get_code(0xd3)
        return result[0]

    def write_setting(self, record):
        """ Perform the specified setting such as physically writing the laser 
            on, changing the integration time, turning the cooler on, etc. """

        if not hasattr(record, "setting"):
            log.critical("fid.write_setting: invalid record: %s", record, exc_info=1)
            return

        log.debug("fid.write_setting: %s -> %s", record.setting, record.value)

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

        elif record.setting == "detector_tec_enable":
            self.detector_tec_enable = int(record.value)
            self.set_detector_tec_enable(self.detector_tec_enable)

        elif record.setting == "degC_to_dac_coeffs":
            self.set_degC_to_dac_coeffs(record.value) 

        elif record.setting == "wavecal":
            self.set_wavecal_coeffs(record.value)

        elif record.setting == "laser_power_perc":
            self.laser_power_perc = int(record.value)
            self.set_laser_power_perc(self.laser_power_perc)

        elif record.setting == "laser_temperature_setpoint_raw":
            self.laser_temperature_setpoint_raw = int(record.value)
            self.set_laser_temperature_setpoint_raw(self.laser_temperature_setpoint_raw)

        elif record.setting == "ccd_gain":
            self.ccd_gain = float(record.value)
            self.set_ccd_gain(self.ccd_gain)

        elif record.setting == "ccd_offset":
            self.ccd_offset = float(record.value)
            self.set_ccd_offset(self.ccd_offset)

        elif record.setting == "high_gain_mode_enable":
            self.high_gain_mode_enable = int(record.value)
            self.set_high_gain_mode_enable(self.high_gain_mode_enable)

        elif record.setting == "ccd_trigger":
            self.ccd_trigger = int(record.value)
            self.set_ccd_trigger(self.ccd_trigger)

        elif record.setting == "scans_to_average":
            self.scans_to_average = int(record.value)

        elif record.setting == "bad_pixel_mode":
            self.bad_pixel_mode= int(record.value)

        elif record.setting == "log_level":
            self.set_log_level(record.value)

        elif record.setting == "min_usb_interval_ms":
            self.min_usb_interval_ms = int(record.value)

        elif record.setting == "max_usb_interval_ms":
            self.max_usb_interval_ms = int(record.value)

        else:
            log.critical("Unknown setting to write: %s", record.setting)
            return False

        return True

    def set_log_level(self, s):
        lvl = logging.DEBUG if s == "DEBUG" else logging.INFO
        log.info("fid.set_log_level: setting to %s", lvl)
        logging.getLogger().setLevel(lvl)
