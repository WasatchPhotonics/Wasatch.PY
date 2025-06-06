import os
import usb
import json
import numpy as np
import struct
import logging
import datetime

from ctypes import *

from .SpectrometerSettings        import SpectrometerSettings
from .SpectrometerResponse        import SpectrometerResponse
from .InterfaceDevice             import InterfaceDevice
from .StatusMessage               import StatusMessage
from .DeviceID                    import DeviceID
from .Reading                     import Reading
from .ROI                         import ROI

log = logging.getLogger(__name__)

class AndorDevice(InterfaceDevice):
    """
    This is the basic implementation of our interface with Andor cameras     

    @todo have check_result return a SpectrometerResponse 
    @todo try to auto-detect whether x-axis needs inverted via DLL.GetImageFlip()

    ##########################################################################
    This class adopts the external device interface structure.
    This involves receiving a request through the handle_request function.
    A request is processed based on the key in the request.
    The processing function passes the commands to the requested device.
    Once it receives a response from the connected device it then passes that
    back up the chain.
    @verbatim
                               Enlighten Request
                                       |
                                handle_requests
                                       |
                                 ------------
                                /   /  |  \  \  
             { get_laser status, acquire, set_laser_watchdog, etc....}
                                \   \  |  /  /
                                 ------------
                                       |
                         {self.driver.some_andor_sdk_call}
    @endverbatim
    ############################################################################
    """

    SUCCESS = 20002             #!< see load_error_codes()
    SHUTTER_SPEED_MS = 50       #!< allow time for mechanical shutter to stabilize

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        # if passed a string representation of a DeviceID, deserialize it
        super().__init__()
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.device_id      = device_id
        self.message_queue  = message_queue
        self.alert_queue    = alert_queue

        self.load_error_codes()

        self.connected = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. 
        self.command_queue = []

        self.immediate_mode = False

        # An AndorDevice has-a SpectrometerSettings, which has-a EEPROM, which 
        # has-a MultiWavelengthCalibration
        self.settings = SpectrometerSettings(self.device_id)
        self.session_reading_count  = 0
        self.sum_count              = 0
        self.take_one_request       = None

        self.dll_fail               = True
        self.tec_enabled            = True
        self.driver                 = None

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0
        self.spec_index = 0 
        self._scan_averaging = 1
        self.dark = None
        self.boxcar_half_width = 0

        # decide appropriate DLL filename for architecture
        arch = 64 if 64 == struct.calcsize("P") * 8 else 32
        filename = f"atmcd{arch}d.dll"

        # Andor libraries may be found in various locations
        dll_paths = [ r"C:\Program Files\Andor Driver Pack 2",
                      r"C:\Program Files\Andor SDK",
                      r"dist\Andor",
                      r"dist" ]

        # try to find correct DLL in any known location
        for path in dll_paths:
            pathname = os.path.join(path, filename)
            if os.path.exists(pathname):
                try:
                    log.debug(f"attempting to load {pathname}")
                    self.driver = cdll.LoadLibrary(pathname)
                    self.dll_fail = False
                except Exception as e:
                    log.error(f"Error loading {pathname}: {e}")

                if self.driver is not None:
                    break

        if self.driver is None:
            log.error(f"could not find {filename} in search path: {dll_paths}")
            # MZ: interesting that we don't return here

        # "serial_number", "model" etc are ambiguous in an Andor configuration 
        # file -- do they refer to the camera (Andor), or the spectrometer 
        # (Wasatch)?  Therefore, some Wasatch EEPROM fields get extra "wp_" 
        # prefixes in Andor configuration files to be clear.
        self.config_names_to_eeprom = {
            'wp_serial_number': 'serial_number',
            'wp_model': 'model' 
        }

        # set Andor defaults for important "EEPROM" settings
        # (all but has_cooling can be overridden via config file)

        # Andor API doesn't(?) seem to have access to detector info.
        # Note that we use non-iDus cameras, including the Newton.
        self.settings.eeprom.detector = "iDus" 

        self.settings.eeprom.has_cooling = True
        self.settings.eeprom.startup_integration_time_ms = 10
        self.settings.eeprom.startup_temp_degC = -60
        self.settings.eeprom.detector_gain = 1
        self.settings.eeprom.detector_gain_odd = 1

        # MultiWavelengthCalibration attributes
        self.settings.eeprom.multi_wavelength_calibration.set("wavelength_coeffs", [0,1,0,0,0])

        self.process_f = self._init_process_funcs()

    ###############################################################
    # Private Methods
    ###############################################################

    def _queue_message(self, setting, value):
        """
        If an upstream queue is defined, send the name-value pair.  Does nothing
        if the caller hasn't provided a queue.

        "setting" is application (caller) dependent, but ENLIGHTEN currently uses
        "marquee_info" and "marquee_error".
        """
        if self.message_queue is None:
            return

        msg = StatusMessage(setting, value)
        try:
            log.debug(f"queue_message: msg {msg}")
            self.message_queue.put(msg) 
        except:
            log.error(f"failed to enqueue StatusMessage {msg}", exc_info=1)

    def not_implemented(self):
        pass

    def _init_process_funcs(self):
        process_f = {}

        process_f["connect"]                    = self.connect
        process_f["acquire_data"]               = self.acquire_data
        process_f["set_shutter_enable"]         = self.set_shutter_enable
        process_f["set_integration_time_ms"]    = self.set_integration_time_ms
        process_f["get_serial_number"]          = self.get_serial_number
        process_f["init_tec_setpoint"]          = self.init_tec_setpoint
        process_f["set_tec_setpoint"]           = self.set_tec_setpoint
        process_f["init_detector_area"]         = self.init_detector_area
        process_f["scans_to_average"]           = self.set_scans_to_average
        process_f["high_gain_mode_enable"]      = self.high_gain_mode_enable
        process_f["save_config"]                = self.save_config
        process_f["vertical_binning"]           = self.set_vertical_binning
        process_f["take_one_request"]           = self.set_take_one_request

        process_f["reset_scan_averaging"]       = self.not_implemented
        process_f["heartbeat"]                  = self.not_implemented

        ##################################################################
        # What follows is the old init-lambdas that are squashed into process_f
        # Long term, the upstream requests should be changed to match the new format
        # This is an easy fix for the time being to make things behave
        ##################################################################
        process_f["integration_time_ms"]        = lambda x: self.set_integration_time_ms(x)
        process_f["fan_enable"]                 = lambda x: self.set_fan_enable(bool(x))
        process_f["shutter_enable"]             = lambda x: self.set_shutter_enable(bool(x))
        process_f["detector_tec_enable"]        = lambda x: self.toggle_tec(bool(x))
        process_f["detector_tec_setpoint_degC"] = lambda x: self.set_tec_setpoint(int(round(x)))

        return process_f

    def high_gain_mode_enable(self, enabled):
        if enabled:
            result = self.driver.SetPreAmpGain(self.gain_idx[-1])
            assert(self.SUCCESS == result), f"unable to set detector gain, got value of {result}"
            log.debug(f"for {enabled} setting gain to {self.gain_options[-1]}")
            return
        else:
            result = self.driver.SetPreAmpGain(self.gain_idx[0])
            assert(self.SUCCESS == result), f"unable to set detector gain, got value of {result}"
            log.debug(f"for {enabled} setting gain to {self.gain_options[0]}")
            return

    def set_fan_enable(self, x):
        self.check_result(self.driver.SetFanMode(int(x)), f"Andor Fan On {x}")
        return SpectrometerResponse()

    def _get_default_data_dir(self):
        if os.name == "nt":
            return os.path.join(os.path.expanduser("~"), "Documents", "EnlightenSpectra")
        return os.path.join(os.environ["HOME"], "EnlightenSpectra")

    def _check_config_file(self):
        self.config_dir = os.path.join(self._get_default_data_dir(), 'config')
        self.config_file = os.path.join(self.config_dir, self.serial + '.json')
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)
        return os.path.isfile(self.config_file)

    def _get_spectrum_raw(self):
        """
        @todo missing bad-pixel correction
        """
        spec_arr = c_long * self.pixels
        spec_init_vals = [0] * self.pixels
        spectrum = spec_arr(*spec_init_vals)

        # ask for spectrum then collect, NOT multithreaded (though we should look into that!), blocks
        self.driver.StartAcquisition()
        self.driver.WaitForAcquisition()
        result = self.driver.GetAcquiredData(spectrum, c_ulong(self.pixels))

        if (result != self.SUCCESS):
            log.error(f"_get_spectrum_raw: GetAcquiredData failed (result {result})")
            return

        # convert from wasatch.AndorDevice.c_long_Array_512
        spectrum = np.array(spectrum, dtype=np.float32) # [x for x in spectrum]

        if (self.settings.eeprom.invert_x_axis):
            # spectrum.reverse()
            spectrum = spectrum[::-1]

        # Andor cameras can return all zeros when saturated
        if not spectrum.any():
            self._queue_message("marquee_error", "Andor camera is saturated")

        # log.debug(f"_get_spectrum_raw: returning spectrum {spectrum}")
        return spectrum

    def _take_one_averaged_reading(self):
        """ 
        @note this may be collecting a dark spectrum requested through TakeOneRequest.take_dark 
        @returns Reading
        """

        ########################################################################
        # take the averaged spectrum
        ########################################################################

        tor = self.take_one_request
        if tor is not None:
            scans_to_average = tor.scans_to_average
            log.debug(f"take_one_averaged_reading: taking scans_to_average from tor {tor}")
        else:
            scans_to_average = self.settings.state.scans_to_average
            log.debug(f"take_one_averaged_reading: taking scans_to_average from SpectrometerState")

        log.debug(f"take_one_averaged_reading: scans_to_average {scans_to_average}")

        # either take one measurement (normal), or a bunch (blocking averaging)
        reading = Reading(self.device_id) # reading.timestamp is when reading STARTED, not FINISHED!
        self.sum_count = 0
        failure_count = 0
        self.summed_spectrum = None
        MAX_FAILURES = 3

        # this loop can DELIBERATELY be changed (shortened, reset or lengthened)
        # by external changes to self.sum_count and self.settings.state.scans_to_average
        while self.sum_count < scans_to_average:

            # monitor for external changes (but NOT new TakeOneRequest...that will appear in due process)
            if scans_to_average != self.settings.state.scans_to_average:
                log.debug(f"detected change in scans_to_average from {scans_to_average} to {self.settings.state.scans_to_average}")
                self.scan_count = 0
                scans_to_average = self.settings.state.scans_to_average
                self.summed_spectrum = None
            
            try:
                log.debug(f"take_one_averaged_reading: taking spectrum {self.sum_count+1} of {scans_to_average}")
                spectrum = self._get_spectrum_raw()
            except usb.USBError:
                failure_count += 1
                log.error(f"failure_count {failure_count}, encountered USB error in reading for device {self.device}", exc_info=1)
                if failure_count < MAX_FAILURES:
                    continue

            if spectrum is None or spectrum == []:
                failure_count += 1
                log.error(f"failure_count {failure_count}, received empty spectrum {reading.spectrum}")
                if failure_count < MAX_FAILURES:
                    log.error(f"failure_count {failure_count}, trying again")
                    continue

            if failure_count >= MAX_FAILURES:
                msg = f"exceeded MAX_FAILURES {MAX_FAILURES} while trying to read an averaged spectrum"
                log.error(msg)
                return 

            log.debug("successfully read one spectrum")
            self.sum_count += 1
            if scans_to_average > 1 and self.sum_count < scans_to_average:
                # MZ: why aren't we following this pattern in WasatchDevice / FeatureInterfaceDevice?
                log.debug(f"floating message upstream because sum_count {self.sum_count}, scans_to_average {scans_to_average}")
                self._queue_message("scan_averaging", (self.device_id, self.sum_count))

            if self.summed_spectrum is None:
                self.summed_spectrum = spectrum
            else:
                self.summed_spectrum += spectrum
            log.debug(f"take_one_averaged_reading: sum_count {self.sum_count}, summed_spectrum : {self.summed_spectrum[0:9]}")

        # have we completed the averaged reading?
        reading.spectrum = self.summed_spectrum / self.sum_count if self.sum_count > 1 else spectrum

        log.debug(f"take_one_averaged_reading: {'averaged' if self.sum_count > 1 else 'non-averaged'} spectrum : %s ...", reading.spectrum[0:9])
        reading.averaged = True
        reading.sum_count = self.sum_count
        return reading

    def get_detector_temperature_degC(self):
        if self.tec_enabled:
            log.debug("TEC enabled, so reading temperature")

            use_float = True    # seems to work on 785XL (WP-01635 and WP-01491)
            if use_float:
                c_temp = c_float()
                result = self.driver.GetTemperatureF(byref(c_temp))
            else:
                c_temp = c_int()
                result = self.driver.GetTemperature(byref(c_temp))

                # MZ: why were we trying to use GetTemperatureF(float) here, when above it was GetTemperature(int)?
                # temperature = c_float()
                # temp_success = self.driver.GetTemperatureF(byref(temperature))
                # reading.detector_temperature_degC = temperature.value

            label = self.get_error_code(result)
            
            if label in [ "DRV_SUCCESS", 
                          "DRV_TEMPERATURE_DRIFT",
                          "DRV_TEMPERATURE_STABILIZED",
                          "DRV_TEMPERATURE_NOT_REACHED",
                          "DRV_TEMPERATURE_NOT_STABILIZED" ]:
                detector_temperature_degC = c_temp.value
                log.debug(f"Andor temperature {detector_temperature_degC:.2f} ({label})")
                return detector_temperature_degC
            else:
                log.error(f"unable to read detector temperature, result was {label}")


    def _close_ex_shutter(self):
        self.check_result(self.driver.SetShutterEx(1, 1, self.SHUTTER_SPEED_MS, self.SHUTTER_SPEED_MS, 2), "SetShutterEx(2)")
        self.settings.state.shutter_enabled = False
        return SpectrometerResponse(True)

    def _open_ex_shutter(self):
        self.check_result(self.driver.SetShutterEx(1, 1, self.SHUTTER_SPEED_MS, self.SHUTTER_SPEED_MS, 1), "SetShutterEx(1)")
        self.settings.state.shutter_enabled = True
        return SpectrometerResponse(True)

    ###############################################################
    # Public Methods
    ###############################################################

    def check_result(self, result, func):
        if result != self.SUCCESS:
            name = self.get_error_code(result)
            msg = f"error calling {func}: {result} ({name})"
            log.error(msg)
            raise RuntimeError(msg)
        log.debug(f"successfully called {func}")

    def connect(self):
        if self.dll_fail:
            return SpectrometerResponse(False, error_msg="can't find Andor DLL; please confirm Andor Driver Pack 2 installed")

        cameraHandle = c_int()
        self.check_result(self.driver.GetCameraHandle(self.spec_index, byref(cameraHandle)), "GetCameraHandle") # step 1
        self.check_result(self.driver.SetCurrentCamera(cameraHandle.value), "SetCurrentCamera") # step 2

        try:
            path_to_ini = create_string_buffer(b'\000' * 256) 
            self.check_result(self.driver.Initialize(path_to_ini), "Initialize") # step 3
        except:
            log.error("Andor.Initialize failed", exc_info=1)
            return SpectrometerResponse(False, error_msg="Andor initialization failed")

        # @todo missing: step 4 capabilities

        self.get_serial_number() # step 16
        self.init_tec_setpoint() # step 5+6
        self.init_detector_area() # step 7

        if not self._check_config_file():
            log.debug("stubbing Andor EEPROM")
            self.settings.eeprom.stubbed = True
            self.config_values = {
                'detector_serial_number': self.serial,
                'wavelength_coeffs': [0,1,0,0,0],
                'excitation_nm_float': 0,
                'raman_intensity_coeffs': [],
                'raman_intensity_calibration_order': 0,
                'invert_x_axis': False,
                'roi_horizontal_start': 0,
                'roi_horizontal_end': 0,
                'roi_vertical_region_1_start': 0,
                'roi_vertical_region_1_end': 0,
                'stubbed': True
            }
            log.debug(f"connect: config file not found, so defaulting to these: {self.config_values}")
            self.save_config()
        else:
            self._load_config_values()
            log.debug(f"connect: loaded config file: {self.config_values}")

        self.check_result(self.driver.CoolerON(), "CoolerON") # step 8
        self.check_result(self.driver.SetAcquisitionMode(1), "SetAcquisitionMode(single_scan)") # step 9
        self.check_result(self.driver.SetTriggerMode(0), "SetTriggerMode") # step 10
        self.check_result(self.driver.SetReadMode(0), "SetReadMode(full_vertical_binning)") # step 11

        self.init_detector_speed() # step 12+13

        # step 14
        self.check_result(self.driver.SetShutterEx(1, 1, self.SHUTTER_SPEED_MS, self.SHUTTER_SPEED_MS, 0), "SetShutterEx(fully automatic external with internal always open)")
        self.settings.state.shutter_enabled = True

        # step 15
        self.set_integration_time_ms(self.settings.eeprom.startup_integration_time_ms)

        # step 17 (WasatchNET doesn't do this)
        self._obtain_gain_info()

        # step 18 (WasatchNET doesn't do this)
        roi = ROI(self.settings.eeprom.roi_vertical_region_1_start, 
                  self.settings.eeprom.roi_vertical_region_1_end)
        if roi.start != 0 and roi.end != 0:
            # although camera can probably support the (0, 0) case, it's 
            # convenient to treat as unconfigured defaults
            self.set_vertical_binning(roi)

        # success!
        log.info("AndorDevice successfully connected")

        self.connected = True
        self.settings.eeprom.active_pixels_horizontal = self.pixels 
        self.settings.eeprom.has_cooling = True
        return SpectrometerResponse(data=True)

    def set_take_one_request(self, tor):
        log.debug(f"set_take_one_request: storing {tor}")
        self.take_one_request = tor

    def set_vertical_binning(self, roi):
        """
        Note that this follows the same (start, end) vertical ROI API as 
        FeatureInterfaceDevice.set_vertical_binning, and dynamically translates 
        that into the (middle, height) Andor API.
        """

        if isinstance(roi, ROI):
            start, end = roi.start, roi.end
        elif len(roi) == 2:
            start, end = roi[0], roi[1]
        else:
            log.error("set_vertical_binning requires an ROI object or tuple of (start, stop) lines")
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")

        if start < 0 or end < 0:
            log.error("set_vertical_binning requires POSITIVE (start, stop) lines")
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")

        # enforce ascending order
        if start >= end:
            log.error("set_vertical_binning requires ascending order (ignoring %d, %d)", start, end)
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")


        height = end - start
        center = int(round(height / 2, 0)) + start

        log.debug(f"setting Single-Track vertical binning of ROI (start {start}, end {end}) (center {center}, height {height})")
        self.check_result(self.driver.SetReadMode(3), "SetReadMode(single-track)")
        self.check_result(self.driver.SetSingleTrack(center, height), "SetSingleTrack")

        return SpectrometerResponse(data=True)

    def save_config(self, eeprom=None):
        """
        The user has edited the "virtual EEPROM", for instance using ENLIGHTEN's 
        EEPROM Editor, and wants to save the new EEPROM.  Therefore we need to
        generate a fresh JSON equivalent and write it to disk.

        @param eeprom: if provided, overwrite current settings with those in the 
               passed dict before writing to disk
        """
        if eeprom is not None:
            self.update_config_from_eeprom(eeprom)

        f = open(self.config_file, 'w')
        json.dump(self.config_values, f, indent=2, sort_keys=True)
        log.debug(f"saved {self.config_file}: {self.config_values}")

    def update_config_from_eeprom(self, eeprom):
        """ 
        Populates a dict used to update the configuration file `self.config_file`
        from `self.settings.eeprom` members.
        """
        # first, copy over any EEPROM fields which have a different name in 
        # wasatch.EEPROM vs the external JSON file 
        for json_name, python_name in self.config_names_to_eeprom.items():
            self.config_values[json_name] = eeprom.multi_wavelength_calibration.get(python_name)

        # now do all the standard attributes of the wasatch.EEPROM, adding 
        # their values into the same dict
        for python_name in eeprom.__dict__:
            if python_name in self.config_values:
                self.config_values[python_name] = eeprom.multi_wavelength_calibration.get(python_name)

    def _load_config_values(self):
        """
        Loads configuration from file `self.config_file` and populates `self.settings.eeprom` with members.
        """
        f = open(self.config_file,)
        self.config_values = dict(json.load(f))
        log.debug(f"loaded {self.config_file}: {self.config_values}")

        # handle wp_ prefixes
        for k, v in self.config_names_to_eeprom.items():
            if k in self.config_values:
                self.settings.eeprom.multi_wavelength_calibration.set(v, self.config_values[k])

        # same spelling
        for k in [ 'model', 
                   'stubbed', 
                   'detector', 
                   'serial_number', 
                   'invert_x_axis',
                   'wavelength_coeffs', 
                   'excitation_nm_float',
                   'roi_horizontal_end',
                   'roi_horizontal_start',
                   'roi_vertical_region_1_start',
                   'roi_vertical_region_1_end',
                   'raman_intensity_coeffs',
                   'raman_intensity_calibration_order',
                   'startup_temp_degC', 
                   'startup_integration_time_ms' ]:
            if k in self.config_values:
                self.settings.eeprom.multi_wavelength_calibration.set(k, self.config_values[k])

        # default missing-but-obvious fields
        if "raman_intensity_coeffs" in self.config_values:
            if "raman_intensity_calibration_order" not in self.config_values:
                self.settings.eeprom.multi_wavelength_calibration.set("raman_intensity_calibration_order", len(self.settings.eeprom.raman_intensity_coeffs) - 1)

        # post-load initialization
        if 'startup_temp_degC' in self.config_values:
            self.set_tec_setpoint(self.settings.eeprom.startup_temp_degC)

    def acquire_data(self):
        # handle TakeOneRequest.take_dark
        dark_reading = None
        tor = self.take_one_request
        log.debug(f"acquire_data: tor {tor}")
        if tor and tor.take_dark:
            self.set_shutter_enable(True)
            dark_reading = self._take_one_averaged_reading()
            if dark is None:
                return SpectrometerResponse(False, error_msg="failed to collect dark")
            self.set_shutter_enable(False)

        # get spectrum (potentially averaged)
        reading = self._take_one_averaged_reading()
        if reading is None:
            return SpectrometerResponse(False, error_msg="failed to collect reading")

        # fill-out metadata
        if dark_reading:
            reading.dark = dark_reading.spectrum
        reading.integration_time_ms = self.settings.state.integration_time_ms
        reading.detector_temperature_degC = self.get_detector_temperature_degC()

        # only bump session_count by one for a dark-corrected averaged reading 
        # (regardless of how many spectra were collected to generate it)
        self.session_reading_count += 1
        reading.session_count = self.session_reading_count

        # update or close TakeOneRequest
        if tor is not None:
            log.debug(f"acquire_data: adding to to reading: {tor}")
            reading.take_one_request = tor

            # XL units don't provide laser control
            if tor.laser_warmup_ms:     log.error("TakeOneRequest.laser_warmup_ms not supported")
            if tor.auto_raman_request:  log.error("TakeOneRequest.auto_raman_request not supported")
            if tor.enable_laser_before: log.error("TakeOneRequest.enable_laser_before not supported")
            if tor.disable_laser_after: log.error("TakeOneRequest.disable_laser_after not supported")

            # readings_current/target are for "fast (streaming) BatchCollection"
            if tor.readings_target:
                tor.readings_current += 1

            if not tor.readings_target or tor.readings_current >= tor.readings_target:
                log.debug(f"completed {tor}")
                self.take_one_request = None

        log.debug(f"acquire_data: reading {reading}")
        return SpectrometerResponse(data=reading)

    def set_shutter_enable(self, enable):
        if enable:
            return self._open_ex_shutter()
        else:
            return self._close_ex_shutter()

    def set_integration_time_ms(self, ms):
        self.integration_time_ms = ms
        log.debug(f"setting integration time to {self.integration_time_ms}ms")

        exposure = c_float()
        accumulate = c_float()
        kinetic = c_float()

        sec = ms / 1000.0
        self.check_result(self.driver.SetExposureTime(c_float(sec)), f"SetExposureTime({sec})")
        self.check_result(self.driver.GetAcquisitionTimings(byref(exposure), byref(accumulate), byref(kinetic)), "GetAcquisitionTimings")
        log.debug(f"read integration time of {exposure.value:.3f}sec (expected {ms}ms)")
        return SpectrometerResponse(data=True)

    def get_serial_number(self): # -> SpectrometerResponse 
        sn = c_int()
        self.check_result(self.driver.GetCameraSerialNumber(byref(sn)), "GetCameraSerialNumber")
        self.serial = f"CCD-{sn.value}"
        self.settings.eeprom.serial_number = self.serial # temporary
        self.settings.eeprom.detector_serial_number = self.serial
        log.debug(f"get_serial_number: connected to {self.serial}")
        return SpectrometerResponse(True)

    def init_tec_setpoint(self): # -> SpectrometerResponse 
        minTemp = c_int()
        maxTemp = c_int()
        self.check_result(self.driver.GetTemperatureRange(byref(minTemp), byref(maxTemp)), "GetTemperatureRange") # step 5

        self.settings.eeprom.max_temp_degC = maxTemp.value
        self.settings.eeprom.min_temp_degC = minTemp.value

        # commenting-out because Andor camera is reporting -120C for a device 
        # only rated at -60C...leaving hardcoded default for now
        #
        # self.settings.eeprom.startup_temp_degC = minTemp.value 

        # however the startup temperature was set (hardcode, JSON, clamped to min)...apply it
        self.setpoint_deg_c = self.settings.eeprom.startup_temp_degC 
        self.check_result(self.driver.SetTemperature(self.setpoint_deg_c), f"SetTemperature({self.setpoint_deg_c})") # step 6
        log.debug(f"set TEC to {self.setpoint_deg_c}°C (range {self.settings.eeprom.min_temp_degC}, {self.settings.eeprom.max_temp_degC})")

        return SpectrometerResponse(True)

    def toggle_tec(self, toggle_state):
        c_toggle = c_int(toggle_state)
        self.tec_enabled = c_toggle.value
        if self.tec_enabled:
            self.check_result(self.driver.CoolerON(), "CoolerON")
        else:
            self.check_result(self.driver.CoolerOFF(), "CoolerOFF")
        return SpectrometerResponse(True)

    def set_tec_setpoint(self, set_temp):
        if set_temp < self.settings.eeprom.min_temp_degC or set_temp > self.settings.eeprom.max_temp_degC:
            log.error(f"requested temp of {set_temp}, but it is outside range ({self.settings.eeprom.min_temp_degC}C, {self.settings.eeprom.max_temp_degC}C)")
            return
        if not self.tec_enabled:
            log.error(f"returning because tec_enabled {self.tec_enabled}")
            return
        self.setpoint_deg_c = set_temp
        # I don't think CoolerON should need to be called, but I'm not seeing temperature changes
        # when it is not present here.
        self.check_result(self.driver.CoolerON(), "CoolerON")
        self.check_result(self.driver.SetTemperature(self.setpoint_deg_c), f"SetTemperature({self.setpoint_deg_c})")
        return SpectrometerResponse(True)

    def init_detector_area(self):
        xPixels = c_int()
        yPixels = c_int()
        self.check_result(self.driver.GetDetector(byref(xPixels), byref(yPixels)), "GetDetector(x, y)")
        self.pixels = xPixels.value
        self.height = yPixels.value
        log.debug(f"detector {self.pixels} width x {self.height} height")
        return SpectrometerResponse(True)

    def _obtain_gain_info(self):
        num_gains = c_int()
        result = self.driver.GetNumberPreAmpGains(byref(num_gains))
        assert(self.SUCCESS == result), f"unable to get number of gains. Got result {result}"
        log.debug(f"got number of gains is {num_gains.value}")
        self.gain_options = []
        self.gain_idx = []
        spec_gain_opt = c_float()
        for i in range(num_gains.value):
            result = self.driver.GetPreAmpGain(i, byref(spec_gain_opt))
            assert(self.SUCCESS == result), f"unable to get gains index {i}. Got result {result}"
            self.gain_options.append(spec_gain_opt.value)
            self.gain_idx.append(i)
        self.gain_idx = self.gain_idx[::-1]
        self.gain_options = self.gain_options[::-1]
        log.debug(f"obtained gain options for spec, values were {self.gain_options}")

    def init_detector_speed(self):
        speed = c_float()

        # for CCDs, set vertical to recommended
        if self.height > 1:
            VSnumber = c_int()
            self.check_result(self.driver.GetFastestRecommendedVSSpeed(byref(VSnumber), byref(speed)), "GetFastestRecommendedVSSpeed") # step 12
            self.check_result(self.driver.SetVSSpeed(VSnumber.value), f"SetVSSpeed({VSnumber.value})")
        else:
            log.debug("vertical speed does not apply to linear array detectors")

        # set horizontal to max
        nAD = c_int()
        sIndex = c_int()
        STemp = 0.0
        HSnumber = 0
        ADnumber = 0
        self.check_result(self.driver.GetNumberADChannels(byref(nAD)), "GetNumberADChannels") # step 13.1
        for iAD in range(nAD.value):
            self.check_result(self.driver.GetNumberHSSpeeds(iAD, 0, byref(sIndex)), f"GetNumberHSSpeeds({iAD})") # step 13.2
            for iSpeed in range(sIndex.value):
                self.check_result(self.driver.GetHSSpeed(iAD, 0, iSpeed, byref(speed)), f"GetHSSpeed(iAD {iAD}, iSpeed {iSpeed})") # step 13.3
                if speed.value > STemp:
                    STemp = speed.value
                    HSnumber = iSpeed
                    ADnumber = iAD
        self.check_result(self.driver.SetADChannel(ADnumber), f"SetADChannel({ADnumber})") # 13.4
        self.check_result(self.driver.SetHSSpeed(0, HSnumber), f"SetHSSpeed({HSnumber})") # 13.5
        log.debug(f"set AD channel {ADnumber} with horizontal speed {HSnumber} ({STemp})")
        return SpectrometerResponse(True)

    def set_scans_to_average(self, value):
        value = int(value)

        # reset count on changes
        self.sum_count = 0
        self.summed_spectrum = None
        self.settings.state.scans_to_average = value
        return SpectrometerResponse(True)

    def get_error_code_long(self, code):
        if code in self.error_codes:
            return f"{code} ({self.error_codes[code]})"
        return f"{code} (UNKNOWN_ANDOR_ERROR)"

    def get_error_code(self, code):
        if code in self.error_codes:
            return self.error_codes[code]
        return "UNKNOWN_ANDOR_ERROR"

    ## @see ATMCD32D.H
    def load_error_codes(self):
        self.error_codes = {
            20001: "DRV_ERROR_CODES",
            20002: "DRV_SUCCESS",
            20003: "DRV_VXDNOTINSTALLED",
            20004: "DRV_ERROR_SCAN",
            20005: "DRV_ERROR_CHECK_SUM",
            20006: "DRV_ERROR_FILELOAD",
            20007: "DRV_UNKNOWN_FUNCTION",
            20008: "DRV_ERROR_VXD_INIT",
            20009: "DRV_ERROR_ADDRESS",
            20010: "DRV_ERROR_PAGELOCK",
            20011: "DRV_ERROR_PAGEUNLOCK",
            20012: "DRV_ERROR_BOARDTEST",
            20013: "DRV_ERROR_ACK",
            20014: "DRV_ERROR_UP_FIFO",
            20015: "DRV_ERROR_PATTERN",
            20017: "DRV_ACQUISITION_ERRORS",
            20018: "DRV_ACQ_BUFFER",
            20019: "DRV_ACQ_DOWNFIFO_FULL",
            20020: "DRV_PROC_UNKONWN_INSTRUCTION",
            20021: "DRV_ILLEGAL_OP_CODE",
            20022: "DRV_KINETIC_TIME_NOT_MET",
            20023: "DRV_ACCUM_TIME_NOT_MET",
            20024: "DRV_NO_NEW_DATA",
            20025: "DRV_PCI_DMA_FAIL",
            20026: "DRV_SPOOLERROR",
            20027: "DRV_SPOOLSETUPERROR",
            20028: "DRV_FILESIZELIMITERROR",
            20029: "DRV_ERROR_FILESAVE",
            20033: "DRV_TEMPERATURE_CODES",
            20034: "DRV_TEMPERATURE_OFF",
            20035: "DRV_TEMPERATURE_NOT_STABILIZED",
            20036: "DRV_TEMPERATURE_STABILIZED",
            20037: "DRV_TEMPERATURE_NOT_REACHED",
            20038: "DRV_TEMPERATURE_OUT_RANGE",
            20039: "DRV_TEMPERATURE_NOT_SUPPORTED",
            20040: "DRV_TEMPERATURE_DRIFT",
            20049: "DRV_GENERAL_ERRORS",
            20050: "DRV_INVALID_AUX",
            20051: "DRV_COF_NOTLOADED",
            20052: "DRV_FPGAPROG",
            20053: "DRV_FLEXERROR",
            20054: "DRV_GPIBERROR",
            20055: "DRV_EEPROMVERSIONERROR",
            20064: "DRV_DATATYPE",
            20065: "DRV_DRIVER_ERRORS",
            20066: "DRV_P1INVALID", # param #1
            20067: "DRV_P2INVALID", # param #2
            20068: "DRV_P3INVALID", # param #3
            20069: "DRV_P4INVALID", # param #4
            20070: "DRV_INIERROR",
            20071: "DRV_COFERROR",
            20072: "DRV_ACQUIRING",
            20073: "DRV_IDLE",
            20074: "DRV_TEMPCYCLE",
            20075: "DRV_NOT_INITIALIZED",
            20076: "DRV_P5INVALID",
            20077: "DRV_P6INVALID",
            20078: "DRV_INVALID_MODE",
            20079: "DRV_INVALID_FILTER",
            20080: "DRV_I2CERRORS",
            20081: "DRV_I2CDEVNOTFOUND",
            20082: "DRV_I2CTIMEOUT",
            20083: "DRV_P7INVALID",
            20084: "DRV_P8INVALID",
            20085: "DRV_P9INVALID",
            20086: "DRV_P10INVALID",
            20087: "DRV_P11INVALID",
            20089: "DRV_USBERROR",
            20090: "DRV_IOCERROR",
            20091: "DRV_VRMVERSIONERROR",
            20092: "DRV_GATESTEPERROR",
            20093: "DRV_USB_INTERRUPT_ENDPOINT_ERROR",
            20094: "DRV_RANDOM_TRACK_ERROR",
            20095: "DRV_INVALID_TRIGGER_MODE",
            20096: "DRV_LOAD_FIRMWARE_ERROR",
            20097: "DRV_DIVIDE_BY_ZERO_ERROR",
            20098: "DRV_INVALID_RINGEXPOSURES",
            20099: "DRV_BINNING_ERROR",
            20100: "DRV_INVALID_AMPLIFIER",
            20101: "DRV_INVALID_COUNTCONVERT_MODE",
            20102: "DRV_USB_INTERRUPT_ENDPOINT_TIMEOUT",
            20115: "DRV_ERROR_MAP",
            20116: "DRV_ERROR_UNMAP",
            20117: "DRV_ERROR_MDL",
            20118: "DRV_ERROR_UNMDL",
            20119: "DRV_ERROR_BUFFSIZE",
            20121: "DRV_ERROR_NOHANDLE",
            20130: "DRV_GATING_NOT_AVAILABLE",
            20131: "DRV_FPGA_VOLTAGE_ERROR",
            20150: "DRV_OW_CMD_FAIL",
            20151: "DRV_OWMEMORY_BAD_ADDR",
            20152: "DRV_OWCMD_NOT_AVAILABLE",
            20153: "DRV_OW_NO_SLAVES",
            20154: "DRV_OW_NOT_INITIALIZED",
            20155: "DRV_OW_ERROR_SLAVE_NUM",
            20156: "DRV_MSTIMINGS_ERROR",
            20173: "DRV_OA_NULL_ERROR",
            20174: "DRV_OA_PARSE_DTD_ERROR",
            20175: "DRV_OA_DTD_VALIDATE_ERROR",
            20176: "DRV_OA_FILE_ACCESS_ERROR",
            20177: "DRV_OA_FILE_DOES_NOT_EXIST",
            20178: "DRV_OA_XML_INVALID_OR_NOT_FOUND_ERROR",
            20179: "DRV_OA_PRESET_FILE_NOT_LOADED",
            20180: "DRV_OA_USER_FILE_NOT_LOADED",
            20181: "DRV_OA_PRESET_AND_USER_FILE_NOT_LOADED",
            20182: "DRV_OA_INVALID_FILE",
            20183: "DRV_OA_FILE_HAS_BEEN_MODIFIED",
            20184: "DRV_OA_BUFFER_FULL",
            20185: "DRV_OA_INVALID_STRING_LENGTH",
            20186: "DRV_OA_INVALID_CHARS_IN_NAME",
            20187: "DRV_OA_INVALID_NAMING",
            20188: "DRV_OA_GET_CAMERA_ERROR",
            20189: "DRV_OA_MODE_ALREADY_EXISTS",
            20190: "DRV_OA_STRINGS_NOT_EQUAL",
            20191: "DRV_OA_NO_USER_DATA",
            20192: "DRV_OA_VALUE_NOT_SUPPORTED",
            20193: "DRV_OA_MODE_DOES_NOT_EXIST",
            20194: "DRV_OA_CAMERA_NOT_SUPPORTED",
            20195: "DRV_OA_FAILED_TO_GET_MODE",
            20196: "DRV_OA_CAMERA_NOT_AVAILABLE",
            20211: "DRV_PROCESSING_FAILED",
            20990: "DRV_ERROR_NOCAMERA",
            20991: "DRV_NOT_SUPPORTED",
            20992: "DRV_NOT_AVAILABLE"
        }
