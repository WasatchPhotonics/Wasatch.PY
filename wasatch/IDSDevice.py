import logging
import json
import copy
import os

from .SpectrometerResponse  import SpectrometerResponse, ErrorLevel
from .SpectrometerSettings  import SpectrometerSettings
from .InterfaceDevice       import InterfaceDevice
from .IDSCamera             import IDSCamera
from .DeviceID              import DeviceID
from .Reading               import Reading
from .IMX385                import IMX385
from .ROI                   import ROI

from wasatch import utils

log = logging.getLogger(__name__)

class IDSDevice(InterfaceDevice):
    """
    @see https://www.ids-imaging.us/manuals/ids-peak/ids-peak-api-documentation/2.15.0/en/python.html
    """

    ############################################################################
    # lifecycle
    ############################################################################

    def __init__(self, device_id, message_queue=None, alert_queue=None, config_dir=None, scratch_dir=None, consumer_deletes_area_scan_image=False):
        super().__init__()

        self.device_id = device_id
        self.process_f = self.init_process_funcs()
        self.device = None
        self.camera = IDSCamera(scratch_dir=scratch_dir, consumer_deletes_area_scan_image=consumer_deletes_area_scan_image)

        self.imx385 = IMX385() # re-using existing binning

        self.session_reading_count = 0
        self.reset_averaging()

        self.config_dir = config_dir if config_dir else os.path.join(utils.get_default_data_dir(), "config")

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

        # initialize default settings
        self.settings = SpectrometerSettings(self.device_id)
        self.settings.eeprom.excitation_nm_float = 785 
        self.settings.eeprom.wavecal_coeffs = [0, 1, 0, 0, 0]
        self.settings.eeprom.min_integration_time_ms = 15       # Default UserSet
        self.settings.eeprom.max_integration_time_ms = 120_000  # LongExposure UserSet

        # stomp from camera
        self.settings.eeprom.model = self.camera.model_name
        self.settings.eeprom.serial_number = self.camera.serial_number
        self.settings.eeprom.detector_serial_number = self.camera.serial_number
        self.settings.eeprom.active_pixels_horizontal = self.camera.width
        self.settings.eeprom.active_pixels_vertical = self.camera.height
        self.settings.eeprom.roi_vertical_region_1_start = 0
        self.settings.eeprom.roi_vertical_region_1_end = self.camera.height
        self.settings.eeprom.invert_x_axis = True

        # stomp from virtual eeprom
        self.init_from_json()
        self.settings.eeprom.multi_wavelength_calibration.initialize()

        # now that we've got our "final settings," compute derived values
        self.settings.update_wavecal()

        # pass vertical ROI down into Camera (where binning occurs)
        self.set_start_line(self.settings.eeprom.roi_vertical_region_1_start)
        self.set_stop_line (self.settings.eeprom.roi_vertical_region_1_end)
        # no need to pass horizontal ROI, because that's handled in ENLIGHTEN

        self.set_integration_time_ms(15)

        # since area scan is so important to this camera, perform full rotation
        self.camera.set_rotate_180(self.settings.eeprom.invert_x_axis)

        log.debug("connect: success")
        return SpectrometerResponse(True)

    def disconnect(self):
        if self.camera is not None:
            log.debug("closing IDSCamera")
            self.camera.close()
            self.camera = None

    def init_from_json(self):
        """
        {
          "detector_serial_number": "4108809482",
          "excitation_nm_float": 785.0,
          "invert_x_axis": true,
          "wavelength_coeffs": [
              821.19,
                0.106,
                4.00E-05,
               -1.00E+07,
                5.00E-11
          ],
          "wp_model": "WP-785XS-FS-OEM+STARVIS",
          "wp_serial_number": "WP-002288"
        }

        """
        prefix = f"IDS-{self.camera.serial_number}"
        suffix = ".json"
        json_path = utils.find_first_file(path=self.config_dir, prefix=prefix, suffix=suffix)
        if not json_path:
            log.debug(f"could not find file with prefix {prefix}, suffix {suffix} in path {self.config_dir}")
            return

        with open(json_path) as config_file:
            data = json.load(config_file)
        log.debug(f"loaded from {json_path}: {data}")

        def stomp(k, attr=None):
            if attr is None:
                attr = k
                log.debug(f"attempting to stomp {k}")
            else:
                log.debug(f"attempting to stomp {k} --> {attr}")

            if k in data:
                value = data[k]
                log.debug(f"stomping eeprom.{attr} = {value}")
                setattr(self.settings.eeprom, attr, value)

        for k in [ "detector_gain",
                   "excitation_nm_float", 
                   "invert_x_axis", 
                   "horiz_binning_enabled",       
                   "horiz_binning_mode",
                   "startup_integration_time_ms",
                   "wavelength_coeffs",
                   "roi_horizontal_end",
                   "roi_horizontal_start",
                   "roi_vertical_region_1_start",
                   "roi_vertical_region_1_end" ]:
            stomp(k)
        for k, attr in [ [ "wp_model",         "model" ],
                         [ "wp_serial_number", "serial_number" ] ]:
            stomp(k, attr)

    def set_integration_time_ms(self, ms):
        """
        It did not look like we need to stop/start the camera when changing exposure time:
        cpp/afl_features_live_qtwidgets/backend.cpp BackEnd::SetExposure
        """
        log.debug(f"set_integration_time_ms: {ms}ms")
        self.settings.state.integration_time_ms = self.camera.set_integration_time_ms(ms)
        return SpectrometerResponse(True)

    def set_gain_factor(self, factor):
        """ Appears to be a scalar multiplier rather than dB, since it defaults to 1.0? """
        log.debug(f"set_gain_factor: gain factor {factor:.1f}")
        self.settings.state.detector_gain = self.camera.set_gain_factor(factor)
        return SpectrometerResponse(True)

    def set_gain_db(self, db):
        log.debug(f"set_gain_db: gain {db:.1f}")
        self.settings.state.gain_db = db
        factor = utils.from_db_to_linear(db)
        return self.set_gain_factor(factor)

    def set_start_line(self, line):
        log.debug(f"set_start_line: line {line}")
        self.camera.set_start_line(line)
        return SpectrometerResponse(True)

    def set_stop_line(self, line):
        log.debug(f"set_stop_line: line {line}")
        self.camera.set_stop_line(line)
        return SpectrometerResponse(True)

    def set_vertical_roi(self, roi):
        if isinstance(roi, ROI):
            start, end = roi.start, roi.end
        elif len(roi) == 2:
            start, end = roi[0], roi[1]
        else:
            log.error("set_vertical_roi requires an ROI object or tuple of (start, stop) lines")
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")

        log.debug(f"set_vertical_roi: start {start}, end {end}")
        self.set_start_line(start)
        self.set_stop_line(end)
        return SpectrometerResponse(True)

    def set_output_format_name(self, format_name):
        if format_name  not in self.camera.SUPPORTED_CONVERSIONS:
            log.error("unsupported output format name {format_name}")
            return SpectrometerResponse(False)

        log.debug(f"setting output_format_name to {format_name}")
        self.camera.init_image_converter(format_name)
        return SpectrometerResponse(True)

    def set_area_scan_enable(self, flag):
        log.debug(f"set_area_scan_enable: flag {flag}")
        self.camera.area_scan_enabled = flag
        return SpectrometerResponse(True)

    def set_scans_to_average(self, n):
        self.settings.state.scans_to_average = n
        log.debug(f"set_scans_to_average {self.settings.state.scans_to_average}")
        return SpectrometerResponse(True)

    def get_spectrum(self):
        try:
            # log.debug("get_spectrum: calling send_trigger")
            self.camera.send_trigger()
            # log.debug("get_spectrum: back from send_trigger, calling camera.get_spectrum")
            spectrum = self.camera.get_spectrum()
            # log.debug("get_spectrum: back from camera.get_spectrum")
        except:
            log.error("error getting spectrum from IDSCamera", exc_info=1)
            return SpectrometerResponse(False)
            
        if spectrum is None:
            log.debug("get_spectrum: received None")
            return SpectrometerResponse(False)

        # log.debug("get_spectrum: applying horizontal binning")
        spectrum = self.apply_horizontal_binning(spectrum)
        # log.debug("get_spectrum: done")
        return spectrum

    def apply_horizontal_binning(self, spectrum):
        if not self.settings.eeprom.horiz_binning_enabled:
            return spectrum

        mode = self.settings.eeprom.multi_wavelength_calibration.get("horiz_binning_mode")
        if mode == IMX385.BIN_2X2:
            return self.imx385.bin_2x2(spectrum)
        elif mode == IMX385.CORRECT_SSC:
            return self.imx385.correct_ssc(spectrum, self.settings.wavelengths)
        elif mode == IMX385.CORRECT_SSC_BIN_2X2:
            spectrum = self.imx385.correct_ssc(spectrum, self.settings.wavelengths)
            return self.imx385.bin_2x2(spectrum)
        elif mode == IMX385.BIN_4X2:
            return self.imx385.bin_4x2(spectrum)
        elif mode == IMX385.BIN_4X2_INTERP:
            return self.imx385.bin_4x2_interp(spectrum, self.settings.wavelengths)
        elif mode == IMX385.BIN_4X2_AVG:
            return self.imx385.bin_4x2_avg(spectrum)
        else:
            # there may be legacy units in the field where this byte is 
            # uninitialized to 0xff...treat as 0x00 for now
            log.error("invalid horizontal binning mode {mode}...defaulting to bin_2x2")
            return self.imx385.bin_2x2(spectrum)


    ############################################################################
    # operations
    ############################################################################

    def reset_averaging(self):
        log.debug("reset averaging")
        self.summed_spectra = None
        self.sum_count = 0

    def acquire_data(self):
        # pre-process scan averaging
        # log.debug(f"acquire_data: start (scans_to_average {self.settings.state.scans_to_average})")
        if self.settings.state.scans_to_average > 1:
            if self.sum_count > self.settings.state.scans_to_average:
                self.reset_averaging()

        reading = Reading(self.device_id)
        # log.debug("acquire_data: calling get_spectrum")
        reading.spectrum = self.get_spectrum()
        # log.debug("acquire_data: back from get_spectrum")

        if not self.camera:
            return SpectrometerResponse(False)

        # attach an AreaScanImage if there is one
        reading.area_scan_image = self.camera.last_area_scan_image
        self.camera.last_area_scan_image = None # don't re-send the same one twice

        self.session_reading_count += 1
        reading.session_count = self.session_reading_count

        # post-process scan averaging
        if self.settings.state.scans_to_average > 1:
            if self.summed_spectra is None:
                self.summed_spectra = copy.copy(reading.spectrum)
            else:
                for pixel, intensity in enumerate(reading.spectrum):
                    self.summed_spectra[pixel] += intensity

            self.sum_count += 1
            reading.sum_count = self.sum_count

            if self.sum_count >= self.settings.state.scans_to_average:
                log.debug("acquire_data: averaging complete")
                for i in range(len(self.summed_spectra)):
                    self.summed_spectra[i] = round(1.0 * self.summed_spectra[i] / self.sum_count, 2)
                reading.averaged = True
                self.reset_averaging()
            else:
                log.debug(f"acquire_data: averaged {self.sum_count}/{self.settings.state.scans_to_average}")

        # log.debug(f"acquire_data: returning {reading}")
        return SpectrometerResponse(data=reading)

    def heartbeat(self, arg):
        return SpectrometerResponse(data=True)

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
        process_f["heartbeat"]           = self.heartbeat
                                         
        # deliberately don't support gain_factor or detector_gain
        process_f["gain_db"]             = lambda x: self.set_gain_db(float(x)) 
        process_f["integration_time_ms"] = lambda x: self.set_integration_time_ms(int(x))
        process_f["scans_to_average"]    = lambda x: self.set_scans_to_average(int(x))

        process_f["vertical_binning"]    = lambda x: self.set_vertical_roi(x)
        process_f["start_line"]          = lambda x: self.set_start_line(x)
        process_f["stop_line"]           = lambda x: self.set_stop_line(x)
        process_f["area_scan_enable"]    = lambda x: self.set_area_scan_enable(bool(x))

        process_f["output_format_name"] = lambda x: self.set_output_format_name(x)

        return process_f
