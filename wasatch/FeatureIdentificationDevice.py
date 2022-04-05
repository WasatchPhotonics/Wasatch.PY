import platform
import datetime
import logging
import random
import copy
import math
import usb
import usb.core
import usb.util
import os
import re

from random import randint
from time   import sleep

from . import utils

from .SpectrometerSettings import SpectrometerSettings
from .SpectrometerState    import SpectrometerState
from .DetectorRegions      import DetectorRegions
from .StatusMessage        import StatusMessage
from .DetectorROI          import DetectorROI
from .EEPROM               import EEPROM

log = logging.getLogger(__name__)

MICROSEC_TO_SEC = 0.000001
UNINITIALIZED_TEMPERATURE_DEG_C = -999

class SpectrumAndRow(object):
    def __init__(self, spectrum=None, row=-1):
        self.spectrum = None
        self.row = row

        if spectrum is not None:
            self.spectrum = spectrum.copy()

##
# This is the basic implementation of our FeatureIdentificationDevice (FID)
# spectrometer USB API as defined in ENG-0001.
#
# This class is roughly comparable to Wasatch.NET's Spectrometer.cs.
#
# This class is normally not accessed directly, but through the higher-level
# abstraction WasatchDevice.
#
# @see ENG-0001
class FeatureIdentificationDevice(object):

    # ##########################################################################
    # Lifecycle
    # ##########################################################################

    ##
    # Instantiate a FeatureIdentificationDevice with from the given device_id.
    #
    # @param device_id [in] device ID ("USB:0x24aa:0x1000:1:24")
    # @param message_queue [out] if provided, provides an outbound (from FID)
    #        queue for writing StatusMessage objects upstream
    def __init__(self, device_id, message_queue=None):
        self.device_id = device_id
        self.message_queue = message_queue

        self.device = None
        if device_id.vid != 111111 and device_id.pid != 4000:
            self.device_type = device_id.device_type
        else:
            self.device_type = device_id

        self.last_usb_timestamp = None

        self.laser_temperature_invalid = False
        self.ccd_temperature_invalid = False

        self.settings = SpectrometerSettings(device_id)
        self.eeprom_backup = None

        # ######################################################################
        # these are "driver state" within FeatureIdentificationDevice, and don't
        # really relate to the spectrometer hardware
        # ######################################################################

        self.detector_tec_setpoint_has_been_set = False
        self.last_applied_laser_power = 0.0 # last power level APPLIED to laser, either by turning off (0) or on (immediate or ramping)
        self.next_applied_laser_power = None # power level to be applied NEXT time the laser is enabled (immediate or ramping)

        self.raise_exceptions = False
        self.inject_random_errors = False
        self.random_error_perc = 0.001   # 0.1%
        self.allow_default_gain_reset = True

        self.connected = False
        self.connecting = False
        self.shutdown_requested = False

        self.last_spectrum = None
        self.spectrum_count = 0
        self.prev_pixels = None

        # in case of I2C collisions within the spectrometer, e.g. due to battery-LED status
        self.retry_enabled = True
        self.retry_ms = 5
        self.retry_max = 3

        # initialize the table of lambdas used to process setting changes
        self.init_lambdas()

    ##
    # Connect to the device and initialize basic settings.
    #
    # @returns True on success
    # @warning this causes a problem in non-blocking mode (WasatchDeviceWrapper)
    #          on MacOS
    def connect(self):

        self.connecting = True

        # ######################################################################
        # USB Connection
        # ######################################################################

        # Generate a fresh listing of USB devices with the requested VID and PID.
        # Note that this is NOT how WasatchBus traverses the list.  It actually
        # calls usb.busses(), then iterates over bus.devices, but that's because
        # it doesn't know what PIDs it might be looking for.  We know, so just
        # narrow down the search to those devices.

        log.info(self.device_type)
        devices = self.device_type.find(find_all=True, idVendor=self.device_id.vid, idProduct=self.device_id.pid)
        log.info(devices)
        dev_list = list(devices) # convert from array

        device = None
        log.info(dev_list)
        for dev in dev_list:
            if dev.bus != self.device_id.bus:
                log.debug("FID.connect: rejecting device (bus %d != requested %d)", dev.bus, self.device_id.bus)
            elif dev.address != self.device_id.address:
                log.debug("FID.connect: rejecting device (address %d != requested %d)", dev.address, self.device_id.address)
            else:
                device = dev
                break

        if device is None:
            log.debug("FID.connect: unable to find DeviceID %s", str(self.device_id))
            self.connecting = False
            return False
        else:
            log.debug("FID.connect: matched DeviceID %s", str(self.device_id))

        if os.name != "posix":
            log.debug("on Windows, so NOT setting configuration and claiming interface")
        elif "macOS" in platform.platform():
            log.debug("on MacOS, so NOT setting configuration and claiming interface")
        else:
            log.debug("on posix, so setting configuration and claiming interface")
            try:
                log.debug("setting configuration")
                result = self.device_type.set_configuration(device)
            except Exception as exc:
                #####################################################################################################################
                # This additional if statement is present for the Raspberry Pi. There is an issue with resource busy errors.
                # Adding dev.reset() solves this. See https://stackoverflow.com/questions/29345325/raspberry-pyusb-gets-resource-busy
                #####################################################################################################################
                if "Resource busy" in str(exc):
                    log.warn("Hardware Failure in setConfiguration. Resource busy error. Attempting to reattach driver by reset.")
                    self.device_type.reset(dev)
                    connect()
                    return self.connected
                log.warn("Hardware Failure in setConfiguration", exc_info=1)
                self.connecting = False
                raise

            try:
                log.debug("claiming interface")
                result = self.device_type.claim_interface(device, 0)
            except Exception as exc:
                log.warn("Hardware Failure in claimInterface", exc_info=1)
                self.connecting = False
                raise

        self.device = device

        return self.post_connect()

    ##
    # Split-out from physical / bus connect() to simplify MockSpectrometer.
    #
    # @returns True on success
    def post_connect(self):            

        # ######################################################################
        # model-specific settings
        # ######################################################################

        log.debug("model-specific settings")

        if self.settings.is_ingaas():
            # This must be for some very old InGaAs spectrometers?
            # Will probably want to remove this for SiG...
            if not self.settings.is_arm():
                self.settings.eeprom.active_pixels_horizontal = 512

            if not self.get_high_gain_mode_enabled():
                self.set_high_gain_mode_enable(True)

        # ######################################################################
        # EEPROM
        # ######################################################################

        log.debug("reading EEPROM")

        if not self.read_eeprom():
            log.error("failed to read EEPROM")
            self.connecting = False
            return False

        degC = UNINITIALIZED_TEMPERATURE_DEG_C;
        eeprom = self.settings.eeprom
        if (eeprom.startup_temp_degC >= eeprom.min_temp_degC and 
            eeprom.startup_temp_degC <= eeprom.max_temp_degC):
            degC = eeprom.startup_temp_degC
        elif (re.match(r"10141|9214", eeprom.detector, re.IGNORECASE)):
            degC = -15
        elif (re.match(r"11511|11850|13971|7031", eeprom.detector, re.IGNORECASE)):
            degC = 10

        if (eeprom.has_cooling and degC != UNINITIALIZED_TEMPERATURE_DEG_C):
            #TEC doesn't do anything unless you give it a temperature first
            log.debug(f"setting TEC setpoint to {degC} deg C")
            self.detector_tec_setpoint_degC = degC
            self.set_detector_tec_setpoint_degC(self.detector_tec_setpoint_degC)

            log.debug("enabling detector TEC")
            #detector_tec_enabled = true
            self.detector_tec_setpoint_has_been_set = True

        # ######################################################################
        # FPGA
        # ######################################################################

        log.debug("reading FPGA compilation options")
        self.read_fpga_compilation_options()

        log.debug("configuring FPGA")

        # automatically push EEPROM values to the FPGA (on modern EEPROMs)
        # (this will work on SiG as well, even if we subsequently track its gain
        #  somewhat differently as state.gain_db)
        if self.settings.eeprom.format >= 4:
            log.debug("sending gain/offset to FPGA")
            self.set_detector_gain      (self.settings.eeprom.detector_gain)
            self.set_detector_offset    (self.settings.eeprom.detector_offset)
            self.set_detector_gain_odd  (self.settings.eeprom.detector_gain_odd)
            self.set_detector_offset_odd(self.settings.eeprom.detector_offset_odd)

        # initialize state.gain_db from EEPROM startup value
        self.settings.state.gain_db = self.settings.eeprom.detector_gain

        if self.settings.is_micro():
            roi = self.settings.get_vertical_roi()
            if roi is not None:
                self.set_vertical_binning(roi)

        self.settings.init_regions()        

        # ######################################################################
        # post-connection defaults
        # ######################################################################

        # default to internal triggering
        self.set_trigger_source(SpectrometerState.TRIGGER_SOURCE_INTERNAL)

        # probably the default, but just to be sure
        if self.settings.is_micro():
            # some kind of SiG
            if self.settings.eeprom.has_laser:
                log.debug("applying SiG settings")
                self.set_laser_watchdog_sec(10)
            else:
                log.debug("skipping laser features for non-Raman SiG")

        self.set_integration_time_ms(self.settings.eeprom.startup_integration_time_ms)

        # for now, enable Gen 1.5 accessory connector by default
        if self.settings.is_gen15():
            log.debug("enabling Gen 1.5 accessory connector")
            self.set_accessory_enable(True)

        # ######################################################################
        # Done
        # ######################################################################

        log.debug("connection successful")
        self.connected = True
        self.connecting = False

        return self.connected

    def disconnect(self):
        if self.last_applied_laser_power:
            log.debug("fid.disconnect: disabling laser")
            self._set_laser_enable_immediate(False)

        self.connected = False

        log.critical("fid.disconnect: releasing interface")
        try:
            result = self.device_type.release_interface(self.device, 0)
        except Exception as exc:
            log.warn("Failure in release interface", exc_info=1)
            raise
        return True

    ##
    # Something in the driver has caused it to request the controlling
    # application to close the peripheral.  The next time
    # WasatchDevice.acquire_data is called, it will pass a "poison pill" back
    # up the response queue.
    #
    # Alternately, non-ENLIGHTEN callers can set "raise_exceptions" -> True for
    # in-process exception-handling.
    #
    def schedule_disconnect(self, exc):
        if self.raise_exceptions:
            log.critical("schedule_disconnect: raising exception %s", exc)
            raise exc
        else:
            log.critical("requesting shutdown due to exception %s", exc)
            self.shutdown_requested = True

    # ##########################################################################
    # Utility Methods
    # ##########################################################################

    ##
    # Laser modulation and continuous-strobe commands take arguments in micro-
    # seconds as 40-bit values, where the least-significant 16 bits are passed
    # as wValue, the next-significant 16 as wIndex, and the most-significant
    # as a single byte of payload.  This function takes an unsigned integral
    # value (presumably microseconds) and returns a tuple of wValue, wIndex
    # and a buffer to pass as payload.
    def to40bit(self, val):
        lsw = val & 0xffff
        msw = (val >> 16) & 0xffff
        buf = [ (val >> 32) & 0xff, 0 * 7 ]
        return (lsw, msw, buf)

    ##
    # Wait until any enforced USB packet intervals have elapsed. This does
    # nothing in most cases - the function is normally a no-op.
    #
    # However, if the application has defined min/max_usb_interval_ms (say
    # (20, 50ms), then pick a random delay in the defined window (e.g. 37ms)
    # and sleep until it has been at least that long since the last USB
    # exchange.
    #
    # The purpose of this function was to wring-out some early ARM micro-
    # controllers with apparent timing issues under high-speed USB 2.0, to see
    # if communications issues disappeared if we enforced a communication
    # latency from the software side.
    def wait_for_usb_available(self):
        if self.settings.state.max_usb_interval_ms <= 0:
            return

        if self.last_usb_timestamp is not None:
            delay_ms = randint(self.settings.state.min_usb_interval_ms, self.settings.state.max_usb_interval_ms)
            next_usb_timestamp = self.last_usb_timestamp + datetime.timedelta(milliseconds=delay_ms)
            now = datetime.datetime.now()
            if now < next_usb_timestamp:
                sleep_sec = (next_usb_timestamp - now).total_seconds()
                log.debug("fid: sleeping %.3f sec to enforce %d ms USB interval", sleep_sec, delay_ms)
                sleep(sleep_sec)
        self.last_usb_timestamp = datetime.datetime.now()

    ##
    # This function is provided to simulate random USB communication errors
    # during regression testing, and is normally a no-op.
    def check_for_random_error(self):
        if not self.inject_random_errors:
            return False

        if random.random() <= self.random_error_perc:
            log.critical("Randomly-injected error")
            self.schedule_disconnect(Exception("Randomly-injected error"))
            return True
        return False

    ##
    # @returns True if believed successful
    def send_code(self, bRequest, wValue=0, wIndex=0, data_or_wLength=None, label="", dry_run=False, retry_on_error=False, success_result=0x00):
        if self.shutdown_requested or (not self.connected and not self.connecting):
            log.debug("send_code: not attempting because not connected")
            return False

        prefix = "" if not label else ("%s: " % label)
        result = None

        if data_or_wLength is None:
            if self.settings.is_arm():
                data_or_wLength = [0] * 8
            else:
                data_or_wLength = 0

        log.debug("%ssend_code: request 0x%02x value 0x%04x index 0x%04x data/len %s",
            prefix, bRequest, wValue, wIndex, data_or_wLength)

        if dry_run:
            return True

        if self.check_for_random_error():
            return False

        retry_count = 0
        while True:
            try:
                self.wait_for_usb_available()
                result = self.device_type.ctrl_transfer(self.device,
                                                   0x40,        # HOST_TO_DEVICE
                                                   bRequest,
                                                   wValue,
                                                   wIndex,
                                                   data_or_wLength) # add TIMEOUT_MS parameter?
            except Exception as exc:
                log.critical("Hardware Failure FID Send Code Problem with ctrl transfer", exc_info=1)
                self.schedule_disconnect(exc)
                return False

            log.debug("%ssend_code: request 0x%02x value 0x%04x index 0x%04x data/len %s: result %s",
                prefix, bRequest, wValue, wIndex, data_or_wLength, result)

            if not retry_on_error:
                return True

            # retry logic enabled, so compare result to expected
            matched_expected = True
            if len(success_result) < len(result):
                matched_expected = False
            else:
                for i in range(len(success_result)):
                    if result[i] != success_result[i]:
                        matched_expected = False
                        break

            if matched_expected:
                return True

            # apparently it didn't match expected
            retry_count += 1
            if retry_count > self.retry_max:
                log.error("giving up after %d retries", retry_count)
                return False

            # try again
            log.error("retrying (attempt %d)", retry_count + 1)

    ## @note weird that so few calls to this function override the default wLength
    # @todo consider adding retry logic as well
    def get_code(self, bRequest, wValue=0, wIndex=0, wLength=64, label="", msb_len=None, lsb_len=None):
        prefix = "" if not label else ("%s: " % label)
        result = None

        if self.shutdown_requested or (not self.connected and not self.connecting):
            log.debug("get_code: not attempting because not connected")
            return result

        if self.check_for_random_error():
            log.debug("random error")
            return False

        try:
            self.wait_for_usb_available()
            result = self.device_type.ctrl_transfer(self.device,
                                               0xc0,        # DEVICE_TO_HOST
                                               bRequest,
                                               wValue,
                                               wIndex,
                                               wLength)
        except Exception as exc:
            log.critical("Hardware Failure FID Get Code Problem with ctrl transfer", exc_info=1)
            self.schedule_disconnect(exc)
            return None

        log.debug("%sget_code: request 0x%02x value 0x%04x index 0x%04x = [%s]",
            prefix, bRequest, wValue, wIndex, result)

        if result is None:
            log.critical("get_code[%s, %s]: received null", label, self.device_id)
            self.schedule_disconnect(exc)
            return None

        # demarshall or return raw array
        value = 0
        if msb_len is not None:
            for i in range(msb_len):
                value = value << 8 | result[i]
            return value
        elif lsb_len is not None:
            for i in range(lsb_len):
                value = (result[i] << (8 * i)) | value
            return value
        else:
            return result

    def get_upper_code(self, wValue, wIndex=0, wLength=64, label="", msb_len=None, lsb_len=None):
        return self.get_code(0xff, wValue, wIndex, wLength, label=label, msb_len=msb_len, lsb_len=lsb_len)

    # ##########################################################################
    # initialization
    # ##########################################################################

    def read_eeprom(self):
        buffers = []
        for page in range(EEPROM.MAX_PAGES):
            buf = None
            try:
                buf = self.get_upper_code(0x01, page, label="GET_MODEL_CONFIG(%d)" % page)
            except:
                log.error("exception reading upper_code 0x01 with page %d", page, exc_info=1)
            buf_len = 0 if buf is None else len(buf)
            if buf is None or buf_len < 64:
                log.error(f"unable to read EEPROM received buf of {buf} and len {buf_len}")
                return False
            buffers.append(buf)
        return self.settings.eeprom.parse(buffers)

    ##
    # at least one linearity coeff is other than 0 or -1
    #
    # @todo check for NaN
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

    ##
    # @todo test endian order (in and out)
    def get_battery_register(self, reg):
        reg = reg & 0xffff
        return self.get_upper_code(0x14, wIndex=reg, label="GET_BATTERY_REG", msb_len=2)

    ## cache values for 1sec
    def get_battery_state_raw(self):
        now = datetime.datetime.now()
        if (self.settings.state.battery_timestamp is not None and now < self.settings.state.battery_timestamp + datetime.timedelta(seconds=1)):
            return self.settings.state.battery_raw

        self.settings.state.battery_timestamp = now
        self.settings.state.battery_raw = self.get_upper_code(0x13, label="GET_BATTERY_STATE", msb_len=3)

        log.debug("battery_state_raw: 0x%06x", self.settings.state.battery_raw)
        return self.settings.state.battery_raw

    def get_battery_percentage(self):
        word = self.get_battery_state_raw()
        lsb = (word >> 16) & 0xff
        msb = (word >>  8) & 0xff
        perc = msb + (1.0 * lsb / 256.0)
        log.debug("battery_perc: %.2f%%", perc)
        return perc

    def get_battery_charging(self):
        word = self.get_battery_state_raw()
        charging = (0 != (word & 0xff))
        return charging

    def get_integration_time_ms(self):
        ms = self.get_code(0xbf, label="GET_INTEGRATION_TIME_MS", lsb_len=3)

        if self.settings.state.integration_time_ms > 0:
            log.debug("GET_INTEGRATION_TIME_MS: now %d", ms)
            self.settings.state.integration_time_ms = ms
        else:
            log.debug("declining to initialize session integration_time_ms from spectrometer")

        return ms

    ##
    # Puts ARM-based spectrometers into Device Firmware Update (DFU) mode.
    #
    # @warning reflashing spectrometer firmware without specific instruction and
    #          support from Wasatch Photonics will void your warranty
    def set_dfu_enable(self, value=None):
        if not self.settings.is_arm():
            log.error("DFU mode only supported for ARM-based spectrometers")
            return False

        result = self.send_code(0xfe, label="SET_DFU_ENABLE")

        self.queue_message("marquee_info", "%s in DFU mode" % self.settings.eeprom.serial_number)

        self.schedule_disconnect(Exception("DFU Mode"))
        return result

    def set_detector_offset(self, value):
        word = utils.clamp_to_int16(value)
        self.settings.eeprom.detector_offset = word
        # log.debug("value %d (%s) = 0x%04x (%s)", value, format(value, 'b'), word, format(word, 'b'))
        return self.send_code(0xb6, word, label="SET_DETECTOR_OFFSET")

    def set_detector_offset_odd(self, value):
        if not self.settings.is_ingaas():
            log.debug("SET_DETECTOR_OFFSET_ODD only supported on InGaAs")
            return False

        word = utils.clamp_to_int16(value)
        self.settings.eeprom.detector_offset_odd = word

        return self.send_code(0x9c, word, label="SET_DETECTOR_OFFSET_ODD")

    ##
    # Read the device stored gain.  Convert from binary "half-precision" float.
    #
    # - 1st byte (LSB) is binary encoded: bit 0 = 1/2, bit 1 = 1/4, bit 2 = 1/8 etc.
    # - 2nd byte (MSB) is the integral part to the left of the decimal
    #
    # E.g., 231 dec == 0x01e7 == 1.90234375
    def get_detector_gain(self, update_session_eeprom=False):
        result = self.get_code(0xc5, label="GET_DETECTOR_GAIN")

        if result is None:
            log.error("GET_DETECTOR_GAIN returned NULL!")
            return -1

        lsb = result[0] # LSB-MSB
        msb = result[1]
        raw = (msb << 8) | lsb

        gain = msb + lsb / 256.0
        log.debug("get_detector_gain: %f (raw 0x%04x) (session eeprom %f)" % (
            gain, raw, self.settings.eeprom.detector_gain))

        if update_session_eeprom:
            self.settings.eeprom.detector_gain = gain

        if self.settings.is_micro():
            self.settings.state.gain_db = gain

        return gain

    def get_detector_gain_odd(self, update_session_eeprom=False):
        if not self.settings.is_ingaas():
            log.debug("GET_DETECTOR_GAIN_ODD only supported on InGaAs")
            return self.settings.eeprom.detector_gain_odd

        result = self.get_code(0x9f, label="GET_DETECTOR_GAIN_ODD")

        lsb = result[0] # LSB-MSB
        msb = result[1]
        raw = (msb << 8) | lsb

        gain = msb + lsb / 256.0
        log.debug("get_detector_gain_odd: %f (0x%04x) (session eeprom %f)" % (
            gain, raw, self.settings.eeprom.detector_gain_odd))
        if update_session_eeprom:
            self.settings.eeprom.detector_gain_odd = gain
        return gain

    ##
    # Note that this is used for detector types, including:
    #
    # - Hamamatsu silicon (S16010-*, S16011-*, etc)
    # - Hamamatsu InGaAs (G9214 etc)
    # - Sony IMX (IMX385 etc)
    #
    # It is important to understand that the UNIT of this value changes between
    # Hamamatsu and IMX detectors, but the DATATYPE does not.
    #
    # Reasonable gain levels for Hamamatsu are a floating-point scalar, literally
    # used to scale (gain) the signal, and are usually in the range (0.8 .. 1.2)
    # or thereabouts.
    #
    # Reasonable levels for IMX sensors are in dB and vary by detector, but are
    # usually in the range (0.0 .. 31.0), with exactly 0.1dB precision.  The
    # spectrometer's FW will round to the nearest setting (1.23 will be rounded
    # to 1.2).  IMX sensors switch from "analog gain" to "digital gain" above
    # a given threshold...on the IMX123, analog gain is 0.0 - 31.0, and digital
    # is 31.1 - 72.0 (I think).
    #
    # @see https://wasatchphotonics.com/api/Wasatch.NET/class_wasatch_n_e_t_1_1_funky_float.html
    def set_detector_gain(self, gain):
        raw = self.settings.eeprom.float_to_uint16(gain)

        # MZ: note that we SEND gain MSB-LSB, but we READ gain LSB-MSB?!
        log.debug("Send Detector Gain: 0x%04x (%s)", raw, gain)
        self.settings.eeprom.detector_gain = gain
        return self.send_code(0xb7, raw, label="SET_DETECTOR_GAIN")

    def set_detector_gain_odd(self, gain):
        if not self.settings.is_ingaas():
            log.debug("SET_DETECTOR_GAIN_ODD only supported on InGaAs")
            return False

        raw = self.settings.eeprom.float_to_uint16(gain)

        # MZ: note that we SEND gain MSB-LSB, but we READ gain LSB-MSB?!
        log.debug("Send Detector Gain Odd: 0x%04x (%s)", raw, gain)
        self.settings.eeprom.detector_gain_odd = gain
        return self.send_code(0x9d, raw, label="SET_DETECTOR_GAIN_ODD")

    ##
    # Historically, this opcode moved around a bit.  At one point it was 0xeb
    # (and is now again), which conflicts with CF_SELECT).  At other times it
    # was 0xe9, which conflicted with LASER_RAMP_ENABLE.  This seems to be what
    # we're standardizing on henceforth.
    def set_area_scan_enable(self, flag):
        if self.settings.is_ingaas():
            log.error("area scan is not supported on InGaAs detectors (single line array)")
            return False

        value = 1 if flag else 0
        self.settings.state.area_scan_enabled = flag
        return self.send_code(0xeb, value, label="SET_AREA_SCAN_ENABLE")

    def get_sensor_line_length(self):
        value = self.get_upper_code(0x03, label="GET_LINE_LENGTH", lsb_len=2)
        if value != self.settings.pixels():
            log.error("GET_LINE_LENGTH opcode result %d != SpectrometerSettings.pixels %d (using opcode result)",
                value, self.settings.pixels())
        return value

    def get_microcontroller_firmware_version(self):
        result = self.get_code(0xc0, label="GET_CODE_REVISION")
        version = "?.?.?.?"
        if result is not None and len(result) >= 4:
            version = "%d.%d.%d.%d" % (result[3], result[2], result[1], result[0]) # MSB-LSB
        self.settings.microcontroller_firmware_version = version
        return version

    def get_fpga_firmware_version(self):
        s = ""
        result = self.get_code(0xb4, label="GET_FPGA_REV")
        if result is not None:
            for i in range(len(result)):
                c = result[i]
                if 0x20 <= c < 0x7f: # visible ASCII
                    s += chr(c)
        self.settings.fpga_firmware_version = s
        return s

    ##
    # Send "acquire", then immediately read the bulk endpoint(s).
    #
    # Probably the most important method in this class, more commonly called
    # "getSpectrum" in most drivers.
    #
    # @param trigger (Input) send an initial ACQUIRE
    #
    # @returns tuple of (spectrum[], area_scan_row_count) for success
    # @returns None when it times-out while waiting for an external trigger
    #          (interpret as, "didn't find any fish this time, try again in a bit")
    # @returns False (bool) when it times-out or encounters an exception
    #          when NOT in external-triggered mode
    # @throws exception on timeout (unless external triggering enabled)
    def get_line(self, trigger=True):

        ########################################################################
        # send the ACQUIRE
        ########################################################################

        # main use-case for NOT sending a trigger would be when reading
        # subsequent lines of data from area scan "fast" mode

        acquisition_timestamp = datetime.datetime.now()
        if trigger and self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_INTERNAL:
            # Only send ACQUIRE (internal SW trigger) if external HW trigger is disabled (default)
            log.debug("get_line: requesting spectrum")
            self.send_code(0xad, label="ACQUIRE_SPECTRUM")

        ########################################################################
        # prepare to read spectrum
        ########################################################################

        pixels = self.settings.pixels()

        # when changing detector ROI, exactly ONE READ should be at the previous length
        # if self.prev_pixels is not None:
        #     log.debug(f"get_line: using one-time prev_pixels value of {self.prev_pixels} rather than {pixels}")
        #     pixels = self.prev_pixels
        #     self.prev_pixels = None

        # all models return spectra as [uint16]
        endpoints = [0x82]
        block_len_bytes = pixels * 2
        if pixels == 2048 and not self.settings.is_arm():
            endpoints = [0x82, 0x86]
            block_len_bytes = 2048 # 1024 pixels apiece from two endpoints

        if self.settings.is_micro():
            # we have no idea if microRaman has to "wake up" the sensor, so wait
            # long enough for 6 throwaway frames if need be
            timeout_ms = self.settings.state.integration_time_ms * 8 + 500 * self.settings.num_connected_devices
        else:
            timeout_ms = self.settings.state.integration_time_ms * 2 + 1000 * self.settings.num_connected_devices

        # due to additional firmware processing time for area scan?
        if self.settings.state.area_scan_enabled:
            if trigger:
                timeout_ms += 250
            else:
                # kludge: just use triple the intra-line delay
                timeout_ms = self.settings.eeprom.detector_offset * 3

        self.wait_for_usb_available()

        ########################################################################
        # read the data from bulk endpoint(s)
        ########################################################################

        spectrum = []
        for endpoint in endpoints:
            data = None
            while data is None:
                try:
                    log.debug("waiting for %d bytes (timeout %dms)", block_len_bytes, timeout_ms)
                    data = self.device_type.read(self.device, endpoint, block_len_bytes, timeout=timeout_ms)
                    log.debug("read %d bytes", len(data))
                except Exception as exc:
                    if self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_EXTERNAL:
                        # we don't know how long we'll have to wait for the trigger, so
                        # just loop and hope
                        # log.debug("still waiting for external trigger")
                        return None
                    else:
                        log.error(f"Encountered error on read of {exc}")
                        # if we fail even a single spectrum read, we return a
                        # False (poison-pill) and prepare to disconnect
                        return False

            # This is a convoluted way to iterate across the received bytes in 'data' as
            # two interleaved arrays, both only processing alternating bytes, but one (i)
            # starting at zero (even bytes) and the other (j) starting at 1 (odd bytes).
            subspectrum = [int(i | (j << 8)) for i, j in zip(data[::2], data[1::2])] # LSB-MSB

            spectrum.extend(subspectrum)

            # empirically determined need for 5ms delay when switching endpoints
            # on 2048px detectors during area scan
            if self.settings.state.area_scan_enabled and pixels == 2048: # and endpoint == 0x82:
                log.debug("sleeping 5ms between endpoints")
                sleep(0.005)

        ########################################################################
        # error-check the received spectrum
        ########################################################################

        log.debug("get_line: completed in %.2f sec (vs integration time %d ms)",
            (datetime.datetime.now() - acquisition_timestamp).total_seconds(),
            self.settings.state.integration_time_ms)

        log.debug("get_line: pixels %d, endpoints %s, block %d, spectrum %s ...",
            len(spectrum), endpoints, block_len_bytes, spectrum[0:9])

        if len(spectrum) != pixels:
            log.error("get_line read wrong number of pixels (expected %d, read %d)", pixels, len(spectrum))
            return True
            # if len(spectrum) < pixels:
            #     spectrum.extend([0] * (pixels - len(spectrum)))
            # else:
            #     spectrum = spectrum[:-pixels]

        ########################################################################
        #
        #                   post-process the spectrum
        #
        ########################################################################

        ########################################################################
        # Apply InGaAs even/odd gain/offset in software
        ########################################################################

        # (before x-axis inversion because this is where FPGA will do it)

        # this should be done in the FPGA, but older FW didn't have that
        # implemented, so fix in SW unless EEPROM indicates "already handled"
        if self.settings.is_ingaas() and not self.settings.eeprom.hardware_even_odd:
            self.correct_ingaas_gain_and_offset(spectrum)

        ########################################################################
        # Area Scan (rare)
        ########################################################################

        # (before x-axis inversion because line index at FPGA pixel 0)

        # If we're in area scan mode, use first pixel as row index (leave pixel 
        # in spectrum).  Do this before any horizontal averaging which might 
        # corrupt first pixel).  Note that InGaAs don't support DetectorRegions.
        area_scan_row_count = -1
        if self.settings.state.area_scan_enabled:
            area_scan_row_count = spectrum[0]

            # for i in range(4):
            #     spectrum[i] = spectrum[4] # KLUDGE: NRD-dual

            # Leave the row counter in place if we're in "Fast" Area Scan mode,
            # since downstream software can use it to assemble the final image.
            # ("Slow" Area Scan mode sent this value back as a separate field in
            # the Reading, but this isn't possible in Fast mode.  Just delete
            # Slow mode when Fast is widely deployed.)
            #
            if not self.settings.state.area_scan_fast:
                spectrum[0] = spectrum[1]

        ########################################################################
        # Start-of-Spectrum Marker (rare)
        ########################################################################

        # (before x-axis inversion because marker at FPGA pixel 0)

        # Check and track the "start of spectrum" marker.  This is very rare and
        # only for experimental units.  Currently Wasatch does not have any
        # "standard" spectrum framing data, although such would be useful. This
        # is only enabled in FPGAs when trying to debug rare timing issues. The
        # "Marker" is simply a pixel with the value 0xffff.  On FPGA FW where the
        # marker is enabled, spectral data is clamped to 0xfffe, meaning such
        # markers can ONLY appear as the first pixel in a spectrum.
        #
        # Would need updated to work with DetectorRegions.
        if self.settings.has_marker() and not self.settings.state.area_scan_enabled:
            marker = 0xffff
            if spectrum[0] == marker:
                # marker found where expected, so all is good (overwrite for a
                # clean graph)
                spectrum[0] = spectrum[1]
            else:
                # we DIDN'T find the marker where it was expected, so flag and
                # go hunting
                log.error("get_line: missing marker")
                for i in range(pixels):
                    if spectrum[i] == marker:
                        log.error("get_line: marker found at pixel %d", i)

        # consider skipping much of the following if in area scan mode

        ########################################################################
        # Stomp array ends
        ########################################################################

        # (before x-axis inversion because optically masked pixels are physical)

        # some detectors have "garbage" pixels at the front or end of every
        # spectrum (sync bytes and what-not)
        if self.settings.is_micro() and self.settings.state.detector_regions is None:
            if self.settings.is_imx392():
                utils.stomp_first(spectrum, 3)
                utils.stomp_last (spectrum, 17)
            else:
                # presumably IMX385
                utils.stomp_first(spectrum, 3)
                utils.stomp_last (spectrum, 1)

        ########################################################################
        # Invert X-Axis
        ########################################################################

        # For benches where the detector is essentially rotated 180-deg from our
        # typical orientation with regard to the grating (e.g. InGaAs OEM
        # benches, where red wavelengths are diffracted toward pixel 0, and blue
        # wavelengths toward pixel 511).
        #
        # Note this simply performs a horizontal FLIP (mirror) of the vertically-
        # binned 1-D spectra, and is NOT sufficient to perform a genuine 180-
        # degree rotation of 2-D imaging mode; if "authentic" area scan is
        # desired, the caller would likewise need to reverse the display order of
        # the rows.
        #
        #           It is VITALLY IMPORTANT that all drivers agree
        #            on when to perform X-Axis inversion in the
        #                        Order of Operations.
        #
        if self.settings.eeprom.invert_x_axis and not self.settings.state.area_scan_enabled:
            spectrum.reverse()

        ########################################################################
        # Bad Pixel Correction
        ########################################################################

        if self.settings.state.bad_pixel_mode == SpectrometerState.BAD_PIXEL_MODE_AVERAGE:
            self.correct_bad_pixels(spectrum)

        ########################################################################
        # Swap Alternating Pixels (very rare)
        ########################################################################

        # a prototype model output spectra with alternating pixels swapped, and
        # this was quicker than changing in firmware

        if self.settings.state.swap_alternating_pixels and self.settings.state.detector_regions is None:
            log.debug("swapping alternating pixels: spectrum = %s", spectrum[:10])
            corrected = []
            for a, b in zip(spectrum[0::2], spectrum[1::2]):
                corrected.extend([b, a])
            spectrum = corrected
            log.debug("swapped alternating pixels: spectrum = %s", spectrum[:10])

        ########################################################################
        # 2x2 binning
        ########################################################################

        # apply so-called "2x2 pixel binning" 
        spectrum = self.apply_2x2_binning(spectrum)

        ########################################################################
        # Graph Alternating Pixels
        ########################################################################

        # When integrating new sensors, or testing "interleaved" detectors like
        # the InGaAs, sometimes we want to only look at "every other" pixel to
        # flatten-out irregularities in Bayer filters or photodiode arrays.
        # However, we don't want to disrupt the expected pixel-count, so just
        # average-over the skipped pixels.
        #
        # Not important enough to update for DetectorRegions
        if self.settings.state.graph_alternating_pixels:
            log.debug("applying graph_alternating_pixels")
            smoothed = []
            for i in range(len(spectrum)):
                if i % 2 == 0:
                    smoothed.append(spectrum[i])
                else:
                    if i + 1 < len(spectrum):
                        averaged = (spectrum[i-1] + spectrum[i+1]) / 2.0
                    else:
                        averaged = spectrum[i - 1]
                    smoothed.append(averaged)
            spectrum = smoothed

        # Somewhat oddly, we're currently returning a TUPLE of the spectrum and
        # the area scan row count.  When "Fast" Area Scan is more commonplace 
        # we'll change this back to just returning the spectrum array directly.
        return SpectrumAndRow(spectrum, area_scan_row_count)

    ##
    # Until support for even/odd InGaAs gain and offset have been added to the
    # firmware, apply the correction in software.
    def correct_ingaas_gain_and_offset(self, spectrum):
        if not self.settings.is_ingaas() or self.settings.eeprom.hardware_even_odd:
            return False

        # if even and odd pixels have the same settings, there's no point in doing anything
        if self.settings.eeprom.detector_gain_odd   == self.settings.eeprom.detector_gain and \
           self.settings.eeprom.detector_offset_odd == self.settings.eeprom.detector_offset:
            return False

        log.debug("rescaling InGaAs odd pixels from even gain %.4f, offset %d to odd gain %.4f, offset %d",
            self.settings.eeprom.detector_gain,
            self.settings.eeprom.detector_offset,
            self.settings.eeprom.detector_gain_odd,
            self.settings.eeprom.detector_offset_odd)

        log.debug("before: %d, %d, %d, %d, %d", spectrum[0], spectrum[1], spectrum[2], spectrum[3], spectrum[4])

        # iterate over the ODD pixels of the spectrum
        for i in range(1, len(spectrum), 2):

            # back-out the incorrectly applied "even" gain and offset
            old = float(spectrum[i])
            raw = (old - self.settings.eeprom.detector_offset) / self.settings.eeprom.detector_gain

            # apply the correct "odd" gain and offset
            spectrum[i] = (raw * self.settings.eeprom.detector_gain_odd) + self.settings.eeprom.detector_offset_odd

            if i < 5 or i > len(spectrum) - 5:
                log.debug("  pixel %4d: old %.2f raw %.2f new %.2f", i, old, raw, spectrum[i])

        log.debug("after: %d, %d, %d, %d, %d", spectrum[0], spectrum[1], spectrum[2], spectrum[3], spectrum[4])

        return True

    ##
    # If a spectrometer has bad_pixels configured in the EEPROM, then average
    # over them in the driver.
    #
    # Note this function modifies the passed array in-place, rather than
    # returning a modified copy.
    #
    # @note assumes bad_pixels is previously sorted
    def correct_bad_pixels(self, spectrum):

        if self.settings is None or \
                self.settings.eeprom is None or \
                self.settings.eeprom.bad_pixels is None or \
                len(self.settings.eeprom.bad_pixels) == 0 or \
                self.settings.state.detector_regions is not None:
            return False

        if spectrum is None or len(spectrum) == 0:
            return False

        pixels = len(spectrum)
        bad_pixels = self.settings.eeprom.bad_pixels

        # iterate over each bad pixel
        i = 0
        while i < len(bad_pixels):

            bad_pix = bad_pixels[i]

            if bad_pix == 0:
                # handle the left edge
                next_good = bad_pix + 1
                while next_good in bad_pixels and next_good < pixels:
                    next_good += 1
                    i += 1
                if next_good < pixels:
                    for j in range(next_good):
                        spectrum[j] = spectrum[next_good]
            else:

                # find previous good pixel
                prev_good = bad_pix - 1
                while prev_good in bad_pixels and prev_good >= 0:
                    prev_good -= 1

                if prev_good >= 0:
                    # find next good pixel
                    next_good = bad_pix + 1
                    while next_good in bad_pixels and next_good < pixels:
                        next_good += 1
                        i += 1

                    if next_good < pixels:
                        # for now, draw a line between previous and next_good pixels
                        # TODO: consider some kind of curve-fit
                        delta = float(spectrum[next_good] - spectrum[prev_good])
                        rng   = next_good - prev_good
                        step  = delta / rng
                        for j in range(rng - 1):
                            spectrum[prev_good + j + 1] = spectrum[prev_good] + step * (j + 1)
                    else:
                        # we ran off the high end, so copy-right
                        for j in range(bad_pix, pixels):
                            spectrum[j] = spectrum[prev_good]

            # advance to next bad pixel
            i += 1
        return True

    def apply_2x2_binning(self, spectrum):
        if not self.settings.eeprom.bin_2x2:
            return spectrum

        def bin2x2(a):
            if a is None or len(a) == 0:
                return a
            binned = []
            for i in range(len(a)-1):
                binned.append((a[i] + a[i+1]) / 2.0)
            binned.append(a[-1])
            return binned

        if self.settings.state.detector_regions is None:
            log.debug("applying bin_2x2")
            return bin2x2(spectrum)

        log.debug("applying bin_2x2 to regions")
        combined = []
        for subspectrum in self.settings.state.detector_regions.split(spectrum):
            combined.extend(bin2x2(subspectrum))
        return combined

    ## Send the updated integration time in a control message to the device
    #
    # @warning disabled EEPROM range-checking by customer
    #          request; range limits in EEPROM are defined as 16-bit
    #          values, while integration time is actually a 24-bit value,
    #          such that the EEPROM is artificially limiting our range.
    def set_integration_time_ms(self, ms):
        ms = max(1, int(round(ms)))

        lsw =  ms        & 0xffff
        msw = (ms >> 16) & 0x00ff

        result = self.send_code(0xB2, lsw, msw, label="SET_INTEGRATION_TIME_MS")
        log.debug("SET_INTEGRATION_TIME_MS: now %d", ms)
        self.settings.state.integration_time_ms = ms

        if self.settings.is_micro():
            self.update_laser_watchdog();
        return result

    # ##########################################################################
    # Temperature
    # ##########################################################################

    def select_adc(self, n):
        log.debug("select_adc -> %d", n)
        self.settings.state.selected_adc = n
        result = self.send_code(0xed, n, label="SELECT_ADC")
        self.get_code(0xd5, wLength=2, label="GET_ADC (throwaway)") # stabilization read
        return result

    def get_secondary_adc_calibrated(self, raw=None):
        if not self.has_linearity_coeffs():
            log.debug("secondary_adc_calibrated: no calibration")
            return None

        if raw is None:
            raw = self.get_secondary_adc_raw()
        if raw is None:
            return None

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

    ## @note big-endian, reverse of get_laser_temperature_raw
    def get_detector_temperature_raw(self):
        return self.get_code(0xd7, label="GET_CCD_TEMP", msb_len=2)

    def get_detector_temperature_degC(self, raw=None):
        if raw is None:
            raw = self.get_detector_temperature_raw()

        if raw is None:
            return None

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

        log.debug("Set CCD TEC Setpoint: %.2f deg C (raw ADC 0x%04x)", degC, raw)
        ok = self.send_code(0xd8, raw, label="SET_DETECTOR_TEC_SETPOINT")
        self.settings.state.tec_setpoint_degC = degC
        self.detector_tec_setpoint_has_been_set = True
        return ok

    def get_detector_tec_setpoint_degC(self):
        if self.detector_tec_setpoint_has_been_set:
            return self.settings.state.tec_setpoint_degC
        log.error("Detector TEC setpoint has not yet been applied")
        return 0.0

    def get_detector_tec_setpoint_raw(self):
        return self.get_dac(0)

    def get_dac(self, dacIndex=0):
        return self.get_code(0xd9, wIndex=dacIndex, label="GET_DAC", lsb_len=2)

    ## @todo rename set_detector_tec_enable
    def set_tec_enable(self, flag):
        if not self.settings.eeprom.has_cooling:
            log.debug("unable to control TEC: EEPROM reports no cooling")
            return False

        value = 1 if flag else 0

        if not self.detector_tec_setpoint_has_been_set:

            # @todo should this not be eeprom.startup_temp_degC
            log.debug("defaulting TEC setpoint to min %s", self.settings.eeprom.min_temp_degC)
            self.set_detector_tec_setpoint_degC(self.settings.eeprom.min_temp_degC)

        log.debug("Send detector TEC enable: %s", value)
        ok = self.send_code(0xd6, value, label="SET_DETECTOR_TEC_ENABLE")
        if ok:
            self.settings.state.tec_enabled = flag
        return ok

    ##
    # Set the source for incoming acquisition triggers.
    #
    # @param value either 0 for "internal" or 1 for "external"
    #
    # With internal triggering (the default), the spectrometer expects the
    # USB host to explicitly send a START_ACQUISITION (ACQUIRE) opcode to
    # begin each integration.  In external triggering, the spectrometer
    # waits for the rising edge on a signal connected to a pin on the OEM
    # accessory connector.
    #
    # Technically on ARM, the microcontroller is continuously monitoring
    # both the external pin and listening for internal software opcodes.
    # On the FX2 you need to explicitly place the microcontroller into
    # external triggering mode to avail the feature.
    def set_trigger_source(self, value):
        self.settings.state.trigger_source = value
        log.debug("trigger_source now %s", value)

        # Don't send the opcode on ARM. See issue #2 on WasatchUSB project
        if self.settings.is_arm():
            return False

        msb = 0
        lsb = value
        buf = [0] * 8

        # MZ: this is weird...we're sending the buffer on an FX2-only command
        return self.send_code(0xd2, lsb, msb, buf, label="SET_TRIGGER_SOURCE")

    ##
    # CF_SELECT is configured using bit 2 of the FPGA configuration register
    # 0x12.  This bit can be set using vendor commands 0xeb to SET and 0xec
    # to GET.  Note that the set command is expecting a 5-byte unsigned
    # value, the highest byte of which we pass as part of an 8-byte buffer.
    # Not sure why.
    def set_high_gain_mode_enable(self, flag):
        log.debug("Set high gain mode: %s", flag)
        if not self.settings.is_ingaas():
            log.debug("SET_HIGH_GAIN_MODE_ENABLE only supported on InGaAs")
            return False

        value = 1 if flag else 0

        # this is done automatically on ARM, but for this opcode we do it on FX2 as well
        buf = [0] * 8

        self.settings.state.high_gain_mode_enabled = flag
        return self.send_code(0xeb, wValue=value, wIndex=0, data_or_wLength=buf, label="SET_HIGH_GAIN_MODE_ENABLE")

    def get_high_gain_mode_enabled(self):
        if not self.settings.is_ingaas():
            log.debug("GET_HIGH_GAIN_MODE_ENABLE only supported on InGaAs")
            return self.settings.eeprom.high_gain_mode_enabled

        self.settings.state.high_gain_mode_enabled = 0 != self.get_code(0xec, lsb_len=1, label="GET_HIGH_GAIN_MODE_ENABLED")
        return self.settings.state.high_gain_mode_enabled

    ############################################################################
    # Laser commands
    ############################################################################

    def get_opt_laser_control(self):
        return self.get_upper_code(0x09, label="GET_OPT_LASER_CONTROL", msb_len=1)

    def get_opt_has_laser(self):
        available = (0 != self.get_upper_code(0x08, label="GET_OPT_HAS_LASER", msb_len=1))
        if available != self.settings.eeprom.has_laser:
            log.error("OPT_HAS_LASER opcode result %s != EEPROM has_laser %s (using opcode)",
                value, self.settings.eeprom.has_laser)
        return available

    ## @note little-endian, reverse of get_detector_temperature_raw
    def get_laser_temperature_raw(self):
        # flip to primary ADC if needed
        if self.settings.state.selected_adc is None or self.settings.state.selected_adc != 0:
            self.select_adc(0)

        result = self.get_code(0xd5, wLength=2, label="GET_ADC", lsb_len=2)
        if not result:
            log.debug("Unable to read laser temperature")
            return 0
        return result & 0xfff

    ##
    # Laser temperature conversion doesn't use EEPROM coeffs at all.
    # Most Wasatch Raman systems use an IPS Wavelength-Stabilized TO-56
    # laser, which internally uses a Betatherm 10K3CG3 thermistor.
    #
    # @see https://www.ipslasers.com/data-sheets/SM-TO-56-Data-Sheet-IPS.pdf
    #
    # The official conversion from thermistor resistance (in ohms) to degC is:
    #
    # \verbatim
    # 1 / (   C1
    #       + C2 * ln(ohms)
    #       + C3 * pow(ln(ohms), 3)
    #     )
    # - 273.15
    #
    # Where: C1 = 0.00113
    #        C2 = 0.000234
    #        C3 = 8.78e-8
    # \endverbatim
    #
    # @param raw    the value read from the thermistor's 12-bit ADC
    def get_laser_temperature_degC(self, raw=None):
        if raw is None:
            raw = self.get_laser_temperature_raw()

        if raw is None:
            return None

        if raw > 0xfff:
            log.error("get_laser_temperature_degC: read raw value 0x%04x exceeds 12 bits", raw)
            return 0

        # can't take log of zero
        if raw == 0:
            return 0

        degC = 0
        try:
            voltage    = 2.5 * raw / 4096
            resistance = 21450.0 * voltage / (2.5 - voltage) # LB confirms

            if resistance < 0:
                log.error("get_laser_temperature_degC: can't compute degC: raw = 0x%04x, voltage = %f, resistance = %f",
                    raw, voltage, resistance)
                return 0

            logVal     = math.log(resistance / 10000.0)
            insideMain = logVal + 3977.0 / (25 + 273.0)
            degC       = 3977.0 / insideMain - 273.0

            log.debug("Laser temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        except:
            log.error("exception computing laser temperature", exc_info=1)

        return degC

    ##
    # On spectrometers supporting two lasers, select the primary (0) or
    # secondary (1).  Laser Enable, laser power etc should all then
    # affect the currently-selected laser.
    # @warning conflicts with GET_RAMAN_MODE_ENABLE
    def set_selected_laser(self, value):
        n = 1 if value else 0

        if not self.settings.eeprom.has_laser:
            log.error("unable to control laser: EEPROM reports no laser installed")
            return False

        log.debug("selecting laser %d", n)
        self.settings.state.selected_laser = n

        return self.send_code(bRequest        = 0xff,
                              wValue          = 0x15,
                              wIndex          = n,
                              data_or_wLength = [0] * 8,
                              label           = "SET_SELECTED_LASER")

    def get_selected_laser(self):
        return self.settings.state.selected_laser

    def get_laser_enabled(self):
        flag = 0 != self.get_code(0xe2, label="GET_LASER_ENABLED", msb_len=1)
        log.debug("get_laser_enabled: %s", flag)
        self.settings.state.laser_enabled = flag
        return flag

    ##
    # Turn the laser on or off.
    #
    # If laser power hasn't yet been externally configured, applies the default
    # of full-power.
    #
    # If the new laser state is on, AND if laser ramping has been enabled, then
    # the function will internally use the (blocking) software laser ramping
    # algorithm; otherwise the new state will be applied immediately.
    #
    # @param flag (Input) bool (True turns laser on, False turns laser off)
    # @returns whether the new state was applied
    def set_laser_enable(self, flag):
        if not self.settings.eeprom.has_laser:
            log.error("unable to control laser: EEPROM reports no laser installed")
            return False

        # ARM seems to require that laser power be set before the laser is enabled
        if self.next_applied_laser_power is None:
            self.set_laser_power_perc(100.0)

        self.settings.state.laser_enabled = flag
        if flag and self.get_laser_power_ramping_enabled():
            self._set_laser_enable_ramp()
        else:
            self._set_laser_enable_immediate(flag)
        return True

    ##
    # Enable software (blocking) laser power ramping algorithm.
    # @param flag (Input) whether laser ramping is enabled (default False)
    # @see _set_laser_enable_ramp
    def set_laser_power_ramping_enable(self, flag):
        self.settings.state.laser_power_ramping_enabled = flag

    ##
    # @returns whether software laser power ramping is enabled
    # @see _set_laser_enable_ramp
    def get_laser_power_ramping_enabled(self):
        return self.settings.state.laser_power_ramping_enabled

    ##
    # The user has requested to update the laser firing state (on or off), and
    # either laser power ramping is not enabled, or the requested state is "off",
    # so apply the new laser state to the spectrometer immediately.
    #
    # Because the ability to immediately disable a laser is a safety-related
    # feature (noting that truly safety-critical capabilities should be
    # implemented in hardware, and generally can't be robustly achieved through
    # Python scripts), this function takes the unusual step of looping over
    # multiple attempts to set the laser state until either the command succeeds,
    # or 3 consecutive failures have occured.
    #
    # This behavior was added after a developmental, unreleased prototype was
    # found to occasionally drop USB packets, and was therefore susceptible to
    # inadvertently failing to disable the laser upon command.
    #
    # @private (as callers are recommended to use set_laser_enable)
    # @param flag (Input) whether the laser should be on (true) or off (false)
    # @returns true if new state was successfully applied
    def _set_laser_enable_immediate(self, flag):
        log.debug("Send laser enable: %s", flag)
        if flag:
            self.last_applied_laser_power = 0.0
        else:
            self.last_applied_laser_power = self.next_applied_laser_power

        tries = 0
        while True:
            self.set_strobe_enable(flag)
            if flag == self.get_laser_enabled():
                return True
            tries += 1
            if tries > 3:
                log.critical("laser_enable %s command failed, giving up", flag)
                self.queue_message("marquee_error", "laser setting failed")
                return False
            else:
                log.error("laser_enable %s command failed, re-trying", flag)

    ##
    # EXPERIMENTAL: Enable the laser (turn it on), and then gradually step the
    # laser power from the previously-applied power level to the most recently-
    # requested power level.
    #
    # This function was added for one OEM using one particular 100mW single-mode
    # 830nm laser, and likely would add little value to newer systems using
    # multi-mode lasers.
    #
    # Different Wasatch Photonics spectrometers have used various internal lasers
    # in different models over time.  Some low-power, single-mode lasers in
    # particular took a few seconds for the measured output power to stabilize
    # after a change in requested laser power.  For instance, if the laser had
    # been at 50% power, and the user changed it to 60%, there could be a short
    # over-power surge to 62%, followed by a quick drop to 58%, before the power
    # would gradually converge to 60% over a period of 5+ seconds.  Graphically,
    # a sample measured power trace might resemble the following:
    #
    # \verbatim
    # 60%       ^  ____------->
    #          | \/
    #          |
    # 50% _____|
    #    (sample measured laser power over time)
    # \endverbatim
    #
    # At customer request, a software algorithm was provided to provide marginal
    # reduction in stabilization time by manually stepping the laser power to the
    # desired value.  In testing, this could reduce the average stabilization
    # period from ~6sec to ~4sec.  The revised power trace might resemble the
    # following:
    #
    # \verbatim
    # 60%        ' _,--------->
    #           / '
    #          |
    # 50% _____|
    #    (sample measured laser power over time)
    # \endverbatim
    #
    # The algorithm essentially functions by jumping the laser power to a mid-
    # point 80% of the way between the previous power level and the new level,
    # and then stepping the laser incrementally from the 80% midpoint to the
    # final level in a curve with exponential die-off.  The number of steps used
    # to ramp the laser is defined in SpectrometerState.laser_power_ramp_increments,
    # and a hardcoded 10ms delay is applied after each jump.
    #
    # This is a blocking function (does not internally spawn a background thread),
    # and so will block the caller for the duration of the ramp (typically 4sec+).
    #
    # Users are not recommended to call this method directly; it will be used
    # internally by set_laser_enable() if laser ramping has been configured,
    # which is not enabled by default.
    #
    # @note does not currently support second / external laser
    # @note currently hard-coded to use 1% power resolution (100µs period),
    #       while driver default is 0.1% (1000µs period)
    #
    # @private (use set_laser_enable)
    def _set_laser_enable_ramp(self):
        prefix = "set_laser_enable_ramp"

        # todo: should make enums for all opcodes

        # prepare
        current_laser_setpoint = self.last_applied_laser_power
        target_laser_setpoint = self.next_applied_laser_power
        log.debug("%s: ramping from %s to %s", prefix, current_laser_setpoint, target_laser_setpoint)

        time_start = datetime.datetime.now()

        ########################################################################
        # start at current (last applied) power level
        ########################################################################

        # set modulation period to 100us
        self.set_mod_period_us(100)

        width = int(round(current_laser_setpoint))
        buf = [0] * 8

        # re-apply current power (possibly redundant, but also enabling laser)
        self.set_mod_enable(True)
        self.set_mod_width_us(width)
        self.set_strobe_enable(True)

        # are we done?
        if current_laser_setpoint == target_laser_setpoint:
            return

        ########################################################################
        # apply first 80% jump
        ########################################################################

        # compute the 80% midpoint between the current (previous) power level,
        # and the new target
        if current_laser_setpoint < target_laser_setpoint:
            laser_setpoint = ((float(target_laser_setpoint) - float(current_laser_setpoint)) / 100.0) * 80.0
            laser_setpoint += float(current_laser_setpoint)
            eighty_percent_start = laser_setpoint
        else:
            laser_setpoint = ((float(current_laser_setpoint) - float(target_laser_setpoint)) / 100.0) * 80.0
            laser_setpoint = float(current_laser_setpoint) - laser_setpoint
            eighty_percent_start = laser_setpoint

        self.set_mod_width_us(int(round(eighty_percent_start)))
        sleep(0.02) # 20ms

        ########################################################################
        # gradually step to the final value in exponential die-off curve
        ########################################################################

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
            self.set_mod_width_us(width)

            # allow 10ms to settle
            log.debug("%s: counter = %3d, width = 0x%04x, target_loop_setpoint = %8.2f", prefix, counter, width, target_loop_setpoint)
            sleep(0.01) # 10ms

        log.debug("%s: ramp time %.3f sec", prefix, (datetime.datetime.now() - time_start).total_seconds())

        self.last_applied_laser_power = self.next_applied_laser_power
        log.debug("%s: last_applied_laser_power = %d", prefix, self.next_applied_laser_power)

    def has_laser_power_calibration(self):
        return self.settings.eeprom.has_laser_power_calibration()

    def set_laser_power_mW(self, mW_in):
        if mW_in is None or not self.has_laser_power_calibration():
            log.error("EEPROM doesn't have laser power calibration")
            self.settings.state.laser_power_mW = 0
            self.settings.state.laser_power_perc = 0
            self.settings.state.use_mW = False
            return False

        mW = min(self.settings.eeprom.max_laser_power_mW, max(self.settings.eeprom.min_laser_power_mW, mW_in))

        perc = self.settings.eeprom.laser_power_mW_to_percent(mW)
        log.debug("set_laser_power_mW: range (%.2f, %.2f), requested %.2f, approved %.2f, percent = %.2f",
            self.settings.eeprom.min_laser_power_mW,
            self.settings.eeprom.max_laser_power_mW,
            mW_in,
            mW,
            perc)
        self.settings.state.laser_power_mW = mW
        return self.set_laser_power_perc(perc, set_in_perc=False)

    def set_laser_power_high_resolution(self, flag):
        self.settings.state.laser_power_high_resolution = True if flag else False

    def set_laser_power_require_modulation(self, flag):
        self.settings.state.laser_power_require_modulation = True if flag else False

    ##
    # @todo support floating-point value, as we have a 12-bit ADC and can provide
    # a bit more precision than 100 discrete steps (goal to support 0.1 - .125% resolution)
    def set_laser_power_perc(self, value_in, set_in_perc=True):
        if not self.settings.eeprom.has_laser:
            log.error("unable to control laser: EEPROM reports no laser installed")
            return False

        # if the laser is already engaged and we're using ramping, then ramp to
        # the new level
        value = float(max(0, min(100, value_in)))
        self.settings.state.laser_power_perc = value
        log.debug("set_laser_power_perc: range (0, 100), requested %.2f, applying %.2f", value_in, value)

        if set_in_perc:
            # apparently the laser power was explicitly commanded as a percentage
            # of full, and not "computed from mW" using a calibration, so clear
            # the mW setpoint
            self.settings.state.laser_power_mW = 0

        if self.get_laser_power_ramping_enabled() and self.settings.state.laser_enabled:
            self.next_applied_laser_power = value
            return self._set_laser_enable_ramp()
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
    # device.ctrl_transfer(bmRequestType=device_to_host,
    #                      bRequest=0xDB,
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

        # laser can flicker if we're on the wrong ADC?

        # don't want anything weird when passing over USB
        value = float(max(0, min(100, value)))

        # If full power (and allowed), disable modulation and exit
        if value >= 100:
            if self.settings.state.laser_power_require_modulation:
                log.debug("100% power requested, yet laser modulation required, so not disabling modulation")
            else:
                log.debug("Turning off laser modulation (full power)")
                self.next_applied_laser_power = 100.0
                log.debug("next_applied_laser_power = 100.0")
                return self.set_mod_enable(False)

        period_us = 1000 if self.settings.state.laser_power_high_resolution else 100
        width_us = int(round(1.0 * value * period_us / 100.0, 0)) # note that value is in range (0, 100) not (0, 1)

        # pulse width can't be longer than period, or shorter than 1us
        width_us = max(1, min(width_us, period_us))

        # Change the pulse period.  Note that we're not parsing into 40-bit
        # because this implementation is hard-coded to either 100 or 1000us
        # (both fitting well within uint16)
        result = self.set_mod_period_us(period_us)
        if result is None:
            log.critical("Hardware Failure to send laser mod. pulse period")
            return False

        # Set the pulse width to the 0-100 percentage of power
        result = self.set_mod_width_us(width_us)
        if result is None:
            log.critical("Hardware Failure to send pulse width")
            return False

        # Enable modulation
        result = self.set_mod_enable(True)
        if result is None:
            log.critical("Hardware Failure to send laser modulation")
            return False

        log.debug("Laser power set to: %d", value)

        self.next_applied_laser_power = value
        log.debug("next_applied_laser_power = %s", self.next_applied_laser_power)

        return result

    ##
    # @note never used, provided for OEM
    def get_laser_temperature_setpoint_raw(self):
        if not self.settings.eeprom.has_laser:
            log.error("unable to control laser: EEPROM reports no laser installed")
            return None

        result = self.get_code(0xe8, label="GET_LASER_TEC_SETPOINT")
        return result[0]

    def set_laser_temperature_setpoint_raw(self, value):
        log.debug("Send laser temperature setpoint raw: %d", value)
        return self.send_code(0xe7, value, label="SET_LASER_TEC_SETPOINT")

    def get_laser_watchdog_sec(self):
        self.settings.state.laser_watchdog_sec = \
            self.get_upper_code(0x17, label="GET_LASER_WATCHDOG_SEC", msb_len=2)
        return self.settings.state.laser_watchdog_sec

    def set_laser_watchdog_sec(self, sec):
        if not self.settings.is_micro():
            log.error("Laser watchdog only supported on microRaman")
            return False

        # send value as big-endian
        msb = (sec >> 8) & 0xff
        lsb =  sec       & 0xff
        value = (msb << 8) | lsb

        self.settings.state.laser_watchdog_sec = sec
        return self.send_code(bRequest        = 0xff,
                              wValue          = 0x18,
                              wIndex          = value,
                              data_or_wLength = [0] * 8,
                              label           = "SET_LASER_WATCHDOG_SEC")

    ##
    # Automatically set the laser watchdog long enough to handle the current
    # integration time, assuming we have to perform 6 throwaways on the sensor
    # in case it went to sleep.
    #
    # @todo don't override if the user has "manually" set in ENLIGHTEN
    def update_laser_watchdog(self):
        if not self.settings.is_micro():
            return False

        throwaways_sec = self.settings.state.integration_time_ms    \
                       * (8 + self.settings.state.scans_to_average) \
                       / 1000.0
        watchdog_sec = int(max(10, throwaways_sec)) * 2
        return self.set_laser_watchdog_sec(watchdog_sec)

    ## legacy wrapper over can_laser_fire
    def get_laser_interlock(self):
        return self.can_laser_fire()

    ##
    # @note only works on FX2-based spectrometers with FW >= 10.0.0.11
    # @returns True if there is a laser and either the interlock is
    #          closed (in firing position), or there is no readable
    #          interlock
    def can_laser_fire(self):
        if not self.settings.eeprom.has_laser:
            log.error("EEPROM reports no laser installed")
            return False

        if not self.settings.eeprom.has_interlock_feedback:
            log.debug("CAN_LASER_FIRE requires has_interlock_feedback (defaulting True)")
            return True

        return 0 != self.get_code(0xef, label="CAN_LASER_FIRE", msb_len=1)

    ##
    # Check if the laser actually IS firing, independent of laser_enable or 
    # can_laser_fire.
    def is_laser_firing(self):
        if not self.settings.eeprom.has_interlock_feedback:
            log.debug("IS_LASER_FIRING requires has_interlock_feedback (defaulting to laser_enabled)")
            return self.get_laser_enabled()

        return 0 != self.get_upper_code(0x0d, label="IS_LASER_FIRING", msb_len=1)

    ############################################################################
    # (end of laser commands)
    ############################################################################

    def reset_fpga(self):
        log.debug("fid: resetting FPGA")
        result = self.send_code(0xb5, label="RESET_FPGA")
        log.debug("fid: sleeping 3sec")
        sleep(3)
        return result

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
    def get_trigger_source(self):
        value = self.get_code(0xd3, label="GET_TRIGGER_SOURCE", msb_len=1)
        self.settings.state.trigger_source = value
        return value

    ##
    # @note not called by ENLIGHTEN
    # @warning conflicts with GET_SELECTED_LASER
    def get_raman_mode_enabled_NOT_USED(self):
        return (0 != self.get_upper_code(0x15, label="GET_RAMAN_MODE_ENABLED", msb_len=1))

    ##
    # Enable "Raman mode" (automatic laser) in the spectrometer firmware.
    def set_raman_mode_enable_NOT_USED(self, flag):
        if not self.settings.is_micro():
            log.debug("Raman mode only supported on microRaman")
            return False

        return self.send_code(bRequest        = 0xff,
                              wValue          = 0x16,
                              wIndex          = 1 if flag else 0,
                              data_or_wLength = [0] * 8,
                              label           = "SET_RAMAN_MODE_ENABLE")

    def get_raman_delay_ms(self):
        self.settings.state.raman_delay_ms = \
            self.get_upper_code(0x19, label="GET_RAMAN_DELAY_MS", msb_len=2)
        return self.settings.state.raman_delay_ms

    def set_raman_delay_ms(self, ms):
        if not self.settings.is_micro():
            log.debug("Raman delay only supported on microRaman")
            return False

        # send value as big-endian
        msb = (ms >> 8) & 0xff
        lsb =  ms       & 0xff
        value = (msb << 8) | lsb

        self.settings.state.raman_delay_ms = ms
        return self.send_code(bRequest        = 0xff,
                              wValue          = 0x20,
                              wIndex          = value,
                              data_or_wLength = [0] * 8,
                              label           = "SET_RAMAN_DELAY_MS")

    def get_laser_watchdog_sec(self):
        self.settings.state.laser_watchdog_sec = \
            self.get_upper_code(0x17, label="GET_LASER_WATCHDOG_SEC", msb_len=2)
        return self.settings.state.laser_watchdog_sec

    def set_laser_watchdog_sec(self, sec):
        if not self.settings.is_micro():
            log.error("Laser watchdog only supported on microRaman")
            return False

        # send value as big-endian
        msb = (sec >> 8) & 0xff
        lsb =  sec       & 0xff
        value = (msb << 8) | lsb

        self.settings.state.laser_watchdog_sec = sec
        return self.send_code(bRequest        = 0xff,
                              wValue          = 0x18,
                              wIndex          = value,
                              data_or_wLength = [0] * 8,
                              label           = "SET_LASER_WATCHDOG_SEC")

    ##
    # Automatically set the laser watchdog long enough to handle the current
    # integration time, assuming we have to perform 6 throwaways on the sensor
    # in case it went to sleep.
    #
    # @todo don't override if the user has "manually" set in ENLIGHTEN
    def update_laser_watchdog(self):
        if not self.settings.is_micro() or not self.settings.eeprom.has_laser:
            return False

        throwaways_sec = self.settings.state.integration_time_ms    \
                       * (8 + self.settings.state.scans_to_average) \
                       / 1000.0
        watchdog_sec = int(max(10, throwaways_sec)) * 2
        return self.set_laser_watchdog_sec(watchdog_sec)

    def set_vertical_binning(self, lines):
        if not self.settings.is_micro():
            log.debug("Vertical Binning only configurable on microRaman")
            return False

        try:
            start = lines[0]
            end   = lines[1]
        except:
            log.error("set_vertical_binning requires a tuple of (start, stop) lines")
            return False

        if start < 0 or end < 0:
            log.error("set_vertical_binning requires a tuple of POSITIVE (start, stop) lines")
            return False

        # enforce ascending order (also, note that stop line is "last line binned + 1", so stop must be > start)
        if start >= end:
            # (start, end) = (end, start)
            log.error("set_vertical_binning requires ascending order (ignoring %d, %d)", start, end)
            return False

        ok1 = self.send_code(bRequest        = 0xff,
                             wValue          = 0x21,
                             wIndex          = start,
                             data_or_wLength = [0] * 8,
                             label           = "SET_CCD_START_LINE")

        ok2 = self.send_code(bRequest        = 0xff,
                             wValue          = 0x23,
                             wIndex          = end,
                             data_or_wLength = [0] * 8,
                             label           = "SET_CCD_STOP_LINE")
        return ok1 and ok2

    ## 
    # @params mode: integral value 0-3
    #
    # \verbose
    # mode  ADC (AD)   Pixel Width (OD)
    # b00   10-bit     10-bit
    # b01   10-bit     12-bit
    # b10   12-bit     10-bit
    # b11   12-bit     12-bit
    # \endverbose
    def set_pixel_mode(self, mode):
        if not self.settings.is_micro():
            log.debug("Pixel Mode only configurable on microRaman")
            return False

        # we only care about the two least-significant bits
        mode = int(round(mode)) & 0x3 

        result = self.send_code(bRequest = 0xfd,
                                wValue   = mode,
                                label    = "SET_PIXEL_MODE")

        log.debug("waiting 1sec...")
        sleep(1)

        return result

    def clear_regions(self):
        x1 = self.settings.eeprom.active_pixels_horizontal
        y1 = self.settings.eeprom.active_pixels_vertical
        log.debug(f"resettings detector to full ({x1}, {y1}) extent")

        self.settings.state.region = None
        self.settings.update_wavecal()

        return self.set_detector_roi([0, 0, y1, 0, x1], store=False)

    ##
    # This function uses the the multi-region feature to select just a single 
    # pre-configured region at a time.  Whichever region is selected, that 
    # region's parameters are written to "region 0" of the spectrometer, and
    # the global wavecal is updated to use that region's calibration.
    #
    # @todo consider clear_region() function to restore physical ROI to 
    #       (0, active_vertical_pixels, 0, active_horizontal_pixels)
    #       (leave wavecal alone?)
    def set_single_region(self, n):
        if self.settings.state.detector_regions is None:
            log.debug(f"no detector regions configured")
            return False

        roi = self.settings.state.detector_regions.get_roi(n)
        if roi is None:
            log.debug(f"unconfigured region {n} (max {self.settings.eeprom.region_count}")
            return False

        log.debug(f"set_single_region: applying region {n}: {roi}")
        self.settings.set_single_region(n)

        # send a "fake" ROI downstream, overriding to position 0
        self.set_detector_roi([0, roi.y0, roi.y1, roi.x0, roi.x1], store=False)

    ##
    # Note this only sends the ROI downstream to the spectrometer (and stores
    # it in DetectorRegions).  If you want to update the wavecal and store
    # the "selected" region index, use set_region() instead (which calls this).
    #
    # You should use set_region() if you are selecting one of the standard
    # regions already configured on the EEPROM.  You should use set_detector_roi()
    # if you're making ad-hoc ROIs which aren't configured on the EEPROM.
    #
    # @param args: either a DetectorROI or a tuple of (region, y0, y1, x0, x1)
    def set_detector_roi(self, args, store=True):
        if not self.settings.is_micro():
            log.debug("Detector ROI only configurable on microRaman")
            return False

        if isinstance(args, DetectorROI):
            roi = args
            log.debug(f"passed DetectorROI: {roi}")
        else:
            # convert args to ROI
            log.debug(f"creating DetectorROI from args: {args}")

            if len(args) != 5:
                log.error(f"invalid detector roi args: {args}")
                return False

            region = int(round(args[0]))
            y0     = int(round(args[1]))
            y1     = int(round(args[2]))
            x0     = int(round(args[3]))
            x1     = int(round(args[4]))

            if not (0 <= region <= 3 and \
                    y0 < y1 and \
                    x0 < x1 and \
                    y0 >= 0 and \
                    x0 >= 0 and \
                    y1 <= self.settings.eeprom.active_pixels_horizontal and \
                    x1 <= self.settings.eeprom.active_pixels_horizontal):
                log.error(f"invalid detector roi: {args}")
                return False
            roi = DetectorROI(region, y0, y1, x0, x1)
            log.debug(f"created DetectorROI: {roi}")

        # determine previous total pixels
        self.prev_pixels = self.settings.pixels()
        log.debug(f"prev_pixels = {self.prev_pixels}")

        if store:
            if self.settings.state.detector_regions is None:
                log.debug("creating DetectorRegions")
                self.settings.state.detector_regions = DetectorRegions()

            # this is a no-op if it's already present and unchanged
            log.debug("saving DetectorROI in DetectorRegions")
            self.settings.state.detector_regions.add(roi)

        log.debug(f"total_pixels now {self.settings.pixels()}")

        buf = utils.uint16_to_little_endian([ roi.y0, roi.y1, roi.x0, roi.x1 ])
        log.debug("would send buf: %s", buf)

        result = self.send_code(bRequest        = 0xff,
                                wValue          = 0x25,
                                wIndex          = roi.region,
                                data_or_wLength = buf,
                                label           = "SET_DETECTOR_ROI")

        log.debug("waiting 1sec...")
        sleep(1)

        # Just in case, flows the updated DetectorRegions object upstream
        # so caller has access to it.
        if store:
            self.queue_message("detector_regions", self.settings.state.detector_regions)

        return result

    def get_fpga_configuration_register(self, label=""):
        raw = self.get_code(0xb3, lsb_len=2, label="GET_FPGA_CONFIGURATION_REGISTER")
        log.debug(f"FPGA Configuration Register: 0x{raw:04x} ({label})")
        return raw

    # ##########################################################################
    #
    #                           Accessory Connector
    #
    # ##########################################################################

    # ##########################################################################
    # Accessory Enable
    # ##########################################################################

    ## @todo change opcode (conflicts with GET_DETECTOR_START_LINE)
    def set_accessory_enable(self, flag):
        if not self.settings.is_gen15():
            log.debug("accessory requires Gen 1.5")
            return False
        value = 1 if flag else 0
        return self.send_code(bRequest        = 0x22,
                              wValue          = value,
                              wIndex          = 0,
                              data_or_wLength = [0] * 8,
                              label           = "SET_ACCESSORY_ENABLE")

    ## @todo find out opcode
    def get_discretes_enabled(self):
        if not self.settings.is_gen15():
            log.error("accessory requires Gen 1.5")
            return False
        # return self.get_code(0x37, label="GET_ACCESSORY_ENABLED", msb_len=1)

    # ##########################################################################
    # Fan
    # ##########################################################################

    def set_fan_enable(self, flag):
        if not self.settings.is_gen15():
            log.debug("fan requires Gen 1.5")
            return False
        value = 1 if flag else 0
        return self.send_code(bRequest        = 0x36,
                              wValue          = value,
                              wIndex          = 0,
                              data_or_wLength = [0] * 8,
                              label           = "SET_FAN_ENABLE")

    def get_fan_enabled(self):
        if not self.settings.is_gen15():
            log.error("fan requires Gen 1.5")
            return False
        return 0 != self.get_code(0x37, label="GET_FAN_ENABLED", msb_len=1)

    # ##########################################################################
    # Lamp
    # ##########################################################################

    def set_lamp_enable(self, flag):
        if not self.settings.is_gen15():
            log.debug("lamp requires Gen 1.5")
            return False
        value = 1 if flag else 0
        return self.send_code(bRequest        = 0x32,
                              wValue          = value,
                              wIndex          = 0,
                              data_or_wLength = [0] * 8,
                              label           = "SET_LAMP_ENABLE")

    def get_lamp_enabled(self):
        if not self.settings.is_gen15():
            log.error("lamp requires Gen 1.5")
            return False
        return 0 != self.get_code(0x33, label="GET_LAMP_ENABLED", msb_len=1)

    # ##########################################################################
    # Shutter
    # ##########################################################################

    def set_shutter_enable(self, flag):
        if not self.settings.is_gen15():
            log.debug("shutter requires Gen 1.5")
            return False
        value = 1 if flag else 0
        return self.send_code(bRequest        = 0x30,
                              wValue          = value,
                              wIndex          = 0,
                              data_or_wLength = [0] * 8,
                              label           = "SET_SHUTTER_ENABLE")

    def get_shutter_enabled(self):
        if not self.settings.is_gen15():
            log.error("shutter requires Gen 1.5")
            return False
        return 0 != self.get_code(0x31, label="GET_SHUTTER_ENABLED", msb_len=1)

    # ##########################################################################
    # Laser Modulation and Continuous Strobe
    # ##########################################################################

    def set_mod_enable(self, flag):
        self.settings.state.mod_enabled = flag
        value = 1 if flag else 0
        return self.send_code(0xbd, value, label="SET_MOD_ENABLE")

    def get_mod_enabled(self):
        flag = 0 != self.get_code(0xe3, label="GET_MOD_ENABLED", msb_len=1)
        self.settings.state.mod_enabled = flag
        return flag

    def set_mod_period_us(self, us):
        self.settings.state.mod_period_us = us
        (lsw, msw, buf) = self.to40bit(us)
        return self.send_code(0xc7, lsw, msw, buf, label="SET_MOD_PERIOD")

    def get_mod_period_us(self):
        value = self.get_code(0xcb, label="GET_MOD_PERIOD", lsb_len=5)
        self.settings.state.mod_period_us = value
        return value

    def set_mod_width_us(self, us):
        self.settings.state.mod_width_us = us
        (lsw, msw, buf) = self.to40bit(us)
        return self.send_code(0xdb, lsw, msw, buf, label="SET_MOD_WIDTH")

    def get_mod_width_us(self):
        value = self.get_code(0xdc, label="GET_MOD_WIDTH", lsb_len=5)
        self.settings.state.mod_width_us = value
        return value

    def set_mod_delay_us(self, us):
        self.settings.state.mod_delay_us = us
        (lsw, msw, buf) = self.to40bit(us)
        return self.send_code(0xc6, lsw, msw, buf, label="SET_MOD_DELAY")

    def get_mod_delay_us(self):
        value = self.get_code(0xca, label="GET_MOD_DELAY", lsb_len=5)
        self.settings.state.mod_delay_us = value
        return value

    def set_mod_duration_us_NOT_USED(self, us):
        self.settings.state.mod_duration_us = us
        (lsw, msw, buf) = self.to40bit(us)
        return self.send_code(0xb9, lsw, msw, buf, label="SET_MOD_DURATION")

    def get_mod_duration_us(self):
        value = self.get_code(0xc3, label="GET_MOD_DURATION", lsb_len=5)
        self.settings.state.mod_duration_us = value
        return value

    ## this is a synonym for _set_laser_enable_immediate(), but without side-effects
    def set_strobe_enable(self, flag):
        value = 1 if flag else 0
        return self.send_code(0xbe, value, label="SET_STROBE_ENABLE")

    ## a literal pass-through to get_laser_enabled()
    def get_strobe_enabled(self):
        return self.get_laser_enabled()

    # ##########################################################################
    # Ambient Temperature
    # ##########################################################################

    ## @see https://www.nxp.com/docs/en/data-sheet/LM75B.pdf
    def get_ambient_temperature_degC(self):
        if not self.settings.is_gen15():
            log.error("ambient temperature requires Gen 1.5")
            return -999

        log.debug("attempting to read ambient temperature")
        result = self.get_code(0x34, label="GET_AMBIENT_TEMPERATURE", msb_len=2)
        if result is None or len(result) != 2:
            log.error("failed to read ambient temperature")
            return -999
        log.debug("ambient temperature raw: %s", result)

        raw = raw >> 5
        degC = 0.125 * utils.twos_comp(raw, 11)

        log.debug("parsed ambient temperature from raw %s to %.3f degC", result, degC)
        return degC

    # ##########################################################################
    # added for wasatch-shell
    # ##########################################################################

    def get_tec_enabled(self):
        if not self.settings.eeprom.has_cooling:
            log.error("unable to control TEC: EEPROM reports no cooling")
            return False
        return 0 != self.get_code(0xda, label="GET_CCD_TEC_ENABLED", msb_len=1)

    def get_actual_frames(self):
        return self.get_code(0xe4, label="GET_ACTUAL_FRAMES", lsb_len=2)

    def get_actual_integration_time_us(self):
        return self.get_code(0xdf, label="GET_ACTUAL_INTEGRATION_TIME_US", lsb_len=3)

    def get_detector_offset(self):
        value = self.get_code(0xc4, label="GET_DETECTOR_OFFSET", lsb_len=2)
        self.settings.eeprom.detector_offset = value
        return value

    def get_detector_offset_odd(self):
        if not self.settings.is_ingaas():
            log.debug("GET_DETECTOR_OFFSET_ODD only supported on InGaAs")
            return self.settings.eeprom.detector_offset_odd

        value = self.get_code(0x9e, label="GET_DETECTOR_OFFSET_ODD", lsb_len=2)
        self.settings.eeprom.detector_offset_odd = value
        return value

    def get_ccd_sensing_threshold(self):
        return self.get_code(0xd1, label="GET_CCD_SENSING_THRESHOLD", lsb_len=2)

    def get_ccd_threshold_sensing_mode(self):
        return self.get_code(0xcf, label="GET_CCD_THRESHOLD_SENSING_MODE", msb_len=1)

    def get_external_trigger_output(self):
        return self.get_code(0xe1, label="GET_EXTERNAL_TRIGGER_OUTPUT", msb_len=1)

    def set_mod_linked_to_integration(self, flag):
        value = 1 if flag else 0
        return self.send_code(0xdd, value, label="SET_MOD_LINKED_TO_INTEGRATION")

    def get_mod_linked_to_integration(self):
        return 0 != self.get_code(0xde, label="GET_MOD_LINKED_TO_INTEGRATION", msb_len=1)

    def get_selected_adc(self):
        value = self.get_code(0xee, label="GET_SELECTED_ADC", msb_len=1)
        if self.settings.state.selected_adc != value:
            log.error("GET_SELECTED_ADC %d != state.selected_adc %d", value, self.settings.state.selected_adc)
            self.settings.state.selected_adc = value
        return value

    ##
    # A configurable delay from when an inbound trigger signal is
    # received by the spectrometer, until the triggered acquisition actually starts.
    #
    # Default value is 0us.
    #
    # Unit is in 0.5 microseconds (500ns), so value of 25 would represent 12.5us.
    #
    # Value is 24bit, so max value is 16777216 (8.388608 sec).
    #
    # Like triggering, only currently supported on ARM.
    def set_trigger_delay(self, half_us):
        if not self.settings.is_arm():
            log.error("SET_TRIGGER_DELAY only supported on ARM")
            return False
        lsw = half_us & 0xffff
        msb = (half_us >> 16) & 0xff
        return self.send_code(0xaa, wValue=lsw, wIndex=msb, label="SET_TRIGGER_DELAY")

    # not tested
    def get_trigger_delay(self):
        if not self.settings.is_arm():
            log.error("GET_TRIGGER_DELAY only supported on ARM")
            return -1
        return self.get_code(0xe4, label="GET_TRIGGER_DELAY", lsb_len=3) # not sure about LSB

    def get_vr_continuous_ccd(self):
        return 0 != self.get_code(0xcc, label="GET_VR_CONTINUOUS_CCD", msb_len=1)

    def get_vr_num_frames(self):
        return self.get_code(0xcd, label="GET_VR_NUM_FRAMES", msb_len=1)

    def get_opt_actual_integration_time(self):
        return 0 != self.get_upper_code(0x0b, label="GET_OPT_ACT_INT_TIME", msb_len=1)

    def get_opt_area_scan(self):
        return 0 != self.get_upper_code(0x0a, label="GET_OPT_AREA_SCAN", msb_len=1)

    def get_opt_cf_select(self):
        return 0 != self.get_upper_code(0x07, label="GET_OPT_CF_SELECT", msb_len=1)

    def get_opt_data_header_tab(self):
        return self.get_upper_code(0x06, label="GET_OPT_DATA_HEADER_TAB", msb_len=1)

    def get_opt_horizontal_binning(self):
        return self.get_upper_code(0x0c, label="GET_OPT_HORIZONTAL_BINNING", msb_len=1)

    def get_opt_integration_time_resolution(self):
        return self.get_upper_code(0x05, label="GET_OPT_INTEGRATION_TIME_RESOLUTION", msb_len=1)

    # ##########################################################################
    # Analog output
    # ##########################################################################

    ## @param value (Input) tuple of (bool enable, int mode)
    def set_analog_output_mode(self, value):
        if not self.settings.is_gen2():
            logger.error("analog output only available on Gen2")
            return False

        wIndex = 0

        # parse enable and mode from value tuple
        try:
            if isinstance(value[0], bool):
                enable = value[0]
            else:
                enable = value[0] != 0

            mode = int(value[1])
            if (mode != 0 and mode != 1):
                logger.error("invalid analog output mode 0x%02x, disabling", mode)
                enable = False
                mode = 0

            if enable:
                wIndex = 0x02 | mode # 0x02 sets the "enable" bit
                self.state.analog_out_enable = True
                self.state.analog_out_mode = mode
                self.state.analog_out_value = 0 if mode == 0 else 40 # 4mA default current in deci-mA
            else:
                wIndex = 0
                self.state.analog_out_enable = False
                self.state.analog_out_mode = 0
                self.state.analog_out_value = 0

        except:
            logger.error("set_analog_output_mode takes tuple of (bool, int), disabling")
            wIndex = 0

        return self.send_code(bRequest  = 0xff,
                              wValue    = 0x11,
                              wIndex    = wIndex,
                              label     = "SET_ANALOG_OUT_MODE")

    def set_analog_output_value(self, value):
        if not self.settings.is_gen2():
            logger.error("analog output only available on Gen2")
            return False

        # spectrometer should range-limit, but just to codify:
        if self.state.analog_out_mode == 0:
            # voltage (decivolts, range 0-50 (0-5V))
            if value < 0:
                value = 0
            if value > 50:
                value = 50
        elif self.state.analog_out_mode == 1:
            # current (deci-mA, range 40-200 (4-20mA))
            if value < 40:
                value = 40
            elif value > 200:
                value = 200
        else:
            log.error("invalid analog out mode %d, ignoring value", self.state.analog_out_mode)
            return False

        return self.send_code(bRequest  = 0xff,
                              wValue    = 0x12,
                              wIndex    = value,
                              label     = "SET_ANALOG_OUT_VALUE")

    ## @returns triplet of (bool enable, int mode, int value)
    def get_analog_output_state(self):
        if not self.settings.is_gen2():
            logger.error("analog output only available on Gen2")
            return False

        data = self.get_upper_code(0x1a, wLength=3, label="GET_ANALOG_OUT_STATE")
        if (data is None or len(data) != 3):
            logger.error("invalid analog out state read: %s", data)
            return False

        if data[0] != 0 and data[0] != 1:
            logger.error("received invalid analog out enable: %d", data[0])
        if data[1] != 0 and data[1] != 1:
            logger.error("received invalid analog out mode: %d", data[1])

        self.state.analog_out_enable = data[0] != 0
        self.state.analog_out_mode = data[1]
        self.state.analog_out_value = data[2] # no range-checking applied

        return (self.state.analog_out_enable,
                self.state.analog_out_mode,
                self.state.analog_out_value)

    ##
    # @returns decivolts
    def get_analog_input_value(self):
        if not self.settings.is_gen2():
            logger.error("analog input only available on Gen2")
            return False
        return self.get_upper_code(0x1b, lsb_len=1, label="GET_ANALOG_IN_VALUE")

    # ##########################################################################
    # EEPROM Cruft
    # ##########################################################################

    ##
    # Given a (serial_number, EEPROM) pair, update this process's "session"
    # EEPROM with just the EDITABLE fields of the passed EEPROM.
    def update_session_eeprom(self, pair):
        log.debug("fid.update_session_eeprom: %s updating EEPROM instance", self.settings.eeprom.serial_number)

        if not self.eeprom_backup:
            self.eeprom_backup = copy.deepcopy(self.settings.eeprom)

        self.settings.eeprom.update_editable(pair[1])
        return True

    ##
    # Given a (serial_number, EEPROM) pair, replace this process's "session"
    # EEPROM with the passed EEPROM.
    def replace_session_eeprom(self, pair):
        log.debug("fid.replace_session_eeprom: %s replacing EEPROM instance", self.settings.eeprom.serial_number)

        if not self.eeprom_backup:
            self.eeprom_backup = copy.deepcopy(self.settings.eeprom)

        self.settings.eeprom = pair[1]
        self.settings.eeprom.dump()

    ## Actually store the current session EEPROM fields to the spectrometer.
    def write_eeprom(self):
        if not self.eeprom_backup:
            log.critical("expected to update or replace EEPROM object before write command")
            self.queue_message("marquee_error", "Failed to write EEPROM")
            return False

        # backup contents of previous EEPROM in log
        log.debug("Original EEPROM contents")
        self.eeprom_backup.dump()
        log.debug("Original EEPROM buffers: %s", self.eeprom_backup.buffers)

        try:
            self.settings.eeprom.generate_write_buffers()
        except:
            log.critical("failed to render EEPROM write buffers", exc_info=1)
            self.queue_message("marquee_error", "Failed to write EEPROM")
            return False

        log.debug("Would write new buffers: %s", self.settings.eeprom.write_buffers)

        for page in range(EEPROM.MAX_PAGES):
            if self.settings.is_arm():
                log.debug("writing page %d: %s", page, self.settings.eeprom.write_buffers[page])
                self.send_code(bRequest        = 0xff, # second-tier
                               wValue          = 0x02,
                               wIndex          = page,
                               data_or_wLength = self.settings.eeprom.write_buffers[page],
                               label           = "WRITE_EEPROM")
            else:
                DATA_START = 0x3c00
                offset = DATA_START + page * 64
                log.debug("writing page %d at offset 0x%04x: %s", page, offset, self.settings.eeprom.write_buffers[page])
                self.send_code(bRequest        = 0xa2,   # dangerous
                               wValue          = offset, # arguably an index but hey
                               wIndex          = 0,
                               data_or_wLength = self.settings.eeprom.write_buffers[page],
                               label           = "WRITE_EEPROM")

        self.queue_message("marquee_info", "EEPROM successfully updated")

        # any value in doing this?
        self.settings.eeprom.buffers = self.settings.eeprom.write_buffers

        return True

    # ##########################################################################
    # Interprocess Communications
    # ##########################################################################

    ##
    # @todo move string-to-enum converter to AppLog
    def set_log_level(self, s):
        lvl = logging.DEBUG if s == "DEBUG" else logging.INFO
        log.debug("fid.set_log_level: setting to %s", lvl)
        logging.getLogger().setLevel(lvl)

    ##
    # If an upstream queue is defined, send the name-value pair.  Does nothing
    # if the caller hasn't provided a queue.
    def queue_message(self, setting, value):
        if self.message_queue is None:
            return False

        msg = StatusMessage(setting, value)
        try:
            self.message_queue.put(msg) # put_nowait(msg)
        except:
            log.error("failed to enqueue StatusMessage (%s, %s)", setting, value, exc_info=1)
            return False
        return True

    ##
    # Perform the specified setting such as physically writing the laser
    # on, changing the integration time, turning the cooler on, etc.
    def write_setting(self, record):
        setting = record.setting
        value   = record.value

        log.debug("fid.write_setting: %s -> %s", setting, value)

        if setting not in self.lambdas:
            # noisily fail unsupported tokens
            log.error("Unknown setting to write: %s", setting)
            return False

        f = self.lambdas.get(setting, None)
        if f is None:
            # quietly fail no-ops
            return False

        return f(value)

    ##
    # Please keep this list in sync with README_SETTINGS.md
    def init_lambdas(self):
        f = {}

        def clean_bool(x): return True if x else False

        # spectrometer control
        f["laser_enable"]                       = lambda x: self.set_laser_enable(clean_bool(x))
        f["integration_time_ms"]                = lambda x: self.set_integration_time_ms(x)

        f["detector_tec_setpoint_degC"]         = lambda x: self.set_detector_tec_setpoint_degC(int(round(x)))
        f["detector_tec_enable"]                = lambda x: self.set_tec_enable(clean_bool(x))
        f["detector_gain"]                      = lambda x: self.set_detector_gain(float(x))
        f["detector_offset"]                    = lambda x: self.set_detector_offset(int(round(x)))
        f["detector_gain_odd"]                  = lambda x: self.set_detector_gain_odd(float(x))
        f["detector_offset_odd"]                = lambda x: self.set_detector_offset_odd(int(round(x)))
        f["degC_to_dac_coeffs"]                 = lambda x: self.settings.eeprom.set("degC_to_dac_coeffs", x)

        f["laser_power_perc"]                   = lambda x: self.set_laser_power_perc(x)
        f["laser_power_mW"]                     = lambda x: self.set_laser_power_mW(x)
        f["laser_temperature_setpoint_raw"]     = lambda x: self.set_laser_temperature_setpoint_raw(int(round(x)))
        f["laser_power_ramping_enable"]         = lambda x: self.set_laser_power_ramping_enable(clean_bool(x))
        f["laser_power_ramp_increments"]        = lambda x: self.settings.state.set("laser_power_ramp_increments", int(x))
        f["laser_power_high_resolution"]        = lambda x: self.set_laser_power_high_resolution(x)
        f["laser_power_require_modulation"]     = lambda x: self.set_laser_power_require_modulation(x)
        f["selected_laser"]                     = lambda x: self.set_selected_laser(int(x))

        f["high_gain_mode_enable"]              = lambda x: self.set_high_gain_mode_enable(clean_bool(x))
        f["trigger_source"]                     = lambda x: self.set_trigger_source(int(x))
        f["enable_secondary_adc"]               = lambda x: self.settings.state.set("secondary_adc_enabled", clean_bool(x))
        f["area_scan_enable"]                   = lambda x: self.set_area_scan_enable(clean_bool(x))
        f["area_scan_fast"]                     = lambda x: self.settings.state.set("area_scan_fast", clean_bool(x))

        f["bad_pixel_mode"]                     = lambda x: self.settings.state.set("bad_pixel_mode", int(x))
        f["min_usb_interval_ms"]                = lambda x: self.settings.state.set("min_usb_interval_ms", int(round(x)))
        f["max_usb_interval_ms"]                = lambda x: self.settings.state.set("max_usb_interval_ms", int(round(x)))

        f["accessory_enable"]                   = lambda x: self.set_accessory_enable(clean_bool(x))
        f["fan_enable"]                         = lambda x: self.set_fan_enable(clean_bool(x))
        f["lamp_enable"]                        = lambda x: self.set_lamp_enable(clean_bool(x))
        f["shutter_enable"]                     = lambda x: self.set_shutter_enable(clean_bool(x))
        f["strobe_enable"]                      = lambda x: self.set_strobe_enable(clean_bool(x))
        f["mod_enable"]                         = lambda x: self.set_mod_enable(clean_bool(x))
        f["mod_period_us"]                      = lambda x: self.set_mod_period_us(int(round(x)))
        f["mod_width_us"]                       = lambda x: self.set_mod_width_us(int(round(x)))

        # BatchCollection
        f["free_running_mode"]                  = lambda x: self.settings.state.set("free_running_mode", clean_bool(x))
        f["acquisition_laser_trigger_enable"]   = lambda x: self.settings.state.set("acquisition_laser_trigger_enable", clean_bool(x))
        f["acquisition_laser_trigger_delay_ms"] = lambda x: self.settings.state.set("acquisition_laser_trigger_delay_ms", int(round(x)))
        f["acquisition_take_dark_enable"]       = lambda x: self.settings.state.set("acquisition_take_dark_enable", clean_bool(x))

        # microRaman
       #f["raman_mode_enable"]                  = lambda x: self.set_raman_mode_enable(clean_bool(x))
        f["raman_delay_ms"]                     = lambda x: self.set_raman_delay_ms(int(round(x)))
        f["laser_watchdog_sec"]                 = lambda x: self.set_laser_watchdog_sec(int(round(x)))

        # regions
        f["vertical_binning"]                   = lambda x: self.set_vertical_binning(x)
        f["single_region"]                      = lambda x: self.set_single_region(int(round(x)))
        f["clear_regions"]                      = lambda x: self.clear_regions()
        f["detector_roi"]                       = lambda x: self.set_detector_roi(x)
        f["pixel_mode"]                         = lambda x: self.set_pixel_mode(x)

        # EEPROM updates
        f["update_eeprom"]                      = lambda x: self.update_session_eeprom(x)
        f["replace_eeprom"]                     = lambda x: self.replace_session_eeprom(x)
        f["write_eeprom"]                       = lambda x: self.write_eeprom()

        # manufacturing
        f["reset_fpga"]                         = lambda x: self.reset_fpga()
        f["dfu_enable"]                         = lambda x: self.set_dfu_enable()

        # legacy
        f["allow_default_gain_reset"]           = lambda x: setattr(self, "allow_default_gain_reset", clean_bool(x))

        # experimental (R&D)
        f["graph_alternating_pixels"]           = lambda x: self.settings.state.set("graph_alternating_pixels", clean_bool(x))
        f["swap_alternating_pixels"]            = lambda x: self.settings.state.set("swap_alternating_pixels", clean_bool(x))
        f["invert_x_axis"]                      = lambda x: self.settings.eeprom.set("invert_x_axis", clean_bool(x))
        f["bin_2x2"]                            = lambda x: self.settings.eeprom.set("bin_2x2", clean_bool(x))
        f["wavenumber_correction"]              = lambda x: self.settings.set_wavenumber_correction(float(x))

        # heartbeats & connection data
        f["raise_exceptions"]                   = lambda x: setattr(self, "raise_exceptions", clean_bool(x))
        f["log_level"]                          = lambda x: self.set_log_level(x)
        f["num_connected_devices"]              = lambda x: self.settings.set_num_connected_devices(int(x))
        f["subprocess_timeout_sec"]             = lambda x: None
        f["heartbeat"]                          = lambda x: None

        self.lambdas = f
