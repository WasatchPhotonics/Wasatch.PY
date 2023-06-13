import threading
import datetime
import logging
import time
from queue import Queue
from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest import SpectrometerRequest
from .SpectrometerResponse import ErrorLevel
from .WasatchDevice import WasatchDevice
from .ControlObject import ControlObject
from .AndorDevice import AndorDevice
from .OceanDevice import OceanDevice
from .SPIDevice import SPIDevice
from .BLEDevice import BLEDevice
from .Reading import Reading
log = logging.getLogger(__name__)


class WrapperWorker(threading.Thread):
    POLLER_WAIT_SEC = 0.05

    def __init__(self, device_id, command_queue, response_queue,
        settings_queue, message_queue, is_ocean, is_andor, is_spi, is_ble,
        parent=None):
        threading.Thread.__init__(self)
        self.device_id = device_id
        self.is_ocean = is_ocean
        self.is_andor = is_andor
        self.is_spi = is_spi
        self.is_ble = is_ble
        self.command_queue = command_queue
        self.response_queue = response_queue
        self.settings_queue = settings_queue
        self.message_queue = message_queue
        self.connected_device = None

    def run(self):
        is_options = self.is_ocean, self.is_andor, self.is_ble, self.is_spi
        device_classes = (OceanDevice, AndorDevice, BLEDevice, SPIDevice,
            WasatchDevice)
        try:
            if any(is_options):
                type_connection = is_options.index(True)
                log.debug(
                    f'trying to instantiate device of type {device_classes[type_connection]}'
                    )
                self.connected_device = device_classes[type_connection](
                    device_id=self.device_id, message_queue=self.message_queue)
            else:
                log.debug(
                    f"Couldn't recognize device of {self.device_id} {is_options}, trying to instantiate as WasatchDevice"
                    )
                self.connected_device = device_classes[device_classes.index
                    (WasatchDevice)](device_id=self.device_id,
                    message_queue=self.message_queue)
        except:
            log.critical('exception instantiating device', exc_info=1)
            return self.settings_queue.put(None)
        log.debug('calling connect')
        ok = False
        req = SpectrometerRequest('connect')
        try:
            ok, = self.connected_device.handle_requests([req])
        except:
            log.critical('exception connecting', exc_info=1)
            return self.settings_queue.put_nowait(SpectrometerResponse(
                error_msg='exception while connecting'))
        log.debug(f'on connect request got results of {ok}')
        if not ok.data:
            log.critical('failed to connect')
            return self.settings_queue.put_nowait(ok)
        log.debug('successfully connected')
        log.debug(
            'returning SpectrometerSettings to parent via SpectrometerResponse'
            )
        self.settings_queue.put_nowait(SpectrometerResponse(self.
            connected_device.settings))
        log.debug('entering loop')
        last_command = datetime.datetime.now()
        min_thread_timeout_sec = 10
        thread_timeout_sec = min_thread_timeout_sec
        received_poison_pill_command = False
        received_poison_pill_response = False
        num_connected_devices = 1
        while True:
            now = datetime.datetime.now()
            dedupped = self.dedupe(self.command_queue)
            if dedupped:
                for record in dedupped:
                    if record is None:
                        received_poison_pill_command = True
                    else:
                        log.debug('processing command queue: %s', record.
                            setting)
                        last_command = now
                        if record.setting == 'reset':
                            log.debug(f'calling reset from command queue')
                        req = SpectrometerRequest(record.setting, args=[
                            record.value])
                        self.connected_device.handle_requests([req])
                        if record.setting == 'num_connected_devices':
                            num_connected_devices = record.value
                        elif record.setting == 'subprocess_timeout_sec':
                            thread_timeout_sec = record.value
            else:
                log.debug('command queue empty')
            if received_poison_pill_command:
                log.critical('exiting per command queue (poison pill received)'
                    )
                req = SpectrometerRequest('disconnect')
                self.connected_device.handle_requests([req])
                break
            try:
                log.debug('acquiring data')
                req = SpectrometerRequest('acquire_data')
                reading_response, = self.connected_device.handle_requests([req]
                    )
            except Exception as exc:
                log.critical('exception calling WasatchDevice.acquire_data',
                    exc_info=1)
                continue
            if not isinstance(reading_response, SpectrometerResponse):
                log.error(
                    f'Reading is not type ReadingResponse. Should not get naked responses. Happened with request {req}'
                    )
                continue
            log.debug(
                f'response {reading_response} data is {reading_response.data}')
            if reading_response.keep_alive == True:
                log.debug('worker is flowing up keep_alive')
                self.response_queue.put(reading_response)
            elif reading_response.error_msg != '':
                if reading_response.data == None:
                    reading_response.data = Reading()
                self.response_queue.put(reading_response)
            elif reading_response.data is None:
                log.debug('worker saw no reading (but not error, either)')
            elif reading_response.data == False:
                log.critical(
                    f'hardware level error...exiting because data False')
                reading_response.poison_pill = True
                self.response_queue.put(reading_response)
            elif reading_response.data.failure is not None:
                log.critical(
                    f'hardware level error...exiting because failure {reading_response.data.failure}'
                    )
                reading_response.poison_pill = True
                self.response_queue.put(reading_response)
            elif reading_response.poison_pill:
                log.critical(
                    f'hardware level error...exiting because poison-pill')
                self.response_queue.put(reading_response)
            elif reading_response.data.spectrum is not None:
                log.debug('sending Reading %d back to GUI thread (%s)',
                    reading_response.data.session_count, reading_response.
                    data.spectrum[0:5])
                try:
                    self.response_queue.put_nowait(reading_response)
                except:
                    log.error('unable to push Reading %d to GUI',
                        reading_response.data.session_count, exc_info=1)
            else:
                log.error(
                    'received non-failure Reading without spectrum...ignoring?'
                    )
            sleep_sec = WrapperWorker.POLLER_WAIT_SEC * num_connected_devices
            log.debug('sleeping %.2f sec', sleep_sec)
            time.sleep(sleep_sec)
        if received_poison_pill_command:
            log.critical(
                'exiting because of downstream poison-pill command from ENLIGHTEN'
                )
        else:
            log.critical('exiting for no reason?!')
        log.critical('done')

    def dedupe(self, q):
        keep = []
        while True:
            if not q.empty():
                control_object = q.get_nowait()
                if control_object is None:
                    setting = None
                    value = None
                else:
                    setting = control_object.setting
                    value = control_object.value
                new_keep = []
                for co in keep:
                    if co.setting != setting:
                        new_keep.append(co)
                keep = new_keep
                keep.append(control_object)
            else:
                break
        return keep
