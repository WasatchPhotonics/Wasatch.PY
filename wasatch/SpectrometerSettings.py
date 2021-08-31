import logging
import numpy as np
import json
import math
import re

from . import utils

from .SpectrometerState import SpectrometerState
from .HardwareInfo      import HardwareInfo
from .FPGAOptions       import FPGAOptions
from .DeviceID          import DeviceID
from .EEPROM            import EEPROM

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
class SpectrometerSettings(object):

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

        self.lock_wavecal = False

        self.update_wavecal()
        self.update_raman_intensity_factors()

        # ENLIGHTEN sends this so individual driver processes can adaptively scale USB timeouts
        self.num_connected_devices = 1

        if d is not None:
            self.load_from_dict(d)

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
            log.debug("SS.load_from_dict: assigned wavelengths")

        a = utils.dict_get_norm(d, "wavenumbers")
        if a is not None:
            self.wavenumbers = a
            log.debug("SS.load_from_dict: assigned wavenumbers")

    # ##########################################################################
    # accessors
    # ##########################################################################

    ##
    # Originally model names fit within the 16-char EEPROM field of that name.
    # Now that we're extending model names to 30 characters, append the value
    # of EEPROM.productConfiguration if non-empty.
    def full_model(self):
        a = self.eeprom.model.strip()
        b = self.eeprom.product_configuration

        if b is None:
            return a
        else:
            return a + b.strip()

    def pixels(self):
        if self.state.detector_regions is None:
            return self.eeprom.active_pixels_horizontal
        else:
            return self.state.detector_regions.total_pixels()

    def excitation(self):
        old = float(self.eeprom.excitation_nm)
        new = self.eeprom.excitation_nm_float

        # if 'new' looks corrupt or not populated, use old
        if new is None or math.isnan(new):
            return old

        # if 'new' looks like a reasonable value, use it
        if 200 <= new and new <= 2500:
            return new

        # if 'new' value is unreasonable AND NON-ZERO, complain
        if new != 0.0:
            log.debug("excitation wavelength %e outside (200, 2500) - suspect corrupt EEPROM, using %e", new, old)

        return old

    def has_excitation(self):
        return self.excitation() > 0

    def has_vertical_roi(self):
        start = self.eeprom.roi_vertical_region_1_start
        stop  = self.eeprom.roi_vertical_region_1_end
        height = self.eeprom.active_pixels_vertical
        return start < stop and start >= 0 and stop < height

    ## @todo return ROI
    def get_vertical_roi(self):
        if self.has_vertical_roi():
            return (self.eeprom.roi_vertical_region_1_start, 
                    self.eeprom.roi_vertical_region_1_end)

    ##
    # @note S11510 is ambient so shouldn't have a TEC
    def default_detector_setpoint_degC(self):
        log.debug("default_detector_setpoint_degC: here")

        # newer units should specify this via EEPROM
        if self.eeprom.format >= 4:
            log.debug("default_detector_setpoint_degC: eeprom.format %d so using startup_temp_degC %d",
                self.eeprom.format, self.eeprom.startup_temp_degC)
            return self.eeprom.startup_temp_degC

        # otherwise infer from detector
        det = self.eeprom.detector.upper()
        degC = None
        if   "S11511" in det: degC =  10
        elif "S10141" in det: degC = -15
        elif "G9214"  in det: degC = -15
        elif "7031"   in det: degC = -15

        if degC is not None:
            log.debug("default_detector_setpoint_degC: defaulting to %d per supported detector %s",
                degC, det)
            return degC

        log.error("default_detector_setpoint_degC: serial %s has unknown detector %s",
            self.eeprom.serial_number, det)
        return None

    # ##########################################################################
    # methods
    # ##########################################################################

    def update_raman_intensity_factors(self):
        self.raman_intensity_factors = None
        if not self.eeprom.has_raman_intensity_calibration():
            return
        if 1 <= self.eeprom.raman_intensity_calibration_order <= EEPROM.MAX_RAMAN_INTENSITY_CALIBRATION_ORDER:
            log.debug("updating raman intensity factors")
            coeffs = self.eeprom.raman_intensity_coeffs
            log.debug("coeffs = %s", coeffs)
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
                            #log.debug("pixel %4d: px_to_i %e, coeff[%d] %e, scaled %e, log10_factor %e", pixel, x_to_i, i, coeffs[i], scaled, log10_factor)
                        expanded = math.pow(10, log10_factor)
                        #log.debug("pixel %4d: expanded = %e", pixel, expanded)
                        factors.append(expanded)
                    self.raman_intensity_factors = np.array(factors, dtype=np.float64)
                except:
                    log.error("exception generating Raman intensity factors (coeffs %s)", coeffs, exc_info=1)
                    self.raman_intensity_factors = None
        log.debug("factors = %s", self.raman_intensity_factors)

    def set_wavenumber_correction(self, cm):
        self.state.wavenumber_correction = cm
        self.update_wavecal()

    ##
    # @todo update for DetectorRegions
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
            coeffs = self.eeprom.wavelength_coeffs
        else:
            self.eeprom.wavelength_coeffs = coeffs

        if coeffs:
            self.wavelengths = utils.generate_wavelengths(self.pixels(), self.eeprom.wavelength_coeffs)

        if self.wavelengths is None:
            # this can happen on Stroker Protocol before/without .ini file,
            # or SiG with bad battery / corrupt EEPROM
            log.debug("no wavecal found - using pixel space")
            self.wavelengths = list(range(self.pixels()))

        log.debug("generated %d wavelengths from %.2f to %.2f",
            len(self.wavelengths), self.wavelengths[0], self.wavelengths[-1])

        # keep legacy excitation in sync with floating-point
        if self.has_excitation():
            self.eeprom.excitation_nm = float(round(self.excitation(), 0))
            self.wavenumbers = utils.generate_wavenumbers(self.excitation(), self.wavelengths,
                wavenumber_correction=self.state.wavenumber_correction)
            log.debug("generated %d wavenumbers from %.2f to %.2f (after correction %.2f)",
                len(self.wavenumbers), self.wavenumbers[0], self.wavenumbers[-1], self.state.wavenumber_correction)

    # ##########################################################################
    #
    # We're kind of using SpectrometerSettings as a "universal interface" for 
    # applications to query for things, so let's just consolidate some obvious
    # and common checks here (even if wrapping calls elsewhere).
    #
    # ##########################################################################

    def is_arm(self):
        return self.hardware_info is not None and self.hardware_info.is_arm()

    def is_ingaas(self):
        if self.hardware_info is not None and self.hardware_info.is_ingaas():
            log.debug("is_ingaas TRUE because hardware_info")
            return True
        elif self.eeprom is None or self.eeprom.detector is None:
            log.debug("is_ingaas FALSE because missing EEPROM or detector")
            return False 
        elif re.match(r'ingaas|g9214|g9206|g14237', self.eeprom.detector.lower()):
            log.debug("is_ingaas TRUE because detector")
            return True
        elif self.fpga_options is not None and self.fpga_options.has_cf_select:
            log.debug("is_ingaas TRUE because has_cf_select")
            return True
        # log.debug("is_ingaas FALSE by default")
        return False

    def is_imx(self):
        return "imx" in self.eeprom.detector.lower()

    def is_imx392(self):
        return "imx392" in self.eeprom.detector.lower()

    def is_micro(self):
        return self.is_arm() and \
               ( self.is_imx() or \
                 "micro" in self.full_model().lower() or \
                 "sig"   in self.full_model().lower() )

    def is_non_raman(self):
        return not self.has_excitation()

    def is_gen15(self):
        return self.eeprom.gen15

    def is_gen2(self):
        return False

    ## @todo add this to EEPROM.feature_mask if we decide to keep the feature
    def has_marker(self):
        return self.eeprom.model == "WPX-8CHANNEL"

    def is_sig(self):
        return self.is_micro()

    # probably a simpler way to do this...
    def to_dict(self):
        d = {}
        for k, v in self.__dict__.items():
            if k in ["eeprom_backup"]:
                continue # skip these

            if isinstance(v, (DeviceID, EEPROM, FPGAOptions, SpectrometerState, HardwareInfo)):
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

        if self.wavelengths:
            log.debug("  Wavelengths = (%.2f, %.2f)", self.wavelengths[0], self.wavelengths[-1])
        else:
            log.debug("  Wavelengths = None")

        if self.wavenumbers:
            log.debug("  Wavenumbers = (%.2f, %.2f)", self.wavenumbers[0], self.wavenumbers[-1])
        else:
            log.debug("  Wavenumbers = None")

        if self.state:
            self.state.dump()

        if self.fpga_options:
            self.fpga_options.dump()

        if self.eeprom:
            self.eeprom.dump()
