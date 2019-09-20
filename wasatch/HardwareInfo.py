
##
# This class captures aspects of the hardware which normally ENLIGHTEN can't / 
# shouldn't see, which isn't otherwise reliably inferred from EEPROM, ModelInfo 
# etc.
class HardwareInfo(object):

    def __init__(self, vid=None, pid=None):
        self.vid = vid
        self.pid = pid
    
    def is_ingaas(self):
        return self.pid == 0x2000

    def is_arm(self):
        return self.pid == 0x4000

    ##
    # I can't think of another way to determine whether or not a spectrometer 
    # supports this feature other than via its USB PID (ergo architecture), 
    # which isn't something ENLIGHTEN should be using in business logic.
    def supports_triggering(self):
        return self.is_arm()

    ## 
    # Maybe we don't need this as it's kind of logically available from these other
    # two sources, but provided for completeness.
    #
    # @see FPGAOptions.has_cf_select
    # @see enlighten.ModelInfo.has_high_gain_mode
    def supports_high_gain_mode(self):
        return self.is_ingaas()

    def to_dict(self):
        return self.__dict__
