import sys
import pdb
import time
import random
import logging
import datetime
import multiprocessing
from queue import Queue

from . import applog
from . import utils

from PySide2 import QtCore
from PySide2.QtCore import QThread, QObject, Signal, Slot

from .SpectrometerSettings import SpectrometerSettings
from .ControlObject        import ControlObject
from .WasatchDevice        import WasatchDevice
from .Reading              import Reading

log = logging.getLogger(__name__)

##
# Wrap WasatchDevice in a non-blocking interface run in a separate
# process, using multiprocess.pipes to exchange data (SpectrometerSettings,
# Readings and StatusMessages) for multiprocessing-safe device communications
# and acquisition under Windows and Qt.
#
# @par Lifecycle
#
# From ENLIGHTEN's standpoint (the original Wasatch.PY caller), here's what's going on:
#
# - MainProcess (enlighten.Controller.setup_bus_listener) instantiates a
#   wasatch.WasatchBus (bus) which will be persistent through the application
#   lifetime. setup_bus_listener also creates a QTimer (bus_timer) which will
#   check the USB bus for newly-connected ("hotplug") devices every second or so.
#
# - Controller.tick_bus_listener
#   - does nothing (silently reschedules itself) if any new spectrometers are
#     actively in the process of connecting, because things get hairy if we're
#     trying to enumerate and configure several spectrometers at once
#
#   - calls bus.update(), which will internally instantiate and use a
#     wasatch.DeviceFinderUSB to scan the current USB bus and update its internal
#     list of all Wasatch spectrometers (whether already connected or otherwise)
#
#   - then calls Controller.connect_new() to process the updated device list
#     (including determining whether any new devices are visible, and if so what
#     to do about them)
#
# - Controller.connect_new()
#   - if there is at least one new spectrometer on the device list, pull off that
#     ONE device for connection (don't iterate over multiple new devices...we'll
#     get them on a subsequent bus tick).
#
#   - instantiates a WasatchDeviceWrapper and then calls connect() on it
#   - WasatchDeviceWrapper.connect()
#       - forks a child process running the continuous_poll() method of the _same_
#         WasatchDeviceWrapper instance
#           - the WDW in the MainProcess then waits (blocks) while waiting for a
#             single SpectrometerSettings object to be returned via a pipe from the
#             child process.  This doesn't block the GUI, because this whole sequence
#             is occuring in a background tick event on the bus_timer QTimer.
#           - the WDW in the child process is running continuous_poll()
#               - instantiates a WasatchDevice to access the actual hardware spectrometer
#                 over USB (this object will only ever be referenced within this subprocess)
#               - then calls WasatchDevice.connect()
#                   - WasatchDevice then instantiates an internal FeatureIdentificationDevice,
#                     FileSpectrometer or other implementation based on the passed DeviceID
#                   - WasatchDevice then populates a SpectrometerSettings object based on
#                     the connected device (loading the EEPROM, basic firmware settings etc)
#               - continuous_poll() sends back the single SpectrometerSettings object
#                 to the blocked MainProcess by way of confirmation that a new spectrometer
#                 has successfully connected
#    - Controller.connect_new() then completes the initialization of the spectrometer
#      in the GUI by calling Controller.initialize_new_device(), which adds the spectrometer
#      to Multispec, updates the EEPROMEditor, defaults the TEC controls and so on.
# - In the background, WasatchDeviceWrapper.continuous_poll() continues running, acting as a
#   "free-running mode driver" of the spectrometer, passing down new commands from the
#   enlighten.Controller, and feeding back Readings or StatusMessages when they appear.
#
# @par Shutdown
#
# The whole thing can be shutdown in various ways, using the concept of "poison pill"
# messages which can be flowed either upstream or downstream:
#
#   - if a hardware error occurs in the spectrometer process, it sends a poison
#     pill upstream (a False value where a Reading is expected), then self-destructs
#     (Controller is expected to drop the spectrometer from the GUI)
#   - if the GUI is closing, poison-pills (None values where ControlObjects are expected)
#     are sent downstream to each spectrometer process, telling them to terminate themselves
#
# There's some extra calls to ensure the logger process closes itself as well.
#
# @par Throughput Considerations
#
# It is important to recognize that continuous_poll() updates the command/response
# pipes at a relatively leisurely interval (currently 20Hz, set in POLLER_WAIT_SEC).
# No matter how short the integration time is (1ms), you're not going to get spectra
# faster than 20 per second through this (as ENLIGHTEN was designed as a real-time
# visualization tool, not a high-speed data collection tool).
#
# Now, if you set ACQUISITION_MODE_KEEP_ALL, then you should still get every
# spectrum (whatever the spectrometer achieved through its scan-rate, potentially
# 220/sec or so) -- but you'll get them in chunks (e.g., scan rate of 220/sec
# polled at 20Hz = 20 chunks of 11 ea).
#
# @par Responsiveness
#
# With regard to the "immediacy" of commands like laser_enable, note that spectrometer
# subprocesses (Process-2, etc) are internally single-threaded: the continuous_poll()
# function BLOCKS on WasatchDevice.acquire_data(), meaning that even if new laser_enable
# commands get pushed into the downstream command pipe, continuous_poll won't check for
# them until the end of the current acquisition.
#
# What we'd really like is two threads running in the subprocess, one handling
# acquisitions and most commands, and another handling high-priority events like
# laser_enable.  I'm not sure the pipes are designed for multiple readers, but
# a SEPARATE pipe (or queue) could be setup for high-priority commands, and
# EXCLUSIVELY read by the secondary thread.  That just leaves open the question
# of synchronization on WasatchDevice's USBDevice.
#
# @par Memory Leak
#
# This class appears to leak memory under Linux, but only when debug logging
# is enabled (ergo, the real leak is likely in applog).
#
# - occurs under Python 2.7 and 3.4
# - correlated to response_queue and get_final_item()
#   - DISABLE_RESPONSE_QUEUE reduced ENLIGHTEN leak by 66% (18MB -> 6MB over 60sec @ 10ms)
#     (while obviously blocking core functionality)
# - doesn't show up under memory_profiler
# - Reading.copy() doesn't help
# - exc_clear() doesn't help
#
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
    #   - note that this is essentially ADDED to the total measurement time
    #     of EACH AND EVERY INTEGRATION
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
        #pdb.set_trace()

        self.settings_queue = False
        self.response_queue = False
        self.message_queue = False
        self.command_queue = False

        self.settings_queue = Queue() # spectrometer -> GUI (SpectrometerSettings, one-time)
        self.response_queue = Queue()# spectrometer -> GUI (Readings)
        self.message_queue = Queue() # spectrometer -> GUI (StatusMessages)
        self.command_queue = Queue() # GUI -> spectrometer (ControlObjects)


        self.connected    = False
        self.closing      = False   # Don't permit new acquires during close
        self.poller       = None    # a handle to the subprocess
        self.thread       = False

        # this will contain a populated SpectrometerSettings object from the
        # WasatchDevice, for relay to the instantiating Controller
        self.settings     = None

        if WasatchDeviceWrapper.DISABLE_RESPONSE_QUEUE:
            self.previous_reading = None

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
    # @par settings_queue
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

        # instantiate thread
        self.thread = QThread()
        self.wrapper_worker = Wrapper_Worker(
            device_id      = self.device_id,
            command_queue  = self.command_queue,  # Main --> subprocess
            response_queue = self.response_queue, # Main <-- subprocess \
            settings_queue = self.settings_queue, # Main <-- subprocess  ) consolidate into SubprocessMessage?
            message_queue  = self.message_queue)
        log.debug("device wrapper: Instantce created for worker")

        self.wrapper_worker.moveToThread(self.thread)

        self.thread.started.connect(self.wrapper_worker.run)

        log.debug("deivce wrapper: Initating wrapper thread")
        self.thread.start()

        # If something goes wrong, we won't want to kill the current process (this function runs
        # within MainProcess), but we will want to kill the spawned subprocess, and ensure 'self'
        # (the current WasatchDeviceWrapper instance) is ready for clean destruction (otherwise
        # we'd be leaking resources continuously).
        kill_myself = False

        # expect to read a single post-initialization SpectrometerSettings object off the queue
        # try:
        time_start = datetime.datetime.now()
        settings_timeout_sec = 15
        self.settings = None
        while True:
            log.debug("WasatchDeviceWrapper.connect: blocking on settings_queue_consumer (waiting on forked continuous_poll)")
            # note: testing indicates it may take more than 2.5sec for the forked
            # continuous_poll to actually start moving.  5sec timeout may be on the short side?
            # Initial testing with 2 spectrometers showed 2nd spectrometer taking 6+ sec to initialize.
            # If you kick-off another heavy operation in another window while the spectrometer is
            # enumerating, this can take even longer.

            if not self.settings_queue.empty():
                self.settings = self.settings_queue.get_nowait()
                break

            if (datetime.datetime.now() - time_start).total_seconds() > settings_timeout_sec:
                log.error("WasatchDeviceWrapper.connect: gave up waiting for SpectrometerSettings")
                kill_myself = True
            else:
                log.debug("WasatchDeviceWrapper.connect: still waiting for SpectrometerSettings")
                time.sleep(0.5)

        # except Exception as exc:
        #    log.warn("WasatchDeviceWrapper.connect: settings_queue_consumer.get() caught exception", exc_info=1)
        #    kill_myself = True

        if self.settings is None:
            log.error("WasatchDeviceWrapper.connect: received poison-pill from forked continuous_poll")
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
            time.sleep(1)
            self.command_queue.put(None) # put(None, timeout=2)

            log.warn("WasatchDeviceWrapper.connect: waiting .5 sec")
            

            # log.warn("connect: terminating poller")
            # self.poller.terminate()

            log.warn("WasatchDeviceWrapper.releasing poller")

            return False

        # AttributeError: 'AutoProxy[Queue]' object has no attribute 'close'
        del self.settings_queue

        log.info("WasatchDeviceWrapper.connect: received SpectrometerSettings from subprocess")
        #self.settings.dump()

        # After we return True, the Controller will then take a handle to our received
        # settings object, and will keep a reference to this Wrapper object for sending
        # commands and reading spectra.
        log.debug("WasatchDeviceWrapper.connect: succeeded")
        self.connected = True

        return True

    def disconnect(self):
        # send poison pill to the subprocess
        self.closing = True
        try:
            #need to investigate more, must have 1 sec delay or else crashed
            time.sleep(1)
            self.thread.terminate()
            log.info(f"disconnect: thread stopped {self.thread.isFinished()}")
        except Exception as exc:
            log.critical("disconnect: Cannot terminate thread", exc_info=1)
        log.debug("disconnect: sending poison pill downstream")
        try:
            self.command_queue.put(None) # put(None, timeout=2)
        except:
            pass

        time.sleep(0.1)
        log.debug("WasatchDeviceWrapper.disconnect: done")

        self.connected = False
        self.settings_queue = Queue() # spectrometer -> GUI (SpectrometerSettings, one-time)
        self.response_queue = Queue()# spectrometer -> GUI (Readings)
        self.message_queue = Queue() # spectrometer -> GUI (StatusMessages)
        self.command_queue = Queue() # GUI -> spectrometer (ControlObjects)

        return True

    ##
    # Similar to acquire_data, this method is called by the Controller in
    # MainProcess to dequeue a StatusMessage from the spectrometer sub-
    # process, if one is available.
    def acquire_status_message(self):
        if self.closing or not self.connected:
            return None

        if not self.message_queue.empty():
            return self.message_queue.get_nowait()

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
        if self.closing or not self.connected:
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
    def get_final_item(self, keep_averaged=False):
        last_reading  = None
        last_averaged = None
        dequeue_count = 0
        # kludge - memory profiling
        if WasatchDeviceWrapper.DISABLE_RESPONSE_QUEUE and self.previous_reading is not None:
            self.previous_reading.spectrum = [(1.1 - 0.2 * random.random()) * x for x in self.previous_reading.spectrum]
            return self.previous_reading

        while True:
            # without waiting (don't block), just get the first item off the
            # queue if there is one
            reading = None
            if not self.response_queue.empty():
                reading = self.response_queue.get_nowait()
            else:
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
            if WasatchDeviceWrapper.DISABLE_RESPONSE_QUEUE:
                self.previous_reading = last_averaged
            last_reading = None
            return last_averaged

        # We've had every opportunity short-cut the process: we could have
        # returned potential poison pills or completed averages, but found
        # none of those.  Yet apparently we did read some normal readings.
        # Return the latest of those.

        if WasatchDeviceWrapper.DISABLE_RESPONSE_QUEUE:
            self.previous_reading = last_reading
        return last_reading

    ##
    # Add the specified setting and value to the local control queue.
    #
    # In ENLIGHTEN, this is called by MainProcess.Controller.
    #
    # For OEM customers controlling the spectrometer via the non-blocking
    # WasatchDeviceWrapper interface, this is the method you would call to
    # change the various spectrometer settings.  
    #
    # @see \ref README_SETTINGS.md for a list of valid settings you can
    #      pass, as well as any parameters expected by each
    def change_setting(self, setting, value):
        try:
            if not self.connected:
                return

            log.debug("WasatchDeviceWrapper.change_setting: %s => %s", setting, value)
            control_object = ControlObject(setting, value)

            self.command_queue.put(control_object)

            return
        except Exception as e:
            log.error(f"found an error of {e}")

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
    # None in either the settings_queue or response_queue indicates "this
    # subprocess is shutting down and the spectrometer is going bye-bye."

