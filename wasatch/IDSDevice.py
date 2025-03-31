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
        self.camera.set_integration_time_ms(ms)
        return SpectrometerResponse(True)

    def set_start_line(self, line):
        self.camera.set_start_line(line)
        return SpectrometerResponse(True)

    def set_stop_line(self, line):
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

        self.set_start_line(start)
        self.set_stop_line(stop)
        return SpectrometerResponse(True)

    def get_spectrum(self):
        log.debug("get_spectrum: calling camera.get_spectrum")
        spectrum = self.camera.get_spectrum()
        if spectrum is None:
            log.error("get_spectrum: received None?!")
        log.debug("get_spectrum: done")
        return spectrum

    ############################################################################
    # operations
    ############################################################################

    def acquire_data(self):
        """
        @todo pass along latest PNG for area scan
        """
        reading = Reading(self.device_id)
        spectrum = self.get_spectrum()
        reading.spectrum = spectrum
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

        return process_f
