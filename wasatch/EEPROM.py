import hashlib
import logging
import struct
import array
import math
import copy
import json
import re

from . import utils

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
# @see http://ww1.microchip.com/downloads/en/DeviceDoc/20006270A.pdf
class EEPROM(object):
    
    LATEST_REV = 10
    MAX_PAGES = 8
    MAX_RAMAN_INTENSITY_CALIBRATION_ORDER = 7

    def __init__(self):
        self.format = 0

        self.model                       = None
        self.serial_number               = None
        self.baud_rate                   = 0
        self.has_cooling                 = False
        self.has_battery                 = False
        self.has_laser                   = False
        self.feature_mask                = 0
        self.invert_x_axis               = False
        self.bin_2x2                     = False
        self.gen15                       = False
        self.cutoff_filter_installed     = False
        self.excitation_nm               = 0.0
        self.excitation_nm_float         = 0.0
        self.slit_size_um                = 0
        self.startup_integration_time_ms = 10
        self.startup_temp_degC           = 15
        self.startup_triggering_scheme   = 0
        self.detector_gain               = 1.9
        self.detector_offset             = 0
        self.detector_gain_odd           = 1.9
        self.detector_offset_odd         = 0
        self.laser_warmup_sec            = 0
                                         
        self.wavelength_coeffs           = []
        self.degC_to_dac_coeffs          = []
        self.adc_to_degC_coeffs          = []
        self.max_temp_degC               = 20 # interesting
        self.min_temp_degC               = 10 #    defaults
        self.tec_r298                    = 0
        self.tec_beta                    = 0
        self.calibration_date            = None
        self.calibrated_by               = None
                                         
        self.detector                    = None
        self.active_pixels_horizontal    = 1024
        self.active_pixels_vertical      = 0
        self.min_integration_time_ms     = 10
        self.max_integration_time_ms     = 60000
        self.actual_horizontal           = 0
        self.actual_vertical             = 0     # not a real EEPROM field, though it should be
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

        self.raman_intensity_calibration_order = 0
        self.raman_intensity_coeffs      = []
        self.multi_wavecal               = None

        self.spline_points               = 0
        self.spline_min                  = 0
        self.spline_max                  = 0
        self.spline_wavelengths          = []
        self.spline_y                    = []
        self.spline_y2                   = []
                                         
        self.format                      = 0
        self.subformat                   = 0 # pages 6-7

        self.buffers = []
        self.write_buffers = []
        self.digest = None

        self.editable = [ "excitation_nm",
                          "excitation_nm_float",
                          "detector_gain",
                          "detector_offset",
                          "detector_gain_odd",
                          "detector_offset_odd",
                          "calibrated_by",
                          "calibration_date", 
                          "user_text",
                          "wavelength_coeffs",
                          "linearity_coeffs",
                          "max_laser_power_mW",
                          "min_laser_power_mW",
                          "laser_power_coeffs",
                          "bad_pixels",
                          "bin_2x2",
                          "gen15",
                          "cutoff_filter_installed",
                          "laser_warmup_sec",
                          "roi_horizontal_end",             
                          "roi_horizontal_start",           
                          "roi_vertical_region_1_end",      
                          "roi_vertical_region_1_start",    
                          "roi_vertical_region_2_end",      
                          "roi_vertical_region_2_start",    
                          "roi_vertical_region_3_end",      
                          "roi_vertical_region_3_start",
                          "raman_intensity_calibration_order",
                          "raman_intensity_coeffs" ]

    def latest_rev(self):
        return EEPROM.LATEST_REV

    def to_dict(self):
        d = {}
        for k, v in self.__dict__.items():
            if k not in ["user_data", "buffers", "write_buffers", "editable"]:
                d[k] = v
        return d

    ## whether the given field is normally editable by users via ENLIGHTEN
    #
    # @return False otherwise (don't trust in None's truthiness, as you can't 
    #         pass None to Qt's setEnabled)
    def is_editable(self, name):
        s = name.lower()
        for field in self.editable:
            if s == field.lower():
                return True
        return False

    ## @return tuple of (start, end) pixel coordinates (end is last pixel, not last+1),
    #          or None if no valid horizontal ROI
    # @todo this should return an ROI object, not a tuple
    def get_horizontal_roi(self):
        start  = self.roi_horizontal_start
        end    = self.roi_horizontal_end
        pixels = self.active_pixels_horizontal

        if 0 <= start and start < end and end < pixels:
            return (start, end)

    def has_horizontal_roi(self):
        start  = self.roi_horizontal_start
        end    = self.roi_horizontal_end
        pixels = self.active_pixels_horizontal
        return 0 <= start and start < end and end < pixels
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

        # unpack all the fields we know about
        try:
            self.read_eeprom()
            return True
        except:
            log.error("failed to parse EEPROM", exc_info=1)
            return False

    ##
    # If asked to regenerate, return a digest of the contents that WOULD BE 
    # WRITTEN from current settings in memory.
    def generate_digest(self, regenerate=False):
        buffers = self.buffers
        if regenerate:
            self.generate_write_buffers()
            buffers = self.write_buffers

        h = hashlib.new("md5")
        for buf in buffers:
            h.update(bytes(buf))
        digest = h.hexdigest()

        log.debug("EEPROM MD5 digest = %s (regenerate = %s)", digest, regenerate)
        return digest

    ## render the attributes of this object as a JSON string
    #
    # @note some callers may prefer SpectrometerSettings.to_dict() or to_json()
    def json(self, allow_nan=True):
        tmp_buf  = self.buffers
        tmp_data = self.user_data

        self.buffers   = str(self.buffers)
        self.user_data = str(self.user_data)

        # this does take an allow_nan argument, but it throws an exception on NaN, 
        # rather than replacing with null :-(
        # https://stackoverflow.com/questions/6601812/sending-nan-in-json
        s = json.dumps(self.__dict__, indent=2, sort_keys=True)
        if not allow_nan:
            s = re.sub(r"\bNaN\b", "null", s)

        self.buffers   = tmp_buf
        self.user_data = tmp_data

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
        log.debug("  Bin 2x2:          %s", self.bin_2x2)
        log.debug("  Gen 1.5:          %s", self.gen15)
        log.debug("  Cutoff Filter:    %s", self.cutoff_filter_installed)
        log.debug("  Excitation:       %s nm", self.excitation_nm)
        log.debug("  Excitation (f):   %.2f nm", self.excitation_nm_float)
        log.debug("  Laser Warmup Sec: %d", self.laser_warmup_sec)
        log.debug("  Slit size:        %s um", self.slit_size_um)
        log.debug("  Start Integ Time: %d ms", self.startup_integration_time_ms)
        log.debug("  Start Temp:       %.2f degC", self.startup_temp_degC)
        log.debug("  Start Triggering: 0x%04x", self.startup_triggering_scheme)
        log.debug("  Det Gain:         %f", self.detector_gain)
        log.debug("  Det Offset:       %d", self.detector_offset)
        log.debug("  Det Gain Odd:     %f", self.detector_gain_odd)
        log.debug("  Det Offset Odd:   %d", self.detector_offset_odd)
        log.debug("")
        log.debug("  Wavecal coeffs:   %s", self.wavelength_coeffs)
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
        log.debug("  Active horiz:     %d", self.active_pixels_horizontal)
        log.debug("  Active vertical:  %d", self.active_pixels_vertical)
        log.debug("  Min integration:  %d ms", self.min_integration_time_ms)
        log.debug("  Max integration:  %d ms", self.max_integration_time_ms)
        log.debug("  Actual Horiz:     %d", self.actual_horizontal)
        log.debug("  ROI Horiz Start:  %d", self.roi_horizontal_start)
        log.debug("  ROI Horiz End:    %d", self.roi_horizontal_end)
        log.debug("  ROI Vert Reg 1:   (%d, %d)", self.roi_vertical_region_1_start, self.roi_vertical_region_1_end)
        log.debug("  ROI Vert Reg 2:   (%d, %d)", self.roi_vertical_region_2_start, self.roi_vertical_region_2_end)
        log.debug("  ROI Vert Reg 3:   (%d, %d)", self.roi_vertical_region_3_start, self.roi_vertical_region_3_end)
        log.debug("  Linearity Coeffs: %s", self.linearity_coeffs)
        log.debug("")
        log.debug("  Laser coeffs:     %s", self.laser_power_coeffs)
        log.debug("  Max Laser Power:  %s mW", self.max_laser_power_mW)
        log.debug("  Min Laser Power:  %s mW", self.min_laser_power_mW)
        log.debug("  Avg Resolution:   %.2f", self.avg_resolution)
        log.debug("")
        log.debug("  User Text:        %s", self.user_text)
        log.debug("")
        log.debug("  Bad Pixels:       %s", self.bad_pixels)
        log.debug("  Product Config:   %s", self.product_configuration)
        log.debug("")
        log.debug("  Raman Int Order:  %d", self.raman_intensity_calibration_order)
        log.debug("  Raman Int Coeffs: %s", self.raman_intensity_coeffs)

    # ##########################################################################
    #                                                                          #
    #                             Private Methods                              #
    #                                                                          #
    # ##########################################################################

    ## 
    # Assuming a set of 8 buffers have been passed in via parse(), actually
    # unpack (deserialize / unmarshall) the binary data into the appropriate
    # fields and datatypes.
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
        if self.format >= 9:
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
            self.startup_integration_time_ms = self.unpack((0, 43,  2), "H", "start_integ")
            self.startup_temp_degC           = self.unpack((0, 45,  2), "h", "start_temp")
            self.startup_triggering_scheme   = self.unpack((0, 47,  1), "B", "start_trigger")
            self.detector_gain               = self.unpack((0, 48,  4), "f", "gain") # "even pixels" for InGaAs
            self.detector_offset             = self.unpack((0, 52,  2), "h", "offset") # "even pixels" for InGaAs
            self.detector_gain_odd           = self.unpack((0, 54,  4), "f", "gain_odd") # InGaAs-only
            self.detector_offset_odd         = self.unpack((0, 58,  2), "h", "offset_odd") # InGaAs-only

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
        self.max_temp_degC                   = self.unpack((1, 28,  2), "h", "max_temp")
        self.min_temp_degC                   = self.unpack((1, 30,  2), "h", "min_temp")
        self.adc_to_degC_coeffs = []
        self.adc_to_degC_coeffs        .append(self.unpack((1, 32,  4), "f", "adcToDegC_coeff_0"))
        self.adc_to_degC_coeffs        .append(self.unpack((1, 36,  4), "f"))
        self.adc_to_degC_coeffs        .append(self.unpack((1, 40,  4), "f"))
        self.tec_r298                        = self.unpack((1, 44,  2), "h", "r298")
        self.tec_beta                        = self.unpack((1, 46,  2), "h", "beta")
        self.calibration_date                = self.unpack((1, 48, 12), "s", "date")
        self.calibrated_by                   = self.unpack((1, 60,  3), "s", "tech")
                                    
        # ######################################################################
        # Page 2                    
        # ######################################################################

        self.detector                        = self.unpack((2,  0, 16), "s", "detector")
        self.active_pixels_horizontal        = self.unpack((2, 16,  2), "H", "pixels")
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

        self.actual_horizontal               = self.unpack((2, 25,  2), "H" if self.format >= 4 else "h", "actual_horiz")
        self.actual_vertical                 = self.active_pixels_vertical  # approximate for now
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
        self.max_laser_power_mW              = self.unpack((3, 28,  4), "f", "max_laser_mW")
        self.min_laser_power_mW              = self.unpack((3, 32,  4), "f", "min_laser_mW")

        if self.format >= 4:
            self.excitation_nm_float         = self.unpack((3, 36,  4), "f", "excitation(float)")
        else:
            self.excitation_nm_float = self.excitation_nm

        if self.format >= 5:
            self.min_integration_time_ms     = self.unpack((3, 40,  4), "I", "min_integ(uint)")
            self.max_integration_time_ms     = self.unpack((3, 44,  4), "I", "max_integ(uint)") 

        if self.format >= 7:
            self.avg_resolution              = self.unpack((3, 48, 4), "f", "avg_resolution")

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
            self.product_configuration       = self.unpack((5,  30, 16), "s", "product_config")
        if self.format >= 7:
            self.subformat                   = self.unpack((5,  63,  1), "B", "subformat")

        # ######################################################################
        # Page 6-7
        # ######################################################################

        if self.subformat == 0:
            # todo: extend user_data
            pass
        elif self.subformat == 1:
            self.read_raman_intensity_calibration()
        elif self.subformat == 2:
            self.read_spline()
        elif self.subformat == 3:
            self.read_multi_wavecal()
        else:
            log.critical("Unsupported EEPROM subformat: %d", self.subformat)

        # ######################################################################
        # feature mask
        # ######################################################################

        self.invert_x_axis           = 0 != self.feature_mask & 0x0001
        self.bin_2x2                 = 0 != self.feature_mask & 0x0002
        self.gen15                   = 0 != self.feature_mask & 0x0004
        self.cutoff_filter_installed = 0 != self.feature_mask & 0x0008

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

    def read_raman_intensity_calibration(self):
        self.raman_intensity_coeffs = []
        self.raman_intensity_calibration_order = self.unpack((6, 0, 1), "B", "raman_intensity_calibration_order")
        if 0 == self.raman_intensity_calibration_order:
            pass
        elif self.raman_intensity_calibration_order <= EEPROM.MAX_RAMAN_INTENSITY_CALIBRATION_ORDER:
            order = self.raman_intensity_calibration_order
            terms = order + 1
            for i in range(terms):
                offset = i * 4 + 1
                self.raman_intensity_coeffs.append(self.unpack((6, offset, 4), "f", "raman_intensity_coeff_%d" % i))
        else:
            log.critical("Unsupported Raman Intensity Calibration order: %d", self.raman_intensity_calibration_order)

    def write_raman_intensity_calibration(self):
        self.pack((6, 0,  1), "B", self.raman_intensity_calibration_order)
        if 0 <= self.raman_intensity_calibration_order <= EEPROM.MAX_RAMAN_INTENSITY_CALIBRATION_ORDER:
            order = self.raman_intensity_calibration_order
            terms = order + 1
            for i in range(EEPROM.MAX_RAMAN_INTENSITY_CALIBRATION_ORDER + 1):
                offset = i * 4 + 1
                if i < terms and self.raman_intensity_coeffs is not None and i < len(self.raman_intensity_coeffs):
                    coeff = self.raman_intensity_coeffs[i]
                else:
                    coeff = 0.0
                # log.debug("packing raman_intensity_coeffs[%d] (offset %d, order %d, terms %d) => %e", i, offset, order, terms, coeff)
                self.pack((6, offset, 4), "f", coeff)
        else:
            log.critical("Unsupported Raman Intensity Calibration order: %d", self.raman_intensity_calibration_order)
            for i in range(EEPROM.MAX_RAMAN_INTENSITY_CALIBRATION_ORDER + 1):
                offset = i * 4 + 1
                self.pack((6, offset, 4), "f", 0.0)

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
        if lo >= hi:
            log.error("invalid spline (min %f, max %f)", self.spline_min, self.spline_max)
            return

    def write_spline(self):
        log.critical("EEPROM.write_spline not implemented")

    def multi_wavecal_page_start(self, pos):
        if pos == 0:
            page = 1
            start = 0
        else:
            page = 6 if pos < 5 else 7
            start = ((pos - 1) % 4) * 16
        return (page, start)

    ##
    # @todo make EEPROMMultiWavecal
    def read_multi_wavecal(self):

        # store as dict rather than array in case some positions are invalid
        tmp = {}

        # parse each of the 9 wavecal positions
        for pos in range(9):
            (page, start) = self.multi_wavecal_page_start(pos)
            try:
                coeffs = []
                for i in range(4):
                    coeffs.append(self.unpack((page, start + i * 4, 4), "f"))

                if utils.coeffs_look_valid(coeffs):
                    tmp[pos] = coeffs
            except:
                log.error("invalid multi-wavecal at position %d", pos, exc_info=1)

        if len(tmp) > 0:
            self.multi_wavecal = tmp
        else:
            self.multi_wavecal = None
    
    def write_multi_wavecal(self):
        if self.multi_wavecal is None:
            return
        for pos, coeffs in self.multi_wavecal.items():
            (page, start) = self.multi_wavecal_page_start(pos)
            for i in range(len(coeffs)):
                self.pack((page, start + i * 4, 4), "f", coeffs[i])

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

        extra = "" if label is None else (" (%s)" % label)
        log.debug("Packed (%d, %2d, %2d) '%s' value %s -> %s%s", 
            page, start_byte, length, data_type, value, buf[start_byte:end_byte], extra)

    def generate_feature_mask(self):
        mask = 0
        mask |= 0x0001 if self.invert_x_axis           else 0
        mask |= 0x0002 if self.bin_2x2                 else 0
        mask |= 0x0004 if self.gen15                   else 0
        mask |= 0x0008 if self.cutoff_filter_installed else 0
        return mask

    ##
    # Call this to populate an internal array of "write buffers" which may be written back
    # to spectrometers (or used to generate the digest of what WOULD be written).
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

        # ######################################################################
        # Page 1
        # ######################################################################

        self.pack((1,  0,  4), "f", self.wavelength_coeffs[0])
        self.pack((1,  4,  4), "f", self.wavelength_coeffs[1])
        self.pack((1,  8,  4), "f", self.wavelength_coeffs[2])
        self.pack((1, 12,  4), "f", self.wavelength_coeffs[3])
        self.pack((1, 16,  4), "f", self.degC_to_dac_coeffs[0])
        self.pack((1, 20,  4), "f", self.degC_to_dac_coeffs[1])
        self.pack((1, 24,  4), "f", self.degC_to_dac_coeffs[2])
        self.pack((1, 32,  4), "f", self.adc_to_degC_coeffs[0])
        self.pack((1, 36,  4), "f", self.adc_to_degC_coeffs[1])
        self.pack((1, 40,  4), "f", self.adc_to_degC_coeffs[2])
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
            if len(self.wavelength_coeffs) > 4:
                coeff = self.wavelength_coeffs[4]
            self.pack((2, 21,  4), "f", coeff)
        self.pack((2, 25,  2), "H", self.actual_horizontal)
        self.pack((2, 27,  2), "H", self.roi_horizontal_start)
        self.pack((2, 29,  2), "H", self.roi_horizontal_end)
        self.pack((2, 31,  2), "H", self.roi_vertical_region_1_start)
        self.pack((2, 33,  2), "H", self.roi_vertical_region_1_end)
        self.pack((2, 35,  2), "H", self.roi_vertical_region_2_start)
        self.pack((2, 37,  2), "H", self.roi_vertical_region_2_end)
        self.pack((2, 39,  2), "H", self.roi_vertical_region_3_start)
        self.pack((2, 41,  2), "H", self.roi_vertical_region_3_end)
        self.pack((2, 43,  4), "f", self.linearity_coeffs[0])
        self.pack((2, 47,  4), "f", self.linearity_coeffs[1])
        self.pack((2, 51,  4), "f", self.linearity_coeffs[2])
        self.pack((2, 55,  4), "f", self.linearity_coeffs[3])
        self.pack((2, 59,  4), "f", self.linearity_coeffs[4])

        # ######################################################################
        # Page 3
        # ######################################################################

        self.pack((3, 12,  4), "f", self.laser_power_coeffs[0])
        self.pack((3, 16,  4), "f", self.laser_power_coeffs[1])
        self.pack((3, 20,  4), "f", self.laser_power_coeffs[2])
        self.pack((3, 24,  4), "f", self.laser_power_coeffs[3])
        self.pack((3, 28,  4), "f", self.max_laser_power_mW)
        self.pack((3, 32,  4), "f", self.min_laser_power_mW)
        self.pack((3, 36,  4), "f", self.excitation_nm_float)
        self.pack((3, 40,  4), "I", self.min_integration_time_ms)
        self.pack((3, 44,  4), "I", self.max_integration_time_ms)
        self.pack((3, 48,  4), "f", self.avg_resolution)

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
        for i in range(15):
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

        if self.subformat == 1:
            self.write_raman_intensity_calibration()
        elif self.subformat == 2:
            self.write_spline()
        elif self.subformat == 3:
            self.write_multi_wavecal()
        else:
            log.critical("Unsupported EEPROM subformat: %d", self.subformat)

    # ##########################################################################
    # Laser Power convenience accessors
    # ##########################################################################

    def has_laser_power_calibration(self):
        if self.max_laser_power_mW <= 0:
            return False
        return utils.coeffs_look_valid(self.laser_power_coeffs, count=4)

    def has_raman_intensity_calibration(self):
        if self.format < 6:
            return False

        if 0 < self.raman_intensity_calibration_order <= EEPROM.MAX_RAMAN_INTENSITY_CALIBRATION_ORDER:
            return utils.coeffs_look_valid(self.raman_intensity_coeffs, count = self.raman_intensity_calibration_order + 1)
        return False

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

    def set(self, name, value):
        setattr(self, name, value)
