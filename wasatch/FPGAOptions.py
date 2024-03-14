import logging

log = logging.getLogger(__name__)

INTEG_TIME_RES_ONE_MS           = 0
INTEG_TIME_RES_TEN_MS           = 1
INTEG_TIME_RES_SWITCHABLE       = 2 
                                
DATA_HEADER_NONE                = 0
DATA_HEADER_OCEAN_OPTICS        = 1
DATA_HEADER_WASATCH             = 2
                                
LASER_TYPE_NONE                 = 0
LASER_TYPE_INTERNAL             = 1
LASER_TYPE_EXTERNAL             = 2
                                
LASER_CONTROL_MODULATION        = 0
LASER_CONTROL_TRANSITION_POINTS = 1
LASER_CONTROL_RAMPING           = 2

##
# Encapsulate the set of options used to compile the FPGA code
# in the firmware of the connected spectrometer.  
#
# This class is normally accessed as an attribute of SpectrometerSettings.
class FPGAOptions:

    def __init__(self):
        self.integration_time_resolution = INTEG_TIME_RES_ONE_MS
        self.data_header                 = DATA_HEADER_NONE
        self.has_cf_select               = False
        self.laser_type                  = LASER_TYPE_NONE
        self.laser_control               = LASER_CONTROL_MODULATION
        self.has_area_scan               = False
        self.has_actual_integ_time       = False
        self.has_horiz_binning           = False

    ## 
    # Parse the given 24-bit register according to the following representation:
    #
    # @verbatim
    #   0-2: IntegrationTimeResolution
    #   3-5: DataHeader
    #     6: HasCFSelect
    #   7-8: LaserType
    #  9-11: LaserControl
    #    12: HasAreaScan
    #    13: HasActualIntegTime
    #    14: HasHorizBinning
    # 23-15: Reserved
    # @endverbatim
    def parse(self, word):
        if word is None:
            log.error("can't parse NULL word")
            return

        self.integration_time_resolution = (word & 0x0007)
        self.data_header                 = (word & 0x0038) >> 3
        self.has_cf_select               = (word & 0x0040) != 0
        self.laser_type                  = (word & 0x0180) >> 7
        self.laser_control               = (word & 0x0e00) >> 9
        self.has_area_scan               = (word & 0x1000) != 0
        self.has_actual_integ_time       = (word & 0x2000) != 0
        self.has_horiz_binning           = (word & 0x4000) != 0

        self.dump()

    ## log the parsed values
    def dump(self):
        log.debug("FPGA Compilation Options:")
        log.debug("  integration time resolution = %s", self.stringify_resolution())
        log.debug("  data header                 = %s", self.stringify_header())
        log.debug("  has cf select               = %s", self.has_cf_select)
        log.debug("  laser type                  = %s", self.stringify_laser_type())
        log.debug("  laser control               = %s", self.stringify_laser_control())
        log.debug("  has area scan               = %s", self.has_area_scan)
        log.debug("  has actual integ time       = %s", self.has_actual_integ_time)
        log.debug("  has horiz binning           = %s", self.has_horiz_binning)

    def stringify_resolution(self):
        v = self.integration_time_resolution
        if v == INTEG_TIME_RES_ONE_MS:
            return "1ms"
        elif v == INTEG_TIME_RES_TEN_MS:
            return "10ms"
        elif v == INTEG_TIME_RES_SWITCHABLE:
            return "Switchable"
        else:
            return "ERROR"

    def stringify_header(self):
        v = self.data_header
        if v == DATA_HEADER_NONE:
            return "None"
        elif v == DATA_HEADER_OCEAN_OPTICS:
            return "Ocean Optics"
        elif v == DATA_HEADER_WASATCH:
            return "Wasatch"
        else:
            return "ERROR"

    def stringify_laser_type(self):
        v = self.laser_type
        if v == LASER_TYPE_NONE:
            return "None"
        elif v == LASER_TYPE_INTERNAL:
            return "Internal"
        elif v == LASER_TYPE_EXTERNAL:
            return "External"
        else:
            return "ERROR"

    def stringify_laser_control(self):
        v = self.laser_control
        if v == LASER_CONTROL_MODULATION:
            return "Modulation"
        elif v == LASER_CONTROL_TRANSITION_POINTS:
            return "Transition Points"
        elif v == LASER_CONTROL_RAMPING:
            return "Ramping"
        else:
            return "ERROR"

    def to_dict(self):
        return self.__dict__
