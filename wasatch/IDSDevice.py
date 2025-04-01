import logging

from .SpectrometerResponse  import SpectrometerResponse, ErrorLevel
from .SpectrometerSettings  import SpectrometerSettings
from .InterfaceDevice       import InterfaceDevice
from .IDSCamera             import IDSCamera
from .DeviceID              import DeviceID
from .Reading               import Reading
from .ROI                   import ROI

log = logging.getLogger(__name__)

class IDSDevice(InterfaceDevice):
    """
    @see https://www.ids-imaging.us/manuals/ids-peak/ids-peak-api-documentation/2.15.0/en/python.html
    """

    ############################################################################
    # lifecycle
    ############################################################################

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        super().__init__()

        self.device_id = device_id
        self.process_f = self.init_process_funcs()
        self.device = None
        self.camera = IDSCamera()

    def connect(self):
        """
        This method is called by WrapperWorker.run. At the end of the method,
        an InterfaceDevice should have a non-null .settings with a populated 
        SpectrometerSettings.
        """
        log.debug(f"connect: trying to connect to {self.device_id}")
        if not self.camera.connect():
            log.error("connect: unable to connect to IDSCamera")
            return SpectrometerResponse(False)

        log.debug(f"connect: trying to start {self.device_id}")
        self.camera.start()

        # initialize default settings, so we can start overwriting them from the camera
        self.settings = SpectrometerSettings()

        self.settings.eeprom.model = self.camera.model_name
        self.settings.eeprom.serial_number = self.camera.serial_number
        self.settings.eeprom.active_pixels_horizontal = self.camera.width
        self.settings.eeprom.active_pixels_vertical = self.camera.height
        self.settings.eeprom.roi_vertical_region_1_start = 0
        self.settings.eeprom.roi_vertical_region_1_end   = self.camera.height

        # kludges for testing
        self.settings.eeprom.excitation_nm_float = 785 
        self.settings.eeprom.wavecal_coeffs = [0, 1, 0, 0, 0]

        log.debug("connect: success")
        return SpectrometerResponse(True)

    def disconnect(self):
        if self.camera is not None:
            log.debug("closing IDSCamera")
            self.camera.close()
            self.camera = None

    def set_integration_time_ms(self, ms):
        """
        It did not look like we need to stop/start the camera when changing exposure time:
        cpp/afl_features_live_qtwidgets/backend.cpp BackEnd::SetExposure
        """
        log.debug(f"set_integration_time_ms: here")
        #log.debug(f"set_integration_time_ms: stopping")
        #self.camera.stop()
        log.debug(f"set_integration_time_ms: setting integration time {ms}ms")
        self.camera.set_integration_time_ms(ms)
        #log.debug(f"set_integration_time_ms: starting")
        #self.camera.start()
        log.debug(f"set_integration_time_ms: done")
        return SpectrometerResponse(True)

    def set_start_line(self, line):
        log.debug(f"set_start_line: line {line}")
        self.camera.set_start_line(line)
        return SpectrometerResponse(True)

    def set_stop_line(self, line):
        log.debug(f"set_stop_line: line {line}")
        self.camera.set_stop_line(line)
        return SpectrometerResponse(True)

    def set_vertical_binning(self, roi):
        if isinstance(roi, ROI):
            start, end = roi.start, roi.end
        elif len(roi) == 2:
            start, end = roi[0], roi[1]
        else:
            log.error("set_vertical_binning requires an ROI object or tuple of (start, stop) lines")
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")

        log.debug(f"set_vertical_binning: start {start}, end {end}")
        self.set_start_line(start)
        self.set_stop_line(end)
        return SpectrometerResponse(True)

    def set_area_scan_enable(self, flag):
        log.debug(f"set_area_scan_enable: flag {flag}")
        self.camera.save_area_scan_image = flag
        return SpectrometerResponse(True)

    def get_spectrum(self):
        log.debug(f"get_spectrum: calling send_trigger")
        self.camera.send_trigger()
        log.debug(f"get_spectrum: calling get_spectrum")
        spectrum = self.camera.get_spectrum()
        if spectrum is None:
            log.error("get_spectrum: received None?!")
        log.debug(f"get_spectrum: done")
        return spectrum

    ############################################################################
    # operations
    ############################################################################

    def acquire_data(self):
        reading = Reading(self.device_id)
        reading.spectrum = self.get_spectrum()
        reading.area_scan_image = self.camera.last_area_scan_image
        log.debug(f"acquire_data: area_scan_image {reading.area_scan_image}")
        return SpectrometerResponse(data=reading)

    ############################################################################
    # utility
    ############################################################################

    def to_hex(self, data):
        return "[ " + " ".join([f"0x{v:02x}" for v in data]) + " ]"

    def init_process_funcs(self):
        process_f = {}

        process_f["connect"]             = self.connect
        process_f["disconnect"]          = self.disconnect
        process_f["close"]               = self.disconnect

        process_f["acquire_data"]        = self.acquire_data
                                         
        process_f["integration_time_ms"] = lambda x: self.set_integration_time_ms(x)
        process_f["vertical_binning"]    = lambda x: self.set_vertical_binning(x)
        process_f["start_line"]          = lambda x: self.set_start_line(x)
        process_f["stop_line"]           = lambda x: self.set_stop_line(x)
        process_f["area_scan_enable"]    = lambda x: self.set_area_scan_enable(bool(x))

        return process_f
