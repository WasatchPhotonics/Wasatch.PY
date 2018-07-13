##
# A class holding setting-value pairs sent from ENLIGHTEN to the spectrometer.
# Compare to StatusMessage as an outbound (spectrometer subprocess -> ENLIGHTEN) counterpart.
# 
# There is no enumeration of supported settings, but CommandSettings
# is an (unused) step in that direction.  For now, the true master list
# would be the set implemented by FeatureIdentificationDevice.write_setting.
class ControlObject(object):
    def __init__(self, setting, value):
        self.setting = setting
        self.value = value
