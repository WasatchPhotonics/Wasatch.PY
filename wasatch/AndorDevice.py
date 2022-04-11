import re
import os
import usb
import time
import json
import queue
import struct
import logging
import datetime
from ctypes import *
from typing import TypeVar, Any, Callable

from .SpectrometerSettings        import SpectrometerSettings
from .SpectrometerState           import SpectrometerState
from .SpectrometerResponse        import SpectrometerResponse
from .SpectrometerRequest         import SpectrometerRequest
from .SpectrometerResponse        import ErrorLevel
from .InterfaceDevice             import InterfaceDevice
from .DeviceID                    import DeviceID
from .Reading                     import Reading

log = logging.getLogger(__name__)

class AndorDevice(InterfaceDevice):
    """
    This is the basic implementation of our interface with Andor cameras     

    @todo convert the different asserts to SpectrometerResponse returns
    ##########################################################################
    This class adopts the external device interface structure
    This invlovles receiving a request through the handle_request function
    A request is processed based on the key in the request
    The processing function passes the commands to the requested device
    Once it recevies a response from the connected device it then passes that
    back up the chain
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
    ############################################################################
    """

    SUCCESS = 20002             #!< see page 330 of Andor SDK documentation
    SHUTTER_SPEED_MS = 35       #!< not sure where this comes from...ask Caleb - TS

    def __init__(self, device_id, message_queue=None) -> None:
        # if passed a string representation of a DeviceID, deserialize it
        super().__init__()
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.device_id      = device_id
        self.message_queue  = message_queue

        self.connected = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. Although a logical queue, has nothing to do with multiprocessing.
        self.command_queue = []

        self.immediate_mode = False

        self.settings = SpectrometerSettings(self.device_id)
        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0
        self.take_one               = False
        self.failure_count          = 0
        self.dll_fail               = False
        self.toggle_state           = True

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0
        self.spec_index = 0 
        self._scan_averaging = 1
        self.dark = None
        self.boxcar_half_width = 0

        # select appropriate Andor library per architecture
        try:
            if 64 == struct.calcsize("P") * 8:
                self.driver = cdll.LoadLibrary(r"C:\Program Files\Andor SDK\atmcd64d.dll")
            else:
                self.driver = cdll.LoadLibrary(r"C:\Program Files\Andor SDK\atmcd32d.dll")
        except Exception as e:
            log.error(f"Error while loading DLL library of {e}")
            self.dll_fail = True

        self.settings.eeprom.model = "Andor"
        self.settings.eeprom.detector = "Andor" # Andor API doesn't have access to detector info
        self.settings.eeprom.wavelength_coeffs = [0,1,0,0]
        self.settings.eeprom.has_cooling = True

        self.process_f = self._init_process_funcs()

    ###############################################################
    # Private Methods
    ###############################################################

    def _init_process_funcs(self) -> dict[str, Callable[..., Any]]:
        process_f = {}

        process_f["connect"] = self.connect
        process_f["acquire_data"] = self.acquire_data
        process_f["set_shutter_enable"] = self.set_shutter_enable
        process_f["set_integration_time_ms"] = self.set_integration_time_ms
        process_f["get_serial_number"] = self.get_serial_number
        process_f["init_tec_setpoint"] = self.init_tec_setpoint
        process_f["set_tec_setpoint"] = self.set_tec_setpoint
        process_f["init_detector_area"] = self.init_detector_area
        process_f["scans_to_average"] = self.scans_to_average

        ##################################################################
        # What follows is the old init-lambdas that are squashed into process_f
        # Long term, the upstream requests should be changed to match the new format
        # This is an easy fix for the time being to make things behave
        ##################################################################
        process_f["integration_time_ms"] = lambda x: self.set_integration_time_ms(x) # conversion from millisec to microsec
        process_f["shutter_enable"] = lambda x: self.set_shutter_enable(bool(x))
        process_f["detector_tec_enable"]                = lambda x: self.toggle_tec(bool(x))
        process_f["detector_tec_setpoint_degC"]         = lambda x: self.set_tec_setpoint(int(round(x)))

        return process_f

    def _update_wavelength_coeffs(self, coeffs: list[float]) -> None:
        self.settings.eeprom.wavelength_coeffs = coeffs
        self.config_values['wavelength_coeffs'] = coeffs
        f = open(self.config_file, 'w')
        json.dump(self.config_values, f)

    def _get_default_data_dir(self) -> str:
        if os.name == "nt":
            return os.path.join(os.path.expanduser("~"), "Documents", "EnlightenSpectra")
        return os.path.join(os.environ["HOME"], "EnlightenSpectra")

    def _check_config_file(self) -> bool:
        self.config_dir = os.path.join(self._get_default_data_dir(), 'config')
        self.config_file = os.path.join(self.config_dir, self.serial + '.json')
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)
        return os.path.isfile(self.config_file)

    def _get_spectrum_raw(self) -> list[float]:
        log.debug("requesting spectrum");
        #################
        # read spectrum
        #################
        #int[] spec = new int[pixels];
        spec_arr = c_long * self.pixels
        spec_init_vals = [0] * self.pixels
        spec = spec_arr(*spec_init_vals)

        # ask for spectrum then collect, NOT multithreaded (though we should look into that!), blocks
        #spec = new int[pixels];     //defaults to all zeros
        self.driver.StartAcquisition();
        self.driver.WaitForAcquisition();
        success = self.driver.GetAcquiredData(spec, c_ulong(self.pixels));

        if (success != self.SUCCESS):
            log.debug(f"getting spectra did not succeed. Received code of {success}. Returning")
            return

        convertedSpec = [x for x in spec]

        #if (self.eeprom.featureMask.invertXAxis):
         #   convertedSpec.reverse()

        log.debug(f"getSpectrumRaw: returning {len(spec)} pixels");
        return convertedSpec;

    def _take_one_averaged_reading(self) -> SpectrometerResponse:
        averaging_enabled = (self.settings.state.scans_to_average > 1)

        if averaging_enabled and not self.settings.state.free_running_mode:
            # collect the entire averaged spectrum at once (added for
            # BatchCollection with laser delay)
            #
            # So: we're NOT in "free-running" mode, so we're basically being
            # slaved to parent process and doing exactly what is requested
            # "on command."  That means we can perform a big, heavy blocking
            # scan average all at once, because they requested it.
            self.sum_count = 0
            loop_count = self.settings.state.scans_to_average
        else:
            # we're in free-running mode
            loop_count = 1

        log.debug("take_one_averaged_reading: loop_count = %d", loop_count)

        # either take one measurement (normal), or a bunch (blocking averaging)
        reading = None
        for loop_index in range(0, loop_count):

            # start a new reading
            # NOTE: reading.timestamp is when reading STARTED, not FINISHED!
            reading = Reading(self.device_id)

            if self.settings.eeprom.has_cooling and self.toggle_state:
                c_temp = c_int()
                result = self.driver.GetTemperature(0,c_temp)
                if (self.SUCCESS != result):
                    log.error(f"unable to read tec temp, result was {result}")
                else:
                    log.debug(f"andor read temperature, value of {c_temp.value}")
                    reading.detector_temperature_degC = c_temp.value
            try:
                reading.integration_time_ms = self.settings.state.integration_time_ms
                reading.laser_power_perc    = self.settings.state.laser_power_perc
                reading.laser_power_mW      = self.settings.state.laser_power_mW
                reading.laser_enabled       = self.settings.state.laser_enabled
                reading.spectrum            = self._get_spectrum_raw()

                temperature = c_float()
                temp_success = self.driver.GetTemperatureF(byref(temperature))

                reading.detector_temperature_degC = temperature.value
            except usb.USBError:
                self.failure_count += 1
                log.error(f"Andor Device: encountered USB error in reading for device {self.device}")

            if reading.spectrum is None or reading.spectrum == []:
                if self.failure_count > 3:
                    return SpectrometerResponse(data=False,error_msg="exceeded failure for readings")

            if not reading.failure:
                if averaging_enabled:
                    if self.sum_count == 0:
                        self.summed_spectra = [float(i) for i in reading.spectrum]
                    else:
                        log.debug("device.take_one_averaged_reading: summing spectra")
                        for i in range(len(self.summed_spectra)):
                            self.summed_spectra[i] += reading.spectrum[i]
                    self.sum_count += 1
                    log.debug("device.take_one_averaged_reading: summed_spectra : %s ...", self.summed_spectra[0:9])

            # count spectra
            self.session_reading_count += 1
            reading.session_count = self.session_reading_count
            reading.sum_count = self.sum_count

            # have we completed the averaged reading?
            if averaging_enabled:
                if self.sum_count >= self.settings.state.scans_to_average:
                    reading.spectrum = [ x / self.sum_count for x in self.summed_spectra ]
                    log.debug("device.take_one_averaged_reading: averaged_spectrum : %s ...", reading.spectrum[0:9])
                    reading.averaged = True

                    # reset for next average
                    self.summed_spectra = None
                    self.sum_count = 0
            else:
                # if averaging isn't enabled...then a single reading is the
                # "averaged" final measurement (check reading.sum_count to confirm)
                reading.averaged = True

            # were we told to only take one (potentially averaged) measurement?
            if self.take_one and reading.averaged:
                log.debug("completed take_one")
                self.change_setting("cancel_take_one", True)


        log.debug("device.take_one_averaged_reading: returning %s", reading)
        if reading.spectrum is not None and reading.spectrum != []:
            self.failure_count = 0
        # reading.dump_area_scan()
        return SpectrometerResponse(data=reading)

    def _close_ex_shutter(self) -> SpectrometerResponse:
        assert(self.SUCCESS == self.driver.SetShutterEx(1, 1, self.SHUTTER_SPEED_MS, self.SHUTTER_SPEED_MS, 2)), "unable to set external shutter"
        return SpectrometerResponse(True)

    def _open_ex_shutter(self) -> SpectrometerResponse:
        assert(self.SUCCESS == self.driver.SetShutterEx(1, 1, self.SHUTTER_SPEED_MS, self.SHUTTER_SPEED_MS, 1)), "unable to set external shutter"
        return SpectrometerResponse(True)

    ###############################################################
    # Public Methods
    ###############################################################

    def connect(self) -> SpectrometerResponse:
        if self.dll_fail:
            return SpectrometerResponse(data=False,error_lvl=ErrorLevel.high,error_msg="couldn't load Andor dll")
        cameraHandle = c_int()
        assert(self.SUCCESS == self.driver.GetCameraHandle(self.spec_index, byref(cameraHandle))), "unable to get camera handle"
        assert(self.SUCCESS == self.driver.SetCurrentCamera(cameraHandle.value)), "unable to set current camera"
        log.info("initializing camera...")

        # not sure init_str is actually required
        init_str = create_string_buffer(b'\000' * 16)
        assert(self.SUCCESS == self.driver.Initialize(init_str)), "unable to initialize camera"
        log.info("success")

        self.get_serial_number()
        self.init_tec_setpoint()
        self.init_detector_area()

        if not self._check_config_file():
            self.config_values = {
                'detector_serial_number': self.serial,
                'wavelength_coeffs': [0,1,0,0],
                'excitation_nm_float': 0,
                }
            f = open(self.config_file, 'w')
            json.dump(self.config_values, f)
        else:
            f = open(self.config_file,)
            self.config_values = dict(json.load(f))
            self.settings.eeprom.wavelength_coeffs = self.config_values['wavelength_coeffs']
            self.settings.eeprom.excitation_nm_float = self.config_values['excitation_nm_float']

        assert(self.SUCCESS == self.driver.CoolerON()), "unable to enable TEC"
        log.debug("enabled TEC")

        assert(self.SUCCESS == self.driver.SetAcquisitionMode(1)), "unable to set acquisition mode"
        log.debug("configured acquisition mode (single scan)")

        assert(self.SUCCESS == self.driver.SetTriggerMode(0)), "unable to set trigger mode"
        log.debug("set trigger mode")

        assert(self.SUCCESS == self.driver.SetReadMode(0)), "unable to set read mode"
        log.debug("set read mode (full vertical binning)")

        self.init_detector_speed()

        assert(self.SUCCESS == self.driver.SetShutterEx(1, 1, self.SHUTTER_SPEED_MS, self.SHUTTER_SPEED_MS, 0)), "unable to set external shutter"
        log.debug("set shutter to fully automatic external with internal always open")

        self.set_integration_time_ms(10)
        self.connected = True
        self.settings.eeprom.active_pixels_horizontal = self.pixels 
        self.settings.eeprom.has_cooling = True
        self.settings.eeprom.max_temp_degC = self.detector_temp_max
        self.settings.eeprom.min_temp_degC = self.detector_temp_min
        return SpectrometerResponse(data=True)

    def acquire_data(self) -> SpectrometerResponse:
        reading = self._take_one_averaged_reading()
        return reading

    def set_shutter_enable(self, enable: bool) -> SpectrometerResponse:
        if enable:
            return self._open_ex_shutter()
        else:
            return self._close_ex_shutter()

    def set_integration_time_ms(self, ms: float) -> SpectrometerResponse:
        self.integration_time_ms = ms
        log.debug(f"setting integration time to {self.integration_time_ms}ms")

        exposure = c_float()
        accumulate = c_float()
        kinetic = c_float()
        assert(self.SUCCESS == self.driver.SetExposureTime(c_float(ms / 1000.0))), "unable to set integration time"
        assert(self.SUCCESS == self.driver.GetAcquisitionTimings(byref(exposure), byref(accumulate), byref(kinetic))), "unable to read acquisition timings"
        log.debug(f"read integration time of {exposure.value:.3f}sec (expected {ms}ms)")
        return SpectrometerResponse(data=True)

    def get_serial_number(self) -> SpectrometerResponse:
        sn = c_int()
        assert(self.SUCCESS == self.driver.GetCameraSerialNumber(byref(sn))), "can't get serial number"
        self.serial = f"CCD-{sn.value}"
        self.settings.eeprom.serial_number = self.serial
        log.debug(f"connected to {self.serial}")
        return SpectrometerResponse(True)

    def init_tec_setpoint(self) -> SpectrometerResponse:
        minTemp = c_int()
        maxTemp = c_int()
        assert(self.SUCCESS == self.driver.GetTemperatureRange(byref(minTemp), byref(maxTemp))), "unable to read temperature range"
        self.detector_temp_min = minTemp.value
        self.detector_temp_max = maxTemp.value

        self.setpoint_deg_c = self.detector_temp_min
        #assert(self.SUCCESS == self.driver.SetTemperature(self.setpoint_deg_c)), "unable to set temperature midpoint"
        log.debug(f"set TEC to {self.setpoint_deg_c} C (range {self.detector_temp_min}, {self.detector_temp_max})")
        return SpectrometerResponse(True)

    def toggle_tec(self, toggle_state):
        c_toggle = c_int(toggle_state)
        self.toggle_state = c_toggle.value
        if toggle_state:
            assert(self.SUCCESS == self.driver.CoolerON()), "unable to set temperature midpoint"
        else:
            assert(self.SUCCESS == self.driver.CoolerOFF()), "unable to set temperature midpoint"
        log.debug(f"Toggled TEC to state {c_toggle}")
        return SpectrometerResponse(True)

    def set_tec_setpoint(self, set_temp):
        if set_temp < self.detector_temp_min or set_temp > self.detector_temp_max:
            log.error(f"requested temp of {set_temp}, but it is outside range of min/max, {self.detector_temp_min}/{self.detector_temp_max}")
            return
        if not self.toggle_state:
            log.error(f"returning beacuse toggle state is {self.toggle_state}")
            return
        self.setpoint_deg_c = set_temp
        # I don't think CoolerON should need to be called, but I'm not seeing temperature changes
        # when it is not present here.
        assert(self.SUCCESS == self.driver.CoolerON()), "unable to enable TEC"
        assert(self.SUCCESS == self.driver.SetTemperature(self.setpoint_deg_c)), "unable to set temperature"
        log.debug(f"set TEC to {self.setpoint_deg_c} C (range {self.detector_temp_min}, {self.detector_temp_max})")
        return SpectrometerResponse(True)

    def init_detector_area(self) -> SpectrometerResponse:
        xPixels = c_int()
        yPixels = c_int()
        assert(self.SUCCESS == self.driver.GetDetector(byref(xPixels), byref(yPixels))), "unable to read detector dimensions"
        log.debug(f"detector {xPixels.value} width x {yPixels.value} height")
        self.pixels = xPixels.value
        return SpectrometerResponse(True)

    def init_detector_speed(self) -> SpectrometerResponse:
        # set vertical to recommended
        VSnumber = c_int()
        speed = c_float()
        assert(self.SUCCESS == self.driver.GetFastestRecommendedVSSpeed(byref(VSnumber), byref(speed))), "unable to get fastest recommended VS speed"
        assert(self.SUCCESS == self.driver.SetVSSpeed(VSnumber.value)), f"unable to set VS speed {VSnumber.value}"
        log.debug(f"set vertical speed to {VSnumber.value}")

        # set horizontal to max
        nAD = c_int()
        sIndex = c_int()
        STemp = 0.0
        HSnumber = 0
        ADnumber = 0
        assert(self.SUCCESS == self.driver.GetNumberADChannels(byref(nAD))), "unable to get number of AD channels"
        for iAD in range(nAD.value):
            assert(self.SUCCESS == self.driver.GetNumberHSSpeeds(iAD, 0, byref(sIndex))), f"unable to get number of HS speeds for AD {iAD}"
            for iSpeed in range(sIndex.value):
                assert(self.SUCCESS == self.driver.GetHSSpeed(iAD, 0, iSpeed, byref(speed))), f"unable to get HS speed for iAD {iAD}, iSpeed {iSpeed}"
                if speed.value > STemp:
                    STemp = speed.value
                    HSnumber = iSpeed
                    ADnumber = iAD
        assert(self.SUCCESS == self.driver.SetADChannel(ADnumber)), "unable to set AD channel to {ADnumber}"
        assert(self.SUCCESS == self.driver.SetHSSpeed(0, HSnumber)), "unable to set HS speed to {HSnumber}"
        log.debug(f"set AD channel {ADnumber} with horizontal speed {HSnumber} ({STemp})")
        return SpectrometerResponse(True)

    def scans_to_average(self, value: int) -> SpectrometerResponse:
        self.sum_count = 0
        self.settings.state.scans_to_average = int(value)
        return SpectrometerResponse(True)
