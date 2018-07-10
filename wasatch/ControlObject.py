class ControlObject(object):
    """ A class representing commands sent from ENLIGHTEN to the spectrometer.
        
        Essentially just a name-value pair of strings.  Compare to StatusMessage
        as an outbound (spectrometer subprocess -> ENLIGHTEN) counterpart.

        There is no enumeration of supported settings, but see 
        FeatureIdentificationDevice.write_setting for a representative list.
    """

    def __init__(self, setting, value):
        self.setting = setting
        self.value = value
