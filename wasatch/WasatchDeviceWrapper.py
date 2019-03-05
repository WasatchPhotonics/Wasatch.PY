import sys
import time
import Queue
import logging
import multiprocessing

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
class WasatchDeviceWrapper(object):

    ACQUISITION_MODE_KEEP_ALL      = 0 # don't drop frames
    ACQUISITION_MODE_LATEST        = 1 # only grab most-recent frame (allow dropping)
    ACQUISITION_MODE_KEEP_COMPLETE = 2 # generally grab most-recent frame (allow dropping),
                                       # except don't drop FULLY AVERAGED frames (summation
                                       # contributors can be skipped).  If the most-recent
                                       # frame IS a partial summation contributor, then send it on.

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

        # TODO: see if the managed queues would be more robust
        #
        # self.manager = multiprocessing.Manager()
        # self.command_queue               = self.manager.Queue()
        # self.response_queue              = self.manager.Queue()
        # self.message_queue               = self.manager.Queue()
        # self.spectrometer_settings_queue = self.manager.Queue()

        self.spectrometer_settings_queue = multiprocessing.Queue(1)   # spectrometer -> GUI (SpectrometerSettings, one-time)
        self.response_queue              = multiprocessing.Queue(100) # spectrometer -> GUI (Readings)
        self.message_queue               = multiprocessing.Queue(100) # spectrometer -> GUI (StatusMessages)
        self.command_queue               = multiprocessing.Queue(100) # GUI -> spectrometer (ControlObjects)

        # TODO: make this dynamic:
        #   - initially on number of connected spectrometers
        #   - ideally on configured integration times per spectrometer
        #   - need to check whether it can be modified from inside the process
        #   - need to check whether it can be modified after process creation
        self.poller_wait  = 0.1     # .1sec = 100ms = update from hardware device at 10Hz

        self.closing      = False   # Don't permit new acquires during close
        self.poller       = None    # a handle to the subprocess

        # this will contain a populated SpectrometerSettings object from the 
        # WasatchDevice, for relay to the instantiating Controller
        self.settings     = None    

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
            self.command_queue.put(None, 2)

            log.warn("WasatchDeviceWrapper.connect: waiting .5 sec")
            time.sleep(.5)

            # log.warn("connect: terminating poller")
            # self.poller.terminate()

            log.warn("WasatchDeviceWrapper.releasing poller")
            self.poller = None

            return False

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
        self.command_queue.put(None, 2)

        try:
            self.poller.join(timeout=2)
        except NameError as exc:
            log.warn("disconnect: Poller previously disconnected", exc_info=1)
        except Exception as exc:
            log.critical("disconnect: Cannot join poller", exc_info=1)

        log.debug("Post poller join")

        try:
            self.poller.terminate()
            log.debug("disconnect: poller terminated")
        except Exception as exc:
            log.critical("disconnect: Cannot terminate poller", exc_info=1)

        time.sleep(0.1)
        log.debug("WasatchDeviceWrapper.disconnect: done")

        return True

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

        if mode == self.ACQUISITION_MODE_LATEST:
            return self.get_final_item()

        # presumably mode == self.ACQUISITION_MODE_KEEP_ALL:

        # Get the oldest entry off of the queue. This expects the Controller to be
        # able to process them upstream as fast as possible, because otherwise
        # the queue will grow (we're not currently limiting its size) and the
        # process will eventually crash with memory issues.

        # Note that these two calls to response_queue aren't synchronized
        reading = None
        qsize = self.response_queue.qsize()
        try:
            reading = self.response_queue.get_nowait()
            log.debug("acquire_data: read Reading %d (qsize %d)", reading.session_count, qsize)
        except Queue.Empty:
            log.debug("acquire_data: no data, sending True upstream as KEEPALIVE")
            return True 

        return reading

    ## Read from the response queue until empty (or we find an averaged item) 
    def get_final_item(self, keep_averaged=False):
        reading = None
        dequeue_count = 0
        last_averaged = None
        while True:
            try:
                reading = self.response_queue.get_nowait()
                if reading:
                    if isinstance(reading, bool):
                        log.critical("get_final_item: read Reading %s", reading)
                        return reading
                    log.debug("get_final_item: read Reading %d", reading.session_count)
            except Queue.Empty:
                break

            if reading is None:
                break

            dequeue_count += 1

            # was this the final spectrum in an averaged sequence?
            if keep_averaged and reading.averaged:
                last_averaged = reading

        # if we're doing averaging, take the latest of those
        if last_averaged:
            reading = last_averaged

        if reading is not None and dequeue_count > 1:
            log.debug("discarded %d spectra", dequeue_count - 1)

        return reading

    ## 
    # Add the specified setting and value to the local control queue.
    # 
    # called by MainProcess.Controller
    def change_setting(self, setting, value):
        log.debug("WasatchDeviceWrapper.change_setting: %s => %s", setting, value)
        control_object = ControlObject(setting, value)
        try:
            self.command_queue.put(control_object)
        except Exception as exc:
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
            wasatch_device = WasatchDevice(args.device_id, args.message_queue)
        except:
            log.critical("continuous_poll: exception instantiating WasatchDevice", exc_info=1)
            return args.spectrometer_settings_queue.put(None, timeout=2)

        ok = False
        try:
            ok = wasatch_device.connect()
        except:
            log.critical("continuous_poll: exception connecting", exc_info=1)
            args.spectrometer_settings_queue.put(None, timeout=2)
            return

        if not ok:
            log.critical("continuous_poll: failed to connect")
            args.spectrometer_settings_queue.put(None, timeout=2)
            return

        log.debug("continuous_poll: connected to a spectrometer")

        # send the SpectrometerSettings back to the GUI process
        log.debug("continuous_poll: returning SpectrometerSettings to GUI process")
        args.spectrometer_settings_queue.put(wasatch_device.settings, timeout=10)

        # Read forever until the None poison pill is received
        log.debug("continuous_poll: entering loop")
        
        log.debug("resetting commanded log_level %s", args.log_level)
        logging.getLogger().setLevel(args.log_level)
        while True:

            # ##################################################################
            # Relay downstream commands (GUI -> Spectrometer)
            # ##################################################################

            poison_pill = False

            # only keep the MOST RECENT of any given command (but retain order otherwise)
            dedupped = self.dedupe(args.command_queue)

            # apply dedupped commands
            if dedupped:
                for record in dedupped:
                    if record is None:
                        poison_pill = True
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

            if poison_pill:
                log.debug("continuous_poll: Exiting per command queue (poison pill received)")
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

            if reading is None:
                # FileSpectrometer does this right now...hardware "can't" really, 
                # because we use blocking calls, although we could probably add
                # timeouts to break those blocks.
                #
                # Also leveraging this to delay taking spectra until an EXPLICIT
                # integration time is set by the caller (could occur through "startup"
                # overrides).
                log.debug("continuous_poll: no Reading to be had")
            else:
                if reading.failure is not None:
                    log.critical("continuous_poll: Hardware level ERROR")
                    break

                if reading.spectrum is not None:
                    log.debug("continuous_poll: sending Reading %d back to GUI process (%s)", reading.session_count, reading.spectrum[0:5])
                    args.response_queue.put(reading, timeout=1)

            # only poll hardware at 20Hz
            log.debug("continuous_poll: sleeping %d", self.poller_wait)
            time.sleep(self.poller_wait)

        # send poison-pill upstream to Controller, then quit
        log.critical("sending poison-pill upstream to controller")
        args.response_queue.put(False, timeout=5)

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
