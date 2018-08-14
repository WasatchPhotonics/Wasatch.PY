import datetime
import logging
import copy
import math
import usb
import usb.core
import usb.util

from random import randint
from time   import sleep

from . import utils

from SpectrometerSettings import SpectrometerSettings
from SpectrometerState    import SpectrometerState
from StatusMessage        import StatusMessage
from Overrides            import Overrides
from EEPROM               import EEPROM

log = logging.getLogger(__name__)

USB_TIMEOUT_MS = 60000
MICROSEC_TO_SEC = 0.000001

##
# This is the basic implementation of our FeatureIdentificationDevice (FID) 
# spectrometer USB API as defined in ENG-0001.
#
# Compare this class to Wasatch.NET's Spectrometer.cs.
#
# This class is normally not accessed directly, but through the higher-level 
# abstraction WasatchDevice (which can also wrap legacy StrokerProtocol spectrometers,
# virtual FileSpectrometer devices etc).
#
# @todo inherit from SpectrometerDevice or similar
# @see ENG-0001
class FeatureIdentificationDevice(object):

    # ##########################################################################
    # Lifecycle
    # ##########################################################################

    ##
    # @param PID [in] USB Product ID
    # @param bus_order [in] sequence on the USB chain
    # @param message_queue [out] if provided, provides a queue for writing
    #        StatusMessage objects back to the caller
    def __init__(self, pid, bus_order=0, message_queue=None):

        log.debug("init %s", pid)
        self.vid = 0x24aa
        self.pid = int(pid, 16)
        self.bus_order = bus_order
        self.message_queue = message_queue

        self.device = None

        self.last_usb_timestamp = None

        self.laser_temperature_invalid = False
        self.ccd_temperature_invalid = False

        self.settings = SpectrometerSettings()
        self.eeprom_backup = None

        self.overrides = None
        self.last_override_value = {}

        # ######################################################################
        # these are "driver state" within FeatureIdentificationDevice, and don't
        # really relate to the spectrometer hardware
        # ######################################################################

        self.detector_tec_setpoint_has_been_set = False
        self.last_applied_laser_power = 0.0 # last power level APPLIED to laser, either by turning off (0) or on (immediate or ramping)
        self.next_applied_laser_power = None # power level to be applied NEXT time the laser is enabled (immediate or ramping)

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

        # ######################################################################
        # PID-specific settings
        # ######################################################################

        if self.is_arm():
            self.settings.state.min_usb_interval_ms = 0
            self.settings.state.max_usb_interval_ms = 0

        # overridden by EEPROM...do we need this?
        if self.is_ingaas():
            self.settings.eeprom.active_pixels_horizontal = 512

        self.read_eeprom()
        self.read_fpga_compilation_options()

        return True

    def disconnect(self):
        if self.last_applied_laser_power:
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

    # ##########################################################################
    # Utility Methods
    # ##########################################################################

    def is_arm(self):
        return self.pid == 0x4000

    def is_ingaas(self):
        return self.pid == 0x2000

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

    ##
    # Note: some USB docs call this "bmRequest" for "bitmap" vs "byte", but it's
    #       definitely an octet.  And yes, the USB spec really does say "data_or_length".
    def send_code(self, bRequest, wValue=0, wIndex=0, data_or_wLength=None, label="", dry_run=False):
        prefix = "" if not label else ("%s: " % label)
        result = None

        # MZ: need this?
        doL = data_or_wLength
        if data_or_wLength is None:
            if self.is_arm():
                data_or_wLength = [0] * 8
            else:
                data_or_wLength = ""

        log.debug("%ssend_code: request 0x%02x value 0x%04x index 0x%04x data/len %s (orig %s)",
            prefix, bRequest, wValue, wIndex, data_or_wLength, doL)

        if dry_run:
            return True

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

    ## @note weird that so few calls to this function override the default wLength
    def get_code(self, bRequest, wValue=0, wIndex=0, wLength=64, label="", msb_len=None, lsb_len=None):
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

        # demarshall or return raw array
        value = 0
        if msb_len is not None:
            # this will barf if result is None
            for i in range(msb_len):
                value = value << 8 | result[i]
            return value                    
        elif lsb_len is not None:
            # this will barf if result is None
            for i in range(lsb_len):
                value = (result[i] << (8 * i)) | value
            return value
        else:
            return result

    ## @note doesn't relay wLength, so ALWAYS expects 64-byte response!
    def get_upper_code(self, wValue, wIndex=0, label="", msb_len=None, lsb_len=None):
        return self.get_code(0xff, wValue, wIndex, label=label, msb_len=msb_len, lsb_len=lsb_len)

    # ##########################################################################
    # initialization
    # ##########################################################################

    def read_eeprom(self):
        buffers = []
        for page in range(6):
            buffers.append(self.get_upper_code(0x01, page, label="GET_MODEL_CONFIG(%d)" % page))
        self.settings.eeprom.parse(buffers)

    ##
    # at least one linearity coeff is other than 0 or -1
    def has_linearity_coeffs(self):
        if self.settings.eeprom.linearity_coeffs:
            for c in self.settings.eeprom.linearity_coeffs:
                if c != 0 and c != -1:
                    return True
        return False

    def read_fpga_compilation_options(self):
        word = self.get_upper_code(0x04, label="READ_COMPILATION_OPTIONS", lsb_len=2)
        self.settings.fpga_options.parse(word)

    # ##########################################################################
    # Accessors
    # ##########################################################################

    def get_integration_time_ms(self):
        ms = self.get_code(0xbf, label="GET_INTEGRATION_TIME_MS", lsb_len=3)
        self.settings.state.integration_time_ms = ms
        return ms

    def set_detector_offset(self, value):
        word = int(value) & 0xffff
        self.settings.eeprom.detector_offset = word
        return self.send_code(0xb6, word, label="SET_DETECTOR_OFFSET")

    ##
    # Read the device stored gain.  Convert from binary "half-precision" float.
    #
    # - 1st byte (LSB) is binary encoded: 0 = 1/2, 1 = 1/4, 2 = 1/8 etc.
    # - 2nd byte (MSB) is the part to the left of the decimal
    # 
    # On both sides, expanded exponents (fractional or otherwise) are summed.
    #
    # E.g., 231 dec == 0x01e7 == 1.90234375
    def get_detector_gain(self):
        result = self.get_code(0xc5, label="GET_DETECTOR_GAIN")

        lsb = result[0] # LSB-MSB
        msb = result[1]

        gain = msb + lsb / 256.0
        log.debug("Gain is: %f (msb %d, lsb %d)" % (gain, msb, lsb))
        self.settings.eeprom.detctor_gain = gain

        return gain

    ##
    # Re-implementation for required gain settings with S10141
    # sensor. These comments are from the C DLL for the SDK - also see
    # control.py for details.
    #
    # 201205171534 nharrington:
    # Are you getting strange results even though you write what
    # appears to be the correct 2-byte integer data (first byte being
    # the binary encoding?) It looks like the value gets sent to the
    # device correctly, but is stored incorrectly (maybe).
    #
    # For example, if you run 'get_detector_gain' on the device:
    #
    #   C-00130   gain is 1.421875  1064  G9214
    #   WP-00108  gain is 1.296875  830-C S10141
    #   WP-00132  gain is 1.296875  638-R S11511
    #   WP-00134  gain is 1.296875  638-A S11511
    #   WP-00222  gain is 1.296875  VIS   S11511
    #
    # In practice, what this means is you will pass 1.9 as the gain
    # setting into this function. It will transform it to the value 487
    # according to the shifted gain algorithm below. The CCD will change
    # dynamic range. Reading back the gain will still say 1.296875. This
    # has been tested with WP-00108 on 20170602
    #
    # If you write 1.9 to C-00130, you get 1.296875 back, which seems to
    # imply that only the default gain is set differently with the G9214
    # sensor.
    #
    # To see more confusion: Start: WP-00154
    # Get gain value: 1.296875
    # Start enlighten, set gain to 3.0
    # Get gain again: 1.296875
    # Why does it not change?
    def set_detector_gain(self, gain):

        if round(gain, 2) == 1.90:
            log.warn("legacy spectrometers don't like gain being re-set to default 1.90...ignoring")
            return

        msb = int(gain)
        lsb = int((gain - msb) * 256)
        raw = (msb << 8) + lsb

        # MZ: note that we SEND gain MSB-LSB, but we READ gain LSB-MSB?!

        log.debug("Send Detector Gain: 0x%04x (%s)", raw, gain)
        self.send_code(0xb7, raw, label="SET_DETECTOR_GAIN")
        self.settings.eeprom.detector_gain = gain

    def set_area_scan_enable(self, flag):
        value = 1 if flag else 0
        if not self.settings.isIMX():
            self.send_code(0xe9, value, label="SET_AREA_SCAN_ENABLE")
        self.settings.state.area_scan_enabled = flag

    ## The line length is encoded as a LSB-MSB ushort, such that 0x0004 = 1024 pixels
    def get_sensor_line_length(self):
        value = self.get_upper_code(0x03, label="GET_LINE_LENGTH", lsb_len=2)
        if value != self.settings.eeprom.active_pixels_horizontal:
            log.error("GET_LINE_LENGTH opcode result %d != EEPROM active_pixels_horizontal %d (using opcode)",
                value, self.settings.eeprom.active_pixels_horizontal)
            # MZ: change eeprom value?
        return value

    def get_opt_has_laser(self):
        available = (0 != self.get_upper_code(0x08, label="GET_OPT_HAS_LASER", msb_len=1))
        if available != self.settings.eeprom.has_laser:
            log.error("OPT_HAS_LASER opcode result %s != EEPROM has_laser %s (using opcode)",
                value, self.settings.eeprom.has_laser)
            # MZ: change eeprom value?
        return available

    def get_microcontroller_firmware_version(self):
        result = self.get_code(0xc0, label="GET_CODE_REVISION")
        version = "?.?.?.?"
        if result is not None and len(result) >= 4:
            version = "%d.%d.%d.%d" % (result[3], result[2], result[1], result[0]) # MSB-LSB
        self.settings.microcontroller_firmware_version = version
        return version

    def get_fpga_firmware_version(self):
        result = self.get_code(0xb4, label="GET_FPGA_REV")
        s = ""
        if result is not None:
            for i in range(len(result)):
                s += chr(result[i])
        self.settings.fpga_firmware_version = s
        return s

    ## send "acquire", then immediately read the bulk endpoint(s).
    def get_line(self):
        # Only send the CCD_GET_IMAGE (internal trigger) if external trigger is disabled (default)
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
            log.warn("unusual number of pixels (%d)...guessing at endpoint" % pixels)
            endpoints = [0x82]
            block_len_bytes = pixels * 2

        self.wait_for_usb_available()

        spectrum = []
        for endpoint in endpoints:
            log.debug("waiting for %d bytes", block_len_bytes)
            data = self.device.read(endpoint, block_len_bytes, timeout=USB_TIMEOUT_MS)
            subspectrum = [i + 256 * j for i, j in zip(data[::2], data[1::2])] # LSB-MSB
            spectrum.extend(subspectrum)

        log.debug("get_line: pixels %d, endpoints %s, block %d, spectrum %s ...", 
            len(spectrum), endpoints, block_len_bytes, spectrum[0:9])

        if len(spectrum) != pixels:
            log.error("get_line read wrong number of pixels (expected %d, read %d)", pixels, len(spectrum))
            if len(spectrum) < pixels:
                spectrum.extend([0] * (pixels - len(spectrum)))
            else:
                spectrum = spectrum[:-pixels]

        # if we're in area scan mode, use first pixel as row index (leave pixel in spectrum)
        area_scan_row_count = -1
        if self.settings.state.area_scan_enabled:
            area_scan_row_count = spectrum[0]

            # override row counter to smooth data and avoid a weird peak/trough 
            # which could affect other smoothing, normalization or min/max algos.
            spectrum[0] = spectrum[1]

        # For custom benches where the detector is essentially rotated
        # 180-deg from our typical orientation with regard to the grating
        # (e.g., on this spectrometer red wavelengths are diffracted toward 
        # px0, and blue wavelengths toward px1023).  Note this simply performs
        # a horizontal FLIP of the vertically-binned 1-D spectra, and
        # is NOT sufficient to perform a genuine 180-degree rotation of
        # 2-D imaging mode; if area scan is enabled, the user would likewise
        # need to reverse the display order of the rows.
        if self.settings.state.invert_x_axis:
            spectrum.reverse()

        if self.settings.state.graph_alternating_pixels:
            # |x|x|x|x|x graph
            # 0123456789 pixel
            smoothed = []
            for i in range(len(spectrum)):
                if i % 2 == 0:
                    smoothed.append(spectrum[i])
                else:
                    averaged = spectrum[i - 1]
                    if i + 1 < len(spectrum):
                        averaged = round((spectrum[i-1] + spectrum[i+1]) / 2.0, 0)
                    smoothed.append(averaged)
            spectrum = smoothed

        return (spectrum, area_scan_row_count)

    ## Send the updated integration time in a control message to the device
    def set_integration_time_ms(self, ms):

        if ms < self.settings.eeprom.min_integration_time_ms or ms > self.settings.eeprom.max_integration_time_ms:
            log.error("fid.set_integration_time_ms: %d ms outside range (%d ms, %d ms)",
                ms, self.settings.eeprom.min_integration_time_ms, self.settings.eeprom.max_integration_time_ms)
            return False

        lsw = (ms % 65536) & 0xffff
        msw = (ms / 65536) & 0xffff

        result = self.send_code(0xB2, lsw, msw, label="SET_INTEGRATION_TIME_MS")
        self.settings.state.integration_time_ms = ms
        return result

    # ##########################################################################
    # Temperature
    # ##########################################################################

    def select_adc(self, n):
        log.debug("select_adc -> %d", n)
        self.settings.state.selected_adc = n
        self.send_code(0xed, n, label="SELECT_ADC")
        self.get_code(0xd5, wLength=2, label="GET_ADC (throwaway)")

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
        # flip to secondary ADC if needed
        if self.settings.state.selected_adc is None or self.settings.state.selected_adc != 1:
            self.select_adc(1)

        value = self.get_code(0xd5, wLength=2, label="GET_ADC", lsb_len=2) & 0xfff
        log.debug("secondary_adc_raw: 0x%04x", value)
        return value

    def get_laser_temperature_raw(self):
        # flip to primary ADC if needed
        if self.settings.state.selected_adc is None or self.settings.state.selected_adc != 0:
            self.select_adc(0)

        # Yes, this is little-endian, reverse of get_detector_temperature_raw
        result = self.get_code(0xd5, wLength=2, label="GET_ADC", lsb_len=2)
        if not result:
            raise Exception("Unable to read laser temperature")
        return result & 0xfff

    ##
    # @note laser doesn't use EEPROM coeffs at all
    def get_laser_temperature_degC(self, raw=-1):
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
            voltage    = 2.5 * raw / 4096.0
            resistance = 21450.0 * voltage / (2.5 - voltage)

            if resistance < 0:
                log.error("get_laser_temperature_degC: can't compute degC: raw = 0x%04x, voltage = %f, resistance = %f", raw, voltage, resistance)
                return -99

            logVal     = math.log(resistance / 10000.0)
            insideMain = logVal + 3977.0 / (25 + 273.0)
            degC       = 3977.0 / insideMain - 273.0

            log.debug("Laser temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        except:
            log.error("exception computing laser temperature", exc_info=1)

        return degC

    def get_detector_temperature_raw(self):
        # Yes, this is big-endian, reverse of get_laser_temperature_raw
        return self.get_code(0xd7, label="GET_CCD_TEMP", msb_len=2)

    def get_detector_temperature_degC(self, raw=-1):
        if raw < 0:
            raw = self.get_detector_temperature_raw()

        degC = self.settings.eeprom.adc_to_degC_coeffs[0]             \
             + self.settings.eeprom.adc_to_degC_coeffs[1] * raw       \
             + self.settings.eeprom.adc_to_degC_coeffs[2] * raw * raw
        log.debug("Detector temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        return degC

    ##
    # Attempt to set the CCD cooler setpoint. Verify that it is within an
    # acceptable range. Ideally this is to prevent condensation and other
    # issues. This value is a default and is hugely dependent on the
    # environmental conditions.
    def set_detector_tec_setpoint_degC(self, degC):
        if not self.settings.eeprom.has_cooling:
            log.error("unable to control TEC: EEPROM reports no cooling")
            return False

        if degC < self.settings.eeprom.min_temp_degC:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f below min %f", degC, self.settings.eeprom.min_temp_degC)
            return False

        if degC > self.settings.eeprom.max_temp_degC:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f exceeds max %f", degC, self.settings.eeprom.max_temp_degC)
            return False

        raw = int(round(self.settings.eeprom.degC_to_dac_coeffs[0]
                      + self.settings.eeprom.degC_to_dac_coeffs[1] * degC
                      + self.settings.eeprom.degC_to_dac_coeffs[2] * degC * degC))

        # ROUND (don't mask) to 12-bit DAC 
        raw = max(0, min(raw, 0xfff))

        log.info("Set CCD TEC Setpoint: %.2f deg C (raw ADC 0x%04x)", degC, raw)
        ok = self.send_code(0xd8, raw, label="SET_DETECTOR_TEC_SETPOINT")
        self.settings.state.tec_setpoint_degC = degC
        self.detector_tec_setpoint_has_been_set = True
        return ok

    def get_dac(self, dacIndex=0):
        return self.get_code(0xd9, wIndex=dacIndex, label="GET_DAC", lsb_len=2)

    def set_tec_enable(self, flag):
        if not self.settings.eeprom.has_cooling:
            log.error("unable to control TEC: EEPROM reports no cooling")
            return False

        value = 1 if flag else 0

        if not self.detector_tec_setpoint_has_been_set:
            log.debug("defaulting TEC setpoint to min %s", self.settings.eeprom.min_temp_degC)
            self.set_detector_tec_setpoint_degC(self.settings.eeprom.min_temp_degC)

        log.debug("Send detector TEC enable: %s", value)
        ok = self.send_code(0xd6, value, label="SET_DETECTOR_TEC_ENABLE")
        if ok:
            self.settings.state.tec_enabled = flag
        return ok

    def set_ccd_trigger_source(self, value):
        # Don't send the opcode on ARM. See issue #2 on WasatchUSB project
        if self.is_arm():
            return False

        msb = 0
        lsb = value
        buf = [0] * 8

        # MZ: this is weird...we're sending the buffer on an FX2-only command
        ok = self.send_code(0xd2, lsb, msb, buf, label="SET_CCD_TRIGGER_SOURCE")
        if ok: 
            self.settings.state.trigger_source = value
        return ok

    ##
    # CF_SELECT is configured using bit 2 of the FPGA configuration register
    # 0x12.  This bit can be set using vendor commands 0xEB to SET and 0xEC
    # to GET.  Note that the set command is expecting a 5-byte unsigned
    # value, the highest byte of which we pass as part of an 8-byte buffer.
    # Not sure why.
    def set_high_gain_mode_enable(self, flag):
        log.debug("Set high gain mode: %s", flag)

        msb = 0
        lsb = 1 if flag else 0
        buf = [0] * 8
        self.send_code(0xeb, lsb, msb, buf, label="SET_CF_SELECT")
        self.settings.state.high_gain_mode_enabled = flag

    def set_laser_enable(self, flag):
        if not self.settings.eeprom.has_laser:
            log.error("unable to control laser: EEPROM reports no laser installed")
            return False

        # perhaps ARM doesn't like the laser enabled before laser power is configured?
        if self.next_applied_laser_power is None:
            self.set_laser_power_perc(100.0)

        self.settings.state.laser_enabled = flag
        if flag and self.get_laser_power_ramping_enabled():
            self.set_laser_enable_ramp()
        else:
            self.set_laser_enable_immediate(flag)
        return True

    def set_laser_power_ramping_enable(self, flag):
        self.settings.state.laser_power_ramping_enabled = flag

    def get_laser_power_ramping_enabled(self):
        return self.settings.state.laser_power_ramping_enabled

    def set_laser_enable_immediate(self, flag):
        value = 1 if flag else 0
        log.debug("Send laser enable: %d", value)
        if flag:
            self.last_applied_laser_power = 0.0
        else:
            self.last_applied_laser_power = self.next_applied_laser_power

        lsb = value
        msb = 0
        buf = [0] * 8 # defined but not used

        return self.send_code(0xbe, lsb, msb, buf, label="SET_LASER_ENABLE")

    def set_laser_enable_ramp(self):
        SET_LASER_ENABLE          = 0xbe
        SET_LASER_MOD_ENABLE      = 0xbd
        SET_LASER_MOD_PERIOD      = 0xc7
        SET_LASER_MOD_PULSE_WIDTH = 0xdb

        # MZ: so if we never use SET_LASER_MOD_DURATION (0xb9), what's it for?

        current_laser_setpoint = self.last_applied_laser_power
        target_laser_setpoint = self.next_applied_laser_power
        log.debug("set_laser_enable_ramp: ramping from %s to %s", current_laser_setpoint, target_laser_setpoint)

        timeStart = datetime.datetime.now()

        # start at current point
        self.send_code(SET_LASER_MOD_PERIOD, 100, 0, 100, label="SET_LASER_MOD_PERIOD (ramp)") # Sets the modulation period to 100us

        width = int(round(current_laser_setpoint))
        buf = [0] * 8

        self.send_code(SET_LASER_MOD_ENABLE, 1, 0, buf, label="SET_LASER_MOD_ENABLE (ramp)")
        self.send_code(SET_LASER_MOD_PULSE_WIDTH, width, 0, buf, label="SET_LASER_MOD_PULSE_WIDTH (ramp)")
        self.send_code(SET_LASER_ENABLE, 1, label="SET_LASER_ENABLE (ramp)") # no buf

        # apply first 80% jump
        if current_laser_setpoint < target_laser_setpoint:
            laser_setpoint = ((float(target_laser_setpoint) - float(current_laser_setpoint)) / 100.0) * 80.0
            laser_setpoint += float(current_laser_setpoint)
            eighty_percent_start = laser_setpoint
        else:
            laser_setpoint = ((float(current_laser_setpoint) - float(target_laser_setpoint)) / 100.0) * 80.0
            laser_setpoint = float(current_laser_setpoint) - laser_setpoint
            eighty_percent_start = laser_setpoint

        self.send_code(SET_LASER_MOD_PULSE_WIDTH, int(round(eighty_percent_start)), 0, buf, label="SET_LASER_MOD_PULSE_WIDTH (80%)")
        sleep(0.02) # 20ms

        x = float(self.settings.state.laser_power_ramp_increments)
        MAX_X3 = x * x * x
        for counter in range(self.settings.state.laser_power_ramp_increments):

            # compute this step's pulse width
            x = float(self.settings.state.laser_power_ramp_increments - counter)
            scalar = (MAX_X3 - (x * x * x)) / MAX_X3
            target_loop_setpoint = eighty_percent_start \
                                 + (scalar * (float(target_laser_setpoint) - eighty_percent_start))

            # apply the incremental pulse width
            width = int(round(target_loop_setpoint))
            self.send_code(SET_LASER_MOD_PULSE_WIDTH, width, 0, buf, label="SET_LASER_MOD_PULSE_WIDTH (ramp)")

            # allow 10ms to settle
            log.debug("set_laser_enable_ramp: counter = %3d, width = 0x%04x, target_loop_setpoint = %8.2f", counter, width, target_loop_setpoint)
            sleep(0.01) # 10ms

        timeEnd = datetime.datetime.now()
        log.debug("set_laser_enable_ramp: ramp time %.3f sec", (timeEnd - timeStart).total_seconds())

        self.last_applied_laser_power = self.next_applied_laser_power
        log.debug("set_laser_enable_ramp: last_applied_laser_power = %d", self.next_applied_laser_power)

    def set_laser_power_mW(self, mW_in):
        if not self.settings.eeprom.has_laser_power_calibration():
            log.error("EEPROM doesn't have laser power calibration")
            return False

        mW = min(self.settings.eeprom.max_laser_power_mW, max(self.settings.eeprom.min_laser_power_mW, mW_in))

        perc = self.settings.eeprom.laser_power_mW_to_percent(mW)
        log.debug("set_laser_power_mW: range (%.2f, %.2f), requested %.2f, approved %.2f, percent = %.2f", 
            self.settings.eeprom.min_laser_power_mW, 
            self.settings.eeprom.max_laser_power_mW, 
            mW_in,
            mW,
            perc)
        return self.set_laser_power_perc(perc)

    ##
    # @todo support floating-point value, as we have a 12-bit ADC and can provide
    # a bit more precision than 100 discrete steps (goal to support 0.1 - .125% resolution)
    def set_laser_power_perc(self, value_in):
        if not self.settings.eeprom.has_laser:
            log.error("unable to control laser: EEPROM reports no laser installed")
            return False

        # if the laser is already engaged and we're using ramping, then ramp to
        # the new level
        value = float(max(0, min(100, value_in)))
        self.settings.state.laser_power = value
        self.settings.state.laser_power_in_mW = False
        log.debug("set_laser_power_perc: range (0, 100), requested %.2f, applying %.2f", value_in, value)

        if self.get_laser_power_ramping_enabled() and self.settings.state.laser_enabled:
            self.next_applied_laser_power = value
            return self.set_laser_enable_ramp()
        else:
            # otherwise, set the power level more abruptly
            return self.set_laser_power_perc_immediate(value)

    ##
    # When we're not ramping laser power, this is a separate action that sets the
    # laser power level (modulated pulse width) which will be used next time the
    # laser is turned on (or changed immediately, if the laser is already enabled).
    # Laser power is determined by a combination of the pulse width,
    # period and modulation being enabled. There are many combinations of
    # these values that will produce a given percentage of the total laser
    # power through pulse width modulation. There is no 'get laser power'
    # control message on the device.
    #
    # Some of the goals of Enlighten are for it to be stable, and a reason
    # we have sales. During spectrometer builds, it was discovered that
    # the laser power settings were not implemented. During the
    # implementation process, it was discovered that the laser modulation,
    # pulse period and pulse width commands do not conform to
    # specification. Where you can set integration time 100 ms with the
    # command:
    #
    # device.ctrl_transfer(bRequestType=device_to_host,
    #                      bmRequest=0xDB,
    #                      wValue=100,
    #                      wIndex=0,
    #                      data_or_wLength=0)
    #
    # The laser pulse period must be set where the wValue and
    # data_or_wLength parameters are equal. So if you wanted a pulse
    # period of 100, you must specify the value in both places:
    #
    # ...
    #                      wValue=100,
    #                      data_or_wLength=100)
    # ...
    #
    # This in turn implies that the legacy firmware has a long masked
    # issue when reading the value to update from the data_or_wLength
    # parameter instead of the wValue field. This is only accurate for the
    # laser modulation related functions.
    #
    # This is backed up by the Dash v3 StrokerControl DLL implementation.
    # It was discovered that the StrokerControl DLL sets the wValue and
    # data or wLength parameters to the same value at every control
    # message write.
    #
    # The exciting takeaway here is that Enlighten is stable enough.
    # Turning the laser on with the data or wLength parameter not set
    # correctly will cause a hardware failure and complete device lockup
    # requiring a power cycle.
    #
    # fid:
    #     CRITICAL Hardware Failure FID Send Code Problem with
    #              ctrl transfer: [Errno None] 11
    #
    # Unlike Dash which may lockup and require killing the application,
    # Enlighten does not lock up. The Enlighten code base has now been
    # used to unmask an issue that has been lurking with our legacy
    # firmware for close to 6 years. We've detected this out of
    # specification area of the code before it can adversely impact a
    # customer. """
    #
    # As long as laser power is modulated using a period of 100us,
    # with a necessarily-integral pulse width of 1-99us, then it's
    # not physically possible to support fractional power levels.
    #
    # @todo talk to Jason about changing modulation PERIOD to longer
    #     value (200us? 400? 1000?), OR whether pulse WIDTH can be
    #     in smaller unit (500ns? 100ns?)
    def set_laser_power_perc_immediate(self, value):
        # don't want anything weird when passing over USB
        value = int(max(0, min(100, round(value))))

        # Turn off modulation at full laser power, exit
        if value >= 100 or value < 0:
            log.info("Turning off laser modulation (full power)")
            self.next_applied_laser_power = 100.0
            log.debug("next_applied_laser_power = 100.0")
            lsb = 0
            msb = 0
            buf = [0] * 8
            return self.send_code(0xbd, lsb, msb, buf, label="SET_LASER_MOD_ENABLED (full)")

        # Change the pulse period to 100 us
        # MZ: this doesn't seem to agree with ENG-0001's comment about "square root"
        buf = [0] * 100
        result = self.send_code(0xc7, wValue=100, wIndex=0, data_or_wLength=buf, label="SET_MOD_PERIOD (immediate)")
        if result is None:
            log.critical("Hardware Failure to send laser mod. pulse period")
            return False

        # Set the pulse width to the 0-100 percentage of power;
        # note we send value as wValue AND wLength_or_data
        buf = [0] * max(8, value)
        result = self.send_code(0xdb, wValue=value, wIndex=0, data_or_wLength=buf, label="SET_LASER_MOD_PULSE_WIDTH (immediate)")
        if result is None:
            log.critical("Hardware Failure to send pulse width")
            return False

        # Enable modulation
        #
        # result = self.send_code(0xBD, 1)
        #
        # This will result in a control message failure, for this and 
        # many other functions. A data buffer must be specified to
        # prevent failure. Also present in the get_line control message.
        # Only with libusb; the original Cypress drivers do not have this
        # requirement.
        lsb = 1
        msb = 0
        buf = [0] * 8
        result = self.send_code(0xbd, lsb, msb, buf, label="SET_LASER_MOD_ENABLED (immediate)")

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

    ##
    # @note never used, provided for OEM
    def get_laser_temperature_setpoint_raw(self):
        if not self.settings.eeprom.has_laser:
            log.error("unable to control laser: EEPROM reports no laser installed")
            return 0

        result = self.get_code(0xe8, label="GET_LASER_TEC_SETPOINT")
        return result[0]

    def set_laser_temperature_setpoint_raw(self, value):
        log.debug("Send laser temperature setpoint raw: %d", value)
        return self.send_code(0xe7, value, label="SET_LASER_TEC_SETPOINT")

    ##
    # Read the trigger source setting from the device. 
    #
    # - 0 = internal
    # - 1 = external
    #
    # Use caution when interpreting the larger behavior of
    # the device as ARM and FX2 implementations differ as of 2017-08-02
    #
    # @note never called by ENLIGHTEN - provided for OEMs
    def get_ccd_trigger_source(self):
        value = self.get_code(0xd3, label="GET_CCD_TRIGGER_SOURCE", msb_len=1)
        self.settings.state.trigger_source = value
        return value

    # ##########################################################################
    # newly added for wasatch-shell
    # ##########################################################################

    def get_tec_enabled(self):
        if not self.settings.eeprom.has_cooling:
            log.error("unable to control TEC: EEPROM reports no cooling")
            return False
        return self.get_code(0xda, label="GET_CCD_TEC_ENABLE", msb_len=1)
        
    def get_actual_frames(self):
        return self.get_code(0xe4, label="GET_ACTUAL_FRAMES", lsb_len=2)

    def get_actual_integration_time_us(self):
        return self.get_code(0xdf, label="GET_ACTUAL_INTEGRATION_TIME_US", lsb_len=3)

    def get_detector_offset(self):
        return self.get_code(0xc4, label="GET_DETECTOR_OFFSET", lsb_len=2) # LSB is a guess

    def get_ccd_sensing_threshold(self):
        return self.get_code(0xd1, label="GET_CCD_SENSING_THRESHOLD", lsb_len=2)

    def get_ccd_threshold_sensing_mode(self):
        return self.get_code(0xcf, label="GET_CCD_THRESHOLD_SENSING_MODE", msb_len=1)

    def get_external_trigger_output(self):
        return self.get_code(0xe1, label="GET_EXTERNAL_TRIGGER_OUTPUT", msb_len=1)

    def get_interlock(self):
        return self.get_code(0xef, label="GET_INTERLOCK", msb_len=1)

    def get_laser_enabled(self):
        return self.get_code(0xe2, label="GET_LASER_ENABLED", msb_len=1)
        
    def get_link_laser_mod_to_integration_time(self):
        return self.get_code(0xde, label="GET_LINK_LASER_MOD_TO_INTEGRATION_TIME", msb_len=1)

    def get_laser_mod_enabled(self):
        return self.get_code(0xe3, label="GET_LASER_MOD_ENABLED", msb_len=1)

    def get_laser_mod_pulse_width(self):
        return self.get_code(0xdc, label="GET_LASER_MOD_PULSE_WIDTH", lsb_len=5)

    def get_laser_mod_duration(self):
        return self.get_code(0xc3, label="GET_LASER_MOD_DURATION", lsb_len=5)

    def get_laser_mod_period(self):
        return self.get_code(0xcb, label="GET_LASER_MOD_PERIOD", lsb_len=5)

    def get_laser_mod_pulse_delay(self): 
        return self.get_code(0xca, label="GET_LASER_MOD_PULSE_DELAY", lsb_len=5)

    def get_selected_adc(self):
        value = self.get_code(0xee, label="GET_SELECTED_ADC", msb_len=1)
        if self.settings.state.selected_adc != value:
            log.error("GET_SELECTED_ADC %d != state.selected_adc %d", value, self.settings.state.selected_adc)
            self.settings.state.selected_adc = value
        return value

    # not tested
    def set_trigger_delay(self, half_us):
        if not self.is_arm():
            return log.error("SET_TRIGGER_DELAY only supported on ARM")
        lsw = half_us & 0xffff
        msb = (half_us >> 16) & 0xff
        return self.send_code(0xaa, wValue=lsw, wIndex=msb, label="SET_TRIGGER_DELAY")

    # not tested
    def get_trigger_delay(self):
        if not self.is_arm():
            log.error("GET_TRIGGER_DELAY only supported on ARM")
            return -1
        return self.get_code(0xe4, label="GET_TRIGGER_DELAY", lsb_len=3) # not sure about LSB

    def get_vr_continuous_ccd(self):
        return self.get_code(0xcc, label="GET_VR_CONTINUOUS_CCD", msb_len=1)

    def get_vr_num_frames(self):
        return self.get_code(0xcd, label="GET_VR_NUM_FRAMES", msb_len=1)

    def get_opt_actual_integration_time(self):
        return self.get_upper_code(0x0b, label="GET_OPT_ACT_INT_TIME", msb_len=1)

    def get_opt_area_scan(self):
        return self.get_upper_code(0x0a, label="GET_OPT_AREA_SCAN", msb_len=1)
        
    def get_opt_cf_select(self):
        return self.get_upper_code(0x07, label="GET_OPT_CF_SELECT", msb_len=1)

    def get_opt_data_header_tab(self):
        return self.get_upper_code(0x06, label="GET_OPT_DATA_HEADER_TAB", msb_len=1)

    def get_opt_horizontal_binning(self):
        return self.get_upper_code(0x0c, label="GET_OPT_HORIZONTAL_BINNING", msb_len=1)
        
    def get_opt_integration_time_resolution(self):
        return self.get_upper_code(0x05, label="GET_OPT_INTEGRATION_TIME_RESOLUTION", msb_len=1)

    def get_opt_laser_control(self):
        return self.get_upper_code(0x09, label="GET_OPT_LASER_CONTROL", msb_len=1)

    ##
    # @todo move string-to-enum converter to AppLog
    def set_log_level(self, s):
        lvl = logging.DEBUG if s == "DEBUG" else logging.INFO
        log.info("fid.set_log_level: setting to %s", lvl)
        logging.getLogger().setLevel(lvl)

    def validate_eeprom(self, pair):
        try:
            if len(pair) != 2:
                raise Exception("pair had %d items" % len(pair))
            intended_serial = pair[0]
            new_eeprom = pair[1]
        except:
            log.critical("fid.validate_eeprom: expected (sn, EEPROM) pair", exc_info=1)
            return False

        if not isinstance(new_eeprom, EEPROM):
            log.critical("fid.validate_eeprom: rejecting invalid EEPROM reference")
            return False

        # Confirm that this FeatureIdentificationDevice instance is the intended
        # recipient of the new EEPROM image, else things could get really confusing.
        # (It's okay if the new EEPROM image contains an updated serial number;
        # however, they should pass along the "old / previous" serial number for 
        # validation.
        if intended_serial != self.settings.eeprom.serial_number:
            log.critical("fid.validate_eeprom: %s process rejecting EEPROM intended for %s",
                self.settings.eeprom.serial_number, intended_serial)
            return False

        return True

    ## 
    # Given a (serial_number, EEPROM) pair, update this process's "session" 
    # EEPROM with just the EDITABLE fields of the passed EEPROM.
    def update_session_eeprom(self, pair):
        if not self.validate_eeprom(pair):
            return

        log.debug("fid.update_session_eeprom: %s updating EEPROM instance", self.settings.eeprom.serial_number)

        if not self.eeprom_backup:
            self.eeprom_backup = copy.deepcopy(self.settings.eeprom)

        self.settings.eeprom.update_editable(pair[1])

    ## 
    # Given a (serial_number, EEPROM) pair, replace this process's "session" 
    # EEPROM with the passed EEPROM.
    def replace_session_eeprom(self, pair):
        if not self.validate_eeprom(pair):
            return

        log.debug("fid.replace_session_eeprom: %s replacing EEPROM instance", self.settings.eeprom.serial_number)

        if not self.eeprom_backup:
            self.eeprom_backup = copy.deepcopy(self.settings.eeprom)

        self.settings.eeprom = pair[1]

    ## Actually store the current session EEPROM fields to the spectrometer.
    def write_eeprom(self):
        if not self.eeprom_backup:
            log.critical("expected to update or replace EEPROM object before write command")
            return

        # backup contents of previous EEPROM in log
        log.info("Original EEPROM contents")
        self.eeprom_backup.dump()
        log.info("Original EEPROM buffers: %s", self.eeprom_backup.buffers)

        try:
            self.settings.eeprom.generate_write_buffers()
        except:
            log.critical("failed to render EEPROM write buffers", exc_info=1)
            return

        log.info("Would write new buffers: %s", self.settings.eeprom.write_buffers)

        for page in range(5, -1, -1):
            DATA_START = 0x3c00
            offset = DATA_START + page * 64
            log.debug("writing page %d at offset 0x%04x: %s", page, offset, self.settings.eeprom.write_buffers[page])
            self.send_code(0xa2, offset, 0, self.settings.eeprom.write_buffers[page])

    def set_overrides(self, overrides):
        log.debug("received overrides %s", overrides)
        self.overrides = overrides
        if self.overrides.startup is not None:
            log.debug("applying startup overrides %s", self.overrides.startup)
            for pair in self.overrides.startup:
                log.debug("applying startup override: %s", pair)
                self.apply_override(pair[0], pair[1])
            log.debug("done applying startup overrides")

    def apply_override(self, setting, value):
        if not self.overrides or not self.overrides.has_override(setting):
            log.error("no override for %s", setting)
            return

        if not self.overrides.valid_value(setting, value):
            log.error("[%s] is not a valid value for the %s override", value, setting)
            return

        override = self.overrides.get_override(setting, value)

        if setting in self.last_override_value:
            if str(value) == str(self.last_override_value[setting]):
                log.debug("skipping duplicate setting (%s already is [%s])", setting, value)
                return
            else:
                log.debug("previous override for %s was [%s] (now [%s])", setting, self.last_override_value[setting], value)
        else:
            log.debug("no previous override for %s found", setting)

        log.debug("storing last override %s = [%s]", setting, str(value))
        self.last_override_value[setting] = str(value)

        # apparently it's a valid override setting and value...proceed

        # we're going to send an override, so perform comms initialization
        if self.overrides.comms_init is not None and "byte_strings" in self.overrides.comms_init:
            self.queue_message("marquee_info", "comms init")
            self.apply_override_byte_strings(self.overrides.comms_init["byte_strings"])

        # Theoretically there could be many types of overrides. This is the only type
        # I've implemented for now.
        self.queue_message("marquee_info", "overriding %s -> %s" % (setting, value))
        if "byte_strings" in override:
            self.apply_override_byte_strings(override["byte_strings"])
        else:
            log.error("unsupported override configuration for %s %s", setting, value)

        # store result of override...need a more scalable way to do this
        if setting == "integration_time_ms":
            self.settings.state.integration_time_ms = int(value)

    def queue_message(self, setting, value):
        if self.message_queue is None:
            return

        msg = StatusMessage(setting, value)
        try:
            self.message_queue.put_nowait(msg)
        except:
            log.error("failed to enqueue StatusMessage (%s, %s)", setting, value, exc_info=1)

    ## 
    # assumes 'bytes' is an array of strings, where each string is a 
    # comma-delimited tuple like "2,0A,F0" or "DELAY_US,5" 
    def apply_override_byte_strings(self, byte_strings):
        string_count = len(byte_strings)                
        log.debug("sending %d byte strings over I2C", string_count)
        self.queue_message("progress_bar_max", string_count)

        count = 0
        for s in byte_strings:
            if s[0] == "DELAY_US":
                delay_us = int(s[1]) 
                log.debug("override: sleeping %d us", delay_us)
                sleep(delay_us * MICROSEC_TO_SEC)
                count += 1
                continue

            # ARM seems to expect "at least" 8 bytes, so provide at least that 
            # many, and append if more are needed.  Not sure how we're supposed
            # to handle message length in this case.
            buf = [0] * 8
            data = [int(b.strip(), 16) for b in s.split(',')]

            # the following was empirically determined from sonyConfigUSB.py
            chip_dir   = data[0]
            chip_addr  = data[1]
            chip_value = data[2]

            wIndex = (chip_addr << 8) | chip_dir
            buf[0] = chip_value

            log.debug("sending byte string %d of %d", count + 1, string_count)
            self.send_code(bRequest        = 0xff, 
                           wValue          = 0x11, 
                           wIndex          = wIndex,
                           data_or_wLength = buf,
                           label           = "OVERRIDE_BYTE_STRINGS")

            self.queue_message("progress_bar_value", count + 1)

            if self.overrides.min_delay_us > 0:
                sleep(self.overrides.min_delay_us * MICROSEC_TO_SEC)
            count += 1

    ##
    # Perform the specified setting such as physically writing the laser
    # on, changing the integration time, turning the cooler on, etc. 
    #
    # implemented subset of WasatchDeviceWrapper.DEVICE_CONTROL_COMMANDS
    def write_setting(self, record):

        setting = record.setting
        value   = record.value

        log.debug("fid.write_setting: %s -> %s", setting, value)

        if self.overrides and self.overrides.has_override(setting):
            self.apply_override(setting, value)

        elif setting == "laser_enable":
            self.set_laser_enable(True if value else False)

        elif setting == "integration_time_ms":
            self.set_integration_time_ms(int(round(value)))

        elif setting == "detector_tec_setpoint_degC":
            self.set_detector_tec_setpoint_degC(int(round(value)))

        elif setting == "detector_tec_enable":
            self.set_tec_enable(True if value else False)

        elif setting == "degC_to_dac_coeffs":
            self.settings.eeprom.degC_to_dac_coeffs = value

        elif setting == "laser_power_perc":
            self.set_laser_power_perc(value)

        elif setting == "laser_power_mW":
            self.set_laser_power_mW(value)

        elif setting == "laser_temperature_setpoint_raw":
            self.set_laser_temperature_setpoint_raw(int(round(value)))

        elif setting == "detector_gain":
            self.set_detector_gain(float(value))

        elif setting == "detector_offset":
            self.set_detector_offset(int(round(value)))

        elif setting == "high_gain_mode_enable":
            self.set_high_gain_mode_enable(True if value else False)

        elif setting == "trigger_source":
            self.set_ccd_trigger_source(int(value))

        elif setting == "scans_to_average":
            self.settings.state.scans_to_average = int(value)

        elif setting == "bad_pixel_mode":
            self.settings.state.bad_pixel_mode = int(value)

        elif setting == "log_level":
            self.set_log_level(value)

        elif setting == "min_usb_interval_ms":
            self.settings.state.min_usb_interval_ms = int(round(value))

        elif setting == "max_usb_interval_ms":
            self.settings.state.max_usb_interval_ms = int(round(value))

        elif setting == "reset_fpga":
            self.reset_fpga()

        elif setting == "enable_secondary_adc":
            self.settings.state.secondary_adc_enabled = True if value else False

        elif setting == "invert_x_axis":
            self.settings.state.invert_x_axis = True if value else False

        elif setting == "laser_power_ramping_enable":
            self.set_laser_power_ramping_enable(True if value else False)

        elif setting == "laser_power_ramp_increments":
            self.settings.state.laser_power_ramp_increments = int(value)

        elif setting == "area_scan_enable":
            self.set_area_scan_enable(True if value else False)

        elif setting == "update_eeprom":
            self.update_session_eeprom(value)

        elif setting == "replace_eeprom":
            self.replace_session_eeprom(value)

        elif setting == "write_eeprom":
            self.write_eeprom()

        elif setting == "overrides":
            self.set_overrides(value)

        elif setting == "graph_alternating_pixels":
            self.settings.state.graph_alternating_pixels = True if value else False

        else:
            log.critical("Unknown setting to write: %s", setting)
            return False

        return True
