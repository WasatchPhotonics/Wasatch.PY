import logging
import utils
import json
import math
import re

from SpectrometerState import SpectrometerState
from FPGAOptions       import FPGAOptions
from EEPROM            import EEPROM

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

    def __init__(self, uuid=None):
        # populate this if the settings came from a real device
        self.uuid = uuid

        # volatile state
        self.state = SpectrometerState()

        # For consistency, consider adding a class FPGARegisters here for writable 
        # settings like ccd_gain, ccd_offset etc which aren't naturally supported
        # by on-screen widgets like integration time.

        # permanent attributes
        self.microcontroller_firmware_version = None
        self.fpga_firmware_version = None
        self.fpga_options = FPGAOptions()

        # semi-permanent attributes
        self.eeprom = EEPROM()

        # derived attributes
        self.wavelengths = None
        self.wavenumbers = None

        self.update_wavecal()

    # given a JSON-formatted string, parse and apply FPGAOptions and EEPROM
    # sections if available
    def update_from_json(self, s):
        log.debug("updating SpectrometerSettings from JSON: %s", s)
        obj = json.loads(s)
        if 'FPGAOptions' in obj:
            utils.update_obj_from_dict(self.fpga_options, obj['FPGAOptions'])
        if 'EEPROM' in obj:
            utils.update_obj_from_dict(self.eeprom, obj['EEPROM'])
            self.update_wavecal()

    # ##########################################################################
    # accessors
    # ##########################################################################

    def pixels(self):
        return self.eeprom.active_pixels_horizontal

    def excitation(self):
        old = self.eeprom.excitation_nm
        new = self.eeprom.excitation_nm_float

        # if 'new' looks corrupt or not populated, use old
        if new is None or math.isnan(new):
            return old

        # if 'new' looks like a reasonable value, use it
        if 200 <= new and new <= 2500:
            return new

        # if 'new' looks bizarrely out-of-range, use old
        log.debug("excitation wavelength %f outside (200, 2500) - suspect corrupt EEPROM, using %f", new, old)
        return old

    def isIMX(self):
        return "imx" in self.eeprom.detector.lower()
    
    def default_detector_setpoint_degC(self):
        # newer units should specify this via EEPROM
        if False and self.eeprom.format >= 4:
            return self.eeprom.startup_temp_degC

        # otherwise infer from detector
        det = self.eeprom.detector.upper()
        if "S11511" in det:
            return 10
        elif "S10141" in det:
            return -15 
        elif "G9214" in det:
            return -15 
        elif "7031" in det:
            # reviewed by Caleb "Cookie" Carden
            return -15 

        return None

    # ##########################################################################
    # methods
    # ##########################################################################

    def update_wavecal(self, coeffs=None):
        if coeffs is None:
            coeffs = self.eeprom.wavelength_coeffs
        else:
            self.eeprom.wavelength_coeffs = coeffs

        if coeffs:
            self.wavelengths = utils.generate_wavelengths(
                self.pixels(), 
                self.eeprom.wavelength_coeffs[0], 
                self.eeprom.wavelength_coeffs[1], 
                self.eeprom.wavelength_coeffs[2], 
                self.eeprom.wavelength_coeffs[3])
        else:
            # this can happen on Stroker Protocol before/without .ini file
            log.debug("no wavecal found - using pixel space")
            self.wavelengths = range(self.pixels())

        log.debug("generated %d wavelengths from %.2f to %.2f", 
            len(self.wavelengths), self.wavelengths[0], self.wavelengths[-1])

        # keep legacy excitation in sync with floating-point
        if self.excitation() > 0:
            self.eeprom.excitation_nm = float(round(self.excitation(), 0))

        if self.excitation() > 0:
            self.wavenumbers = utils.generate_wavenumbers(self.excitation(), self.wavelengths)
            log.debug("generated %d wavenumbers from %.2f to %.2f", 
                len(self.wavenumbers), self.wavenumbers[0], self.wavenumbers[-1])

    def is_InGaAs(self):
        if re.match('ingaas|g9214', self.eeprom.detector.lower()):
            return True
        elif self.fpga_options.has_cf_select:
            return True
        return False

    # probably a simpler way to do this...
    def toJSON(self):
        tmp = {}
        for k in self.__dict__.keys():
            v = getattr(self, k)
            if isinstance(v, EEPROM) or isinstance(v, FPGAOptions) or isinstance(v, SpectrometerState):
                tmp[k] = v.__dict__
            else:
                tmp[k] = v
        return json.dumps(tmp, indent=4, sort_keys=True, default=str)
                
    def dump(self):
        log.info("SpectrometerSettings:")
        log.info("  UUID = %s", self.uuid)
        log.info("  Microcontroller Firmware Version = %s", self.microcontroller_firmware_version)
        log.info("  FPGA Firmware Version = %s", self.fpga_firmware_version)

        if self.wavelengths:
            log.info("  Wavelengths = (%.2f, %.2f)", self.wavelengths[0], self.wavelengths[-1])
        else:
            log.info("  Wavelengths = None")

        if self.wavenumbers:
            log.info("  Wavenumbers = (%.2f, %.2f)", self.wavenumbers[0], self.wavenumbers[-1])
        else:
            log.info("  Wavenumbers = None")

        if self.state:
            self.state.dump()

        if self.fpga_options:
            self.fpga_options.dump()

        if self.eeprom:
            self.eeprom.dump()
