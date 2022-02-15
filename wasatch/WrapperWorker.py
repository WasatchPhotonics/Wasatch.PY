import threading
import datetime
import logging
import time

from .WasatchDevice        import WasatchDevice
from .AndorDevice          import AndorDevice
from .OceanDevice          import OceanDevice
from .Reading              import Reading

log = logging.getLogger(__name__)

##
# Continuously process in background thread. While waiting forever for the None 
# poison pill on the command queue, continuously read from the device and post 
# the results on the response queue.
class WrapperWorker(threading.Thread):

    # TODO: make this dynamic:
    #   - initially on number of connected spectrometers
    #   - ideally on configured integration times per spectrometer
    #   - note that this is essentially ADDED to the total measurement time
    #     of EACH AND EVERY INTEGRATION
    POLLER_WAIT_SEC = 0.05    # .05sec = 50ms = update from hardware device at 20Hz

    def __init__(
            self,
            device_id,
            command_queue,
            response_queue,
            settings_queue,
            message_queue,
            is_ocean,
            is_andor,
            parent=None):

        threading.Thread.__init__(self)

        self.device_id      = device_id
        self.is_ocean       = is_ocean
        self.is_andor       = is_andor
        self.command_queue  = command_queue
        self.response_queue = response_queue
        self.settings_queue = settings_queue
        self.message_queue  = message_queue
        self.wasatch_device = False
        self.sum_count = 0

    ##
    # This is essentially the main() loop in a thread.
    # All communications with the parent thread are routed through
    # one of the three queues (cmd inputs, response outputs, and
    # a one-shot SpectrometerSettings).
    def run(self):
        if self.is_ocean:
            self.ocean_device = OceanDevice(
                device_id = self.device_id,
                message_queue = self.message_queue)
        elif self.is_andor:
            self.andor_device = AndorDevice(
                device_id = self.device_id,
                message_queue = self.message_queue
                )
        else:
            try:
                log.debug("instantiating WasatchDevice")
                self.wasatch_device = WasatchDevice(
                    device_id = self.device_id,
                    message_queue = self.message_queue)
            except:
                log.critical("exception instantiating WasatchDevice", exc_info=1)
                return self.settings_queue.put(None) 

        log.debug("calling connect")
        ok = False
        if self.is_ocean:
            ok = self.ocean_device.connect()
        elif self.is_andor:
            ok = self.andor_device.connect()
        else:
            try:
                ok = self.wasatch_device.connect()
            except:
                log.critical("exception connecting", exc_info=1)
                return self.settings_queue.put(None) # put(None, timeout=2)

        if not ok:
            log.critical("failed to connect")
            return self.settings_queue.put(None) # put(None, timeout=2)

        log.debug("successfully connected")

        # send the SpectrometerSettings back to the GUI thread
        log.debug("returning SpectrometerSettings to parent")
        if self.is_ocean:
            self.settings_queue.put_nowait(self.ocean_device.settings)
        elif self.is_andor:
            self.settings_queue.put_nowait(self.andor_device.settings)
        else:
            self.settings_queue.put_nowait(self.wasatch_device.settings)

        log.debug("entering loop")
        last_command = datetime.datetime.now()
        min_thread_timeout_sec = 10
        thread_timeout_sec = min_thread_timeout_sec

        received_poison_pill_command  = False # from ENLIGHTEN
        received_poison_pill_response = False # from WasatchDevice

        sent_good = False
        num_connected_devices = 1
        while True:
            now = datetime.datetime.now()
            dedupped = self.dedupe(self.command_queue)

            # apply dedupped commands
            if dedupped:
                for record in dedupped:
                    if record is None:
                        # reminder, the DOWNSTREAM poison_pill is a None, while the UPSTREAM
                        # poison_pill is False...need to straighten that out.
                        received_poison_pill_command = True

                        # do NOT put a 'break' here -- if caller is in process of
                        # cleaning shutting things down, let them switch off the
                        # laser etc in due sequence
                    else:
                        log.debug("processing command queue: %s", record.setting)

                        last_command = now

                        # basically, this simply moves each de-dupped command from
                        # WasatchDeviceWrapper.command_queue to WasatchDevice.command_queue,
                        # where it gets read during the next call to
                        # WasatchDevice.acquire_data.
                        if self.is_ocean:
                            self.ocean_device.change_setting(record.setting, record.value)
                        elif self.is_andor:
                            self.andor_device.change_setting(record.setting, record.value)
                        else:
                            self.wasatch_device.change_setting(record.setting, record.value)

                        # peek in some settings locally
                        if record.setting == "num_connected_devices":
                            num_connected_devices = record.value
                        elif record.setting == "subprocess_timeout_sec":
                            thread_timeout_sec = record.value

            else:
                log.debug("command queue empty")

            if received_poison_pill_command:
                log.critical("exiting per command queue (poison pill received)")
                break

            # has ENLIGHTEN crashed and stopped sending us heartbeats?
            if False and thread_timeout_sec is not None:
                sec_since_last_command = (now - last_command).total_seconds()
                log.debug("sec_since_last_command = %d sec", sec_since_last_command)
                if sec_since_last_command > thread_timeout_sec:
                    log.critical("thread killing self (%d sec since last command, timeout %d sec)",
                        sec_since_last_command, thread_timeout_sec)
                    break

            # ##################################################################
            # Relay one upstream reading (Spectrometer -> GUI)
            # ##################################################################

            try:
                # Note: this is a BLOCKING CALL.  If integration time is longer
                # than subprocess_timeout_sec, this call itself will trigger
                # shutdown.
                log.debug("acquiring data")
                if self.is_ocean:
                    reading_response = self.ocean_device.acquire_data()
                elif self.is_andor:
                    reading_response = self.andor_device.acquire_data()
                else:
                    reading_response = self.wasatch_device.acquire_data()
                #log.debug("continuous_poll: acquire_data returned %s", str(reading))
            except Exception as exc:
                log.critical("exception calling WasatchDevice.acquire_data", exc_info=1)
                break
            log.info(f"response {reading_response} data is {reading_response.data}")

            if reading_response.keep_alive == True:
                # just pass it upstream and move on
                log.debug("worker is flowing up keep alive")
                try:
                    self.response_queue.put(reading_response) # put(reading, timeout=2)
                    sent_good = True
                except:
                    log.error("unable to push Reading %d to GUI", reading.session_count, exc_info=1)
                if reading_response.data.failure is not None:
                    return
            elif reading_response.poison_pill:
                # it was an upstream poison pill
                #
                # There's nothing we need to do here except 'break'...that will
                # exit the loop, and the last thing this function does is flow-up
                # a poison pill anyway, so we're done
                log.critical("received upstream poison pill...exiting")
                received_poison_pill_response = True
                break

            elif reading_response.data is None:
                log.debug("no worker saw no reading")

            elif reading_response.data.failure is not None:
                # this wasn't passed-up as a poison-pill, but we're going to treat it
                # as one anyway
                #
                # @todo deprecate Reading.failure and just return False (poison pill?)
                log.critical("hardware level error...exiting")
                reading_response.poison_pill = True
                self.response_queue.put(reading_response)
                return False

            elif reading_response.data.spectrum is not None:
                log.debug("sending Reading %d back to GUI thread (%s)", reading_response.data.session_count, reading_response.data.spectrum[0:5])
                try:
                    self.response_queue.put_nowait(reading_response) # put(reading, timeout=2)
                except:
                    log.error("unable to push Reading %d to GUI", reading_response.data.session_count, exc_info=1)

            else:
                log.error("received non-failure Reading without spectrum...ignoring?")

            # only poll hardware at 20Hz
            sleep_sec = WrapperWorker.POLLER_WAIT_SEC * num_connected_devices
            log.debug("sleeping %.2f sec", sleep_sec)
            time.sleep(sleep_sec)

        ########################################################################
        # we have exited the loop
        ########################################################################

        if received_poison_pill_response:
            # send poison-pill notification upstream to Controller
            log.critical("exiting because of upstream poison-pill response")
            log.critical("sending poison-pill upstream to controller")
            self.response_queue.put(reading_response) 

            # Controller.ACQUISITION_TIMER_SLEEP_MS currently 50ms, so wait more
            log.critical("waiting long enough for Controller to receive it")
            time.sleep(1)
        elif received_poison_pill_command:
            log.critical("exiting because of downstream poison-pill command")
        else:
            log.critical("exiting for no reason?!")

        log.critical("done")

    def dedupe(self, q):
        keep = [] # list, not a set, because we want to keep it ordered
        while True:
            if not q.empty():
                control_object = q.get_nowait() 

                # treat None elements (poison pills) same as everything else
                if control_object is None:
                    setting = None
                    value = None
                else:
                    setting = control_object.setting
                    value = control_object.value

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
        return keep
