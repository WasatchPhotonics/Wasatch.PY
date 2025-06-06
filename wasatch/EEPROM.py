import hashlib
import logging
import struct
import array
import copy
import json
import re

from . import utils

from .ROI import ROI

log = logging.getLogger(__name__)

##
# This class encapsulates the post-read parsing, pre-write marshalling, and current
# state of the 8-page EEPROM used to store non-volatile configuration data in Wasatch
# Photonics spectrometers.  It is essential to keep this class synchronized (in naming,
# datatype / datasize and sequence) with the ENG-0034 customer-facing documentation.
#
# This class is normally accessed as an attribute of SpectrometerSettings.
#
# @see ENG-0034
# @see AT24C256C for ARM (32KB, http://ww1.microchip.com/downloads/en/DeviceDoc/20006270A.pdf)
# @see 24LC128 for FX2 (16KB, https://www.microchip.com/en-us/product/24LC128)
class EEPROM:

    LATEST_REV = 17

    MAX_PAGES = 8
    PAGE_LENGTH = 64
    SUBPAGE_COUNT = 4  # used for BLE
    RAMAN_INTENSITY_CALIBRATION_ORDER = 5
    MAX_BAD_PIXELS = 15

    DEFAULT_LASER_WATCHDOG_SEC = 10

    EEPROM_FIELDS = [
        ((0,  0, 16), "s", "model"),
        ((0, 16, 16), "s", "serial_number"),
        ((0, 32,  4), "I", "baud_rate"),
        ((0, 36,  1), "?", "has_cooling"),
        ((0, 37,  1), "?", "has_battery"),
        ((0, 38,  1), "?", "has_laser"),
        ((0, 39,  2), "H", "feature_mask"),
        ((0, 41,  2), "H", "slit_size_um"),
        ((0, 43,  2), "H", "startup_integration_time_ms"),
        ((0, 45,  2), "h", "startup_temp_degC"),
        ((0, 47,  1), "B", "startup_triggering_scheme"),
        ((0, 48,  4), "f", "detector_gain"), 
        ((0, 52,  2), "h", "detector_offset"), 
        ((0, 54,  4), "f", "detector_gain_odd"), 
        ((0, 58,  2), "h", "detector_offset_odd"), 
        ((0, 63,  1), "B", "format"), 
        ((1,  0,  4), "f", "wavecal_c0"),
        ((1,  4,  4), "f", "wavecal_c1"),
        ((1,  8,  4), "f", "wavecal_c2"),
        ((1, 12,  4), "f", "wavecal_c3"),
        ((1, 16,  4), "f", "degCtoDAC_c0"),
        ((1, 20,  4), "f", "degCtoDAC_c1"),
        ((1, 24,  4), "f", "degCtoDAC_c2"),
        ((1, 28,  2), "h", "max_temp_degC"),
        ((1, 30,  2), "h", "min_temp_degC"),
        ((1, 32,  4), "f", "adcToDegC_c0"),
        ((1, 36,  4), "f", "adcToDegC_c1"),
        ((1, 40,  4), "f", "adcToDegC_c2"),
        ((1, 44,  2), "h", "tec_r298"),
        ((1, 46,  2), "h", "tec_beta"),
        ((1, 48, 12), "s", "calibration_date"),
        ((1, 60,  3), "s", "calibrated_by"),
        ((2,  0, 16), "s", "detector"),
        ((2, 16,  2), "H", "active_pixels_horizontal"),
        ((2, 18,  1), "B", "laser_warmup_sec"),
        ((2, 19,  2), "H", "active_pixels_vertical"),
        ((2, 21,  4), "f", "wavecal_c4"),
        ((2, 25,  2), "H", "actual_pixels_horizontal"),
        ((2, 27,  2), "H", "roi_horizontal_start"),
        ((2, 29,  2), "H", "roi_horizontal_end"),
        ((2, 31,  2), "H", "roi_vertical_region_1_start"),
        ((2, 33,  2), "H", "roi_vertical_region_1_end"),
        ((2, 35,  2), "H", "roi_vertical_region_2_start"),
        ((2, 37,  2), "H", "roi_vertical_region_2_end"),
        ((2, 39,  2), "H", "roi_vertical_region_3_start"),
        ((2, 41,  2), "H", "roi_vertical_region_3_end"),
        ((2, 43,  4), "f", "linearity_c0"),
        ((2, 47,  4), "f", "linearity_c1"),
        ((2, 51,  4), "f", "linearity_c2"),
        ((2, 55,  4), "f", "linearity_c3"),
        ((2, 59,  4), "f", "linearity_c4"),
        ((3, 12,  4), "f", "laser_power_c0"),
        ((3, 16,  4), "f", "laser_power_c1"),
        ((3, 20,  4), "f", "laser_power_c2"),
        ((3, 24,  4), "f", "laser_power_c3"),
        ((3, 28,  4), "f", "max_laser_power_mW"),
        ((3, 32,  4), "f", "min_laser_power_mW"),
        ((3, 36,  4), "f", "excitation_nm_float"),
        ((3, 40,  4), "I", "min_integration_time_ms"),
        ((3, 44,  4), "I", "max_integration_time_ms"),
        ((3, 48,  4), "f", "avg_resolution"),
        ((3, 52,  2), "H", "laser_watchdog_sec"),
        ((3, 55,  2), "H", "power_timeout_sec"),
        ((3, 57,  2), "H", "detector_timeout_sec"),
        ((3, 59,  1), "B", "horiz_binning_mode"),
        ((4,  0, 64), "s", "user_data"),
        ((5, 30, 16), "s", "product_configuration"),
        ((5, 63,  1), "B", "subformat")
    ]

    def __init__(self):

        self.model                       = None
        self.serial_number               = None
        self.baud_rate                   = 0
        self.has_cooling                 = False    # explicitly means detector TEC, not laser
        self.has_battery                 = False
        self.has_laser                   = False
        self.feature_mask                = 0
        self.invert_x_axis               = False
        self.horiz_binning_enabled       = False
        self.gen15                       = False
        self.cutoff_filter_installed     = False
        self.hardware_even_odd           = False
        self.sig_laser_tec               = False # better: sig_sml_laser_tec
        self.has_interlock_feedback      = False
        self.laser_interlock_excluded    = False # True if the unit has a laser, but the normal safety interlock has been removed or otherwise disabled (OEM-only)
        self.has_shutter                 = False
        self.disable_ble_power           = False
        self.disable_laser_armed_indicator = False
        self.excitation_nm               = 0.0
        self.excitation_nm_float         = 0.0
        self.slit_size_um                = 0
        self.startup_integration_time_ms = 10
        self.startup_temp_degC           = 15       # normally used for detector TEC; now also used for raw laser TEC on SiG (needs updated in ENG-0034)
        self.startup_triggering_scheme   = 0
        self.startup_laser_tec_setpoint  = 0
        self.detector_gain               = 1.9
        self.detector_offset             = 0
        self.detector_gain_odd           = 1.9
        self.detector_offset_odd         = 0
        self.laser_warmup_sec            = 0
        self.laser_watchdog_sec          = 0
        self.light_source_type           = 0
        self.power_timeout_sec           = 0
        self.detector_timeout_sec        = 0
        self.horiz_binning_mode          = 0
                                         
        self.wavelength_coeffs           = []
        self.degC_to_dac_coeffs          = []
        self.adc_to_degC_coeffs          = []
        self.max_temp_degC               = 20 
        self.min_temp_degC               = 10
        self.tec_r298                    = 0
        self.tec_beta                    = 0
        self.calibration_date            = None
        self.calibrated_by               = None
                                         
        self.detector                    = None
        self.detector_serial_number      = None
        self.active_pixels_horizontal    = 1024  # number of pixels AFTER horizontal binning, BEFORE roi_horizontal cropping
        self.active_pixels_vertical      = 0
        self.min_integration_time_ms     = 10
        self.max_integration_time_ms     = 60000
        self.actual_pixels_horizontal    = 0     # number of pixels read-out over USB
        self.actual_pixels_vertical      = 0     # not a real EEPROM field, though it should be
        self.roi_horizontal_start        = 0
        self.roi_horizontal_end          = 0
        self.roi_vertical_region_1_start = 0
        self.roi_vertical_region_1_end   = 0
        self.roi_vertical_region_2_start = 0
        self.roi_vertical_region_2_end   = 0
        self.roi_vertical_region_3_start = 0
        self.roi_vertical_region_3_end   = 0
        self.linearity_coeffs            = []

        self.max_laser_power_mW          = 0.0
        self.min_laser_power_mW          = 0.0
        self.laser_power_coeffs          = []
        self.avg_resolution              = 0.0

        self.user_data                   = None
        self.user_text                   = None

        self.bad_pixels                  = [] # should be set, not list (but this works with EEPROMEditor)
        self.product_configuration       = None

        self.format                      = EEPROM.LATEST_REV
        self.subformat                   = 0 # determines format of pages 6-7

        self.buffers = []
        self.write_buffers = []
        self.digest = None
        self.stubbed = False

        self.init_fields()

        self.editable = [
            "avg_resolution",
            "bad_pixels",
            "horiz_binning_enabled",
            "calibrated_by",
            "calibration_date",
            "cutoff_filter_installed",
            "detector_gain",
            "detector_gain_odd",
            "detector_offset",
            "detector_offset_odd",
            "excitation_nm",
            "excitation_nm_float",
            "gen15",
            "has_shutter",
            "laser_power_coeffs",
            "laser_warmup_sec",
            "laser_watchdog_sec",
            "linearity_coeffs",
            "max_laser_power_mW",
            "min_laser_power_mW",
            "raman_intensity_coeffs",
            "roi_horizontal_end",
            "roi_horizontal_start",
            "roi_vertical_region_1_end",
            "roi_vertical_region_1_start",
            "roi_vertical_region_2_end",
            "roi_vertical_region_2_start",
            "roi_vertical_region_3_end",
            "roi_vertical_region_3_start",
            "user_text",
            "wavelength_coeffs",
        ]

        self.init_raman_intensity_calibration()
        self.init_spline()
        self.init_untethered()
        self.multi_wavelength_calibration = MultiWavelengthCalibration(self)

    def init_fields(self):
        """ name-based lookup of page, offset, length and datatype """
        self.fields = {}
        for rec in self.EEPROM_FIELDS:
            pos, data_type, name = rec
            self.fields[name] = EEPROMField(pos, data_type, name)

            # This flags on arrays
            # if not hasattr(self, name):
            #     log.error(f"EEPROMField {name} not found in EEPROM?")

    ## whether the given field is normally editable by users via ENLIGHTEN
    #
    # @return False otherwise (don't trust in None's truthiness, as you can't 
    #         pass None to Qt's setEnabled)
    def is_editable(self, name): # -> bool 
        s = name.lower()
        for field in self.editable:
            if s == field.lower():
                return True
        return False

    ## 
    # passed a temporary copy of another EEPROM object, copy-over any
    # "editable" fields to this one
    def update_editable(self, new_eeprom):
        for field in self.editable:
            old = getattr(self, field)
            new = copy.deepcopy(getattr(new_eeprom, field))
            if old == new:
                log.debug("  %s: no change (%s == %s)", field, old, new)
            else:
                setattr(self, field, new)
                log.debug("  %s: changed %s --> %s", field, old, new)

    # ##########################################################################
    #                                                                          #
    #                               Read EEPROM                                #
    #                                                                          #
    # ##########################################################################

    ## 
    # given a set of the 8 buffers read from a spectrometer via USB,
    # parse those into the approrpriate fields and datatypes
    def parse(self, buffers):
        if len(buffers) < EEPROM.MAX_PAGES:
            log.error("EEPROM.parse expects at least %d buffers", EEPROM.MAX_PAGES)
            return False

        # store these locally so self.unpack() can access them
        self.buffers = buffers
        self.digest = self.generate_digest()

        # handy for debugs
        self.hexbuf = [" ".join([f"{v:02x}" for v in buf]) for buf in buffers]

        # check for known ne'er-do-well
        bad = r"c2 47 05 31 21 00 00 04 00 03 00 00 02 31 a5 00 03 00 33 02 39 0f 00 03 00 43 02 2f 00 00 03 00 " \
            + r"4b 02 2b 23 00 03 00 53 02 2f 00 03 ff 01 00 90 e6 78 e0 54 10 ff c4 54 0f 44 50 f5 09 13 e4"
        for i, s in enumerate(self.hexbuf):
            if bad in s:
                log.error(f"bad string found in EEPROM page {i}")

        # unpack all the fields we know about
        try:
            self.read_eeprom()
            return True
        except:
            log.error("failed to parse EEPROM", exc_info=1)
            return False

    ## 
    # Assuming a set of 8 buffers have been passed in via parse(), actually
    # unpack (deserialize / unmarshall) the binary data into the appropriate
    # fields and datatypes.
    # 
    # @todo update to use self.fields
    #
    # @see https://docs.python.org/2/library/struct.html#format-characters
    # (capitals are unsigned)
    def read_eeprom(self):
        self.format = self.unpack((0, 63,  1), "B", "format")
        log.debug("parsing EEPROM format %d", self.format)

        # ######################################################################
        # Page 0
        # ######################################################################

        self.model                           = self.unpack((0,  0, 16), "s", "model")
        self.serial_number                   = self.unpack((0, 16, 16), "s", "serial")
        self.baud_rate                       = self.unpack((0, 32,  4), "I", "baud")
        self.has_cooling                     = self.unpack((0, 36,  1), "?", "cooling")
        self.has_battery                     = self.unpack((0, 37,  1), "?", "battery")
        self.has_laser                       = self.unpack((0, 38,  1), "?", "laser")
        if self.format > 9:
            self.feature_mask                = self.unpack((0, 39,  2), "H", "feature_mask")
        elif self.format >= 3:
            self.excitation_nm               = self.unpack((0, 39,  2), "H", "excitation_nm (unsigned)")
        else:
            self.excitation_nm               = self.unpack((0, 39,  2), "h", "excitation_nm (signed)")

        if self.format >= 4:
            self.slit_size_um                = self.unpack((0, 41,  2), "H", "slit (unsigned)")
        else:
            self.slit_size_um                = self.unpack((0, 41,  2), "h", "slit (signed)")

        # NOTE: the new InGaAs detector gain/offset won't be usable from 
        #       EEPROM until we start bumping production spectrometers to
        #       EEPROM Page 0 Revision 3!
        if self.format >= 3:
            self.startup_integration_time_ms = self.unpack((0, 43,  2), "H", "startup_integration_time_ms")
            self.startup_temp_degC           = self.unpack((0, 45,  2), "h", "startup_temp_degC")
            self.startup_triggering_scheme   = self.unpack((0, 47,  1), "B", "startup_triggering_scheme")
            self.detector_gain               = self.unpack((0, 48,  4), "f", "detector_gain") # "even pixels" for InGaAs
            self.detector_offset             = self.unpack((0, 52,  2), "h", "detector_offset") # "even pixels" for InGaAs
            self.detector_gain_odd           = self.unpack((0, 54,  4), "f", "detector_gain_odd") # InGaAs-only
            self.detector_offset_odd         = self.unpack((0, 58,  2), "h", "detector_offset_odd") # InGaAs-only

        if self.format >= 16:
            self.startup_laser_tec_setpoint  = self.unpack((0, 60,  2), "H", "startup_laser_tec_setpoint") & 0xfff # XS-only

        # ######################################################################
        # Page 1
        # ######################################################################

        self.wavelength_coeffs = []
        self.wavelength_coeffs         .append(self.unpack((1,  0,  4), "f", "wavecal_coeff_0"))
        self.wavelength_coeffs         .append(self.unpack((1,  4,  4), "f"))
        self.wavelength_coeffs         .append(self.unpack((1,  8,  4), "f"))
        self.wavelength_coeffs         .append(self.unpack((1, 12,  4), "f"))
        self.degC_to_dac_coeffs = []
        self.degC_to_dac_coeffs        .append(self.unpack((1, 16,  4), "f", "degCtoDAC_coeff_0"))
        self.degC_to_dac_coeffs        .append(self.unpack((1, 20,  4), "f"))
        self.degC_to_dac_coeffs        .append(self.unpack((1, 24,  4), "f"))
        self.max_temp_degC                   = self.unpack((1, 28,  2), "h", "max_temp_degC")
        self.min_temp_degC                   = self.unpack((1, 30,  2), "h", "min_temp_degC")
        self.adc_to_degC_coeffs = []
        self.adc_to_degC_coeffs        .append(self.unpack((1, 32,  4), "f", "adcToDegC_coeff_0"))
        self.adc_to_degC_coeffs        .append(self.unpack((1, 36,  4), "f"))
        self.adc_to_degC_coeffs        .append(self.unpack((1, 40,  4), "f"))
        self.tec_r298                        = self.unpack((1, 44,  2), "h", "tec_r298")
        self.tec_beta                        = self.unpack((1, 46,  2), "h", "tec_beta")
        self.calibration_date                = self.unpack((1, 48, 12), "s", "calibration_date")
        self.calibrated_by                   = self.unpack((1, 60,  3), "s", "calibrated_by")
                                    
        # ######################################################################
        # Page 2                    
        # ######################################################################

        self.detector                        = self.unpack((2,  0, 16), "s", "detector")
        self.active_pixels_horizontal        = self.unpack((2, 16,  2), "H", "active_pixels_horizontal")
        if self.format >= 10:
            self.laser_warmup_sec            = self.unpack((2, 18,  1), "B", "laser_warmup_sec")
        self.active_pixels_vertical          = self.unpack((2, 19,  2), "H" if self.format >= 4 else "h")

        if self.format >= 8:
            self.wavelength_coeffs     .append(self.unpack((2, 21,  4), "f", "wavecal_coeff_4"))
        else:
            # just go ahead and initialize the 5th coeff to zero
            self.wavelength_coeffs.append(0)
            if self.format < 5:
                self.min_integration_time_ms     = self.unpack((2, 21,  2), "H", "min_integ(ushort)")
                self.max_integration_time_ms     = self.unpack((2, 23,  2), "H", "max_integ(ushort)") 

        self.actual_pixels_horizontal        = self.unpack((2, 25,  2), "H" if self.format >= 4 else "h", "actual_pixels_horizontal")
        self.actual_pixels_vertical          = self.active_pixels_vertical  # approximate for now
        self.roi_horizontal_start            = self.unpack((2, 27,  2), "H" if self.format >= 4 else "h")
        self.roi_horizontal_end              = self.unpack((2, 29,  2), "H" if self.format >= 4 else "h")
        self.roi_vertical_region_1_start     = self.unpack((2, 31,  2), "H" if self.format >= 4 else "h")
        self.roi_vertical_region_1_end       = self.unpack((2, 33,  2), "H" if self.format >= 4 else "h")
        self.roi_vertical_region_2_start     = self.unpack((2, 35,  2), "H" if self.format >= 4 else "h")
        self.roi_vertical_region_2_end       = self.unpack((2, 37,  2), "H" if self.format >= 4 else "h")
        self.roi_vertical_region_3_start     = self.unpack((2, 39,  2), "H" if self.format >= 4 else "h")
        self.roi_vertical_region_3_end       = self.unpack((2, 41,  2), "H" if self.format >= 4 else "h")
        self.linearity_coeffs = []
        self.linearity_coeffs          .append(self.unpack((2, 43,  4), "f", "linearity_coeff_0")) # overloading for secondary ADC
        self.linearity_coeffs          .append(self.unpack((2, 47,  4), "f"))
        self.linearity_coeffs          .append(self.unpack((2, 51,  4), "f"))
        self.linearity_coeffs          .append(self.unpack((2, 55,  4), "f"))
        self.linearity_coeffs          .append(self.unpack((2, 59,  4), "f"))

        # ######################################################################
        # Page 3
        # ######################################################################
        
        self.laser_power_coeffs = []
        self.laser_power_coeffs        .append(self.unpack((3, 12,  4), "f", "laser_power_coeff_0"))
        self.laser_power_coeffs        .append(self.unpack((3, 16,  4), "f"))
        self.laser_power_coeffs        .append(self.unpack((3, 20,  4), "f"))
        self.laser_power_coeffs        .append(self.unpack((3, 24,  4), "f"))
        self.max_laser_power_mW              = self.unpack((3, 28,  4), "f", "max_laser_power_mW")
        self.min_laser_power_mW              = self.unpack((3, 32,  4), "f", "min_laser_power_mW")

        if self.format >= 4:
            self.excitation_nm_float         = self.unpack((3, 36,  4), "f", "excitation(float)")
        else:
            self.excitation_nm_float = self.excitation_nm

        if self.format >= 5:
            self.min_integration_time_ms     = self.unpack((3, 40,  4), "I", "min_integration_time_ms")
            self.max_integration_time_ms     = self.unpack((3, 44,  4), "I", "max_integration_time_ms")

        if self.format >= 7:
            self.avg_resolution              = self.unpack((3, 48,  4), "f", "avg_resolution")

        if self.format >= 15:
            self.laser_watchdog_sec          = self.unpack((3, 52,  2), "H", "laser_watchdog_sec")
            self.light_source_type           = self.unpack((3, 54,  1), "B", "light_source_type")

        if self.format >= 16:
            self.power_timeout_sec           = self.unpack((3, 55,  2), "H", "power_timeout_sec")
            self.detector_timeout_sec        = self.unpack((3, 57,  2), "H", "detector_timeout_sec")
            self.horiz_binning_mode          = self.unpack((3, 59,  1), "B", "horiz_binning_mode")

        # ######################################################################
        # Page 4
        # ######################################################################

        self.user_data = self.buffers[4][:63]
        self.user_text = self.printable(self.user_data)

        # ######################################################################
        # Page 5
        # ######################################################################

        bad = set()
        for count in range(15):
            pixel = self.unpack((5, count * 2, 2), "h")
            if pixel != -1:
                bad.add(pixel)
        self.bad_pixels = list(bad)
        self.bad_pixels.sort()

        if self.format >= 5:
            self.product_configuration       = self.unpack((5,  30, 16), "s", "product_configuration")
        if self.format >= 7:
            self.subformat                   = self.unpack((5,  63,  1), "B", "subformat")

        # ######################################################################
        # Page 6-7
        # ######################################################################

        if self.subformat in [1, 5]:
            self.read_raman_intensity_calibration()

        if self.subformat == 2:
            self.read_spline()

        if self.subformat == 3:
            self.read_untethered()

        # Now that all other data has been loaded, re-initialize and load Multi-
        # Wavelength Calibration (this way it can copy the "baseline" Raman
        # intensity calibration, plus append new ones).
        self.multi_wavelength_calibration.initialize()
        if self.subformat == 5:
            self.multi_wavelength_calibration.read(calibration=1)

        # ######################################################################
        # feature mask
        # ######################################################################

        if self.format >= 9:
            self.invert_x_axis                 = 0 != self.feature_mask & 0x0001
            self.horiz_binning_enabled         = 0 != self.feature_mask & 0x0002
            self.gen15                         = 0 != self.feature_mask & 0x0004
            self.cutoff_filter_installed       = 0 != self.feature_mask & 0x0008
            self.hardware_even_odd             = 0 != self.feature_mask & 0x0010
            self.sig_laser_tec                 = 0 != self.feature_mask & 0x0020
            self.has_interlock_feedback        = 0 != self.feature_mask & 0x0040
            self.has_shutter                   = 0 != self.feature_mask & 0x0080
            self.disable_ble_power             = 0 != self.feature_mask & 0x0100
            self.disable_laser_armed_indicator = 0 != self.feature_mask & 0x0200
            self.laser_interlock_excluded      = 0 != self.feature_mask & 0x0400
        else:
            self.invert_x_axis                 = False 
            self.horiz_binning_enabled         = False
            self.gen15                         = False
            self.cutoff_filter_installed       = False
            self.hardware_even_odd             = False
            self.sig_laser_tec                 = False
            self.has_interlock_feedback        = False
            self.has_shutter                   = False
            self.disable_ble_power             = False 
            self.disable_laser_armed_indicator = False

        # ######################################################################
        # sanity checks
        # ######################################################################

        utils.clean_nan(self.wavelength_coeffs)

        if self.min_integration_time_ms == 0xffff:
            self.min_integration_time_ms = 1 
            self.max_integration_time_ms = 60000

        if self.min_integration_time_ms > self.max_integration_time_ms:
            (self.min_integration_time_ms, self.max_integration_time_ms) = \
            (self.max_integration_time_ms, self.min_integration_time_ms)

        if self.startup_integration_time_ms < self.min_integration_time_ms:
            self.startup_integration_time_ms = self.min_integration_time_ms

        if self.min_temp_degC > self.max_temp_degC:
            (self.min_temp_degC, self.max_temp_degC) = \
            (self.max_temp_degC, self.min_temp_degC) 

        if self.min_laser_power_mW > self.max_laser_power_mW:
            (self.min_laser_power_mW, self.max_laser_power_mW) = \
            (self.max_laser_power_mW, self.min_laser_power_mW)

    ############################################################################
    #                                                                          #
    #                               Write EEPROM                               #
    #                                                                          #
    ############################################################################

    def generate_feature_mask(self):
        mask = 0
        mask |= 0x0001 if self.invert_x_axis                 else 0
        mask |= 0x0002 if self.horiz_binning_enabled         else 0
        mask |= 0x0004 if self.gen15                         else 0
        mask |= 0x0008 if self.cutoff_filter_installed       else 0
        mask |= 0x0010 if self.hardware_even_odd             else 0
        mask |= 0x0020 if self.sig_laser_tec                 else 0
        mask |= 0x0040 if self.has_interlock_feedback        else 0
        mask |= 0x0080 if self.has_shutter                   else 0
        mask |= 0x0100 if self.disable_ble_power             else 0
        mask |= 0x0200 if self.disable_laser_armed_indicator else 0
        mask |= 0x0400 if self.laser_interlock_excluded      else 0
        return mask

    ##
    # Call this to populate an internal array of "write buffers" which may be written back
    # to spectrometers (or used to generate the digest of what WOULD be written).
    #
    # @todo update to use self.fields
    def generate_write_buffers(self):
        # stub-out 8 blank buffers
        self.write_buffers = []
        for page in range(EEPROM.MAX_PAGES):
            self.write_buffers.append(array.array('B', [0] * 64))

        # Eventually we'll stop worrying about the legacy per-page format versions, but
        # for now maximize compatibility with StrokerConsole/ModelConfigurationFormat.cs
        # by making its expected format values the default for each page:
        revs = { 0: 1,
                 1: 1,
                 2: 2, 
                 3: 255,
                 4: 1, 
                 5: 1,
                 6: 0 }
        for page in list(revs.keys()):
            self.write_buffers[page][63] = revs[page]

        # ...but the truth is that we don't really care about the old per-page formats,
        # and all modern code should just be looking at this one byte:
        self.write_buffers[0][63] = EEPROM.LATEST_REV

        # ######################################################################
        # Page 0
        # ######################################################################

        self.pack((0,  0, 16), "s", self.model)
        self.pack((0, 16, 16), "s", self.serial_number)
        self.pack((0, 32,  4), "I", self.baud_rate)
        self.pack((0, 36,  1), "?", self.has_cooling)
        self.pack((0, 37,  1), "?", self.has_battery)
        self.pack((0, 38,  1), "?", self.has_laser)
        self.pack((0, 39,  2), "H", self.generate_feature_mask(), "FeatureMask")
        self.pack((0, 41,  2), "H", self.slit_size_um)
        self.pack((0, 43,  2), "H", self.startup_integration_time_ms)
        self.pack((0, 45,  2), "h", self.startup_temp_degC)
        self.pack((0, 47,  1), "B", self.startup_triggering_scheme)
        self.pack((0, 48,  4), "f", self.detector_gain)
        self.pack((0, 52,  2), "h", self.detector_offset)
        self.pack((0, 54,  4), "f", self.detector_gain_odd)
        self.pack((0, 58,  2), "h", self.detector_offset_odd)
        self.pack((0, 60,  2), "H", self.startup_laser_tec_setpoint)

        # ######################################################################
        # Page 1
        # ######################################################################

        wavelength_coeffs = self.multi_wavelength_calibration.get("wavelength_coeffs")
        if wavelength_coeffs is not None:
            for i in range(min(4, len(wavelength_coeffs))):
                self.pack((1,  0 + i * 4,  4), "f", wavelength_coeffs[i])
                
        if self.degC_to_dac_coeffs is not None:
            for i in range(min(3, len(self.degC_to_dac_coeffs))):
                self.pack((1, 16 + i * 4,  4), "f", self.degC_to_dac_coeffs[i])

        if self.adc_to_degC_coeffs is not None:
            for i in range(min(3, len(self.adc_to_degC_coeffs))):
                self.pack((1, 32 + i * 4,  4), "f", self.adc_to_degC_coeffs[i])

        self.pack((1, 28,  2), "h", self.max_temp_degC)
        self.pack((1, 30,  2), "h", self.min_temp_degC)
        self.pack((1, 44,  2), "h", self.tec_r298)
        self.pack((1, 46,  2), "h", self.tec_beta)
        self.pack((1, 48, 12), "s", self.calibration_date)
        self.pack((1, 60,  3), "s", self.calibrated_by)
                                    
        # ######################################################################
        # Page 2                    
        # ######################################################################

        self.pack((2,  0, 16), "s", self.detector)
        self.pack((2, 16,  2), "H", self.active_pixels_horizontal)
        self.pack((2, 18,  1), "B", self.laser_warmup_sec)
        self.pack((2, 19,  2), "H", self.active_pixels_vertical)
        if self.format < 7:
            self.pack((2, 21,  2), "H", max(0xffff, self.min_integration_time_ms))
            self.pack((2, 23,  2), "H", max(0xffff, self.max_integration_time_ms))
        else:
            coeff = 0.0
            if len(wavelength_coeffs) > 4:
                coeff = wavelength_coeffs[4]
            self.pack((2, 21,  4), "f", coeff)
        self.pack((2, 25,  2), "H", self.actual_pixels_horizontal)
        self.pack((2, 27,  2), "H", self.multi_wavelength_calibration.get("roi_horizontal_start"))
        self.pack((2, 29,  2), "H", self.multi_wavelength_calibration.get("roi_horizontal_end"))
        self.pack((2, 31,  2), "H", self.roi_vertical_region_1_start)
        self.pack((2, 33,  2), "H", self.roi_vertical_region_1_end)
        self.pack((2, 35,  2), "H", self.roi_vertical_region_2_start)
        self.pack((2, 37,  2), "H", self.roi_vertical_region_2_end)
        self.pack((2, 39,  2), "H", self.roi_vertical_region_3_start)
        self.pack((2, 41,  2), "H", self.roi_vertical_region_3_end)

        if self.linearity_coeffs is not None:
            for i in range(min(5, len(self.linearity_coeffs))):
                self.pack((2, 43 + i * 4,  4), "f", self.linearity_coeffs[i])

        # ######################################################################
        # Page 3
        # ######################################################################

        if self.laser_power_coeffs is not None:
            for i in range(min(4, len(self.laser_power_coeffs))):
                self.pack((3, 12 + i * 4,  4), "f", self.laser_power_coeffs[i])

        self.pack((3, 28,  4), "f", self.max_laser_power_mW)
        self.pack((3, 32,  4), "f", self.min_laser_power_mW)
        self.pack((3, 36,  4), "f", self.multi_wavelength_calibration.get("excitation_nm_float"))
        self.pack((3, 40,  4), "I", self.min_integration_time_ms)
        self.pack((3, 44,  4), "I", self.max_integration_time_ms)
        self.pack((3, 48,  4), "f", self.multi_wavelength_calibration.get("avg_resolution"))
        self.pack((3, 52,  2), "H", self.laser_watchdog_sec)
        self.pack((3, 54,  1), "B", self.light_source_type)
        self.pack((3, 55,  2), "H", self.power_timeout_sec)
        self.pack((3, 57,  2), "H", self.detector_timeout_sec)
        self.pack((3, 59,  1), "B", self.multi_wavelength_calibration.get("horiz_binning_mode"))

        # ######################################################################
        # Page 4
        # ######################################################################

        self.pack((4,  0, 63), "s", self.user_text)

        # ######################################################################
        # Page 5
        # ######################################################################

        bad_pixel_set = set()
        for i in self.bad_pixels:
            if i >= 0:
                bad_pixel_set.add(i)
        bad_pixels = list(bad_pixel_set)
        bad_pixels.sort()
        for i in range(self.MAX_BAD_PIXELS):
            if i < len(bad_pixels):
                value = bad_pixels[i]
            else:
                value = -1
            self.pack((5, i * 2, 2), "h", value)

        self.pack((5, 30, 16), "s", self.product_configuration)
        self.pack((5, 63,  1), "B", self.subformat)

        # ######################################################################
        # Page 6-7
        # ######################################################################

        if self.subformat == 0:
            pass
        elif self.subformat == 1:
            self.write_raman_intensity_calibration()
        elif self.subformat == 2:
            self.write_spline()
        elif self.subformat == 3:
            self.write_untethered()
        elif self.subformat == 4:
            log.critical("Subformat 4 is deprecated")
        elif self.subformat == 5:
            self.write_raman_intensity_calibration()
            self.multi_wavelength_calibration.write()
        else:
            log.error(f"Unwriteable EEPROM subformat {self.subformat}")

    # ##########################################################################
    #                                                                          #
    #                             Utility methods                              #
    #                                                                          #
    # ##########################################################################

    ## make a printable ASCII string out of possibly-binary data
    def printable(self, buf):
        s = ""
        for c in buf:
            if 31 < c < 127:
                s += chr(c)
            elif c == 0:
                break
            else:
                s += '.'
        return s

    def set(self, name, value):
        setattr(self, name, value)

    ##
    # Convert a floating-point value into the big-endian 16-bit "funky float" 
    # used for detector gain in the FPGA on both Hamamatsu and IMX sensors.
    #
    # Note that this TRUNCATES (takes the floor) the fractional portion rather 
    # than rounding, which I believe matches WasatchNET.FunkyFloat.fromFloat().
    #
    # Conversely, it DOES provide minor rounding on the INTEGRAL portion, to 
    # avoid this problem:
    #
    # \verbatim
    # dB = 0
    # while dB < 2.1:
    #     self.set_detector_gain(dB) # old code treats 1.0 as 0.0 because actually sends 0.99999999
    #     dB += 0.1
    # \endverbatim
    #
    # @see https://wasatchphotonics.com/api/Wasatch.NET/class_wasatch_n_e_t_1_1_funky_float.html
    def float_to_uint16(self, gain):
        msb = int(round(gain, 5)) & 0xff
        # if self.format >= 13: lsb = round((gain - msb) * 256) & 0xff
        lsb = int((gain - msb) * 256) & 0xff
        raw = (msb << 8) | lsb
        log.debug("float_to_uint16: %f -> 0x%04x", gain, raw)
        return raw

    ## 
    # Unpack a single field at a given buffer offset of the given datatype.
    #
    # @param address    a tuple of the form (buf, offset, len)
    # @param data_type  see https://docs.python.org/2/library/struct.html#format-characters
    # @param label      if provided, is included in debug log output
    def unpack(self, address, data_type, label=None):
        page       = address[0]
        start_byte = address[1]
        length     = address[2]
        end_byte   = start_byte + length

        if page > len(self.buffers):
            log.error("error unpacking EEPROM page %d, offset %d, len %d as %s: invalid page (label %s)", 
                page, start_byte, length, data_type, label, exc_info=1)
            return

        buf = self.buffers[page]
        if buf is None or end_byte > len(buf):
            log.error("error unpacking EEPROM page %d, offset %d, len %d as %s: buf is %s (label %s)", 
                page, start_byte, length, data_type, buf, label, exc_info=1)
            return

        if data_type == "s":
            # This stops at the first NULL, so is not appropriate for binary data (user_data).
            # OTOH, it doesn't currently enforce "printable" characters either (nor support Unicode).
            unpack_result = ""
            for c in buf[start_byte:end_byte]:
                if c == 0:
                    break
                unpack_result += chr(c)
        else:
            unpack_result = 0
            try:
                unpack_result = struct.unpack(data_type, buf[start_byte:end_byte])[0]
            except:
                log.error("error unpacking EEPROM page %d, offset %d, len %d as %s", page, start_byte, length, data_type, exc_info=1)

        if label is None:
            log.debug("Unpacked [%s]: %s", data_type, unpack_result)
        else:
            log.debug("Unpacked [%s]: %s (%s)", data_type, unpack_result, label)
        return unpack_result

    ## 
    # Marshall or serialize a single field at a given buffer offset of the given datatype.
    #
    # @param address    a tuple of the form (buf, offset, len)
    # @param data_type  see https://docs.python.org/2/library/struct.html#format-characters
    # @param value      value to serialize
    def pack(self, address, data_type, value, label=None):
        page       = address[0]
        start_byte = address[1]
        length     = address[2]
        end_byte   = start_byte + length

        if page > len(self.write_buffers):
            log.error("error unpacking EEPROM page %d, offset %d, len %d as %s: invalid page (label %s)", 
                page, start_byte, length, data_type, label, exc_info=1)
            return

        # don't try to write negatives to unsigned types
        if data_type in ["H", "I"] and value < 0:
            log.error("rounding negative to zero when writing to unsigned field (address %s, data_type %s, value %s)", address, data_type, value)
            value = 0

        buf = self.write_buffers[page]
        if buf is None or end_byte > 64:
            raise Exception("error packing EEPROM page %d, offset %2d, len %2d as %s: buf is %s" % (
                page, start_byte, length, data_type, buf))

        if data_type == "s":
            if value is None:
                value = ""
            for i in range(min(length, len(value))):
                if i < len(value):
                    buf[start_byte + i] = ord(value[i])
                else:
                    buf[start_byte + i] = 0
        else:
            struct.pack_into(data_type, buf, start_byte, value)

        if False:
            extra = "" if label is None else (" (%s)" % label)
            log.debug("Packed (%d, %2d, %2d) '%s' value %s -> %s%s", 
                page, start_byte, length, data_type, value, buf[start_byte:end_byte], extra)

    ##
    # If asked to regenerate, return a digest of the contents that WOULD BE 
    # WRITTEN from current settings in memory.
    def generate_digest(self, regenerate=False):
        buffers = self.buffers
        digest = 'invalid'
        try:
            if regenerate:
                self.generate_write_buffers()
                buffers = self.write_buffers

            h = hashlib.new("md5")
            for buf in buffers:
                h.update(bytes(buf))
            digest = h.hexdigest()
        except: 
            log.error(f"exception generating EEPROM digest...using '{digest}'", exc_info=1)

        # log.debug(f"EEPROM MD5 digest {digest} (regenerate {regenerate})")
        return digest

    def to_dict(self):
        d = {}
        for k, v in self.__dict__.items():
            if k not in ["user_data", "buffers", "write_buffers", "editable"]:
                d[k] = v
        return d

    ## render the attributes of this object as a JSON string
    #
    # @note some callers may prefer SpectrometerSettings.to_dict() or to_json()
    def json(self, allow_nan=True):
        tmp_buf    = self.buffers
        tmp_data   = self.user_data
        tmp_fields = self.fields
        tmp_mwc    = self.multi_wavelength_calibration

        self.buffers   = str(self.buffers)
        self.user_data = str(self.user_data)
        self.fields    = None
        self.multi_wavelength_calibration = self.multi_wavelength_calibration.toJSON()

        # this does take an allow_nan argument, but it throws an exception on NaN, 
        # rather than replacing with null :-(
        # https://stackoverflow.com/questions/6601812/sending-nan-in-json
        s = json.dumps(self.__dict__, indent=2, sort_keys=True)
        if not allow_nan:
            s = re.sub(r"\bNaN\b", "null", s)

        self.fields    = tmp_fields
        self.buffers   = tmp_buf
        self.user_data = tmp_data
        self.multi_wavelength_calibration = tmp_mwc

        return s

    ## log this object
    def dump(self):
        log.debug("EEPROM settings:")
        log.debug("  Model:            %s", self.model)
        log.debug("  Serial Number:    %s", self.serial_number)
        log.debug("  Baud Rate:        %d", self.baud_rate)
        log.debug("  Has Cooling:      %s", self.has_cooling)
        log.debug("  Has Battery:      %s", self.has_battery)
        log.debug("  Has Laser:        %s", self.has_laser)
        log.debug("  Invert X-Axis:    %s", self.invert_x_axis)
        log.debug("  Horiz Bin Enable: %s", self.horiz_binning_enabled)
        log.debug("  Gen 1.5:          %s", self.gen15)
        log.debug("  Cutoff Filter:    %s", self.cutoff_filter_installed)
        log.debug("  HW Even/Odd:      %s", self.hardware_even_odd)
        log.debug("  SiG Laser TEC:    %s", self.sig_laser_tec)
        log.debug("  Int'Lck Feedback: %s", self.has_interlock_feedback)
        log.debug("  Shutter:          %s", self.has_shutter)
        log.debug("  Disable BLE Power:%s", self.disable_ble_power)
        log.debug("  Dis Laser Arm Ind:%s", self.disable_laser_armed_indicator)
        log.debug("  Excitation (f):   %.2f nm", self.multi_wavelength_calibration.get("excitation_nm_float", default=0))
        log.debug("  Laser Warmup Sec: %d", self.laser_warmup_sec)
        log.debug("  Laser Watchdog:   %d", self.laser_watchdog_sec)
        log.debug("  Light Source:     %d", self.light_source_type)
        log.debug("  Power Timeout:    %d", self.power_timeout_sec)
        log.debug("  Detector Timeout: %d", self.detector_timeout_sec)
        log.debug("  Horiz Bin Mode:   %d", self.multi_wavelength_calibration.get("horiz_binning_mode", default=0))
        log.debug("  Slit size:        %s um", self.slit_size_um)
        log.debug("  Start Integ Time: %d ms", self.startup_integration_time_ms)
        log.debug("  Start Temp:       %.2f degC", self.startup_temp_degC)
        log.debug("  Start Triggering: 0x%04x", self.startup_triggering_scheme)
        log.debug("  Det Gain:         %f", self.detector_gain)
        log.debug("  Det Offset:       %d", self.detector_offset)
        log.debug("  Det Gain Odd:     %f", self.detector_gain_odd)
        log.debug("  Det Offset Odd:   %d", self.detector_offset_odd)
        log.debug("  Start Laser TEC:  %d (raw)", self.startup_laser_tec_setpoint)
        log.debug("")
        log.debug("  Wavecal coeffs:   %s", self.multi_wavelength_calibration.get("wavelength_coeffs"))
        log.debug("  degCToDAC coeffs: %s", self.degC_to_dac_coeffs)
        log.debug("  adcToDegC coeffs: %s", self.adc_to_degC_coeffs)
        log.debug("  Det temp max:     %s degC", self.max_temp_degC)
        log.debug("  Det temp min:     %s degC", self.min_temp_degC)
        log.debug("  TEC R298:         %s", self.tec_r298)
        log.debug("  TEC beta:         %s", self.tec_beta)
        log.debug("  Calibration Date: %s", self.calibration_date)
        log.debug("  Calibration By:   %s", self.calibrated_by)
        log.debug("")
        log.debug("  Detector name:    %s", self.detector)
        log.debug("  Active Px Horiz:  %d", self.active_pixels_horizontal)
        log.debug("  Active Px Vert:   %d", self.active_pixels_vertical)
        log.debug("  Actual Px Horiz:  %d", self.actual_pixels_horizontal)
        log.debug("  Actual Px Vert:   %d", self.actual_pixels_vertical)
        log.debug("  Min integration:  %d ms", self.min_integration_time_ms)
        log.debug("  Max integration:  %d ms", self.max_integration_time_ms)
        log.debug("  ROI Horiz Start:  %d", self.multi_wavelength_calibration.get("roi_horizontal_start", default=-1))
        log.debug("  ROI Horiz End:    %d", self.multi_wavelength_calibration.get("roi_horizontal_end", default=-1))
        log.debug("  ROI Vert Reg 1:   (%d, %d)", self.roi_vertical_region_1_start, self.roi_vertical_region_1_end)
        log.debug("  ROI Vert Reg 2:   (%d, %d)", self.roi_vertical_region_2_start, self.roi_vertical_region_2_end)
        log.debug("  ROI Vert Reg 3:   (%d, %d)", self.roi_vertical_region_3_start, self.roi_vertical_region_3_end)
        log.debug("  Linearity Coeffs: %s", self.linearity_coeffs)
        log.debug("")
        log.debug("  Laser coeffs:     %s", self.laser_power_coeffs)
        log.debug("  Max Laser Power:  %s mW", self.max_laser_power_mW)
        log.debug("  Min Laser Power:  %s mW", self.min_laser_power_mW)
        log.debug("  Avg Resolution:   %.2f", self.multi_wavelength_calibration.get("avg_resolution", default=0))
        log.debug("")
        log.debug("  User Text:        %s", self.user_text)
        log.debug("")
        log.debug("  Bad Pixels:       %s", self.bad_pixels)
        log.debug("  Product Config:   %s", self.product_configuration)

        if self.subformat == 1:
            self.dump_raman_intensity_calibration()
        elif self.subformat == 2:
            self.dump_spline()
        elif self.subformat == 3:
            self.dump_raman_intensity_calibration()
            self.dump_untethered()
        elif self.subformat == 5:
            self.multi_wavelength_calibration.dump()

    # ##########################################################################
    #                                                                          #
    #                              Convenience accessors                       #
    #                                                                          #
    # ##########################################################################

    def latest_rev(self):
        return EEPROM.LATEST_REV

    def get_horizontal_roi(self):
        start  = self.multi_wavelength_calibration.get("roi_horizontal_start", default=-1)
        end    = self.multi_wavelength_calibration.get("roi_horizontal_end", default=-1)

        if 0 <= start and start < end and end < self.active_pixels_horizontal:
            return ROI(start, end)

    ## 
    # On a 1024-pixel detector, note the expected / correct result based on the 
    # roi_horizontal_start/stop fields:
    #
    # - (0, 1024) FALSE (last pixel invalid)
    # - (0, 1023) TRUE  (even though no vignetting is occuring)
    # - (0, 1022) TRUE  (crops last pixel)
    # - (1, 1023) TRUE  (crops first pixel)
    # - (1, 1024) FALSE (last pixel invalid)
    # - (1,    1) FALSE (start must < end)
    # - (1,    2) TRUE  (valid 2-pixel spectrum)
    # - (2,    1) FALSE (start must < end)
    #   
    # @return whether a valid horizontal ROI is configured
    def has_horizontal_roi(self): # -> bool 
        return self.get_horizontal_roi() is not None

    def has_laser_power_calibration(self): # -> bool 
        if self.max_laser_power_mW <= 0:
            return False
        return utils.coeffs_look_valid(self.laser_power_coeffs, count=4)

    def has_detector_tec_calibration(self): # -> bool 
        """ simplified version of WasatchNET.Util.validTECCal """

        if not utils.coeffs_look_valid(self.degC_to_dac_coeffs, count=3):
            return False

        # check it's not the "default"
        if self.degC_to_dac_coeffs[0] == 2700 and \
           self.degC_to_dac_coeffs[1] == 0 and \
           self.degC_to_dac_coeffs[2] == 0:
            return False

        return True

    def has_raman_intensity_calibration(self):
        if self.format < 6:
            log.debug(f"has_raman_intensity_calibration: false because format {self.format}")
            return False

        # could check subformat too

        coeffs = self.multi_wavelength_calibration.get("raman_intensity_coeffs")
        if not utils.coeffs_look_valid(coeffs):
            log.debug(f"has_raman_intensity_calibration: false because coeffs look weird")
            return False

        return True

    def has_sml(self):
        """ @todo include light_source_type """
        return self.has_laser and self.max_laser_power_mW < 150

    def has_mml(self):
        """ @todo include light_source_type """
        return self.has_laser and self.max_laser_power_mW >= 150

    ## convert the given laser output power from milliwatts to percentage
    #  using the configured calibration
    def laser_power_mW_to_percent(self, mW):
        if not self.has_laser_power_calibration():
            return 0

        perc = self.laser_power_coeffs[0] \
             + self.laser_power_coeffs[1] * mW \
             + self.laser_power_coeffs[2] * mW * mW \
             + self.laser_power_coeffs[3] * mW * mW * mW

        return perc

    def is_valid_serial_number(self):
        return re.match(r"^[-+a-z0-9_ ]{1,16}$", self.serial_number, re.IGNORECASE)

    # ##########################################################################
    #                                                                          #
    #                                Subformats                                #
    #                                                                          #
    # ##########################################################################

    # ##########################################################################
    # Subformat 1: Raman Intensity Calibration
    # ##########################################################################

    def init_raman_intensity_calibration(self):
        self.raman_intensity_calibration_order = 5
        self.raman_intensity_coeffs = []

    def read_raman_intensity_calibration(self):
        self.raman_intensity_coeffs = []
        order = self.RAMAN_INTENSITY_CALIBRATION_ORDER
        for i in range(order + 1):
            offset = i * 4 + 1
            self.raman_intensity_coeffs.append(self.unpack((6, offset, 4), "f", "raman_intensity_coeff_%d" % i))

    def write_raman_intensity_calibration(self):
        self.pack((6, 0,  1), "B", self.raman_intensity_calibration_order)
        order = self.RAMAN_INTENSITY_CALIBRATION_ORDER
        for i in range(order + 1):
            offset = i * 4 + 1
            coeff = 0.0
            if self.raman_intensity_coeffs is not None and i < len(self.raman_intensity_coeffs):
                coeff = self.raman_intensity_coeffs[i]
            self.pack((6, offset, 4), "f", coeff)

    def dump_raman_intensity_calibration(self):
        log.debug("Raman Intensity Calibration:")
        log.debug("  Raman Int Coeffs: %s", self.raman_intensity_coeffs)

    # ##########################################################################
    # Subformat 2: Spline
    # ##########################################################################

    def init_spline(self):
        self.spline_points               = 0
        self.spline_min                  = 0
        self.spline_max                  = 0
        self.spline_wavelengths          = []
        self.spline_y                    = []
        self.spline_y2                   = []

    ##
    # @todo turn into EEPROMSpline
    def read_spline(self):
        self.spline_wavelengths = []
        self.spline_y = []
        self.splint_y2 = []

        self.spline_points = self.unpack((6, 0, 1), "B", "spline.points")
        if self.spline_points <= 0:
            log.debug("empty spline")
            return

        if self.spline_points > 14:
            log.error("invalid spline (%d points)", self.spline_points)
            return

        for i in len(range(self.spline_points)):
            if i < 5:
                page = 6
                base = 4
                first = 0
            elif i < 10:
                page = 7
                base = 0
                first = 5
            else:
                page = 4
                base = 0
                first = 10

            offset = base + (i - first) * 12
            wavelength = self.unpack((page, offset, 4), "f", "spline.wavelength[%d]" % i)
            y  = self.unpack((page, offset + 4, 4), "f", "spline.y[%d]" % i)
            y2 = self.unpack((page, offset + 8, 4), "f", "spline.y2[%d]" % i)

            self.spline_wavelengths.append(wavelength)
            self.spline_y.append(y)
            self.spline_y2.append(y2)
        
        self.spline_min = self.unpack((4, 56, 4), "f", "spline_wavelength_min")
        self.spline_max = self.unpack((4, 60, 4), "f", "spline_wavelength_max")
        if self.spline_min >= self.spline_max:
            log.error("invalid spline (min %f, max %f)", self.spline_min, self.spline_max)
            return

    def write_spline(self):
        log.error("EEPROM.write_spline not implemented")

    def dump_spline(self):
        return # not implemented

    # ##########################################################################
    # Subformat 3: Untethered
    # ##########################################################################

    def init_untethered(self):
        self.untethered_library_type     = 0
        self.untethered_library_id       = 0
        self.untethered_scans_to_average = 0
        self.untethered_min_ramp_pixels  = 0
        self.untethered_min_peak_height  = 0
        self.untethered_match_threshold  = 0
        self.untethered_library_count    = 0
                                         
    def read_untethered(self):
        self.untethered_library_type     = self.unpack((7,  0,  1), "B", "library_type")
        self.untethered_library_id       = self.unpack((7,  1,  2), "H", "library_id")
        self.untethered_scans_to_average = self.unpack((7,  3,  1), "B", "scans_to_average")
        self.untethered_min_ramp_pixels  = self.unpack((7,  4,  1), "B", "min_ramp_pixels")
        self.untethered_min_peak_height  = self.unpack((7,  5,  2), "H", "min_peak_height")
        self.untethered_match_threshold  = self.unpack((7,  7,  1), "B", "match_threshold")
        self.untethered_library_count    = self.unpack((7,  8,  1), "B", "library_count")

    def write_untethered(self):
        self.pack((7, 0, 1), "B", self.untethered_library_type)
        self.pack((7, 1, 2), "H", self.untethered_library_id)
        self.pack((7, 3, 1), "B", self.untethered_scans_to_average)
        self.pack((7, 4, 1), "B", self.untethered_min_ramp_pixels)
        self.pack((7, 5, 2), "H", self.untethered_min_peak_height)
        self.pack((7, 7, 1), "B", self.untethered_match_threshold)
        self.pack((7, 8, 1), "B", self.untethered_library_count)

    def dump_untethered(self):
        log.debug("Untethered:")
        log.debug("  Library Type:     %d", self.untethered_library_type)
        log.debug("  Library ID:       %d", self.untethered_library_id)
        log.debug("  Scans to Average: %d", self.untethered_scans_to_average)
        log.debug("  Min Ramp Pixels:  %d", self.untethered_min_ramp_pixels)
        log.debug("  Min Peak Height:  %d", self.untethered_min_peak_height)
        log.debug("  Match Threshold:  %d", self.untethered_match_threshold)
        log.debug("  Library Count:    %d", self.untethered_library_count)

