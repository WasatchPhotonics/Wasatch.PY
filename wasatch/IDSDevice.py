import logging
import json
import copy
import os

from .SpectrometerResponse  import SpectrometerResponse, ErrorLevel
from .SpectrometerSettings  import SpectrometerSettings
from .InterfaceDevice       import InterfaceDevice
from .IDSCamera             import IDSCamera
from .AutoRaman             import AutoRaman
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

    # MZ: who passes-in config_dir, scratch_dir and consumer_deletes_area_scan_image?
    def __init__(self, device_id, message_queue=None, alert_queue=None, config_dir=None, scratch_dir=None, consumer_deletes_area_scan_image=False):
        super().__init__(device_id=device_id, message_queue=message_queue, alert_queue=alert_queue)

        self.process_f = self.init_process_funcs()
        self.camera = IDSCamera(scratch_dir=scratch_dir, consumer_deletes_area_scan_image=consumer_deletes_area_scan_image)
        self.imx385 = IMX385() # re-using existing binning

        self.session_reading_count = 0
        self.reset_averaging()

        self.config_dir = config_dir if config_dir else os.path.join(utils.get_default_data_dir(), "config")

        # populate on connection
        self.auto_raman = None 

        # This may be set post-connection to connect this IDSDevice with a 
        # WasatchDevice (with coupled FeatureInterfaceDevice) serving as a 
        # laser driver board. 
        #
        # We also want to use its EEPROM, which means this really needs to be 
        # populated BEFORE connection (so the JSON file, if found, will override
        # EEPROM defaults).
        #
        # Thinking...
        #
        # 1. Assume ENLIGHTEN will have already enumerated / connected 220250
        #    BEFORE IDSDevice, which will continue to require a plugin "click"
        #    to connect for the foreseeable future.
        # 2. Going forward, we could presumably write the IDS camera serial 
        #    number to the 220250's EEPROM (perhaps in user_text) to "forcibly
        #    associate" them and avoid mistakes. But we can't easily do that
        #    to units already in the field.
        # 3. Plan: give WasatchDeviceWrapper a lightweight "Multispec"-like
        #    static registry of all connected WrapperWorkers, such that one
        #    can iterate through and select a peer from the catalog.
        # 4. Note that this whole discussion could potentially make the current
        #    LaserControlFeature.current_spectrometer_callback unnecessary.
        self.laser_device = None

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

        # attempt to link to an existing InterfaceDevice (presumably a 
        # WasatchDevice) with a laser but no detector
        log.debug("connect: searching for available laser partner")
        for interface_device in WasatchDeviceWrapper.get_interface_devices():
            if not interface_device.settings.eeprom.detector or "none" in interface_device.settings.eeprom.detector.lower():
                if interface_device.settings.eeprom.has_laser:
                    log.debug(f"connect: linked to laser-only InterfaceDevice {interface_device.device_id}")
                    self.laser_device = interface_device

        if self.laser_device:
            log.debug("using laser_device EEPROM")
        else:
            # initialize default settings
            self.settings = SpectrometerSettings(self.device_id)
            self.settings.eeprom.excitation_nm_float = 785 
            self.settings.eeprom.wavecal_coeffs = [0, 1, 0, 0, 0]
            self.settings.eeprom.min_integration_time_ms = 15       # Default UserSet
            self.settings.eeprom.max_integration_time_ms = 120_000  # LongExposure UserSet

            # stomp from camera
            self.settings.eeprom.model = self.camera.model_name
            self.settings.eeprom.detector = self.camera.sensor_name
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

        self.set_integration_time_ms(self.settings.eeprom.startup_integration_time_ms)
        self.set_gain_db(self.settings.eeprom.startup_gain_db)

        # since area scan is so important to this camera, perform full rotation
        self.camera.set_rotate_180(self.settings.eeprom.invert_x_axis)

        self.auto_raman = AutoRaman(idevice=self, auto_collection_mode=True)

        log.debug("connect: success")
        return SpectrometerResponse(True)

    def disconnect(self):
        if self.camera is not None:
            log.debug("closing IDSCamera")
            self.camera.close()
            self.camera = None

    def init_from_json(self):
        """
        This should be needed "less" now that we're associating IDS cameras with
        EEPROM-equipped Wasatch "laser drivers", but retained for non-Raman support.

        Example: IDS-4108809482-WP-02288.json
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
          "wp_serial_number": "WP-02288"
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

        # copy over all fields matching standard wasatch.EEPROM attribute names
        for k in self.settings.eeprom.fields:
            stomp(k)

        # overrides where JSON has different name than EEPROM field
        for k, attr in [ [ "wp_model",         "model" ],
                         [ "wp_serial_number", "serial_number" ] ]:
            stomp(k, attr)

    def set_integration_time_ms(self, ms):
        """
        It did not look like we need to stop/start the camera when changing exposure time:
        cpp/afl_features_live_qtwidgets/backend.cpp BackEnd::SetExposure
        """
        if ms is None:
            ms = 0
        ms = max(ms, 15) # lowest supported by camera
        log.debug(f"set_integration_time_ms: {ms}ms")
        self.settings.state.integration_time_ms = self.camera.set_integration_time_ms(ms)
        return SpectrometerResponse(True)

    def _set_gain_factor(self, factor):
        """ 
        Used by set_gain_db().
        Appears to be a scalar multiplier rather than dB, since it defaults to 1.0? 
        """
        log.debug(f"_set_gain_factor: gain factor {factor:.1f}")
        self.settings.state.detector_gain = self.camera.set_gain_factor(factor)
        return SpectrometerResponse(True)

    def set_detector_gain(self, db):
        return self.set_gain_db(db)

    def set_gain_db(self, db):
        if db is None:
            return
        log.debug(f"set_gain_db: gain {db:.1f}")
        self.settings.state.gain_db = db
        factor = utils.from_db_to_linear(db)
        return self._set_gain_factor(factor)

    def set_start_line(self, line):
        if line is None:
            return
        log.debug(f"set_start_line: line {line}")
        self.camera.set_start_line(line)
        self.settings.eeprom.roi_vertical_region_1_start = line
        return SpectrometerResponse(True)

    def set_stop_line(self, line):
        if line is None:
            return
        log.debug(f"set_stop_line: line {line}")
        self.camera.set_stop_line(line)
        self.settings.eeprom.roi_vertical_region_1_end = line
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
            self.camera.send_trigger()
            spectrum = self.camera.get_spectrum()
        except:
            log.error("error getting spectrum from IDSCamera", exc_info=1)
            return SpectrometerResponse(False)
            
        if spectrum is None:
            log.debug("get_spectrum: received None")
            return SpectrometerResponse(False)

        spectrum = self.apply_horizontal_binning(spectrum)
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
        reading.spectrum = self.get_spectrum()

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

        # track image format
        reading.image_format = self.camera.output_format_name

        # log.debug(f"acquire_data: returning {reading}")
        return SpectrometerResponse(data=reading)

    ############################################################################
    # laser proxy (if coupled with 220250 "laser driver board"
    ############################################################################

    def can_laser_fire(self):
        if not self.laser_device:
            return SpectrometerResponse(False)
        return self.laser_device.handle_requests([SpectrometerRequest("can_laser_fire")])[0]

    def is_laser_firing(self):
        if not self.laser_device:
            return SpectrometerResponse(False)
        return self.laser_device.handle_requests([SpectrometerRequest("is_laser_firing")])[0]

    def set_laser_enable(self, flag):
        if not self.laser_device:
            return SpectrometerResponse(False)
        return self.laser_device.handle_requests([SpectrometerRequest('set_laser_enable', args=[flag])])[0]

    def get_laser_tec_mode(self):
        if not self.laser_device:
            return SpectrometerResponse(None)
        return self.laser_device.handle_requests([SpectrometerRequest("get_laser_tec_mode")])[0]

    def get_laser_warning_delay_sec(self):
        if not self.laser_device:
            return SpectrometerResponse(3)
        return self.laser_device.handle_requests([SpectrometerRequest("get_laser_warning_delay_sec")])[0]

    def set_laser_warning_delay_sec(self, sec):
        if not self.laser_device:
            return SpectrometerResponse(False)
        return self.laser_device.handle_requests([SpectrometerRequest('set_laser_warning_delay_sec', args=[sec])])[0]

    def get_ambient_temperature_degC(self):
        if not self.laser_device:
            return SpectrometerResponse(None)
        return self.laser_device.handle_requests([SpectrometerRequest("get_ambient_temperature_degC")])[0]

    ############################################################################
    # utility
    ############################################################################

    def to_hex(self, data):
        return "[ " + " ".join([f"0x{v:02x}" for v in data]) + " ]"

    def init_process_funcs(self):
        process_f = {}

        for fn_name in [ 
                "can_laser_fire",
                "get_ambient_temperature_degC",
                "get_laser_tec_mode",
                "get_laser_warning_delay_sec",
                "is_laser_firing",
                "set_detector_gain",
                "set_integration_time_ms",
                "set_laser_enable",
                "set_laser_warning_delay_sec",
            ]:
            process_f[fn_name] = getattr(self, fn_name)

        process_f["connect"]             = self.connect
        process_f["disconnect"]          = self.disconnect
        process_f["close"]               = self.disconnect

        process_f["acquire_data"]        = self.acquire_data
        process_f["get_line"]            = self.get_spectrum
                                         
        process_f["gain_db"]             = lambda x: self.set_gain_db(float(x)) 
        process_f["integration_time_ms"] = lambda x: self.set_integration_time_ms(int(x))
        process_f["scans_to_average"]    = lambda x: self.set_scans_to_average(int(x))

        process_f["vertical_binning"]    = lambda x: self.set_vertical_roi(x)
        process_f["start_line"]          = lambda x: self.set_start_line(x)
        process_f["stop_line"]           = lambda x: self.set_stop_line(x)
        process_f["area_scan_enable"]    = lambda x: self.set_area_scan_enable(bool(x))

        process_f["output_format_name"] = lambda x: self.set_output_format_name(x)

        return process_f
