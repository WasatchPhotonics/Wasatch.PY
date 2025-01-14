import threading
import logging
import time
from queue import Queue
from datetime import datetime

from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest  import SpectrometerRequest
from .WasatchDevice        import WasatchDevice
from .AndorDevice          import AndorDevice
from .OceanDevice          import OceanDevice
from .SPIDevice            import SPIDevice
from .BLEDevice            import BLEDevice
from .TCPDevice            import TCPDevice
from .Reading              import Reading

log = logging.getLogger(__name__)

##
# Continuously process in background thread. While waiting forever for the None 
# poison pill on the command queue, continuously read from the device and post 
# the results on the response queue.
#
# Consider moving this class, and WasatchDeviceWrapper, our of Wasatch.PY and
# into ENLIGHTEN.  Let Wasatch.PY be a simple single-spectrometer blocking
# driver, similar to Wasatch.NET and Wasatch.VCPP.  No need to have Wasatch.PY
# handle the threads.
class WrapperWorker(threading.Thread):

    # TODO: make this dynamic:
    #   - initially on number of connected spectrometers
    #   - ideally on configured integration times per spectrometer
    #   - note that this is essentially ADDED to the total measurement time
    #     of EACH AND EVERY INTEGRATION
    # TODO: replace if check for each type of spec with single call
    # TODO: Create ABC of hardware device that keeps common functions like handle_requests
    POLLER_WAIT_SEC = 0.05    # .05sec = 50ms = update from hardware device at 20Hz

    DEBUG_SEC = 10 # enforce debug logging for the 1st 10sec after connecting a new spectrometer

    def __init__(
            self,
            device_id,
            command_queue,
            response_queue,
            settings_queue,
            message_queue,
            is_ocean, # MZ: all these bools should have just been a string "type"
            is_andor,
            is_spi,
            is_ble,
            is_tcp,
            log_level,
            callback=None,
            alert_queue=None):

        threading.Thread.__init__(self)

        self.device_id      = device_id
        self.is_ocean       = is_ocean
        self.is_andor       = is_andor
        self.is_spi         = is_spi
        self.is_ble         = is_ble
        self.is_tcp         = is_tcp
        self.command_queue  = command_queue
        self.response_queue = response_queue
        self.settings_queue = settings_queue
        self.message_queue  = message_queue
        self.alert_queue    = alert_queue  
        self.log_level      = log_level
        self.callback       = callback

        self.connected_device = None

        self.thread_start = datetime.now()
        self.initial_connection_logging = True

        # enforce debug logging around ENLIGHTEN connections
        if not self.callback:
            logging.getLogger().setLevel("DEBUG") 

    ##
    # This is essentially the main() loop in a thread.
    # All communications with the parent thread are routed through
    # one of the three queues (cmd inputs, response outputs, and
    # a one-shot SpectrometerSettings).
    def run(self):
        is_options = (self.is_ocean, self.is_andor, self.is_ble, self.is_spi, self.is_tcp)
        device_classes = (OceanDevice, AndorDevice, BLEDevice, SPIDevice, TCPDevice, WasatchDevice)
        try:
            if any(is_options):
                type_connection = is_options.index(True)
                log.debug(f"trying to instantiate device of type {device_classes[type_connection]}")
                self.connected_device = device_classes[type_connection](
                    device_id = self.device_id,
                    message_queue = self.message_queue,
                    alert_queue = self.alert_queue)
            else:
                log.debug(f"Couldn't recognize device of {self.device_id} {is_options}, trying to instantiate as WasatchDevice")
                self.connected_device = device_classes[device_classes.index(WasatchDevice)](
                    device_id = self.device_id,
                    message_queue = self.message_queue,
                    alert_queue = self.alert_queue)
        except:
            log.critical("exception instantiating device", exc_info=1)
            return self.settings_queue.put(None) 

        log.debug("calling connect")
        ok = False
        req = SpectrometerRequest("connect")
        try:
            (ok,) = self.connected_device.handle_requests([req])
        except:
            log.critical("exception connecting", exc_info=1)
            return self.settings_queue.put_nowait(SpectrometerResponse(error_msg="exception while connecting"))

        log.debug(f"on connect request got results of {ok}")

        if not ok.data:
            log.critical("failed to connect")
            return self.settings_queue.put_nowait(ok) 

        log.debug("successfully connected")

        # send the SpectrometerSettings back to the GUI thread
        log.debug("returning SpectrometerSettings to parent via SpectrometerResponse")
        self.settings_queue.put_nowait(SpectrometerResponse(self.connected_device.settings))

        log.debug("entering loop")
        received_poison_pill_command  = False # from ENLIGHTEN

        num_connected_devices = 1
        while True:
            dedupped = self.dedupe(self.command_queue)

            # apply dedupped commands
            if dedupped:
                for record in dedupped:
                    if record is None:
                        # We have received a "poison pill" (shutdown command) 
                        # from ENLIGHTEN.
                        #
                        # Reminder, poison_pills moving DOWNSTREAM from ENLIGHTEN
                        # are None, while poison_pills moving UPSTREAM from WasatchDevice
                        # are indicated with SpectrometerResponse.poison_pill.
                        received_poison_pill_command = True
                        #
                        # Do NOT put a 'break' here just yet -- if caller is in process of
                        # cleaning shutting things down, let them switch off the
                        # laser etc in due sequence.  We can break AFTER relaying
                        # applying the queued settings.
                    else:
                        try:
                            log.debug("processing command queue: %s", record.setting)

                            # basically, this simply moves each de-dupped command from
                            # WasatchDeviceWrapper.command_queue to WasatchDevice.command_queue,
                            # where it gets read during the next call to
                            # WasatchDevice.acquire_data.
                            if record.setting == "reset":
                                log.debug(f"calling reset from command queue")
                            req = SpectrometerRequest(record.setting, args=[record.value])
                            self.connected_device.handle_requests([req])
     
                            # peek in some settings locally
                            if record.setting == "num_connected_devices":
                                num_connected_devices = record.value
                        except:
                            log.error("failed to process record {record}, treating as poison pill", exc_info=1)
                            received_poison_pill_command = True
            else:
                log.debug("command queue empty")

            if received_poison_pill_command:
                # ...NOW we can break
                log.critical("exiting per command queue (poison pill received)")
                req = SpectrometerRequest("disconnect")
                # I don't see this called. I think it should. 
                # At minimum it's required for the BLE devices
                self.connected_device.handle_requests([req]) 
                break

            # ##################################################################
            # Relay one upstream reading (Spectrometer -> GUI)
            # ##################################################################

            try:
                # Note: this is a BLOCKING CALL.  If integration time is longer
                # than subprocess_timeout_sec, this call itself will trigger
                # shutdown.
                log.debug("acquiring data")
                req = SpectrometerRequest("acquire_data")
                (reading_response,) = self.connected_device.handle_requests([req])
                #log.debug("continuous_poll: acquire_data returned %s", str(reading))
            except:
                log.critical("exception calling WasatchDevice.acquire_data", exc_info=1)
                continue

            if not isinstance(reading_response, SpectrometerResponse):
                log.error(f"Reading is not type ReadingResponse. Should not get naked responses. Happened with request {req}")
                continue

            log.debug(f"response {reading_response} data is {reading_response.data}")

            if self.callback:
                log.debug("worker returning response via callback")
                self.callback(reading_response)

            elif reading_response.keep_alive:
                log.debug("worker is flowing up keep_alive")
                self.response_queue.put(reading_response) 

            elif reading_response.error_msg != "":
                if reading_response.data is None:
                    reading_response.data = Reading()
                self.response_queue.put(reading_response)

            elif reading_response.data is None:
                log.debug("worker saw no reading (but not error, either)")

            elif not reading_response.data:
                log.critical(f"hardware level error...exiting because data False")
                reading_response.poison_pill = True
                self.response_queue.put(reading_response)

            elif reading_response.data.failure is not None:
                log.critical(f"hardware level error...exiting because failure {reading_response.data.failure}")
                reading_response.poison_pill = True
                self.response_queue.put(reading_response)

            elif reading_response.poison_pill:
                log.critical(f"hardware level error...exiting because poison-pill")
                self.response_queue.put(reading_response)

            elif reading_response.data.spectrum is not None:
                log.debug("sending Reading %d back to GUI thread (%s)", reading_response.data.session_count, reading_response.data.spectrum[0:5])
                try:
                    self.response_queue.put_nowait(reading_response) 
                except:
                    log.error("unable to push Reading %d to GUI", reading_response.data.session_count, exc_info=1)

                # We just successfully sent a reading back to the main thread.
                # After sending 10sec of such readings, we can disable the default
                # DEBUG logging and revert to whatever the caller requested.
                if (self.initial_connection_logging and 
                        (datetime.now() - self.thread_start).total_seconds() > self.DEBUG_SEC):
                    log.info(f"relaxing log_level to {self.log_level} after {self.DEBUG_SEC}sec")
                    logging.getLogger().setLevel(self.log_level)
                    self.initial_connection_logging = False

            else:
                log.error("received non-failure Reading without spectrum...ignoring?")

            # only poll hardware buses at 20Hz
            sleep_sec = WrapperWorker.POLLER_WAIT_SEC * num_connected_devices
            log.debug("sleeping %.2f sec", sleep_sec)
            time.sleep(sleep_sec)

        ########################################################################
        # we have exited the loop
        ########################################################################

        if received_poison_pill_command:
            log.critical("exiting because of downstream poison-pill command from ENLIGHTEN")
        else:
            log.critical("exiting for no reason?!")

        log.critical("done")

    def dedupe(self, q: Queue):
        keep = [] # list, not a set, because we want to keep it ordered
        try:
            while True:
                if not q.empty():
                    control_object = q.get_nowait() 

                    # treat None elements (poison pills) same as everything else
                    setting = None if control_object is None else control_object.setting

                    # remove previous setting if duplicate
                    new_keep = []
                    for co in keep:
                        if co.setting != setting:
                            new_keep.append(co)
                    keep = new_keep

                    # append the setting to the de-dupped list and track index
                    keep.append(control_object)
                else:
                    break
        except:
            log.error("failed to dedupe command queue", exc_info=1)
        return keep
