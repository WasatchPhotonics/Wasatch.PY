import logging

log = logging.getLogger(__name__)

# volatile attributes (must persist here for multi-spectrometers)
#
# Note that these should generally not include READOUTS from the 
# spectrometer like temperature, ADC etc...unless that proves convenient.
#
class SpectrometerState(object):
    def __init__(self):

        # detector
        self.integration_time_ms = 0
        self.tec_setpoint_degC = 0 # int?
        self.ccd_gain = 0.0
        self.high_gain_mode_enabled = False
        self.triggering_enabled = False

        # laser
        self.laser_enabled = False
        self.laser_power_perc = 0

    def dump(self):
        log.info("SpectrometerState:")
        log.info("  Integration Time:       %dms", self.integration_time_ms)
        log.info("  TEC Setpoint:           %.2f degC", self.tec_setpoint_degC)
        log.info("  CCD Gain:               %.2f", self.ccd_gain)
        log.info("  High Gain Mode Enabled: %s", self.high_gain_mode_enabled)
        log.info("  Triggering Enabled:     %s", self.triggering_enabled)
        log.info("  Laser Enabled:          %s", self.laser_enabled)
        log.info("  Laser Power             %d%%", self.laser_power_perc)
