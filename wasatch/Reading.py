import datetime
import logging

log = logging.getLogger(__name__)

## 
# A single set of data read from a device. This includes spectrum,
# temperature, gain, offset, etc. Essentially a snapshot of the device
# state in time. 
class Reading:

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
        self.ambient_temperature_degC  = 0
        self.secondary_adc_raw         = None
        self.secondary_adc_calibrated  = None
        self.laser_status              = None   # MZ: sim-only?
        self.laser_power_perc          = 0      
        self.laser_power_mW            = 0
        self.failure                   = None
        self.averaged_count            = 0
        self.session_count             = 0      # can treat as reading_id
        self.area_scan_row_count       = -1
        self.area_scan_data            = None
        self.battery_raw               = None
        self.battery_percentage        = None
        self.battery_charging          = None
        self.laser_can_fire            = False  # per interlock board
        self.laser_is_firing           = False  # per interlock board, not laser_enable
        self.laser_tec_enabled         = False
        self.take_one_request          = None

        # currently only populated by AutoRaman
        self.new_integration_time_ms   = None
        self.new_gain_db               = None

        # for the rare case (BatchCollection with LaserMode "Spectrum") where the 
        # driver is asked to collect a dark just before enabling the laser
        self.dark                      = None

    def __str__(self):
        return "wasatch.Reading {device_id %s, spectrum %s, averaged_count %d, session_count %d, area_scan_row_count %d, timestamp %s, timestamp_complete %s, failure %s, laser_enabled %s, ambient %s, take_one_request %s }" % (
            self.device_id, 
            "None" if self.spectrum is None else ("%d values" % len(self.spectrum)),
            self.averaged_count, 
            self.session_count,
            self.area_scan_row_count,
            self.timestamp, 
            self.timestamp_complete, 
            self.failure,
            self.laser_enabled,
            self.ambient_temperature_degC,
            self.take_one_request)

    def __init__(self, device_id=None):
        self.clear()

        self.device_id = str(device_id)

        # NOTE: this will generally indicate when the acquisition STARTS, not ENDS
        # (WasatchDevice.acquire_spectrum instantiates Reading before calling hardware.get_line,
        #  and does not overwrite it)
        self.timestamp = datetime.datetime.now()

    def dump_area_scan(self):
        if self.area_scan_data is None:
            return

        rows = len(self.area_scan_data)
        for i in range(rows):
            spectrum = self.area_scan_data[i]
            log.debug("dump_area_scan: row %2d, %4d pixels, max %5d: %s ... %s", i, len(spectrum), max(spectrum), spectrum[0:5], spectrum[-5:])
