import logging
import numpy as np
import json
import math
import re
import os

from datetime import datetime
from . import utils

from .FirmwareRequirements import FirmwareRequirements
from .SpectrometerState    import SpectrometerState
from .MockUSBDevice        import MockUSBDevice
from .RealUSBDevice        import RealUSBDevice
from .HardwareInfo         import HardwareInfo
from .FPGAOptions          import FPGAOptions
from .DeviceID             import DeviceID
from .EEPROM               import EEPROM
from .ROI                  import ROI

log = logging.getLogger(__name__)

##
# Encapsulate a spectrometer's state, including compiled firmware (FPGAOptions),
# non-volatile configuration (EEPROM) and volatile state (SpectrometerState).
#
# This class serves two goals:
#
# 1. A picklable object that can be passed between the spectrometer process and
#    the GUI, containing everything the GUI might need to know in convenient
#    form.
#
# 2. A place where the GUI can store settings of MANY different connected
#    spectrometers, and quickly switch between them.
#
class SpectrometerSettings:

    ##
    # @param device_id (Input) where the spectrometer was found (optional)
    # @param d         (Input) input dictionary (optional)
    def __init__(self, device_id=None, d=None):
        # populate this if the settings came from a real device
        self.device_id = device_id

        # volatile state
        self.state = SpectrometerState()

        # permanent attributes
        self.microcontroller_firmware_version = None
        self.fpga_firmware_version = None
        self.detector_serial_number = None          # Andor
        self.microcontroller_serial_number = None   # STM32H7
        self.ble_firmware_version = None
        self.fpga_options = FPGAOptions()

        # semi-permanent attributes
        self.eeprom = EEPROM()

        # expose some hardware attributes upstream (let ENLIGHTEN know if device
        # supports triggering etc)
        self.hardware_info = None
        if self.device_id is not None:
            if self.device_id.is_usb():
                self.hardware_info = HardwareInfo(vid = self.device_id.vid,
                                                  pid = self.device_id.pid)
        # derived attributes
        self.wavelengths = None
        self.wavenumbers = None
        self.raman_intensity_factors = None
        self.linear_pixel_calibration = None

        self.lock_wavecal = False

        self.update_wavecal()
        self.update_raman_intensity_factors()

        # ENLIGHTEN sends this so individual driver processes can adaptively scale USB timeouts
        self.num_connected_devices = 1

        if d is not None:
            self.load_from_dict(d)

        self.firmware_requirements = FirmwareRequirements(self)

    def set_num_connected_devices(self, n):
        self.num_connected_devices = n

    # given a JSON-formatted string, parse and apply FPGAOptions and EEPROM
    # sections if available
    def update_from_json(self, s):
        log.debug("updating SpectrometerSettings from JSON: %s", s)
        d = json.loads(s)
        self.load_from_dict(d)

    ##
    # Assuming that we've loaded a Measurement from JSON, or received a
    # Measurement-like structure externally via JSON, update whatever we can
    # from it.
    def load_from_dict(self, d):
        utils.update_obj_from_dict(self.fpga_options, utils.dict_get_norm(d, "FPGAOptions"))
        utils.update_obj_from_dict(self.state,        utils.dict_get_norm(d, ["SpectrometerState", "State"]))

        d2 = utils.dict_get_norm(d, "EEPROM")
        if d2 is not None:
            utils.update_obj_from_dict(self.eeprom, d2)
            self.update_wavecal()
            self.update_raman_intensity_factors()

        a = utils.dict_get_norm(d, "wavelengths")
        if a is not None:
            self.wavelengths = a

        a = utils.dict_get_norm(d, "wavenumbers")
        if a is not None:
            self.wavenumbers = a

    # ##########################################################################
    # accessors
    # ##########################################################################

    ##
    # Originally model names fit within the 16-char EEPROM field of that name.
    # Now that we're extending model names to 30 characters, append the value
    # of EEPROM.productConfiguration if non-empty.
    def full_model(self):
        if self.eeprom.model is None:
            return ''
        a = self.eeprom.model.strip()
        b = self.eeprom.product_configuration

        if b is None:
            return a
        else:
            return a + b.strip()

    def pixels(self):
        return self.eeprom.active_pixels_horizontal

    def excitation(self):
        return self.eeprom.multi_wavelength_calibration.get("excitation_nm_float", default=0.0)

    def is_mml(self): # -> bool 
        if not self.eeprom.has_laser:
            return False
        if self.eeprom.has_laser and not self.is_sml():
            return True

    def is_sml(self): # -> bool 
        if not self.eeprom.has_laser:
            return False
        elif (self.eeprom.has_laser and 
              self.eeprom.max_laser_power_mW >= 95 and 
              self.eeprom.max_laser_power_mW <= 120):
            return True
        else:
            return False

    def has_excitation(self):
        return self.excitation() > 0

    def has_vertical_roi(self): 
        roi = self.get_vertical_roi()
        if roi is None:
            return False

        height = self.eeprom.active_pixels_vertical
        return roi.start < roi.stop and roi.start >= 0 and roi.stop < height

    def get_vertical_roi(self):
        calibration = self.eeprom.multi_wavelength_calibration.selected_calibration
        if calibration == 0:
            return ROI(self.eeprom.roi_vertical_region_1_start, self.eeprom.roi_vertical_region_1_end)
        elif calibration == 1:
            return ROI(self.eeprom.roi_vertical_region_2_start, self.eeprom.roi_vertical_region_2_end)
        elif calibration == 2:
            return ROI(self.eeprom.roi_vertical_region_3_start, self.eeprom.roi_vertical_region_3_end)
        else:
            log.error(f"get_vertical_roi: invalid calibration {calibration}")

    def default_detector_setpoint_degC(self):
        
        # newer units should specify this via EEPROM
        if self.eeprom.format >= 4:
            log.debug("default_detector_setpoint_degC: eeprom.format %d so using startup_temp_degC %d",
                self.eeprom.format, self.eeprom.startup_temp_degC)
            return self.eeprom.startup_temp_degC

        # otherwise infer from detector
        det = self.eeprom.detector.upper()
        degC = None
        if   "11511" in det: degC =  10
        elif "16011" in det: degC =  10
        elif "13971" in det: degC =  10
        elif "10141" in det: degC = -15
        elif "9214"  in det: degC = -15
        elif "7031"  in det: degC = -15

        if degC is not None:
            log.debug("default_detector_setpoint_degC: defaulting to %d per supported detector %s", degC, det)
            return degC

        log.error("default_detector_setpoint_degC: serial %s has unknown detector %s",
            self.eeprom.serial_number, det)
        return None

    # ##########################################################################
    # methods
    # ##########################################################################

    def calibrations(self):
        if self.eeprom.subformat != 5:
            return 1
        return self.eeprom.multi_wavelength_calibration.calibrations

    def select_calibration(self, calibration):
        self.eeprom.multi_wavelength_calibration.selected_calibration = calibration
        self.update_wavecal()
        self.update_raman_intensity_factors()

    def update_raman_intensity_factors(self):
        """
        @todo Note that WasatchNET.Util.applyRamanCorrection() only generates 
              factors from (roiStart, roiEnd), whereas this function generates
              them for the whole detector. They're only valid within the ROI,
              and should only be applied within the ROI, so this is generating
              more than we need (wasting memory and risking bugs).
        """
        self.raman_intensity_factors = None
        if not self.eeprom.has_raman_intensity_calibration():
            return
        if 1 <= self.eeprom.raman_intensity_calibration_order <= EEPROM.MAX_RAMAN_INTENSITY_CALIBRATION_ORDER:
            coeffs = self.eeprom.multi_wavelength_calibration.get("raman_intensity_coeffs")
            if coeffs is not None:
                try:
                    # probably faster numpy way to do this
                    factors = []
                    for pixel in range(self.pixels()):
                        log10_factor = 0.0
                        for i in range(len(coeffs)):
                            x_to_i = math.pow(pixel, i)
                            scaled = coeffs[i] * x_to_i
                            log10_factor += scaled
                        expanded = math.pow(10, log10_factor)
                        factors.append(expanded)
                    self.raman_intensity_factors = np.array(factors, dtype=np.float64)
                except:
                    log.error("exception generating Raman intensity factors (coeffs %s)", coeffs, exc_info=1)
                    self.raman_intensity_factors = None

    def set_linear_pixel_calibration(self, data):
        self.linear_pixel_calibration = None
        try:
            slopes = data[0]
            offsets = data[1]
            if len(slopes) != len(offsets) or len(slopes) != self.pixels():
                raise ValueError("length mismatch: slopes {len(slopes)}, offsets {len(offsets)}, pixels {self.pixels()}")
            self.linear_pixel_calibration = (slopes, offsets)
        except:
            log.error("set_linear_pixel_calibration requires 2-element tuple, " +
                      "where first element is array of slopes and second of " +
                      "offsets, both lengths matching the detector pixel count", exc_info=1)
            return

    def set_wavenumber_correction(self, cm):
        self.state.wavenumber_correction = cm
        self.update_wavecal()

    def get_wavecal_coeffs(self):
        return self.eeprom.multi_wavelength_calibration.get("wavelength_coeffs", default=[0, 1, 0, 0, 0])

    def set_wavecal_coeffs(self, coeffs):
        self.eeprom.multi_wavelength_calibration.set("wavelength_coeffs", coeffs)

    def update_wavecal(self, coeffs=None):
        if self.lock_wavecal:
            log.debug("wavecal is locked")
            return

        self.wavelengths = None
        self.wavenumbers = None

        if self.pixels() < 1:
            log.error("no pixels defined, cannot generate wavecal")
            return

        if coeffs is None:
            coeffs = self.get_wavecal_coeffs()
        else:
            self.set_wavecal_coeffs(coeffs)

        self.wavelengths = utils.generate_wavelengths(self.pixels(), coeffs)

        if self.wavelengths is None or self.wavelengths[0] == self.wavelengths[-1]:
            # this can happen on Stroker Protocol before/without .ini file,
            # or SiG with bad battery / corrupt EEPROM, or Andor without config file
            log.debug("no wavecal found - using pixel space")
            self.wavelengths = list(range(self.pixels()))

        log.debug("generated %d wavelengths from %.2f to %.2f",
            len(self.wavelengths), self.wavelengths[0], self.wavelengths[-1])

        if self.has_excitation():
            excitation = self.excitation()
            self.eeprom.excitation_nm = float(round(excitation, 0)) # keep legacy excitation in sync with floating-point
            self.wavenumbers = utils.generate_wavenumbers(excitation, self.wavelengths,
                wavenumber_correction=self.state.wavenumber_correction)
            log.debug("generated %d wavenumbers from %.2f to %.2f (after correction %.2f) using excitation %.3f",
                len(self.wavenumbers), self.wavenumbers[0], self.wavenumbers[-1], self.state.wavenumber_correction, excitation)

    # ##########################################################################
    #
    # We're kind of using SpectrometerSettings as a "universal interface" for
    # applications to query for things, so let's just consolidate some obvious
    # and common checks here (even if wrapping calls elsewhere).
    #
    # ##########################################################################

    def is_arm(self): # -> bool 
        return self.hardware_info is not None and self.hardware_info.is_arm()

    def is_ingaas(self): # -> bool 
        if self.hardware_info is not None and self.hardware_info.is_ingaas():
            return True
        elif self.eeprom is None or self.eeprom.detector is None:
            return False
        elif re.match(r'ingaas|g9214|g9206|g14237|du490', self.eeprom.detector.lower()):
            return True
        elif not self.is_arm() and (self.fpga_options is not None and self.fpga_options.has_cf_select):
            # new SiG ARM code removes GET_FPGA_COMPILATION_OPTIONS and therefore returns all 0xff for unsupported register
            return True
        return False

    def is_imx(self):
        return self.eeprom is not None and \
               self.eeprom.detector is not None and \
               "imx" in self.eeprom.detector.lower()

    def is_imx385(self):
        return self.is_imx() and "imx385" in self.eeprom.detector.lower()

    def is_imx392(self): # -> bool 
        return self.is_imx() and "imx392" in self.eeprom.detector.lower()

    def is_spi(self): # -> bool 
        return self.hardware_info is not None and \
               self.hardware_info.pid == 0x6014

    def is_micro(self): # -> bool 
        return ( self.is_arm() and (
                   self.is_imx() or
                   "micro" in self.full_model().lower() or
                   "sig"   in self.full_model().lower() or
                   "xs"    in self.full_model().lower()
                 )
               ) or self.is_spi()

    def is_non_raman(self): # -> bool 
        return not self.has_excitation()

    def is_gen15(self): # -> bool 
        if "DISABLE_GEN15" in os.environ:
            return False
        return self.eeprom.gen15

    def is_gen2(self): # -> bool 
        return False

    ## @todo add this to EEPROM.feature_mask if we decide to keep the feature
    def has_marker(self): # -> bool 
        return self.eeprom.model == "WPX-8CHANNEL"

    def is_andor(self): # -> bool 
        return '0x136e' in str(self.device_id)

    def is_sig(self): # -> bool 
        return self.is_micro()

    def is_xs(self): # -> bool 
        return self.is_micro()

    def supports_feature(self, feature):
        return self.firmware_requirements.supports(feature)

    # ##########################################################################
    # serialization
    # ##########################################################################

    # probably a simpler way to do this...
    def to_dict(self):
        d = {}
        for k, v in self.__dict__.items():
            if k in ["eeprom_backup"]:
                continue # skip these

            if isinstance(v, (DeviceID, EEPROM, FPGAOptions, SpectrometerState, HardwareInfo, RealUSBDevice, MockUSBDevice, datetime)):
                o = v.to_dict()
            elif isinstance(v, np.ndarray):
                o = v.tolist()
            else:
                o = v

            d[k] = o
        return d

    def to_json(self):
        d = dict(self)
        return json.dumps(d, indent=4, sort_keys=True, default=str)

    def dump(self):
        log.debug("SpectrometerSettings:")
        log.debug("  DeviceID = %s", self.device_id)
        log.debug("  Microcontroller Firmware Version = %s", self.microcontroller_firmware_version)
        log.debug("  FPGA Firmware Version = %s", self.fpga_firmware_version)

        if self.wavelengths is not None:
            log.debug("  Wavelengths = (%.2f, %.2f)", self.wavelengths[0], self.wavelengths[-1])
        else:
            log.debug("  Wavelengths = None")

        if self.wavenumbers is not None:
            log.debug("  Wavenumbers = (%.2f, %.2f)", self.wavenumbers[0], self.wavenumbers[-1])
        else:
            log.debug("  Wavenumbers = None")

        if self.state:
            self.state.dump()

        if self.fpga_options:
            self.fpga_options.dump()

        if self.eeprom:
            self.eeprom.dump()
