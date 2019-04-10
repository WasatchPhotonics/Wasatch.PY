import sys
import time
import Queue
import random
import logging
import multiprocessing

# from memory_profiler import profile

from . import applog
from . import utils

from SpectrometerSettings import SpectrometerSettings
from ControlObject        import ControlObject
from WasatchDevice        import WasatchDevice
from Reading              import Reading

log = logging.getLogger(__name__)

class SubprocessArgs(object):

    def __init__(self, 
            device_id, 
            log_queue, 
            command_queue, 
            response_queue, 
            spectrometer_settings_queue,
            message_queue,
            log_level):
        self.device_id                   = device_id
        self.log_queue                   = log_queue
        self.command_queue               = command_queue
        self.response_queue              = response_queue
        self.spectrometer_settings_queue = spectrometer_settings_queue
        self.message_queue               = message_queue
        self.log_level                   = log_level

##
# Wrap WasatchDevice in a non-blocking interface run in a separate
# process. Use a "settings queue" to pass metadata about the device 
# (SpectrometerSettings) for multiprocessing-safe device communications
# and acquisition on Windows. 
# 
# From ENLIGHTEN's standpoint (the original Wasatch.PY caller), here's what's going on:
# 
# 1. MainProcess creates a Controller.bus_timer which on timeout (tick) calls
#    Controller.update_connections()
#
# 2. Controller.update_connections() calls Controller.connect_new()
#
# 3. connect_new(): if we're not already connected to a spectrometer, yet
#    bus.device_1 is not "disconnected" (implying something was found on the
#    bus), then connect_new() instantiates a WasatchDeviceWrapper and then
#    calls connect() on it
#
# 4. WasatchDeviceWrapper.connect() forks a child process running the
#    continuous_poll() method of the same WasatchDeviceWrapper instance,
#    then waits on SpectrometerSettings to be returned via a pipe (queue).
# 
#    (at this point, the same WasatchDeviceWrapper instance is being 
#        accessed by two processes...be careful!)
# 
# 5. continuous_poll() instantiantes a WasatchDevice.  This object will only
#    ever be referenced within the subprocess.
#
# 6. continuous_poll() calls WasatchDevice.connect() (exits on failure)
#
#    6.a WasatchDevice instantiates a FID, SP or FileSpectrometer based on DeviceID
#
#    6.b if FID, WasatchDevice.connect() loads the EEPROM
#
# 7. continuous_poll() populates (exactly once) a SpectrometerSettings object,
#    then feeds it back to MainProcess
#
# It is incredibly important to recognize that continuous_poll() updates the 
# command/response queues at a leisurely interval (currently 20Hz, set in 
# poller_wait_sec.  No matter how short the integration time is (1ms), you're not
# going to get spectra faster than 20 per second through this.
#
# Now, if you set ACQUISITION_MODE_KEEP_ALL, then you should still get every 
# spectrum (whatever the spectrometer achieved through its scan-rate, potentially
# 220/sec or so) -- but you'll get them in chunks (e.g., scan rate of 220/sec 
# polled at 20Hz = 20 chunks of 11 ea).
class WasatchDeviceWrapper(object):

    ACQUISITION_MODE_KEEP_ALL      = 0 # don't drop frames
    ACQUISITION_MODE_LATEST        = 1 # only grab most-recent frame (allow dropping)
    ACQUISITION_MODE_KEEP_COMPLETE = 2 # generally grab most-recent frame (allow dropping),
                                       # except don't drop FULLY AVERAGED frames (summation
                                       # contributors can be skipped).  If the most-recent
                                       # frame IS a partial summation contributor, then send it on.

    DISABLE_RESPONSE_QUEUE = False

    # TODO: make this dynamic:
    #   - initially on number of connected spectrometers
    #   - ideally on configured integration times per spectrometer
    #   - need to check whether it can be modified from inside the process
    #   - need to check whether it can be modified after process creation
    POLLER_WAIT_SEC = 0.05    # .05sec = 50ms = update from hardware device at 20Hz


    # ##########################################################################
    #                                                                          #
    #                                MainProcess                               #
    #                                                                          #
    # ##########################################################################

    ##
    # Instantiated by Controller.connect_new(), if-and-only-if a WasatchBus
    # reports a DeviceID which has not already connected to the GUI.  The DeviceID
    # is "unique and relevant" to the bus which reported it, but neither the
    # bus class nor instance is passed in to this object.  If the DeviceID looks like
    # "USB:VID:PID:bus:addr", then it is presumably USB.  If the DeviceID looks like 
    # "FILE:/path/to/dir", then assume it is a FileSpectrometer.  However, device_id is just
    # a string scalar to this class, and actually parsing / using it should be
    # entirely encapsulated within WasatchDevice and lower using DeviceID.
    def __init__(self, device_id, log_queue, log_level):
        self.device_id = device_id
        self.log_queue = log_queue
        self.log_level = log_level

        ########################################################################
        # Both of these seem to work...pick one
        ########################################################################
        
        # Option 1:
        self.manager = multiprocessing.Manager()
        manager = self.manager
        #
        #    ...OR...
        #
        # Option 2:
        # manager = multiprocessing

        self.spectrometer_settings_queue = manager.Queue(1)   # spectrometer -> GUI (SpectrometerSettings, one-time)
        self.response_queue              = manager.Queue(100) # spectrometer -> GUI (Readings)
        self.message_queue               = manager.Queue(100) # spectrometer -> GUI (StatusMessages)
        self.command_queue               = manager.Queue(100) # GUI -> spectrometer (ControlObjects)

        self.closing      = False   # Don't permit new acquires during close
        self.poller       = None    # a handle to the subprocess

        # this will contain a populated SpectrometerSettings object from the 
        # WasatchDevice, for relay to the instantiating Controller
        self.settings     = None    

        # self.previous_reading = None

    ## 
    # Create a low level device object with the specified identifier, kick off 
    # the subprocess to attempt to read from it. 
    #
    # Called by Controller.connect_new() immediately after instantiation
    # 
    # Fork a child process running the continuous_poll() method on THIS 
    # object instance.  Each process will end up with a copy of THIS Wrapper
    # instance, but they won't be "the same instance" (as they're in different
    # processes).  
    #
    # The two instances are coupled via the 3 queues:
    #
    # @par spectrometer_settings_queue
    #
    #       Lets the subprocess (WasatchDevice) send a single, one-time copy
    #       of a populated SpectrometerSettings object back through the 
    #       Wrapper to the calling Controller.  This is the primary way the 
    #       Controller knows what kind of spectrometer it has connected to, 
    #       what hardware features and EEPROM settings are applied etc.  
    #
    #       Thereafter both WasatchDevice and Controller will maintain
    #       their own copies of SpectrometerSettings, and they are not 
    #       automatically synchronized (is there a clever way to do this?).
    #       They may well drift out-of-sync regarding state, although the 
    #       command_queue helps keep them somewhat in sync.  
    #
    # @par command_queue
    #
    #       The Controller will send ControlObject instances (basically
    #       (name, value) pairs) through the Wrapper to the WasatchDevice
    #       to set attributes or commands on the WasatchDevice spectrometer.
    #       These may be volatile hardware settings (laser power, integration 
    #       time), meta-commands to the WasatchDevice class (scan averaging),
    #       or EEPROM updates.
    #
    # @par response_queue
    #
    #       The WasatchDevice will stream a continuous series of Reading
    #       instances back through the Wrapper to the Controller.  These
    #       each contain a newly read spectrum, as well as metadata about
    #       the spectrometer at the time the spectrum was taken (integration
    #       time, laser power), plus additional readings from the spectrometer
    #       (detector and laser temperature, secondary ADC).  
    #
    def connect(self):
        if self.poller != None:
            log.critical("WasatchDeviceWrapper.connect: already polling, cannot connect")
            return False

        # Fork a child process running the continuous_poll() method on this
        # object instance.  
        subprocessArgs = SubprocessArgs(
            device_id                   = self.device_id, 
            log_level                   = self.log_level, # log.getEffectiveLevel(),

            # the all-important message queues
            log_queue                   = self.log_queue,                     #          subprocess --> log
            command_queue               = self.command_queue,                 # Main --> subprocess
            response_queue              = self.response_queue,                # Main <-- subprocess \
            spectrometer_settings_queue = self.spectrometer_settings_queue,   # Main <-- subprocess  ) consolidate into SubprocessMessage?
            message_queue               = self.message_queue)                 # Main <-- subprocess /

        # instantiate subprocess
        self.poller = multiprocessing.Process(target=self.continuous_poll, args=(subprocessArgs,))

        # spawn subprocess
        self.poller.start()

        # give subprocess a moment to wake-up (reading EEPROM will take a bit)
        time.sleep(1)

        # If something goes wrong, we won't want to kill the current process (this function runs
        # within MainProcess), but we will want to kill the spawned subprocess, and ensure 'self'
        # (the current WasatchDeviceWrapper instance) is ready for clean destruction (otherwise
        # we'd be leaking resources continuously).
        kill_myself = False

        # expect to read a single post-initialization SpectrometerSettings object off the queue
        try:
            log.debug("WasatchDeviceWrapper.connect: blocking on spectrometer_settings_queue (waiting on forked continuous_poll)")
            # note: testing indicates it may take more than 2.5sec for the forked
            # continuous_poll to actually start moving.  5sec timeout may be on the short side?
            # Initial testing with 2 spectrometers showed 2nd spectrometer taking 6+ sec to initialize.
            # If you kick-off another heavy operation in another window while the spectrometer is 
            # enumerating, this can take even longer.

            self.settings = self.spectrometer_settings_queue.get(timeout=15)
            if self.settings is None:
                log.error("WasatchDeviceWrapper.connect: received poison-pill from forked continuous_poll")
                kill_myself = True

        except Exception as exc:
            log.warn("WasatchDeviceWrapper.connect: spectrometer_settings_queue.get() caught exception", exc_info=1)
            kill_myself = True
            
        if kill_myself:
            # Apparently something failed in initialization of the subprocess, and it
            # never succeeded in sending a SpectrometerSettings object. Do our best to
            # kill the subprocess (they can be hard to kill), then report upstream
            # that we were unable to connect (Controller will allow this Wrapper object 
            # to exit from scope).

            # MZ: should this bit be merged with disconnect()?

            self.settings = None
            self.closing = True

            log.warn("WasatchDeviceWrapper.connect: sending poison pill to poller")
            try:
                self.command_queue.put(None, timeout=2)
            except Queue.Full:
                pass

            log.warn("WasatchDeviceWrapper.connect: waiting .5 sec")
            time.sleep(.5)

            # log.warn("connect: terminating poller")
            # self.poller.terminate()

            log.warn("WasatchDeviceWrapper.releasing poller")
            self.poller = None

            return False

        # AttributeError: 'AutoProxy[Queue]' object has no attribute 'close'
        # self.spectrometer_settings_queue.close()

        log.info("WasatchDeviceWrapper.connect: received SpectrometerSettings from subprocess")
        self.settings.dump()

        # After we return True, the Controller will then take a handle to our received
        # settings object, and will keep a reference to this Wrapper object for sending
        # commands and reading spectra.
        log.debug("WasatchDeviceWrapper.connect: succeeded")
        return True

    def disconnect(self):
        # send poison pill to the subprocess
        self.closing = True
        try:
            self.command_queue.put(None, timeout=2)
        except:
            pass

        log.debug("joining poller")
        try:
            self.poller.join(timeout=2)
        except NameError as exc:
            log.warn("disconnect: Poller previously disconnected", exc_info=1)
        except Exception as exc:
            log.critical("disconnect: Cannot join poller", exc_info=1)

        log.debug("clearing queues")
        self.clear_all_queues()

        try:
            # do we need to do this?
            self.poller.terminate()
            log.debug("disconnect: poller terminated")
        except Exception as exc:
            log.critical("disconnect: Cannot terminate poller", exc_info=1)

        time.sleep(0.1)
        log.debug("WasatchDeviceWrapper.disconnect: done")

        return True

    def clear_queue(self, q):
        while True:
            try:
                q.get_nowait()
            except:
                break

    def clear_all_queues(self):
        self.clear_queue(self.spectrometer_settings_queue)
        self.clear_queue(self.response_queue)
        self.clear_queue(self.message_queue)

    ##
    # Similar to acquire_data, this method is called by the Controller in
    # MainProcess to dequeue a StatusMessage from the spectrometer sub-
    # process, if one is available.
    def acquire_status_message(self):
        if self.closing:
            return None

        try:
            return self.message_queue.get_nowait()
        except Queue.Empty:
            return None

    ## 
    # This method is called by the Controller in MainProcess.  It checks
    # the response_queue it shares with the subprocess to see if any
    # Reading objects have been queued from the spectrometer to the GUI.
    # 
    # Don't use 'if queue.empty()' for flow control on python 2.7 on
    # windows, as it will hang. Catch the Queue.Empty exception as
    # shown below instead.
    # 
    # It is the upstream interface's job to decide how to process the
    # potentially voluminous amount of data returned from the device.
    # get_last by default will make sure the queue is cleared, then
    # return the most recent reading from the device.
    #
    # See WasatchDevice.acquire_data for a full discussion of return
    # codes, or Controller.acquire_reading to see how they're handled,
    # but in short:
    #
    # - False = Poison pill (shutdown subprocess)
    # - None or True = Keepalive (no-op)
    # - Reading = measured data
    #
    # @note it is not clear that measurement modes other than 
    #       ACQUISITION_MODE_KEEP_COMPLETE have been well-tested,
    #       especially in the context of multiple spectrometers,
    #       BatchCollection etc.
    def acquire_data(self, mode=None):

        if self.closing:
            log.critical("WasatchDeviceWrapper.acquire_data: closing (sending poison-pill upstream)")
            return False

        # ENLIGHTEN "default" - if we're doing scan averaging, take either
        #
        # 1. the NEWEST fully-averaged spectrum (purge the queue), or
        # 2. if no fully-averaged spectra are in the queue, then the NEWEST
        #    incomplete (pre-averaged) spectrum (purging the queue).
        # 
        # If we're not doing scan averaging, then take the NEWEST spectrum
        # and purge the queue.
        #
        if mode is None or mode == self.ACQUISITION_MODE_KEEP_COMPLETE:
            return self.get_final_item(keep_averaged=True)

        # if mode == self.ACQUISITION_MODE_LATEST:
        #     return self.get_final_item()
        # 
        # # presumably mode == self.ACQUISITION_MODE_KEEP_ALL:
        # 
        # # Get the oldest entry off of the queue. This expects the Controller to be
        # # able to process them upstream as fast as possible, because otherwise
        # # the queue will grow (we're not currently limiting its size) and the
        # # process will eventually crash with memory issues.
        # 
        # # Note that these two calls to response_queue aren't synchronized
        # reading = None
        # qsize = "NA" # self.response_queue.qsize()
        # try:
        #     reading = self.response_queue.get_nowait()
        #     log.debug("acquire_data: read Reading %d (qsize %s)", str(reading.session_count), qsize)
        # except Queue.Empty:
        #     log.debug("acquire_data: no data, sending keepalive")
        #     return None
        # 
        # return reading

    ## Read from the response queue until empty (or we find an averaged item) 
    # 
    # In the currently implementation, it seems unlikely that a "True" will ever
    # be passed up (we're basically converting them to None here).
    # @profile
    def get_final_item(self, keep_averaged=False):
        last_reading  = None
        last_averaged = None
        dequeue_count = 0

        while True:
            # without waiting (don't block), just get the first item off the 
            # queue if there is one
            reading = None
            try:
                reading = self.response_queue.get_nowait()
            except Queue.Empty:
                # If there is nothing more to read, then we've emptied the queue
                break

            # If we come across a poison-pill, flow that up immediately -- 
            # game-over, we're done
            if isinstance(reading, bool) and reading == False:
                log.critical("get_final_item: poison-pill!")
                reading = None
                return False

            # If we come across a NONE or a True, ignore it for the moment.
            # Returning "None" will always be the "default" action at the
            # end, so for now continue cleaning-out the queue.
            if reading is None or isinstance(reading, bool):
                log.debug("get_final_item: ignoring keepalive")
                reading = None
                continue

            # apparently we read a Reading
            log.debug("get_final_item: read Reading %s", str(reading.session_count))
            last_reading = reading
            dequeue_count += 1

            # Was this the final spectrum in an averaged sequence?
            #
            # If so, grab a reference, but DON'T flow it up yet...there
            # may be a NEWER fully-averaged spectrum later on.
            #
            # It is the purpose of this function ("get FINAL item...")
            # to PURGE THE QUEUE -- we are not intending to leave any
            # values in the queue (None, bool, or Readings of any kind.
            if keep_averaged and reading.averaged:
                last_averaged = reading

            reading = None
        reading = None

        # kludge - memory profiling
        # if WasatchDeviceWrapper.DISABLE_RESPONSE_QUEUE and self.previous_reading is not None:
        #     self.previous_reading.spectrum = [(1.1 - 0.2 * random.random()) * x for x in self.previous_reading.spectrum]
        #     return self.previous_reading

        if last_reading is None:
            # apparently we didn't read anything...just pass up a keepalive
            last_averaged = None
            return None

        # apparently we read at least some readings.  For interest, how how many
        # readings did we throw away (not return up to ENLIGHTEN)?
        if dequeue_count > 1:
            log.debug("discarded %d readings", dequeue_count - 1)

        # if we're doing averaging, and we found one or more averaged readings,
        # return the latest of those
        if last_averaged is not None:
            # self.previous_reading = last_averaged
            last_reading = None
            return last_averaged

        # We've had every opportunity short-cut the process: we could have 
        # returned potential poison pills or completed averages, but found
        # none of those.  Yet apparently we did read some normal readings.
        # Return the latest of those.
        
        # self.previous_reading = last_reading
        return last_reading

    ## 
    # Add the specified setting and value to the local control queue.
    # 
    # called by MainProcess.Controller
    def change_setting(self, setting, value):
        log.debug("WasatchDeviceWrapper.change_setting: %s => %s", setting, value)
        control_object = ControlObject(setting, value)
        try:
            self.command_queue.put(control_object, timeout=2)
        except Queue.Full:
            log.critical("WasatchDeviceWrapper.change_setting: Problem enqueuing %s", setting, exc_info=1)

    # ##########################################################################
    #                                                                          #
    #                                 Subprocess                               #
    #                                                                          #
    # ##########################################################################

    ##
    # Continuously process with the simulated device. First setup
    # the log queue handler. While waiting forever for the None poison
    # pill on the command queue, continuously read from the device and
    # post the results on the response queue. 
    #    
    # This is essentially the main() loop in a forked process (not 
    # thread).  Hopefully we can scale this to one per spectrometer.  
    # All communications with the parent process are routed through
    # one of the three queues (cmd inputs, response outputs, and
    # a one-shot SpectrometerSettings).
    #
    # @param args [in] a SubprocessArgs instance
    #
    # @par Return value
    #
    # This method doesn't have a return-vaue per-se, but reports upstream
    # via the various queues in SubprocessArgs.  In particular, a value of
    # None in either the spectrometer_settings_queue or response_queue 
    # indicates "this subprocess is shutting down and the spectrometer is
    # going bye-bye."
    def continuous_poll(self, args):

        # We have just forked into a new process, so the first thing is to
        # configure logging for this process.  Although we've been passed-in
        # args.log_level, let's start with DEBUG so we can always capture
        # connect() activity.
        applog.process_log_configure(args.log_queue, logging.DEBUG)

        log.info("continuous_poll: start (device_id %s, log_level %s)", 
            args.device_id, args.log_level)

        # The second thing we do is actually instantiate a WasatchDevice.  Note
        # that for multi-process apps like ENLIGHTEN which use WasatchDeviceWrapper,
        # WasatchDevice objects (and by implication, FeatureIdentificationDevice)
        # are only instantiated inside the subprocess.
        #
        # Another way to say that is, we always go the full distance of forking
        # the subprocess even before we try to instantiate / connect to a device,
        # so if for some reason this WasatchBus entry was a misfire (bad PID, whatever),
        # we've gone to the effort of creating a process and several queues just to
        # find that out.
        #
        # (On the other hand, WasatchBus objects are created inside the MainProcess,
        # as that is how the Controller knows to call Controller.create_new and
        # instantiate this Wrapper in the first place.)
        #
        # Finally, note that the only hint we are passing into WasatchDevice
        # in terms of what type of device it should be instantiating is the 
        # DeviceID.  This parameter is all that WasatchDevice gets to 
        # decide whether to instantiate a FeatureIdentificationDevice (and which), 
        # something else, or nothing.
        #
        # Now that we've removed bus_order, we should replace it with "serial_number".
        #
        # Regardless, if anything goes wrong here, ensure we do our best to 
        # cleanup these processes and queues.
        try:
            wasatch_device = WasatchDevice(
                device_id = args.device_id, 
                message_queue = args.message_queue,
                response_queue = args.response_queue)
        except:
            log.critical("continuous_poll: exception instantiating WasatchDevice", exc_info=1)
            return args.spectrometer_settings_queue.put(None, timeout=2)

        ok = False
        try:
            ok = wasatch_device.connect()
        except:
            log.critical("continuous_poll: exception connecting", exc_info=1)
            return args.spectrometer_settings_queue.put(None, timeout=2)

        if not ok:
            log.critical("continuous_poll: failed to connect")
            return args.spectrometer_settings_queue.put(None, timeout=2)

        log.debug("continuous_poll: connected to a spectrometer")

        # send the SpectrometerSettings back to the GUI process
        log.debug("continuous_poll: returning SpectrometerSettings to GUI process")
        args.spectrometer_settings_queue.put(wasatch_device.settings, timeout=10)

        # AttributeError: 'AutoProxy[Queue]' object has no attribute 'close'
        #args.spectrometer_settings_queue.close()

        # Read forever until the None poison pill is received
        log.debug("continuous_poll: entering loop")
        
        log.debug("resetting commanded log_level %s", args.log_level)
        logging.getLogger().setLevel(args.log_level)

        received_poison_pill_command  = False # from ENLIGHTEN
        received_poison_pill_response = False # from WasatchDevice

        sent_good = False

        while True:
            # ##################################################################
            # Relay downstream commands (GUI -> Spectrometer)
            # ##################################################################

            # only keep the MOST RECENT of any given command (but retain order otherwise)
            dedupped = self.dedupe(args.command_queue)

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
                        log.debug("continuous_poll: Processing command queue: %s", record.setting)

                        # basically, this simply moves each de-dupped command from 
                        # WasatchDeviceWrapper.command_queue to WasatchDevice.command_queue,
                        # where it gets read during the next call to 
                        # WasatchDevice.acquire_data.
                        wasatch_device.change_setting(record.setting, record.value)
            else:
                log.debug("continuous_poll: Command queue empty")

            if received_poison_pill_command:
                log.critical("continuous_poll: Exiting per command queue (poison pill received)")
                break

            # ##################################################################
            # Relay one upstream reading (Spectrometer -> GUI)
            # ##################################################################

            try:
                log.debug("continuous_poll: acquiring data")
                reading = wasatch_device.acquire_data()
            except Exception as exc:
                log.critical("continuous_poll: Exception", exc_info=1)
                break

            if WasatchDeviceWrapper.DISABLE_RESPONSE_QUEUE and sent_good:
                log.debug("leaving Reading on the floor during memory profiling")
                continue

            if reading is None:
                # FileSpectrometer does this right now...hardware "can't" really, 
                # because we use blocking calls, although we could probably add
                # timeouts to break those blocks.
                #
                # Also leveraging this to delay taking spectra until an EXPLICIT
                # integration time is set by the caller (could occur through "startup"
                # overrides).
                log.debug("continuous_poll: no Reading to be had")

            elif isinstance(reading, bool):
                # we received either a True (keepalive) or False (upstream poison pill)

                # was it just a keepalive?
                if reading == True:
                    # just pass it upstream and move on
                    try:
                        args.response_queue.put(reading, timeout=2)
                        sent_good = True
                    except Queue.Full:
                        log.error("unable to push Reading %d to GUI", reading.session_count, exc_info=1)
                else:
                    # it was an upstream poison pill
                    #
                    # There's nothing we need to do here except 'break'...that will
                    # exit the loop, and the last thing this function does is flow-up
                    # a poison pill anyway, so we're done
                    log.critical("continuous_poll: received upstream poison pill...exiting")
                    received_poison_pill_response = True
                    break
                    
            elif reading.failure is not None:
                # this wasn't passed-up as a poison-pill, but we're going to treat it
                # as one anyway
                #
                # @todo deprecate Reading.failure and just return False (poison pill?)
                log.critical("continuous_poll: hardware level error...exiting")
                break

            elif reading.spectrum is not None:
                log.debug("continuous_poll: sending Reading %d back to GUI process (%s)", reading.session_count, reading.spectrum[0:5])
                try:
                    args.response_queue.put(reading, timeout=2)
                except Queue.Full:
                    log.error("unable to push Reading %d to GUI", reading.session_count, exc_info=1)
                
            else:
                log.error("continuous_poll: received non-failure Reading without spectrum...ignoring?")

            # only poll hardware at 20Hz
            log.debug("continuous_poll: sleeping %.2f sec", WasatchDeviceWrapper.POLLER_WAIT_SEC)
            time.sleep(WasatchDeviceWrapper.POLLER_WAIT_SEC)

        if received_poison_pill_response:
            # send poison-pill notification upstream to Controller
            log.critical("exiting because of upstream poison-pill response")
            log.critical("sending poison-pill upstream to controller")
            try:
                args.response_queue.put(False, timeout=5)
            except Queue.Full:
                pass

            # Controller.ACQUISITION_TIMER_SLEEP_MS currently 50ms, so wait 500ms
            log.critical("waiting long enough for Controller to receive it")
            time.sleep(0.5)
        elif received_poison_pill_command:
            log.critical("exiting because of downstream poison-pill command")
            pass
        else:
            log.critical("exiting for no reason?!")
            pass

        log.critical("continuous_poll: done")
        sys.exit()

    def dedupe(self, q):
        keep = [] # list, not a set, because we want to keep it ordered
        while True:
            try:
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

            except Queue.Empty as exc:
                break

        return keep
