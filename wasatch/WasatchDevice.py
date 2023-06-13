import re
import os
import time
import psutil
import logging
import datetime
import threading
from queue import Queue
from configparser import ConfigParser
from . import utils
from .FeatureIdentificationDevice import FeatureIdentificationDevice
from .SpectrometerSettings import SpectrometerSettings
from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest import SpectrometerRequest
from .SpectrometerResponse import ErrorLevel
from .InterfaceDevice import InterfaceDevice
from .BalanceAcquisition import BalanceAcquisition
from .SpectrometerState import SpectrometerState
from .ControlObject import ControlObject
from .WasatchBus import WasatchBus
from .DeviceID import DeviceID
from .Reading import Reading
log = logging.getLogger(__name__)


class WasatchDevice(InterfaceDevice):

    def __init__(self, device_id, message_queue=None):
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)
        self.device_id = device_id
        self.message_queue = message_queue
        self.lock = threading.Lock()
        self.connected = False
        self.hardware = None
        self.command_queue = []
        self.immediate_mode = False
        self.settings = SpectrometerSettings()
        self.summed_spectra = None
        self.sum_count = 0
        self.session_reading_count = 0
        self.take_one = False
        self.last_complete_acquisition = None
        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0
        self.process_f = self._init_process_funcs()

    def connect(self):
        if self.device_id.is_usb() or self.device_id.is_mock():
            log.debug('trying to connect to %s device' % ('USB' if self.
                device_id.is_usb() else 'Mock'))
            result = self.connect_feature_identification()
            if result.data:
                log.debug('Connected to FeatureIdentificationDevice')
                self.connected = True
                self.initialize_settings()
                return SpectrometerResponse(True)
            else:
                log.debug('Failed to connect to FeatureIdentificationDevice')
                return result
        else:
            log.critical('unsupported DeviceID protocol: %s', self.device_id)
        log.debug("Can't connect to %s", self.device_id)
        return SpectrometerResponse(False)

    def disconnect(self):
        log.debug('WasatchDevice.disconnect: calling hardware disconnect')
        try:
            req = SpectrometerRequest('disconnect')
            self.hardware.handle_requests([req])
        except Exception as exc:
            log.critical('Issue disconnecting hardware', exc_info=1)
        time.sleep(0.1)
        self.connected = False
        return True

    def connect_feature_identification(self):
        FID_list = ['1000', '2000', '4000']
        pid_hex = self.device_id.get_pid_hex()
        if not pid_hex in FID_list:
            log.debug(
                'connect_feature_identification: device_id %s PID %s not in FID list %s'
                , self.device_id, pid_hex, FID_list)
            return SpectrometerResponse(False)
        dev = None
        try:
            log.debug('connect_fid: instantiating FID with device_id %s pid %s'
                , self.device_id, pid_hex)
            dev = FeatureIdentificationDevice(device_id=self.device_id,
                message_queue=self.message_queue)
            log.debug('connect_fid: instantiated')
            try:
                log.debug('connect_fid: calling dev.connect')
                response = dev.connect()
                log.debug('connect_fid: back from dev.connect')
            except Exception as exc:
                log.critical('connect_feature_identification: %s', exc,
                    exc_info=1)
                return SpectrometerResponse(False)
            if not response.data:
                log.critical('Low level failure in device connect')
                return response
            self.hardware = dev
        except Exception as exc:
            log.critical('Problem connecting to: %s', self.device_id,
                exc_info=1)
            return SpectrometerResponse(False)
        log.debug('Connected to FeatureIdentificationDevice %s', self.device_id
            )
        return SpectrometerResponse(True)

    def initialize_settings(self):
        if not self.connected:
            return
        self.settings = self.hardware.settings
        req_fw_v = SpectrometerRequest('get_microcontroller_firmware_version')
        req_fpga_v = SpectrometerRequest('get_fpga_firmware_version')
        req_int = SpectrometerRequest('get_integration_time_ms')
        req_gain = SpectrometerRequest('get_detector_gain')
        reqs = [req_fw_v, req_fpga_v, req_int, req_gain]
        self.hardware.handle_requests(reqs)
        self.settings.update_wavecal()
        self.settings.update_raman_intensity_factors()
        self.settings.dump()

    def acquire_data(self):
        """

        Process all enqueued settings, then read actual data (spectrum and

        temperatures) from the device.



        @see Controller.acquire_reading

        """
        log.debug('acquire_data: start')
        self.monitor_memory()
        if self.hardware.shutdown_requested:
            log.critical('acquire_data: hardware shutdown requested')
            return SpectrometerResponse(False, poison_pill=True)
        needs_acquisition = self.process_commands()
        if not (needs_acquisition or self.settings.state.free_running_mode):
            return SpectrometerResponse(None)
        if self.settings.state.integration_time_ms <= 0:
            log.debug('skipping acquire_data because no integration_time_ms')
            return SpectrometerResponse(None)
        return self.acquire_spectrum()

    def acquire_spectrum(self):
        averaging_enabled = self.settings.state.scans_to_average > 1
        acquire_response = SpectrometerResponse()
        dark_reading = SpectrometerResponse()
        if self.settings.state.acquisition_take_dark_enable:
            log.debug('taking internal dark')
            dark_reading = self.take_one_averaged_reading()
            if dark_reading.poison_pill or dark_reading.error_msg:
                log.debug(f'dark reading was bool {dark_reading}')
                return dark_reading
            log.debug('done taking internal dark')
        auto_enable_laser = (self.settings.state.
            acquisition_laser_trigger_enable)
        log.debug('acquire_spectrum: auto_enable_laser = %s', auto_enable_laser
            )
        if auto_enable_laser:
            log.debug('acquire_spectum: enabling laser, then sleeping %d ms',
                self.settings.state.acquisition_laser_trigger_delay_ms)
            req = SpectrometerRequest('set_laser_enable', args=[True])
            self.hardware.handle_requests([req])
            if self.hardware.shutdown_requested:
                log.debug(f'auto_enable_laser shutdown requested')
                acquire_response.poison_pill = True
                return acquire_response
            time.sleep(self.settings.state.
                acquisition_laser_trigger_delay_ms / 1000.0)
        self.perform_optional_throwaways()
        log.debug('taking averaged reading')
        take_one_response = self.take_one_averaged_reading()
        reading = take_one_response.data
        if take_one_response.poison_pill:
            log.debug(
                f'take_one_averaged_reading floating poison pill {take_one_response}'
                )
            return take_one_response
        if take_one_response.keep_alive:
            log.debug(f'floating up keep alive')
            return take_one_response
        if take_one_response.data == None:
            log.debug(
                f'Received a none reading, floating it up {take_one_response}')
            return take_one_response
        if dark_reading.data is not None:
            log.debug('attaching dark to reading')
            reading.dark = dark_reading.data.spectrum

        def disable_laser(force=False):
            if force or auto_enable_laser:
                log.debug('acquire_spectrum: disabling laser post-acquisition')
                req = SpectrometerRequest('set_laser_enable', args=[False])
                self.hardware.handle_requests([req])
                acquire_response.poison_pill = True
            return acquire_response
        if self.settings.eeprom.has_laser:
            try:
                count = 2 if self.settings.state.secondary_adc_enabled else 1
                for throwaway in range(count):
                    req = SpectrometerRequest('get_laser_temperature_raw')
                    res = self.hardware.handle_requests([req])[0]
                    if res.error_msg != '':
                        return res
                    reading.laser_temperature_raw = res.data
                    if self.hardware.shutdown_requested:
                        return disable_laser(force=True)
                req = SpectrometerRequest('get_laser_temperature_degC',
                    args=[reading.laser_temperature_raw])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.laser_temperature_degC = res.data
                if self.hardware.shutdown_requested:
                    return disable_laser(force=True)
                if not auto_enable_laser:
                    for func, attr in [('get_laser_enabled',
                        'laser_enabled'), ('can_laser_fire',
                        'laser_can_fire'), ('is_laser_firing',
                        'laser_is_firing')]:
                        req = SpectrometerRequest(func)
                        res = self.hardware.handle_requests([req])[0]
                        if res.error_msg != '':
                            return res
                        setattr(reading, attr, res.data)
                if self.hardware.shutdown_requested:
                    return disable_laser(force=True)
            except Exception as exc:
                log.debug('Error reading laser temperature', exc_info=1)
        if self.settings.state.secondary_adc_enabled:
            try:
                req = SpectrometerRequest('select_adc', args=[1])
                self.hardware.handle_requests([req])
                if self.hardware.shutdown_requested:
                    return disable_laser(force=True)
                for throwaway in range(2):
                    req = SpectrometerRequest('get_secondary_adc_raw')
                    res = self.hardware.handle_requests([req])[0]
                    if res.error_msg != '':
                        return res
                    reading.secondary_adc_raw = res.data
                    if self.hardware.shutdown_requested:
                        return disable_laser(force=True)
                req = SpectrometerRequest('get_secondary_adc_calibrated',
                    args=[reading.secondary_adc_raw])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.secondary_adc_calibrated = res.data
                req = SpectrometerRequest('select_adc', args=[0])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                if self.hardware.shutdown_requested:
                    return disable_laser(force=True)
            except Exception as exc:
                log.debug('Error reading secondary ADC', exc_info=1)
        disable_laser()
        if self.settings.eeprom.has_cooling:
            try:
                req = SpectrometerRequest('get_detector_temperature_raw')
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.detector_temperature_raw = res.data
                if self.hardware.shutdown_requested:
                    log.debug('detector_temperature_raw shutdown')
                    acquire_response.poison_pill = True
                    return acquire_response
                req = SpectrometerRequest('get_detector_temperature_degC',
                    args=[reading.detector_temperature_raw])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.detector_temperature_degC = res.data
                if self.hardware.shutdown_requested:
                    log.debug('detector_temperature_degC shutdown')
                    acquire_response.poison_pill = True
                    return acquire_response
            except Exception as exc:
                log.debug('Error reading detector temperature', exc_info=1)
        if self.settings.is_gen15():
            try:
                pass
            except Exception as exc:
                log.debug('Error reading ambient temperature', exc_info=1)
        if self.settings.eeprom.has_battery:
            if (self.settings.state.battery_timestamp is None or datetime.
                datetime.now() >= self.settings.state.battery_timestamp +
                datetime.timedelta(seconds=10)):
                req = SpectrometerRequest('get_battery_state_raw')
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.battery_raw = res.data
                if self.hardware.shutdown_requested:
                    log.debug('battery_raw shutdown')
                    acquire_response.poison_pill = True
                    return acquire_response
                req = SpectrometerRequest('get_battery_percentage')
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.battery_percentage = res.data
                if self.hardware.shutdown_requested:
                    log.debug('battery_perc shutdown')
                    acquire_response.poison_pill = True
                    return acquire_response
                self.last_battery_percentage = reading.battery_percentage
                req = SpectrometerRequest('get_battery_percentage')
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.battery_charging = res.data
                if self.hardware.shutdown_requested:
                    log.debug('battery_charging shutdown')
                    acquire_response.poison_pill = True
                    return acquire_response
                log.debug('battery: level %.2f%% (%s)', reading.
                    battery_percentage, 'charging' if reading.
                    battery_charging else 'not charging')
            elif reading is not None:
                reading.battery_percentage = self.last_battery_percentage
        acquire_response.data = reading
        self.last_complete_acquisition = datetime.datetime.now()
        return acquire_response

    def perform_optional_throwaways(self):
        if self.settings.is_micro() and self.take_one:
            count = 2
            readout_ms = 5
            if self.last_complete_acquisition is None or (datetime.datetime
                .now() - self.last_complete_acquisition).total_seconds() > 1.0:
                while count * (self.settings.state.integration_time_ms +
                    readout_ms) < 2000:
                    count += 1
            for i in range(count):
                log.debug(
                    'performing optional throwaway %d of %d before ramanMicro TakeOne'
                    , i, count)
                req = SpectrometerRequest('get_line')
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                spectrum_and_row = res.data

    def take_one_averaged_reading(self):
        take_one_response = SpectrometerResponse()
        averaging_enabled = self.settings.state.scans_to_average > 1
        if averaging_enabled and not self.settings.state.free_running_mode:
            self.sum_count = 0
            loop_count = self.settings.state.scans_to_average
        else:
            loop_count = 1
        log.debug('take_one_averaged_reading: loop_count = %d', loop_count)
        reading = None
        for loop_index in range(0, loop_count):
            reading = Reading(self.device_id)
            reading.integration_time_ms = (self.settings.state.
                integration_time_ms)
            reading.laser_power_perc = self.settings.state.laser_power_perc
            reading.laser_power_mW = self.settings.state.laser_power_mW
            reading.laser_enabled = self.settings.state.laser_enabled
            if (self.settings.state.area_scan_enabled and self.settings.
                state.area_scan_fast):
                with self.lock:
                    reading.area_scan_data = []
                    try:
                        rows = self.settings.eeprom.active_pixels_vertical
                        first = True
                        log.debug(
                            'trying to read a fast area scan frame of %d rows',
                            rows)
                        row_data = {}
                        while True:
                            log.debug(f'trying to read fast area scan row')
                            req = SpectrometerRequest('get_line', kwargs={
                                'trigger': first})
                            response = self.hardware.handle_requests([req])[0]
                            if response.error_msg != '':
                                return response
                            spectrum_and_row = response.data
                            first = False
                            if response.poison_pill:
                                take_one_response.transfer_response(response)
                                log.debug(
                                    f'get_line returned {spectrum_and_row}, breaking'
                                    )
                                break
                            elif response.keep_alive:
                                take_one_response.transfer_response(response)
                                log.debug(
                                    f'get_line returned keep alive, passing up'
                                    )
                                return take_one_response
                            elif self.hardware.shutdown_requested:
                                take_one_response.transfer_response(response)
                                return take_one_response
                            elif spectrum_and_row.spectrum is None:
                                log.debug(
                                    'device.take_one_averaged_spectrum: get_line None, sending keepalive for now (area scan fast)'
                                    )
                                take_one_response.transfer_response(response)
                                return take_one_response
                            spectrum = spectrum_and_row.spectrum
                            row = spectrum_and_row.row
                            reading.spectrum = spectrum
                            row_data[row] = spectrum
                            reading.timestamp_complete = datetime.datetime.now(
                                )
                            log.debug(
                                'device.take_one_averaged_reading(area scan fast): got %s ... (row %d) (min %d)'
                                , spectrum[0:9], row, min(reading.spectrum))
                        reading.area_scan_data = []
                        reading.area_scan_row_count = -1
                        for row in sorted(row_data.keys()):
                            reading.area_scan_data.append(row_data[row])
                            reading.area_scan_row_count = row
                    except Exception as exc:
                        log.critical('Error reading hardware data', exc_info=1)
                        reading.spectrum = None
                        reading.failure = str(exc)
                        take_one_response.error_msg = exc
                        take_one_response.error_lvl = ErrorLevel.medium
                        take_one_response.keep_alive = True
                        return take_one_response
            else:
                externally_triggered = (self.settings.state.trigger_source ==
                    SpectrometerState.TRIGGER_SOURCE_EXTERNAL)
                try:
                    while True:
                        req = SpectrometerRequest('get_line')
                        res = self.hardware.handle_requests([req])[0]
                        if res.error_msg != '':
                            return res
                        spectrum_and_row = res.data
                        if res.poison_pill:
                            take_one_response.transfer_response(res)
                            return take_one_response
                        if res.keep_alive:
                            take_one_response.transfer_response(res)
                            return take_one_response
                        if isinstance(spectrum_and_row, bool):
                            take_one_response.poison_pill = True
                            return take_one_response
                        if self.hardware.shutdown_requested:
                            take_one_response.poison_pill = True
                            return take_one_response
                        if (spectrum_and_row is None or spectrum_and_row.
                            spectrum is None):
                            log.debug(
                                'device.take_one_averaged_spectrum: get_line None, sending keepalive for now'
                                )
                            take_one_response.transfer_response(res)
                            return take_one_response
                        else:
                            break
                    reading.spectrum = spectrum_and_row.spectrum
                    reading.area_scan_row_count = spectrum_and_row.row
                    reading.timestamp_complete = datetime.datetime.now()
                    log.debug(
                        'device.take_one_averaged_reading: got %s ... (row %d)'
                        , reading.spectrum[0:9], reading.area_scan_row_count)
                except Exception as exc:
                    take_one_response.error_msg = exc
                    take_one_response.error_lvl = ErrorLevel.medium
                    take_one_response.keep_alive = True
                    if externally_triggered:
                        log.debug(
                            'caught exception from get_line while externally triggered...sending keepalive'
                            )
                        return take_one_response
                    log.critical('Error reading hardware data', exc_info=1)
                    reading.spectrum = None
                    reading.failure = str(exc)
            if not reading.failure:
                if averaging_enabled:
                    if self.sum_count == 0:
                        self.summed_spectra = [float(i) for i in reading.
                            spectrum]
                    else:
                        log.debug(
                            'device.take_one_averaged_reading: summing spectra'
                            )
                        for i in range(len(self.summed_spectra)):
                            self.summed_spectra[i] += reading.spectrum[i]
                    self.sum_count += 1
                    log.debug(
                        'device.take_one_averaged_reading: summed_spectra : %s ...'
                        , self.summed_spectra[0:9])
            self.session_reading_count += 1
            reading.session_count = self.session_reading_count
            reading.sum_count = self.sum_count
            if averaging_enabled:
                if self.sum_count >= self.settings.state.scans_to_average:
                    reading.spectrum = [(x / self.sum_count) for x in self.
                        summed_spectra]
                    log.debug(
                        'device.take_one_averaged_reading: averaged_spectrum : %s ...'
                        , reading.spectrum[0:9])
                    reading.averaged = True
                    self.summed_spectra = None
                    self.sum_count = 0
            else:
                reading.averaged = True
            if self.take_one and reading.averaged:
                log.debug('completed take_one')
                self.change_setting('cancel_take_one', True)
        log.debug('device.take_one_averaged_reading: returning %s', reading)
        take_one_response.data = reading
        return take_one_response

    def monitor_memory(self):
        now = datetime.datetime.now()
        if (now - self.last_memory_check).total_seconds() < 5:
            return
        self.last_memory_check = now
        size_in_bytes = psutil.Process(self.process_id).memory_info().rss
        log.info('monitor_memory: PID %d memory = %d bytes', self.
            process_id, size_in_bytes)

    def process_commands(self):
        control_object = 'throwaway'
        retval = False
        log.debug('process_commands: processing')
        while len(self.command_queue) > 0:
            control_object = self.command_queue.pop(0)
            log.debug('process_commands: %s', control_object)
            if control_object.setting.lower() == 'acquire':
                log.debug('process_commands: acquire found')
                retval = True
            else:
                req = SpectrometerRequest(control_object.setting, args=[
                    control_object.value])
                self.hardware.handle_requests([req])
            if (control_object.setting == 'free_running_mode' and not self.
                hardware.settings.state.free_running_mode):
                log.debug('exited free-running mode')
        return retval

    def _init_process_funcs(self):
        process_f = {}
        process_f['connect'] = self.connect
        process_f['disconnect'] = self.disconnect
        process_f['acquire_data'] = self.acquire_data
        return process_f

    def balance_acquisition(self, device=None, mode=None, intensity=45000,
        threshold=2500, pixel=None, max_integration_time_ms=5000, max_tries=20
        ):
        balancer = BalanceAcquisition(device=self, mode=mode, intensity=
            intensity, threshold=threshold, pixel=pixel,
            max_integration_time_ms=max_integration_time_ms, max_tries=
            max_tries)
        return balancer.balance()

    def change_setting(self, setting, value, allow_immediate=True):
        control_object = ControlObject(setting, value)
        log.debug('WasatchDevice.change_setting: %s', control_object)
        if control_object.setting == 'scans_to_average':
            self.sum_count = 0
            self.settings.state.scans_to_average = int(value)
            return
        elif control_object.setting == 'reset_scan_averaging':
            self.sum_count = 0
            return
        elif control_object.setting == 'take_one':
            self.take_one = True
            self.change_setting('free_running_mode', True)
            return
        elif control_object.setting == 'cancel_take_one':
            self.sum_count = 0
            self.take_one = False
            self.change_setting('free_running_mode', False)
            return
        self.command_queue.append(control_object)
        log.debug('change_setting: queued %s', control_object)
        if allow_immediate and self.immediate_mode or re.search('trigger|laser'
            , setting):
            log.debug('immediately processing %s', control_object)
            self.process_commands()

    def handle_requests(self, requests):
        responses = []
        for request in requests:
            try:
                cmd = request.cmd
                proc_func = self.process_f.get(cmd, None)
                if proc_func == None:
                    try:
                        self.change_setting(cmd, *request.args, **request.
                            kwargs)
                    except Exception as e:
                        log.error(
                            f'error {e} with trying to set setting {cmd} with args and kwargs {request.args} and {request.kwargs}'
                            )
                        return []
                elif request.args == [] and request.kwargs == {}:
                    responses.append(proc_func())
                else:
                    responses.append(proc_func(*request.args, **request.kwargs)
                        )
            except Exception as e:
                log.error(f'error in handling request {request} of {e}')
                responses.append(SpectrometerResponse(error_msg=
                    'error processing cmd', error_lvl=ErrorLevel.medium))
        return responses
