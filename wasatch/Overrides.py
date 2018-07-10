import logging
import json

log = logging.getLogger(__name__)

class Overrides(object):
    """
        Theory of Operation
        ===================

        Say we want to control an experimental spectrometer, which operates 
        more-or-less like our standard units, except that a couple settings 
        need to be handled uniquely.  
        
        An elegant solution would be to give the new device a custom PID, then 
        extend a new UniqueSpectrometer from FeatureIdentificationDevice and 
        simply overload the custom behavior. And probably there will be cases 
        where we want to do that.  Before going there, I'd really want to setup 
        an ABC heirarchy over both FID and SP devices.

        However, that approach means that any tweaking of the custom behavior
        would have to be in Python source code. That means a new ENLIGHTEN 
        installer would need to be built for each test. It also means the custom
        code would presumably appear in Wasatch.PY, an open-source project.

        So, another option is to simply provide an "overrides" framework which
        allows the runtime user to point to an external JSON file containing the
        custom opcodes and byte streams to be passed into the spectrometer. This
        is less elegant and more error-prone, but it provides more power and
        flexibility to the operator, all without requiring continuous minute 
        changes to ENLIGHTEN or Wasatch.PY.

        For this particular instance, we wanted to let the user take explicit 
        control of setting the integration time of a custom sensor.  Two opcodes
        are being provided in the firmware to read and write arbitrary octet 
        strings over I2C.
    """
    
    def __init__(self, pathname):
        self.pathname = pathname

        self.description = None
        self.startup = None
        self.min_delay_us = 0
        self.settings = {}

        if self.pathname:
            try:
                self.load()
            except:
                log.error("Could not load and parse %s", self.pathname, exc_info=1)

    def empty(self):
        return len(self.settings) == 0

    def has_override(self, setting):
        return setting in self.settings

    # for case where "15" is an override, but "15.0" is not
    def normalized_value(self, setting, value):
        if not self.has_override(setting):
            return None

        if str(value) in self.settings[setting]:
            return value

        if isinstance(value, float):
            if value - int(value) == 0.0:
                if str(int(value)) in self.settings[setting]:
                    return int(value)

        return None

    def valid_value(self, setting, value):
        if not self.has_override(setting):
            return False

        if str(value) in self.settings[setting]:
            return True

        if self.normalized_value(setting, value) is not None:
            return True

        return False

    def get_override(self, setting, value):
        if not self.valid_value(setting, value):
            return None

        normalized = self.normalized_value(setting, value)
        return self.settings[setting][str(normalized)]
    
    def load(self):
        log.debug("loading %s", self.pathname)
        with open(self.pathname) as infile:
            config = json.loads(infile.read())
            
        for k in ["description", "startup"]:
            if k in config:
                setattr(self, k, config[k])
                del(config[k])

        k = "min_delay_us"
        if k in config:
            self.min_delay_us = int(config[k])
            del(config[k])

        # any remaining keys are assumed to be settings which ENLIGHTEN
        # would normally pass to WasatchDeviceWrapper
        for setting in sorted(config.keys()):
            self.settings[setting] = {}
            values = sorted(config[setting].keys())
            for value in values:
                self.settings[setting][value] = config[setting][value]

        log.debug("loaded overrides for settings: %s", sorted(self.settings.keys()))
        log.debug("full overrides: %s", self.__dict__)