class MultiWavelengthCalibration:
    """
    Subformat 5: Multi-Wavelength

    This class is used to manage those attributes which can vary in a single 
    multi-wavelength spectrometer (for instance, where a compound grating 
    supports different Raman excitations in different detector ROIs).

    Currently the set of such attributes includes laser excitation wavelength,
    wavelength calibration, horizontal ROI, Raman Intensity Calibration, 
    average resolution, and horizontal binning mode. (Vertical ROI was 
    presciently covered in Format 1.)

    @par Theory of Operation

    A "calibration" is one set of attributes (self.attributes) related to
    a particular grating region.

    Most of this class architecture could conceivably support many different
    calibrations. In practice, it is limited to 3, because it is currently
    using the 3-region Vertical ROI fields included in the original (Rev 1)
    EEPROM format. 

    It is also practically limited to 2 calibrations because currently it's
    using one EEPROM page per "extra" calibration, and the "standard 8" pages
    only has one extra: page 7. This isn't a limitation on XS, which has a much
    larger EEPROM than X series, so we could easily add more pages to subformat
    5 if desired.

    Calibration 0 is the "original calibration," and is stored in the "standard"
    EEPROM pages where such things (wavecal, Raman Intensity, etc) have always
    been stored. When loaded to memory, they are initially read into the EEPROM 
    instance properties (.wavecal_coeffs, .excitation_float_nm etc) where they 
    have always been stored. HOWEVER, this is not where "modern" code should
    attempt to read or write them, as described below.

    Additional calibrations (1+) are persisted on a single EEPROM page each (p7 
    for calibration 1). When loaded from EEPROM, they are NOT stored in object
    properties like Calibration 0, but are stored in a dict by calibration index
    and their field name. For instance, the laser excitation wavelength of the
    second calibration (calibration 1) is in values["excitation_nm_float"][1].

    For consistency, working copies of calibration 0 fields are populated to
    values[name][0]. New code is recommended to access even the old "calibration 
    0" fields through the provided MultiWavelengthCalibration accessors (get()
    and set()), as this will make the new code "future-proof" and multi-
    calibration ready. All known Wasatch.PY and ENLIGHTEN references to 0-
    calibration properties have been updated to use the new accessors as 
    reference examples.
    """

    def __init__(self, eeprom):
        self.eeprom = eeprom

        self.attributes = [ 'excitation_nm_float', 'wavelength_coeffs', 
                            'roi_horizontal_start', 'roi_horizontal_end', 
                            'avg_resolution', 'raman_intensity_coeffs', 
                            'horiz_binning_mode' ]
        self.values = {}
        self.calibrations = 1
        self.selected_calibration = 0 # e.g., self.values['avg_resolution'][selected_calibration]

        # this won't do anything except copy "defaults" into self.values (which 
        # will get overwritten when EEPROM.read re-calls self.initialize after 
        # "better" values are available to assign to self.values), but it helps
        # non-FID classes like AndorDevice to start with an initialized baseline
        self.initialize()

    def is_multi_wavelength(self, name):
        return name in self.attributes

    def initialize(self, name=None):
        """ initialize attributes to single-element arrays with the "standard" value at calibration 0 """
        if name:
            self.values[name] = [ getattr(self.eeprom, name) ]
        else:
            for name in self.attributes:
                self.values[name] = [ getattr(self.eeprom, name) ]

    def get(self, name, calibration=None, index=None, default=None):
        # allow getter to be used for any EEPROM attribute
        if name not in self.attributes:
            return getattr(self.eeprom, name)

        if name not in self.values:
            log.debug(f"get: name {name} in attributes, but not in values: {self.values}")
            return default

        label = f"{name}[{calibration}]" if index is None else f"{name}[{calibration}][{index}]"

        a = self.values[name]
        if calibration is None:
            calibration = self.selected_calibration
        if calibration >= len(a):
            # log.warn(f"MultiWavelengthCalibration.get: returning default {default} because calibration {calibration} not in {label} array len {len(a)}")
            return default

        if index is None:
            value = a[calibration] 
        else:
            if isinstance(a[calibration], list):
                if index >= len(a[calibration]):
                    log.warn(f"MultiWavelengthCalibration.get: returning {label} 0 because index {index} not in array[calibration {calibration}] len {len(a[calibration])}")
                    return 0
                else:
                    value = a[calibration][index]
            else:
                log.warn(f"MultiWavelengthCalibration.get: returning {label} 0 because array[calibration {calibration}] is not a list")
                return 0
                    
        # log.debug(f"MultiWavelengthCalibration.get: returning {label} = {value}")
        return value

    def set(self, name, value, calibration=None, index=None):
        # apply some quick validation based on datatype
        eeprom_field = self.eeprom.fields.get(name, None)
        if eeprom_field is None:
            log.debug(f"MWC.set: {name} not in eeprom.fields?")
        else:
            # enforce integers
            if eeprom_field.data_type.lower() in ["i", "h", "b"]:
                log.debug(f"MWC.set: rounding {name} to int")
                value = int(value)

            # enforce unsigned
            if eeprom_field.data_type in ["I", "H", "B"]:
                log.debug(f"MWC.set: clamping {name} to zero")
                value = int(max(0, value))

        if name not in self.attributes:
            setattr(self.eeprom, name, value)
            return

        try:
            a = self.values[name]
            if calibration is None:
                calibration = self.selected_calibration

            while len(a) - 1 < calibration:
                a.append(0)

            if self.calibrations < calibration + 1:
                self.calibrations = calibration + 1

            if index is None:
                label = f"{name}[{calibration}]"
                a[calibration] = value
                if calibration == 0:
                    setattr(self.eeprom, name, value)
            else:
                label = f"{name}[{calibration}][{index}]"
                a[calibration][index] = value
                if calibration == 0:
                    setattr(self.eeprom, name, a[calibration])

            log.debug(f"MultiWavelengthCalibration.set: set {label} = {value}")
        except:
            log.error(f"MultiWavelengthCalibration.set: failed to set name {name}, calibration {calibration}, index {index}, value {value}", exc_info=1)

    def read(self, calibration):
        if calibration < 1:
            # assume calibration[0] was read with standard eeprom.read
            return

        if self.calibrations < calibration + 1:
            self.calibrations = calibration + 1

        page = 6 + calibration
        log.debug(f"MultiWavelengthCalibration.read: reading calibration {calibration} from page {page}")
        self.values["excitation_nm_float"    ].append(  self.eeprom.unpack((page,  0,  4), "f"))
        self.values["roi_horizontal_start"   ].append(  self.eeprom.unpack((page, 26,  2), "H"))
        self.values["roi_horizontal_end"     ].append(  self.eeprom.unpack((page, 28,  2), "H"))
        self.values["avg_resolution"         ].append(  self.eeprom.unpack((page, 30,  4), "f"))
        self.values["horiz_binning_mode"     ].append(  self.eeprom.unpack((page, 58,  1), "B"))
        self.values["wavelength_coeffs"      ].append([ self.eeprom.unpack((page,  4 + i * 4,  4), "f") for i in range(5) ])
        self.values["raman_intensity_coeffs" ].append([ self.eeprom.unpack((page, 34 + i * 4,  4), "f") for i in range(6) ])

    def write(self):
        log.debug(f"MultiWavelengthCalibration.write: calibrations {self.calibrations}")

        # assume calibration[0] output with standard eeprom.write
        for calibration in range(1, self.calibrations):
            page = 6 + calibration
            log.debug(f"MultiWavelengthCalibration.write: writing calibration {calibration} to page {page}")

            self.eeprom.pack((page,  0,  4), "f", self.get("excitation_nm_float",  calibration))
            self.eeprom.pack((page, 26,  2), "H", self.get("roi_horizontal_start", calibration))
            self.eeprom.pack((page, 28,  2), "H", self.get("roi_horizontal_end",   calibration))
            self.eeprom.pack((page, 30,  4), "f", self.get("avg_resolution",       calibration))
            self.eeprom.pack((page, 58,  1), "B", self.get("horiz_binning_mode",   calibration))
            for i in range(5): self.eeprom.pack((page,  4 + i * 4,  4), "f", self.get("wavelength_coeffs", calibration, i))
            for i in range(6): self.eeprom.pack((page, 34 + i * 4,  4), "f", self.get("raman_intensity_coeffs", calibration, i))

    def dump(self):
        log.debug("Multi-Wavelength:")
        for i in range(self.calibrations):
            log.debug(f"  Calibration #{i}")
            for name in self.attributes:
                log.debug(f"    {name} = {self.get(name, calibration=i)}")

    def toJSON(self): 
        return str(self.__dict__)
        # return json.dumps(self, default=lambda o: o.__dict__, sort_keys=True, indent=4)

class EEPROMField:
    def __init__(self, pos, data_type, name):
        self.pos        = pos
        self.data_type  = data_type
        self.name       = name

        self.page       = pos[0]
        self.offset     = pos[1]
        self.length     = pos[2]

    def toJSON(self): 
        return str(self.__dict__)
