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

class FPGAOptions(object):

    def __init__(self, word):
        # bits 0-2: 0000 0000 0000 0111 IntegrationTimeResolution
        # bit  3-5: 0000 0000 0011 1000 DataHeader
        # bit    6: 0000 0000 0100 0000 HasCFSelect
        # bit  7-8: 0000 0001 1000 0000 LaserType
        # bit 9-11: 0000 1110 0000 0000 LaserControl
        # bit   12: 0001 0000 0000 0000 HasAreaScan
        # bit   13: 0010 0000 0000 0000 HasActualIntegTime
        # bit   14: 0100 0000 0000 0000 HasHorizBinning

        self.integration_time_resolution = (word & 0x0007)
        self.data_header                 = (word & 0x0038) >> 3
        self.has_cf_select               = (word & 0x0040) != 0
        self.laser_type                  = (word & 0x0180) >> 7
        self.laser_control               = (word & 0x0e00) >> 9
        self.has_area_scan               = (word & 0x1000) != 0
        self.has_actual_integ_time       = (word & 0x2000) != 0
        self.has_horiz_binning           = (word & 0x4000) != 0

        self.dump()

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
        return "1ms" if v == 0 else "10ms" if v == 1 else "switchable" if v == 2 else "unknown"

    def stringify_header(self):
        v = self.data_header
        return "none" if v == 0 else "ocean" if v == 1 else "wasatch" if v == 2 else "unknown"

    def stringify_laser_type(self):
        v = self.laser_type
        return "none" if v == 0 else "internal" if v == 1 else "external" if v == 2 else "unknown"

    def stringify_laser_control(self):
        v = self.laser_control
        return "modulation" if v == 0 else "transition" if v == 1 else "ramping" if v == 2 else "unknown"
