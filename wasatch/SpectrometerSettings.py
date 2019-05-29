import logging
from . import utils
import json
import math
import re

from .SpectrometerState import SpectrometerState
from .HardwareInfo      import HardwareInfo
from .FPGAOptions       import FPGAOptions
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

    def __init__(self, device_id=None):
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

    def is_imx(self):
        return "imx" in self.eeprom.detector.lower()

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

    def update_wavecal(self, coeffs=None):
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
            self.wavelengths = utils.generate_wavelengths(
                self.pixels(),
                self.eeprom.wavelength_coeffs[0],
                self.eeprom.wavelength_coeffs[1],
                self.eeprom.wavelength_coeffs[2],
                self.eeprom.wavelength_coeffs[3])

        if self.wavelengths is None:
            # this can happen on Stroker Protocol before/without .ini file,
            # or SiG with bad battery / corrupt EEPROM
            log.debug("no wavecal found - using pixel space")
            self.wavelengths = list(range(self.pixels()))

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
        for k in list(self.__dict__.keys()):
            v = getattr(self, k)
            if isinstance(v, EEPROM) or isinstance(v, FPGAOptions) or isinstance(v, SpectrometerState):
                tmp[k] = v.__dict__
            else:
                tmp[k] = v
        return json.dumps(tmp, indent=4, sort_keys=True, default=str)

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
