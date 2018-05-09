""" Interface wrapper around libusb and cypress drivers to show devices
    compliant with the Wasatch Feature Identification Device (FID) protocol.

    TODO: inherit from SpectrometerDevice or similar
"""

import datetime
import logging
import math
import usb
import usb.core
import usb.util

from time import sleep
from random import randint

from . import common

from SpectrometerSettings import SpectrometerSettings
from SpectrometerState    import SpectrometerState

log = logging.getLogger(__name__)

USB_TIMEOUT_MS = 60000

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

        self.laser_temperature_invalid = False
        self.ccd_temperature_invalid = False

        self.settings = SpectrometerSettings()

        ########################################################################
        # these are "driver state" within FeatureIdentificationDevice, and don't
        # really relate to the spectrometer hardware
        ########################################################################

        self.detector_tec_setpoint_has_been_set = False
        self.last_applied_laser_power = 0 # last power level APPLIED to laser, either by turning off (0) or on (immediate or ramping)
        self.next_applied_laser_power = 100 # power level to be applied NEXT time the laser is enabled (immediate or ramping)

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

        ########################################################################
        # PID-specific settings
        ########################################################################

        if self.pid == 0x4000:
            self.settings.state.min_usb_interval_ms = 10
            self.settings.state.max_usb_interval_ms = 10

        # overridden by EEPROM
        if self.pid == 0x2000:
            self.settings.eeprom.active_pixels_horizontal = 512

        self.read_eeprom()
        self.read_fpga_compilation_options()

        return True

    def disconnect(self):
        if self.last_applied_laser_power != 0:
            log.debug("fid.disconnect: disabling laser")
            self.set_laser_enable_immediate(False)

        log.critical("fid.disconnect: releasing interface")
        try:
            result = usb.util.release_interface(self.device, 0)
        except Exception as exc:
            log.warn("Failure in release interface", exc_info=1)
            raise
        return True

    def schedule_disconnect(self):
        # Not doing this right now, because it's not clear that all
        #
        #   "USBError: [Errno None] libusb0-dll:err [control_msg] sending
        #    control message failed, win error: A device attached to the system
        #    is not functioning."
        #
        # actually indicate unrecoverable errors.  The UV-VIS currently
        # generates that when trying to enable the TEC, even though the TEC
        # seems to work.

        # log.critical("Due to hardware error, attempting reconnection")
        # self.disconnect()
        pass

    ############################################################################
    # Utility Methods
    ############################################################################

    def wait_for_usb_available(self):
        if self.settings.state.max_usb_interval_ms > 0:
            if self.last_usb_timestamp is not None:
                delay_ms = randint(self.settings.state.min_usb_interval_ms, self.settings.state.max_usb_interval_ms)
                next_usb_timestamp = self.last_usb_timestamp + datetime.timedelta(milliseconds=delay_ms)
                if datetime.datetime.now() < next_usb_timestamp:
                    log.debug("fid: sleeping to enforce %d ms USB interval", delay_ms)
                    while datetime.datetime.now() < next_usb_timestamp:
                        sleep(0.001) # 1ms
            self.last_usb_timestamp = datetime.datetime.now()

    # Note: some USB docs call this "bmRequest" for "bitmap" vs "byte", but it's
    #       definitely an octet.  And yes, the USB spec really does say "data_or_length".
    def send_code(self, bRequest, wValue=0, wIndex=0, data_or_wLength="", label=""):
        prefix = "" if not label else ("%s: " % label)
        result = None
        log.debug("%ssend_code: request 0x%02x value 0x%04x index 0x%04x data/len %s",
            prefix, bRequest, wValue, wIndex, data_or_wLength)
        try:
            self.wait_for_usb_available()
            result = self.device.ctrl_transfer(0x40,     # HOST_TO_DEVICE
                                               bRequest,
                                               wValue,
                                               wIndex,
                                               data_or_wLength) # add TIMEOUT_MS parameter?
        except Exception as exc:
            log.critical("Hardware Failure FID Send Code Problem with ctrl transfer", exc_info=1)
            self.schedule_disconnect()

        log.debug("%sSend Raw result: [%s]", prefix, result)
        log.debug("%ssend_code: request 0x%02x value 0x%04x index 0x%04x data/len %s: result %s",
            prefix, bRequest, wValue, wIndex, data_or_wLength, result)
        return result

    # MZ: weird that so few calls to this function override the default wLength
    def get_code(self, bRequest, wValue=0, wIndex=0, wLength=64, label=""):
        prefix = "" if not label else ("%s: " % label)
        result = None
        try:
            self.wait_for_usb_available()
            result = self.device.ctrl_transfer(0xc0,        # DEVICE_TO_HOST
                                               bRequest,
                                               wValue,
                                               wIndex,
                                               wLength)
        except Exception as exc:
            log.critical("Hardware Failure FID Get Code Problem with ctrl transfer", exc_info=1)
            self.schedule_disconnect()

        log.debug("%sget_code: request 0x%02x value 0x%04x index 0x%04x = [%s]",
            prefix, bRequest, wValue, wIndex, result)
        return result

    # note: doesn't relay wLength, so ALWAYS expects 64-byte response!
    def get_upper_code(self, wValue, wIndex=0, label=""):
        return self.get_code(0xff, wValue, wIndex, label=label)

    ############################################################################
    # initialization
    ############################################################################

    def read_eeprom(self):
        buffers = []
        for page in range(6):
            buffers.append(self.get_upper_code(0x01, page, label="GET_MODEL_CONFIG(%d)" % page))
        self.settings.eeprom.parse(buffers)

    # at least one linearity coeff is other than 0 or -1
    def has_linearity_coeffs(self):
        if self.settings.eeprom.linearity_coeffs:
            for c in self.settings.eeprom.linearity_coeffs:
                if c != 0 and c != -1:
                    return True
        return False

    def read_fpga_compilation_options(self):
        buf = self.get_upper_code(0x04, label="READ_COMPILATION_OPTIONS")
        if buf is None or len(buf) < 2:
            log.error("fpga_opts: can't parse response: %s", buf)
            return
        word = buf[0] | (buf[1] << 8)
        self.settings.fpga_options.parse(word)

    ############################################################################
    # Accessors
    ############################################################################

    def get_integration_time(self):
        result = self.get_code(0xbf, label="GET_INTEGRATION_TIME")
        curr_time = (result[2] * 0x10000) + (result[1] * 0x100) + result[0]
        self.settings.state.integration_time_ms = curr_time
        return curr_time

    def set_ccd_offset(self, value):
        word = int(value) & 0xffff
        self.settings.state.ccd_offset = word
        return self.send_code(0xb6, word, label="SET_CCD_OFFSET")

    def get_ccd_gain(self):
        """ Read the device stored gain.  Convert from binary wasatch format.
            1st byte is binary encoded: 0 = 1/2, 1 = 1/4, 2 = 1/8 etc.
            2nd byte is the part to the left of the decimal
            On both sides, expanded exponents (fractional or otherwise) are summed.
            E.g., 231 dec == 0x01e7 == 1.90234375
        """
        result = self.get_code(0xc5, label="GET_CCD_GAIN")

        msb = result[1]
        lsb = result[0]

        gain = msb + lsb / 256.0
        log.debug("Gain is: %f (msb %d, lsb %d)" % (gain, msb, lsb))
        self.settings.state.ccd_gain = gain

        return gain

    def set_ccd_gain(self, gain):
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
        msb = int(gain)
        lsb = int((gain - msb) * 256)
        raw = (msb << 8) + lsb

        log.debug("Send CCD Gain: 0x%04x (%s)", raw, gain)
        self.send_code(0xb7, raw, label="SET_CCD_GAIN")
        self.settings.state.ccd_gain = gain

    def set_area_scan_enable(self, flag):
        value = 1 if flag else 0
        self.send_code(0xe9, value, label="SET_AREA_SCAN_ENABLE")
        self.settings.state.area_scan_enabled = flag

    def get_sensor_line_length(self):
        """ The line length is encoded as a LSB-MSB ushort, such that 0x0004 =
            1024 pixels """
        result = self.get_upper_code(0x03, label="GET_LINE_LENGTH")
        value = result[0] + result[1] << 8
        if value != self.settings.eeprom.active_pixels_horizontal:
            log.error("GET_LINE_LENGTH opcode result %d != EEPROM active_pixels_horizontal %d (using opcode)",
                value, self.settings.eeprom.active_pixels_horizontal)
            # MZ: change eeprom value?
        return value

    def get_laser_availability(self):
        result = self.get_upper_code(0x08, label="OPT_LASER")
        available = result[0] != 0
        if available != self.settings.eeprom.has_laser:
            log.error("OPT_LASER opcode result %s != EEPROM has_laser %s (using opcode)",
                value, self.settings.eeprom.has_laser)
            # MZ: change eeprom value?
        return available

    def get_microcontroller_firmware_version(self):
        result = self.get_code(0xc0, label="GET_CODE_REVISION")
        s = "%d.%d.%d.%d" % (result[3], result[2], result[1], result[0])
        self.settings.microcontroller_firmware_version = s
        return s

    def get_fpga_firmware_version(self):
        result = self.get_code(0xb4, label="GET_FPGA_REV")
        s = ""
        for i in range(len(result)):
            s += chr(result[i])
        self.settings.fpga_firmware_version = s
        return s

    def get_line(self):
        """ getSpectrum: send "acquire", then immediately read the bulk endpoint. """

        # Only send the CCD_GET_IMAGE (internal trigger) if external trigger is disabled
        log.debug("get_line: requesting spectrum")
        if self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_INTERNAL:
            result = self.send_code(0xad, data_or_wLength="00000000", label="ACQUIRE_CCD")

        # regardless of pixel count, assume uint16
        pixels = self.settings.pixels()

        if pixels in (512, 1024):
            endpoints = [0x82]
            block_len_bytes = pixels * 2
        elif pixels == 2048:
            endpoints = [0x82, 0x86]
            block_len_bytes = 2048 # pixels * 2 / 2
        else:
            raise Exception("Unsupported number of pixels: %d" % pixels)

        self.wait_for_usb_available()

        spectrum = []
        for endpoint in endpoints:
            log.debug("waiting for %d bytes", block_len_bytes)
            data = self.device.read(endpoint, block_len_bytes, timeout=USB_TIMEOUT_MS)
            subspectrum = [i + 256 * j for i, j in zip(data[::2], data[1::2])]
            spectrum.extend(subspectrum)

        log.debug("get_line: pixels %d, endpoints %s, block %d, spectrum %s ...", 
            len(spectrum), endpoints, block_len_bytes, spectrum[0:9])

        if len(spectrum) != pixels:
            raise Exception("get_line read wrong number of pixels (expected %d, read %d)" % (pixels, len(spectrum)))

        # For custom benches where the detector is essentially rotated
        # 180-deg from our typical orientation with regard to the grating
        # (e.g., red wavelengths are diffracted toward pixel 0, and blue
        # wavelengths toward pixel 1023).  Note that this simply performs
        # a horizontal flip of the vertically-binned 1-D spectra, and
        # is NOT sufficient to perform a genuine 180-degree rotation of
        # 2-D imaging mode; if area scan is enabled, the user would likewise
        # need to reverse the display order of the rows.
        if self.settings.state.invert_x_axis:
            spectrum.reverse()

        return spectrum

    def set_integration_time(self, ms):
        """ Send the updated integration time in a control message to the device. """

        if ms < self.settings.eeprom.min_integration_time_ms or ms > self.settings.eeprom.max_integration_time_ms:
            log.error("fid.set_integration_time: %d ms outside range (%d ms, %d ms)",
                ms, self.settings.eeprom.min_integration_time_ms, self.settings.eeprom.max_integration_time_ms)
            return

        lsw = (ms % 65536) & 0xffff
        msw = (ms / 65536) & 0xffff

        result = self.send_code(0xB2, lsw, msw, label="SET_INTEGRATION_TIME")
        self.settings.state.integration_time_ms = ms
        return result

    ############################################################################
    # Temperature
    ############################################################################

    def select_adc(self, n):
        log.debug("select_adc -> %d", n)
        self.send_code(0xed, n, label="SELECT_LASER")
        self.settings.state.selected_adc = n

    def get_secondary_adc_calibrated(self, raw=None):
        if not self.has_linearity_coeffs():
            log.debug("secondary_adc_calibrated: no calibration")
            return None

        if raw is None:
            raw = self.get_secondary_adc_raw()
        raw = float(raw)

        # use the first 4 linearity coefficients as a 3rd-order polynomial
        calibrated = float(self.settings.eeprom.linearity_coeffs[0]) \
                   + float(self.settings.eeprom.linearity_coeffs[1]) * raw \
                   + float(self.settings.eeprom.linearity_coeffs[2]) * raw * raw \
                   + float(self.settings.eeprom.linearity_coeffs[3]) * raw * raw * raw
        log.debug("secondary_adc_calibrated: %f", calibrated)
        return calibrated

    def get_secondary_adc_raw(self):
        result = self.get_code(0xd5, wLength=2, label="GET_ADC")
        value = 0
        if result is not None and len(result) == 2:
            # We could validate to 12-bit here if desired
            value = result[0] | (result[1] << 8)
        else:
            log.error("Error reading secondary ADC")
        log.debug("secondary_adc_raw: 0x%04x", value)
        return value

    def get_laser_temperature_raw(self):
        result = self.get_code(0xd5, wLength=2, label="GET_LASER_TEMP")
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
        result = self.get_code(0xd7, label="GET_CCD_TEMP")
        if not result:
            raise Exception("Unable to read detector temperature")
        return result[1] + (result[0] << 8)

    def get_detector_temperature_degC(self, raw=-1):
        if raw < 0:
            raw = self.get_detector_temperature_raw()

        degC = self.settings.eeprom.adc_to_degC_coeffs[0]             \
             + self.settings.eeprom.adc_to_degC_coeffs[1] * raw       \
             + self.settings.eeprom.adc_to_degC_coeffs[2] * raw * raw
        log.debug("Detector temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        return degC

    def set_detector_tec_setpoint_degC(self, degC):
        """ Attempt to set the CCD cooler setpoint. Verify that it is within an
            acceptable range. Ideally this is to prevent condensation and other
            issues. This value is a default and is hugely dependent on the
            environmental conditions. """

        if degC < self.settings.eeprom.min_temp_degC:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f below min %f", degC, self.settings.eeprom.min_temp_degC)
            return False

        if degC > self.settings.eeprom.max_temp_degC:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f exceeds max %f", degC, self.settings.eeprom.max_temp_degC)
            return False

        raw = int(self.settings.eeprom.degC_to_dac_coeffs[0]
                + self.settings.eeprom.degC_to_dac_coeffs[1] * degC
                + self.settings.eeprom.degC_to_dac_coeffs[2] * degC * degC)

        # constrain to 12-bit DAC
        if (raw < 0):
            raw = 0
        if (raw > 0xfff):
            raw = 0xfff

        log.info("Set CCD TEC Setpoint: %.2f deg C (raw ADC 0x%04x)", degC, raw)
        self.send_code(0xd8, raw, label="SET_CCD_TEMP_SETPOINT")
        self.detector_tec_setpoint_has_been_set = True
        self.settings.state.tec_setpoint_degC = degC
        return True

    def set_tec_enable(self, flag):
        value = 1 if flag else 0

        if not self.detector_tec_setpoint_has_been_set:
            log.debug("defaulting TEC setpoint to min %s", self.settings.eeprom.min_temp_degC)
            self.set_detector_tec_setpoint_degC(self.settings.eeprom.min_temp_degC)

        log.debug("Send CCD TEC enable: %s", value)
        self.send_code(0xd6, value, label="SET_CCD_TEC_ENABLE")
        self.settings.state.tec_enabled = flag

    def set_ccd_trigger_source(self, value):
        # Don't send the opcode on ARM. See issue #2 on WasatchUSB project
        if self.pid == 0x2000:
            return

        msb = 0
        lsb = value
        buf = 8 * [0]
        self.send_code(0xd2, lsb, msb, buf, label="SET_CCD_TRIGGER_SOURCE")
        self.settings.state.trigger_source = value

    def set_high_gain_mode_enable(self, flag):
        # CF_SELECT is configured using bit 2 of the FPGA configuration register
        # 0x12.  This bit can be set using vendor commands 0xEB to SET and 0xEC
        # to GET.  Note that the set command is expecting a 5-byte unsigned
        # value, the highest byte of which we pass as part of an 8-byte buffer.
        # Not sure why.
        log.debug("Set high gain mode: %s", flag)

        msb = 0
        lsb = 1 if flag else 0
        buf = 8 * [0]
        self.send_code(0xeb, lsb, msb, buf, label="SET_CF_SELECT")
        self.settings.state.high_gain_mode_enabled = flag

    def set_laser_enable(self, flag):
        self.settings.state.laser_enabled = flag
        if flag and self.settings.state.laser_power_ramping_enabled:
            self.set_laser_enable_ramp()
        else:
            self.set_laser_enable_immediate(flag)

    def set_laser_enable_immediate(self, flag):
        value = 1 if flag else 0
        log.debug("Send laser enable: %d", value)
        if flag:
            self.last_applied_laser_power = 0
        else:
            self.last_applied_laser_power = self.next_applied_laser_power
        return self.send_code(0xbe, value, label="SET_LASER_ENABLE")

    def set_laser_enable_ramp(self):
        SET_LASER_ENABLE          = 0xbe
        SET_LASER_MOD_ENABLE      = 0xbd
        SET_LASER_MOD_PERIOD      = 0xc7
        SET_LASER_MOD_PULSE_WIDTH = 0xdb

        current_laser_setpoint = self.last_applied_laser_power
        target_laser_setpoint = self.next_applied_laser_power
        log.debug("set_laser_enable_ramp: ramping from %s to %s", current_laser_setpoint, target_laser_setpoint)

        timeStart = datetime.datetime.now()

        # start at current point
        self.send_code(SET_LASER_MOD_PERIOD, 100, 0, 100, label="SET_LASER_MOD_PERIOD (ramp)") # Sets the modulation period to 100us

        width = int(current_laser_setpoint)
        buf = [0] * 8

        self.send_code(SET_LASER_MOD_ENABLE, 1, 0, buf, label="SET_LASER_MOD_ENABLE (ramp)")
        self.send_code(SET_LASER_MOD_PULSE_WIDTH, width, 0, buf, label="SET_LASER_MOD_PULSE_WIDTH (ramp)")
        self.send_code(SET_LASER_ENABLE, 1, label="SET_LASER_ENABLE (ramp)")

        # apply first 80% jump
        if current_laser_setpoint < target_laser_setpoint:
            laser_setpoint = ((float(target_laser_setpoint) - float(current_laser_setpoint)) / 100.0) * 80.0
            laser_setpoint += float(current_laser_setpoint)
            eighty_percent_start = laser_setpoint
        else:
            laser_setpoint = ((float(current_laser_setpoint) - float(target_laser_setpoint)) / 100.0) * 80.0
            laser_setpoint = float(current_laser_setpoint) - laser_setpoint
            eighty_percent_start = laser_setpoint

        self.send_code(SET_LASER_MOD_PULSE_WIDTH, int(eighty_percent_start), 0, buf, label="SET_LASER_MOD_PULSE_WIDTH (80%)")
        sleep(0.02)

        x = float(self.settings.state.laser_power_ramp_increments)
        MAX_X3 = x * x * x
        for counter in range(self.settings.state.laser_power_ramp_increments):

            # compute this step's pulse width
            x = float(self.settings.state.laser_power_ramp_increments - counter)
            scalar = (MAX_X3 - (x * x * x)) / MAX_X3
            target_loop_setpoint = eighty_percent_start \
                                 + (scalar * (float(target_laser_setpoint) - eighty_percent_start))

            # apply the incremental pulse width
            width = int(target_loop_setpoint + 0.5)
            self.send_code(SET_LASER_MOD_PULSE_WIDTH, width, 0, buf, label="SET_LASER_MOD_PULSE_WIDTH (ramp)")

            # allow 10ms to settle
            log.debug("set_laser_enable_ramp: counter = %3d, width = 0x%04x, target_loop_setpoint = %8.2f", counter, width, target_loop_setpoint)
            sleep(0.01)

        timeEnd = datetime.datetime.now()
        log.debug("set_laser_enable_ramp: ramp time %.3f sec", (timeEnd - timeStart).total_seconds())

        self.last_applied_laser_power = self.next_applied_laser_power
        log.debug("set_laser_enable_ramp: last_applied_laser_power = %d", self.next_applied_laser_power)

    def set_laser_power_perc(self, value):
        # if the laser is already engaged and we're using ramping, then ramp to
        # the new level
        value = int(max(0, min(100, value)))
        self.settings.state.laser_power_perc = value
        if self.settings.state.laser_power_ramping_enabled and self.settings.state.laser_enabled:
            self.next_applied_laser_power = value
            self.set_laser_enable_ramp()
        else:
            # otherwise, set the power level more abruptly
            self.set_laser_power_perc_immediate(value)

    # when we're not ramping laser power, this is a separate action that sets the
    # laser power level (modulated pulse width) which will be used next time the
    # laser is turned on (or changed immediately, if the laser is already enabled)
    def set_laser_power_perc_immediate(self, value):
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

            device.ctrl_transfer(bRequestType=device_to_host,
                                 bmRequest=0xDB,
                                 wValue=100,
                                 wIndex=0,
                                 data_or_wLength=0)

            The laser pulse period must be set where the wValue and
            data_or_wLength parameters are equal. So if you wanted a pulse
            period of 100, you must specify the value in both places:

            ...
                                 wValue=100,
                                 data_or_wLength=100)
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

            fid:
                CRITICAL Hardware Failure FID Send Code Problem with
                         ctrl transfer: [Errno None] 11

            Unlike Dash which may lockup and require killing the application,
            Enlighten does not lock up. The Enlighten code base has now been
            used to unmask an issue that has been lurking with our legacy
            firmware for close to 6 years. We've detected this out of
            specification area of the code before it can adversely impact a
            customer. """

        # don't want anything weird when passing over USB
        value = int(max(0, min(100, value)))

        # Turn off modulation at full laser power, exit
        if value >= 100 or value < 0:
            log.info("Turning off laser modulation (full power)")
            self.next_applied_laser_power = 100
            log.debug("next_applied_laser_power = 100")
            return self.send_code(0xbd, 0, label="SET_LASER_MOD_ENABLED (full)")

        # Change the pulse period to 100 us
        result = self.send_code(0xc7, 100, 0, 100, label="SET_MOD_PERIOD (immediate)")
        if result is None:
            log.critical("Hardware Failure to send laser mod. pulse period")
            return False

        # Set the pulse width to the 0-100 percentage of power;
        # note we send value as wValue AND wLength_or_data
        result = self.send_code(0xdb, value, 0, value, label="SET_LASER_MOD_PULSE_WIDTH (immediate)")
        if result is None:
            log.critical("Hardware Failure to send pulse width")
            return False

        # Enable modulation
        #
        # result = self.send_code(0xBD, 1)
        #
        # This will result in a control message failure. Only for the
        # laser modulation functions. A data buffer must be specified to
        # prevent failure. Also present in the get_line control message.
        # Only with libusb; the original Cypress drivers do not have this
        # requirement.
        result = self.send_code(0xbd, 1, 0, "00000000", label="SET_LASER_MOD_ENABLED (immediate)")

        if result is None:
            log.critical("Hardware Failure to send laser modulation")
            return False

        log.info("Laser power set to: %d", value)

        self.next_applied_laser_power = value
        log.debug("next_applied_laser_power = %s", self.next_applied_laser_power)

        return result

    def reset_fpga(self):
        log.debug("fid: resetting FPGA")
        self.send_code(0xb5, label="RESET_FPGA")
        log.debug("fid: sleeping 3sec")
        sleep(3)

    def get_laser_temperature_setpoint_raw(self):
        result = self.get_code(0xe8, label="GET_LASER_TEMP_SETPOINT")
        return result[0]

    def set_laser_temperature_setpoint_raw(self, value):
        log.debug("Send laser temperature setpoint raw: %d", value)
        return self.send_code(0xe7, value, label="SET_LASER_TEMP_SETPOINT")

    # never called by ENLIGHTEN - provided for OEMs
    def get_ccd_trigger_source(self):
        """ Read the trigger source setting from the device. 0=internal,
            1=external. Use caution when interpreting the larger behavior of
            the device as ARM and FX2 implementations differ as of 2017-08-02 """

        result = self.get_code(0xd3, label="GET_CCD_TRIGGER_SOURCE")
        value = result[0]
        self.settings.state.trigger_source = value
        return value

    # move string-to-enum converter to AppLog
    def set_log_level(self, s):
        lvl = logging.DEBUG if s == "DEBUG" else logging.INFO
        log.info("fid.set_log_level: setting to %s", lvl)
        logging.getLogger().setLevel(lvl)

    def update_session_eeprom(self, pair):
        try:
            if len(pair) != 2:
                raise Exception("expected 2-tuple")
            intended_serial = pair[0]
            new_eeprom = pair[1]
        except:
            log.critical("fid.update_session_eeprom: was not passed (serial_number, EEPROM) pair", exc_info=1)
            return

        if not isinstance(new_eeprom, EEPROM):
            log.critical("fid.update_session_eeprom: rejecting invalid EEPROM")
            return

        # Confirm that this FeatureIdentificationDevice instance is the intended
        # recipient of the new EEPROM image, else things could get really confusing.
        # (It's okay if the new EEPROM image contains an updated serial number;
        # however, they should pass along the "old / previous" serial number for 
        # validation.
        if intended_serial != self.settings.eeprom.serial_number:
            log.critical("fid.update_session_eeprom: %s process rejecting EEPROM intended for %s",
                self.settings.eeprom.serial_number, intended_serial)
            return

        log.debug("fid: %s accepting EEPROM instance for session use (not writing)", intended_serial)
        self.settings.eeprom = record.value

    # implemented subset of WasatchDeviceWrapper.DEVICE_CONTROL_COMMANDS
    def write_setting(self, record):
        """ Perform the specified setting such as physically writing the laser
            on, changing the integration time, turning the cooler on, etc. """

        log.debug("fid.write_setting: %s -> %s", record.setting, record.value)

        if record.setting == "laser_enable":
            self.set_laser_enable(True if record.value else False)

        elif record.setting == "integration_time_ms":
            self.set_integration_time(int(record.value))

        elif record.setting == "detector_tec_setpoint_degC":
            self.set_detector_tec_setpoint_degC(int(record.value))

        elif record.setting == "detector_tec_enable":
            self.set_tec_enable(True if record.value else False)

        elif record.setting == "degC_to_dac_coeffs":
            self.settings.eeprom.degC_to_dac_coeffs = record.value

        elif record.setting == "laser_power_perc":
            self.set_laser_power_perc(int(record.value))

        elif record.setting == "laser_temperature_setpoint_raw":
            self.set_laser_temperature_setpoint_raw(int(record.value))

        elif record.setting == "ccd_gain":
            self.set_ccd_gain(float(record.value))

        elif record.setting == "ccd_offset":
            self.set_ccd_offset(int(record.value))

        elif record.setting == "high_gain_mode_enable":
            self.set_high_gain_mode_enable(True if record.value else False)

        elif record.setting == "trigger_source":
            self.set_ccd_trigger_source(int(record.value))

        elif record.setting == "scans_to_average":
            self.settings.state.scans_to_average = int(record.value)

        elif record.setting == "bad_pixel_mode":
            self.settings.state.bad_pixel_mode = int(record.value)

        elif record.setting == "log_level":
            self.set_log_level(record.value)

        elif record.setting == "min_usb_interval_ms":
            self.settings.state.min_usb_interval_ms = int(record.value)

        elif record.setting == "max_usb_interval_ms":
            self.settings.state.max_usb_interval_ms = int(record.value)

        elif record.setting == "reset_fpga":
            self.reset_fpga()

        elif record.setting == "enable_secondary_adc":
            self.settings.state.secondary_adc_enabled = True if record.value else False

        elif record.setting == "invert_x_axis":
            self.settings.state.invert_x_axis = True if record.value else False

        elif record.setting == "laser_power_ramping_enabled":
            self.settings.state.laser_power_ramping_enabled = True if record.value else False

        elif record.setting == "laser_power_ramp_increments":
            self.settings.state.laser_power_ramp_increments = int(record.value)

        elif record.setting == "area_scan_enable":
            self.set_area_scan_enable(True if record.value else False)

        elif record.setting == "eeprom":
            self.update_session_eeprom(record.value)

        elif record.setting == "write_eeprom":
            log.critical("WRITE_EEPROM NOT IMPLEMENTED")

        else:
            log.critical("Unknown setting to write: %s", record.setting)
            return False

        return True
