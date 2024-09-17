import datetime
import logging

from wasatch.PollStatus import PollStatus

log = logging.getLogger(__name__)

class SpectrometerState:
    """
    Volatile attributes (must persist here for multi-spectrometers).
    
    Note that these should generally not include READOUTS from the 
    spectrometer like temperature, ADC etc...unless that proves convenient.
    """

    TRIGGER_SOURCE_INTERNAL = 0
    TRIGGER_SOURCE_EXTERNAL = 1

    BAD_PIXEL_MODE_NONE     = 0
    BAD_PIXEL_MODE_AVERAGE  = 1

    def __init__(self):

        # detector
        self.integration_time_ms = 0
        self.ignore_timeouts_until = None

        # TEC       @todo rename detector_tec...
        self.tec_setpoint_degC = 15 # that's a very strange default...
        self.tec_enabled = False

        # high gain mode (InGaAs only)
        self.high_gain_mode_enabled = False

        # laser
        self.laser_enabled = False
        self.laser_power_mW = None  # if last set in mW, will contain setpoint in mW; else will be None
        self.laser_power_perc = 0   # should always contain a number [0, 100]
        self.use_mW = False         # bool
        self.laser_temperature_setpoint_raw = 0
        self.selected_adc = None
        self.selected_laser = 0
        self.laser_power_high_resolution = True
        self.laser_power_require_modulation = False
        self.raman_mode_enabled = False
        self.raman_delay_ms = 0
        self.laser_watchdog_sec = 0 
        self.laser_tec_mode = 0  # 0 off, 1 on, 2 auto, 3 auto-on
        self.laser_tec_enabled = False
        self.laser_tec_setpoint = 800
        self.laser_warning_delay_sec = None

        # triggering
        self.trigger_source = self.TRIGGER_SOURCE_INTERNAL

        # area scan mode
        self.area_scan_enabled = False
        self.area_scan_fast = True # now the default

        # battery
        self.battery_percentage = 0.0
        self.battery_charging = False
        self.battery_timestamp = None
        self.battery_raw = None        

        # wasatch.DetectorRegions
        self.detector_regions = None
        self.region = None

        # wasatch.PollStatus
        self.poll_status = PollStatus.UNDEFINED

        # ######################################################################
        # accessory connector
        # ######################################################################

        self.fan_enabled = False
        self.lamp_enabled = False
        self.shutter_enabled = False
       #self.strobe_enabled = False  # this is not a thing -- the proper field is self.laser_enabled

        # these are NOT currently used by laser power settings, though they could be
        self.mod_enabled = False
        self.mod_period_us = 0 
        self.mod_width_us = 0

        # (gen 2.0 stuff, not yet used)
        self.analog_out_enabled = False
        self.analog_out_mode = 0 # 0 = voltage, 1 = current
        self.analog_out_value = 0 # decivolts or deci-mA

        # ######################################################################
        # What about "application state", which is never actually set in the
        # hardware?  Move these later to ".software" or ".processing" or whatever?
        #
        # The real line-in-the-sand here is that these are all set by the user 
        # in the GUI via on-screen widgets, and THEREFORE will need somewhere to 
        # retain state when "other" spectrometers have the foreground, and 
        # THEREFORE might as well be in here.  Wasatch.PY might as well make use 
        # of them.
        # ######################################################################

        # scan averaging
        self.scans_to_average = 1

        # boxcar 
        self.boxcar_half_width = 0

        # background subtraction (no longer used)
        self.background_subtraction_half_width = 0

        # bad pixel removal
        self.bad_pixel_mode = self.BAD_PIXEL_MODE_AVERAGE

        # USB comms
        self.min_usb_interval_ms = 0
        self.max_usb_interval_ms = 0

        # secondary ADC
        self.secondary_adc_enabled = False

        # pixel binning
        self.graph_alternating_pixels = False
        self.swap_alternating_pixels = False

        # mechanical articulation (e.g. Sandbox optics); this is currently 
        # treated as an integer with discrete steps
        self.position = 0

        # wavenumber correction (ADDED to the default wavenumber axis generated
        # from the wavelength calibration at the specified excitation wavelength)
        self.wavenumber_correction = 0

        # EDC
        self.edc_enabled = False
        self.edc_buffer = []

        # ######################################################################
        # gain (dB) (IMX only)
        # ######################################################################

        # "Detector Gain" is confusing, because Hamamatsu silicon, Hamamatsu 
        # InGaAs, and Sony IMX detectors all treat it differently.  
        #
        # At the moment, all Hamamatsu gain (and offset) is handled through the 
        # EEPROM, as those are not considered to be "user-facing, change-during-
        # measurements" attributes -- they are designed to be pre-set in the 
        # factory and then, for the most part, left alone.  That is to say, we
        # deliberately do NOT provide a friendly on-screen "Gain/Offset" widget 
        # in ENLIGHTEN so the user can just randomly fiddle with gain/offset 
        # values during measurements.
        #
        # Also, Hamamatsu gain is a unitless floating-point (float32) scalar, 
        # which is multiplied into the detector's pixel read-out (AFTER being 
        # digitized by the ADC) within the FPGA.  (Note that a completely 
        # different analog, "hardware" gain is already applied at the board level
        # via op-amps -- the "analog front end" -- BEFORE going into the ADC).  
        #
        # Wasatch spectrometers with Hamamatsu detectors historically use a 
        # default gain of 1.9, but new designs are migrating to a default of 1.0.
        # Software should not make "assumptions" as to what the appropriate
        # gain should be for a given model or detector, but should use the
        # configured value in the EEPROM.
        #
        # IMX gain, on the other hand, is very much a "live" parameter, similar
        # to integration time, which users can adjust and change at any time.
        # Also, IMX gain is an integral value in the range (1, 39) representing
        # an objective gain in decibels (dB).  
        #
        # For space reasons, we still store the "startup" IMX gain in the same 
        # EEPROM field (detector_gain).  We COULD (and have, in the past) use
        # that same EEPROM field for "state", but...that seems hokey and 
        # confusing, especially when there is no intent typically to "write" the
        # currently configured gain to the EEPROM, nor does the current gain dB
        # value necessarily or even likely represent the hardware state of the
        # EEPROM.  So, as with integration time, it is made a fully "stateful"
        # attribute of this class.

        self.gain_db = 8

        # ######################################################################
        # What about truly internal settings like last_applied_laser_power or 
        # detector_tec_setpoint_has_been_set?  It's okay for StrokerProtocolDevice,
        # FeatureIdentificationDevice and control.py to have "some" internal state.
        # Line-in-the-sand is to include here if MULTIPLE files would need to 
        # persist the same information in the same way (providing consistent
        # implementation without copy-paste), OR if the information is actually
        # passed between files in some way (such that verification of internal
        # representation consistency reduces risk of bugs/errors).
        # ######################################################################

    def stringify_trigger_source(self):
        if self.trigger_source == self.TRIGGER_SOURCE_EXTERNAL:
            return "EXTERNAL"
        elif self.trigger_source == self.TRIGGER_SOURCE_INTERNAL:
            return "INTERNAL"
        else:
            return "ERROR"

    def stringify_bad_pixel_mode(self):
        if self.bad_pixel_mode == self.BAD_PIXEL_MODE_AVERAGE:
            return "AVERAGE"
        elif self.bad_pixel_mode == self.BAD_PIXEL_MODE_NONE:
            return "NONE"
        else:
            return "ERROR"

    def dump(self, label=None):
        log.debug(f"SpectrometerState: {label}")
        log.debug(f"  Integration Time:       {self.integration_time_ms}")
        log.debug("  TEC Setpoint:           %.2f degC", self.tec_setpoint_degC)
        log.debug("  TEC Enabled:            %s", self.tec_enabled)
        log.debug("  High Gain Mode Enabled: %s", self.high_gain_mode_enabled)
        log.debug("  Gain (dB):              %d", self.gain_db)
        log.debug("  Laser Enabled:          %s", self.laser_enabled)
        log.debug("  Laser Power %%:          %.2f", self.laser_power_perc)
        log.debug("  Laser Power mW:         %.2f", self.laser_power_mW if self.laser_power_mW is not None else 0)
        log.debug("  Use mW:                 %s", self.use_mW)
        log.debug("  Laser Temp Setpoint:    0x%04x", self.laser_temperature_setpoint_raw)
        log.debug("  Selected ADC:           %s", self.selected_adc)
        log.debug("  Trigger Source:         %s", self.stringify_trigger_source())
        log.debug("  Area Scan Enabled:      %s", self.area_scan_enabled)
        log.debug("  Scans to Average:       %d", self.scans_to_average)
        log.debug("  Boxcar Half-Width:      %d", self.boxcar_half_width)
        log.debug("  Background Subtraction: %d", self.background_subtraction_half_width)
        log.debug("  Bad Pixel Mode:         %s", self.stringify_bad_pixel_mode())
        log.debug("  USB Interval:           (%d, %dms)", self.min_usb_interval_ms, self.max_usb_interval_ms)
        log.debug("  Secondary ADC Enabled:  %s", self.secondary_adc_enabled)
        log.debug("  Position:               %s", self.position)
        log.debug("  Wavenumber Correction:  %d", self.wavenumber_correction)
        log.debug("  Laser Watchdog Sec:     %d", self.laser_watchdog_sec)
        log.debug("  Laser TEC Mode:         %d", self.laser_tec_mode)
        log.debug("  Laser TEC Setpoint:     %d", self.laser_tec_setpoint)

    def to_dict(self):
        d = self.__dict__

        # stringify some
        for k in ["battery_timestamp"]:
            d[k] = str(d[k])

        return d

    def set(self, name, value):
        setattr(self, name, value)

    def ignore_timeouts_for(self, sec):
        self.ignore_timeouts_until = datetime.datetime.now() + datetime.timedelta(seconds=sec)
