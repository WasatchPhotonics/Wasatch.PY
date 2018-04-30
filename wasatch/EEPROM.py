import logging
import struct

log = logging.getLogger(__name__)

class EEPROM(object):
    def __init__(self):
        self.model                       = None
        self.serial_number               = None
        self.baud_rate                   = 0
        self.has_cooling                 = False
        self.has_battery                 = False
        self.has_laser                   = False
        self.excitation_nm               = None
        self.slit_size_um                = 0
                                         
        self.wavelength_coeffs           = []
        self.degC_to_dac_coeffs          = []
        self.adc_to_degC_coeffs          = []
        self.max_temp_degC               = 0
        self.min_temp_degC               = 0
        self.tec_r298                    = 0
        self.tec_beta                    = 0
        self.calibration_date            = None
        self.calibrated_by               = None
                                         
        self.detector                    = None
        self.active_pixels_horizontal    = 0
        self.active_pixels_vertical      = 0
        self.min_integration_time_ms     = 0
        self.max_integration_time_ms     = 0
        self.actual_horizontal           = 0
        self.roi_horizontal_start        = 0
        self.roi_horizontal_end          = 0
        self.roi_vertical_region_1_start = 0
        self.roi_vertical_region_1_end   = 0
        self.roi_vertical_region_2_start = 0
        self.roi_vertical_region_2_end   = 0
        self.roi_vertical_region_3_start = 0
        self.roi_vertical_region_3_end   = 0
        self.linearity_coeffs            = []

        self.max_laser_power_mW          = 0
        self.min_laser_power_mW          = 0
        self.laser_power_coeffs          = []

        self.user_data                   = None

        self.bad_pixels                  = []
                                         
        self.buffers = []

    def write_all(self):
        pass

    def write_user_facing(self):
        pass

    def parse(self, buffers):
        if len(buffers) != 6:
            log.error("EEPROM.parse expects exactly 6 buffers")
            return

        # store these locally so self.unpack() can access them
        self.buffers = buffers

        # unpack all the fields we know about
        self.read_eeprom()

    # see https://docs.python.org/2/library/struct.html#format-characters
    def read_eeprom(self):

        # Page 0
        self.model                       = self.unpack((0,  0, 16), "s")
        self.serial_number               = self.unpack((0, 16, 16), "s")
        self.baud_rate                   = self.unpack((0, 32,  4), "i")
        self.has_cooling                 = self.unpack((0, 36,  1), "?")
        self.has_battery                 = self.unpack((0, 37,  1), "?")
        self.has_laser                   = self.unpack((0, 38,  1), "?")
        self.excitation_nm               = self.unpack((0, 39,  2), "h")
        self.slit_size_um                = self.unpack((0, 41,  2), "h")

        # Page 1
        self.wavelength_coeffs = []
        self.wavelength_coeffs     .append(self.unpack((1,  0,  4), "f"))
        self.wavelength_coeffs     .append(self.unpack((1,  4,  4), "f"))
        self.wavelength_coeffs     .append(self.unpack((1,  8,  4), "f"))
        self.wavelength_coeffs     .append(self.unpack((1, 12,  4), "f"))
        self.degC_to_dac_coeffs = []
        self.degC_to_dac_coeffs    .append(self.unpack((1, 16,  4), "f"))
        self.degC_to_dac_coeffs    .append(self.unpack((1, 20,  4), "f"))
        self.degC_to_dac_coeffs    .append(self.unpack((1, 24,  4), "f"))
        self.adc_to_degC_coeffs = []
        self.adc_to_degC_coeffs    .append(self.unpack((1, 32,  4), "f"))
        self.adc_to_degC_coeffs    .append(self.unpack((1, 36,  4), "f"))
        self.adc_to_degC_coeffs    .append(self.unpack((1, 40,  4), "f"))
        self.max_temp_degC               = self.unpack((1, 28,  2), "h")
        self.min_temp_degC               = self.unpack((1, 30,  2), "h")
        self.tec_r298                    = self.unpack((1, 44,  2), "h")
        self.tec_beta                    = self.unpack((1, 46,  2), "h")
        self.calibration_date            = self.unpack((1, 48, 12), "s")
        self.calibration_by              = self.unpack((1, 60,  3), "s")
                                    
        # Page 2                    
        self.detector                    = self.unpack((2,  0, 16), "s")
        self.active_pixels_horizontal    = self.unpack((2, 16,  2), "h")
        self.active_pixels_vertical      = self.unpack((2, 19,  2), "h") # MZ: skipped 18
        self.min_integration_time_ms     = self.unpack((2, 21,  2), "H")
        self.max_integration_time_ms     = self.unpack((2, 23,  2), "H")
        self.actual_horizontal           = self.unpack((2, 25,  2), "h")
        self.roi_horizontal_start        = self.unpack((2, 27,  2), "h") # not currently used
        self.roi_horizontal_end          = self.unpack((2, 29,  2), "h") # vvv
        self.roi_vertical_region_1_start = self.unpack((2, 31,  2), "h")
        self.roi_vertical_region_1_end   = self.unpack((2, 33,  2), "h")
        self.roi_vertical_region_2_start = self.unpack((2, 35,  2), "h")
        self.roi_vertical_region_2_end   = self.unpack((2, 37,  2), "h")
        self.roi_vertical_region_3_start = self.unpack((2, 39,  2), "h")
        self.roi_vertical_region_3_end   = self.unpack((2, 41,  2), "h")
        self.linearity_coeffs = []
        self.linearity_coeffs      .append(self.unpack((2, 43,  4), "f")) # overloading for secondary ADC
        self.linearity_coeffs      .append(self.unpack((2, 47,  4), "f"))
        self.linearity_coeffs      .append(self.unpack((2, 51,  4), "f"))
        self.linearity_coeffs      .append(self.unpack((2, 55,  4), "f"))
        self.linearity_coeffs      .append(self.unpack((2, 59,  4), "f"))

        # Page 3
        self.laser_power_coeffs = []
        self.laser_power_coeffs    .append(self.unpack((3, 12, 4), "f"))
        self.laser_power_coeffs    .append(self.unpack((3, 16, 4), "f"))
        self.laser_power_coeffs    .append(self.unpack((3, 20, 4), "f"))
        self.laser_power_coeffs    .append(self.unpack((3, 24, 4), "f"))
        self.max_laser_power_mW          = self.unpack((3, 28, 4), "f")
        self.min_laser_power_mW          = self.unpack((3, 32, 4), "f")

        # Page 4
        self.user_data = self.buffers[4]
        self.user_text = self.printable(self.user_data)

        # Page 5
        bad = set()
        for count in range(15):
            pixel = self.unpack((5, count * 2, 2), "h")
            if pixel != -1:
                bad.add(pixel)
        self.bad_pixels = list(bad)
        self.bad_pixels.sort()

    def dump(self):
        log.info("EEPROM settings:")
        log.info("  Model:            %s", self.model)
        log.info("  Serial Number:    %s", self.serial_number)
        log.info("  Baud Rate:        %d", self.baud_rate)
        log.info("  Has Cooling:      %s", self.has_cooling)
        log.info("  Has Battery:      %s", self.has_battery)
        log.info("  Has Laser:        %s", self.has_laser)
        log.info("  Excitation (nm):  %s", self.excitation_nm)
        log.info("  Slit size (um):   %s", self.slit_size_um)
        log.info("")
        log.info("  Wavecal coeff0:   %s", self.wavelength_coeffs[0])
        log.info("  Wavecal coeff1:   %s", self.wavelength_coeffs[1])
        log.info("  Wavecal coeff2:   %s", self.wavelength_coeffs[2])
        log.info("  Wavecal coeff3:   %s", self.wavelength_coeffs[3])
        log.info("  degCToDAC coeff0: %s", self.degC_to_dac_coeffs[0])
        log.info("  degCToDAC coeff1: %s", self.degC_to_dac_coeffs[1])
        log.info("  degCToDAC coeff2: %s", self.degC_to_dac_coeffs[2])
        log.info("  adcToDegC coeff0: %s", self.adc_to_degC_coeffs[0])
        log.info("  adcToDegC coeff1: %s", self.adc_to_degC_coeffs[1])
        log.info("  adcToDegC coeff2: %s", self.adc_to_degC_coeffs[2])
        log.info("  Det temp max:     %s", self.max_temp_degC)
        log.info("  Det temp min:     %s", self.min_temp_degC)
        log.info("  TEC R298:         %s", self.tec_r298)
        log.info("  TEC beta:         %s", self.tec_beta)
        log.info("  Calibration Date: %s", self.calibration_date)
        log.info("  Calibration By:   %s", self.calibration_by)
        log.info("")
        log.info("  Detector name:    %s", self.detector)
        log.info("  Active horiz:     %d", self.active_pixels_horizontal)
        log.info("  Active vertical:  %d", self.active_pixels_vertical)
        log.info("  Min integration:  %d ms", self.min_integration_time_ms)
        log.info("  Max integration:  %d ms", self.max_integration_time_ms)
        log.info("  Actual Horiz:     %d", self.actual_horizontal)
        log.info("  ROI Horiz Start:  %d", self.roi_horizontal_start)
        log.info("  ROI Horiz End:    %d", self.roi_horizontal_end)
        log.info("  ROI Vert Reg 1:   (%d, %d)", self.roi_vertical_region_1_start, self.roi_vertical_region_1_end)
        log.info("  ROI Vert Reg 2:   (%d, %d)", self.roi_vertical_region_2_start, self.roi_vertical_region_2_end)
        log.info("  ROI Vert Reg 3:   (%d, %d)", self.roi_vertical_region_3_start, self.roi_vertical_region_3_end)
        log.info("  Linearity Coeff0: %f", self.linearity_coeffs[0])
        log.info("  Linearity Coeff1: %f", self.linearity_coeffs[1])
        log.info("  Linearity Coeff2: %f", self.linearity_coeffs[2])
        log.info("  Linearity Coeff3: %f", self.linearity_coeffs[3])
        log.info("  Linearity Coeff4: %f", self.linearity_coeffs[4])
        log.info("")
        log.info("  Laser coeff0:     %s", self.laser_power_coeffs[0])
        log.info("  Laser coeff1:     %s", self.laser_power_coeffs[1])
        log.info("  Laser coeff2:     %s", self.laser_power_coeffs[2])
        log.info("  Laser coeff3:     %s", self.laser_power_coeffs[3])
        log.info("  Max Laser mW:     %s", self.max_laser_power_mW)
        log.info("  Min Laser mW:     %s", self.min_laser_power_mW)
        log.info("")
        log.info("  User Text:        %s", self.printable(self.user_text))
        log.info("")
        log.info("  Bad Pixels:       %s", self.bad_pixels)

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

    def unpack(self, address, data_type="s"):
        page       = address[0]
        start_byte = address[1]
        length     = address[2]
        end_byte   = start_byte + length

        buf = self.buffers[page]

        if data_type == "s":
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

        log.debug("Unpacked [%s]: %s", data_type, unpack_result)
        return unpack_result
