import datetime
import logging

log = logging.getLogger(__name__)

## 
# A single set of data read from a device. This includes spectrum,
# temperature, gain, offset, etc. Essentially a snapshot of the device
# state in time. 
class Reading(object):

    def clear(self):
        self.device_id                 = None
        self.timestamp                 = None
        self.timestamp_complete        = None
        self.spectrum                  = None
        self.laser_enabled             = None
        self.laser_temperature_raw     = 0
        self.laser_temperature_degC    = 0
        self.detector_temperature_raw  = 0
        self.detector_temperature_degC = 0
        self.secondary_adc_raw         = None
        self.secondary_adc_calibrated  = None
        self.laser_status              = None   
        self.laser_power               = 0      
        self.laser_power_in_mW         = False
        self.failure                   = None
        self.averaged                  = False
        self.session_count             = 0
        self.area_scan_row_count       = -1
        self.battery_raw               = None
        self.battery_percentage        = None
        self.battery_charging          = None

        # for the rare case (BatchCollection with LaserMode "Spectrum") where the 
        # driver is asked to collect a dark just before enabling the laser
        self.dark                      = None

    def __init__(self, device_id=None):
        self.clear()

        self.device_id = str(device_id)

        # NOTE: this will generally indicate when the acquisition STARTS, not ENDS
        # (WasatchDevice.acquire_spectrum instantiates Reading before calling hardware.get_line,
        #  and does not overwrite it)
        self.timestamp = datetime.datetime.now()

    # def dump(self):
    #     log.info("Reading:")
    #     log.info("  Timestamp:              %s", self.timestamp)
    #     log.info("  Device ID:              %s", self.device_id)
    #     log.info("  Spectrum:               %s", self.spectrum[:max(5, len(self.spectrum))] if self.spectrum else None)
    #     log.info("  Laser Temp Raw:         0x%04x", self.laser_temperature_raw)
    #     log.info("  Laser Temp DegC:        %.2f", self.laser_temperature_degC)
    #     log.info("  Detector Temp Raw:      0x%04x", self.detector_temperature_raw)
    #     log.info("  Detector Temp DegC:     %.2f", self.detector_temperature_degC)
    #     log.info("  2nd ADC Raw:            %s", None if self.secondary_adc_raw is None else "0x%04x" % self.secondary_adc_raw)
    #     log.info("  2nd ADC Calibrated:     %s", None if self.secondary_adc_calibrated is None else "%.2f" % self.secondary_adc_calibrated)
    #     log.info("  Laser Status:           %s", self.laser_status)
    #     log.info("  Laser Power:            %d %s", self.laser_power, "mW" if self.laser_power_in_mW else "%")
    #     log.info("  Failure:                %s", self.failure)
    #     log.info("  Averaged:               %s", self.averaged)
    #     log.info("  Session Count:          %d", self.session_count)
    #     log.info("  Area Scan Row Count:    %d", self.area_scan_row_count)
    #     log.info("  Battery:                %.2f%% (%s)", self.battery_percentage, "charging" if self.battery_charging else "not charging")