class Wrapper_Worker(QObject):
        
    def __init__(
        self,
        device_id, 
        command_queue,
        response_queue,
        settings_queue,
        message_queue,
        parent=None):

        super().__init__(parent)
        self.device_id = device_id
        self.command_queue = command_queue
        self.response_queue = response_queue
        self.settings_queue = settings_queue
        self.message_queue = message_queue
        self. wasatch_device = False
        log.info(f"wrapper thread: Instantiated thread worker.")


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
        

    def run(self):
        log.debug("wrapper thread: separate thread is now running.")
        #self.settings_queue.put("A queue message") # put(None, timeout=2)

        try:
            self.wasatch_device = WasatchDevice(
                device_id = self.device_id,
                message_queue = self.message_queue)
        except:
            log.critical("wrapper thread: exception instantiating WasatchDevice", exc_info=1)
            return self.settings_queue.put(None) # put(None, timeout=2)

        ok = False
        try:
            ok = self.wasatch_device.connect()
        except:
            log.critical("wrapper thread exception connecting", exc_info=1)
            return self.settings_queue.put(None) # put(None, timeout=2)

        if not ok:
            log.critical("wrapper thread: failed to connect")
            return self.settings_queue.put(False) # put(None, timeout=2)

        log.debug("wrapper thread: connected to a spectrometer")

        # send the SpectrometerSettings back to the GUI process
        log.debug("wrapper thread: returning SpectrometerSettings to GUI process")
        self.settings_queue.put(self.wasatch_device.settings) # put(wasatch_device.settings, timeout=10)

        log.debug("wrapper thread: entering loop")
        last_heartbeat = datetime.datetime.now()
        last_command = datetime.datetime.now()
        min_thread_timeout_sec = 10 
        thread_timeout_sec = min_thread_timeout_sec 

        received_poison_pill_command  = False # from ENLIGHTEN
        received_poison_pill_response = False # from WasatchDevice

        sent_good = False
        num_connected_devices = 1

        while True:

            now = datetime.datetime.now()

            #heartbeat logger (outgoing keepalives to logger)
            if (now - last_heartbeat).total_seconds() >= 3:
                log.info("heartbeat")
                last_heartbeat = now

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
                        log.debug("wrapper thread: Processing command queue: %s", record.setting)

                        last_command = now

                        # basically, this simply moves each de-dupped command from
                        # WasatchDeviceWrapper.command_queue to WasatchDevice.command_queue,
                        # where it gets read during the next call to
                        # WasatchDevice.acquire_data.
                        self.wasatch_device.change_setting(record.setting, record.value)

                        # peek in some settings locally
                        if record.setting == "num_connected_devices":
                            num_connected_devices = record.value
                        elif record.setting == "subprocess_timeout_sec":
                            thread_timeout_sec = record.value
                        # elif record.setting == "integration_time_ms":
                        #     if subprocess_timeout_sec is not None:
                        #         subprocess_timeout_sec = max(subprocess_timeout_sec_min, record.value * 3)
                        #         log.debug("continuous_poll: auto-adjusted subprocess_timeout_sec to %.2f", subprocess_timeout_sec)

            else:
                log.debug("wrapper_thread: Command queue empty")

            if received_poison_pill_command:
                log.critical("wrapper_thread: Exiting per command queue (poison pill received)")
                break

            # has ENLIGHTEN crashed and stopped sending us heartbeats?
            if thread_timeout_sec is not None:
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
                log.debug("wrapper thread: acquiring data")
                reading = self.wasatch_device.acquire_data()
                #log.debug("continuous_poll: acquire_data returned %s", str(reading))
            except Exception as exc:
                log.critical("wrapper thread: Exception", exc_info=1)
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
                log.debug("wrapper thread: no Reading to be had")

            elif isinstance(reading, bool):
                # we received either a True (keepalive) or False (upstream poison pill)
                log.debug("wrapper thread: reading was bool")

                # was it just a keepalive?
                if reading == True:
                    # just pass it upstream and move on
                    try:
                        self.response_queue.put(reading) # put(reading, timeout=2)
                        sent_good = True
                    except:
                        log.error("unable to push Reading %d to GUI", reading.session_count, exc_info=1)
                else:
                    # it was an upstream poison pill
                    #
                    # There's nothing we need to do here except 'break'...that will
                    # exit the loop, and the last thing this function does is flow-up
                    # a poison pill anyway, so we're done
                    log.critical("wrapper thread: received upstream poison pill...exiting")
                    received_poison_pill_response = True
                    break

            elif reading.failure is not None:
                # this wasn't passed-up as a poison-pill, but we're going to treat it
                # as one anyway
                #
                # @todo deprecate Reading.failure and just return False (poison pill?)
                log.critical("wrapper thread: hardware level error...exiting")
                break

            elif reading.spectrum is not None:
                log.debug("wrapper thread: sending Reading %d back to GUI process (%s)", reading.session_count, reading.spectrum[0:5])
                try:
                    self.response_queue.put(reading) # put(reading, timeout=2)
                except:
                    log.error("unable to push Reading %d to GUI", reading.session_count, exc_info=1)

            else:
                log.error("wrapper thread: received non-failure Reading without spectrum...ignoring?")

            # only poll hardware at 20Hz
            sleep_sec = WasatchDeviceWrapper.POLLER_WAIT_SEC * num_connected_devices
            log.debug("wrapper thread: sleeping %.2f sec", sleep_sec)
            time.sleep(sleep_sec)

        ########################################################################
        # we have exited the loop
        ########################################################################
        if received_poison_pill_response:
            # send poison-pill notification upstream to Controller
            log.critical("exiting because of upstream poison-pill response")
            log.critical("sending poison-pill upstream to controller")
            self.response_queue.put(False) # put(False, timeout=5)

            # Controller.ACQUISITION_TIMER_SLEEP_MS currently 50ms, so wait 500ms
            log.critical("waiting long enough for Controller to receive it")
            time.sleep(1)
        elif received_poison_pill_command:
            log.critical("exiting because of downstream poison-pill command")
            pass
        else:
            log.critical("exiting for no reason?!")
            pass

        log.critical("wrapper thread: done")


    def dedupe(self, q):
        try:
            keep = [] # list, not a set, because we want to keep it ordered
            while True:
                # try:
                if not q.empty():
                    control_object = q.get_nowait() # get_nowait()

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
        except Exception as e:
            log.error(f"Found an error of {e}")