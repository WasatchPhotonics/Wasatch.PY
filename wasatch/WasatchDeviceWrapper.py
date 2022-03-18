import sys
import pdb
import time
import random
import logging
import datetime
import threading

from queue import Queue

from . import applog
from . import utils

from .SpectrometerSettings import SpectrometerSettings
from .ControlObject        import ControlObject
from .WrapperWorker        import WrapperWorker
from .BLEDevice            import BLEDevice
from .Reading              import Reading

log = logging.getLogger(__name__)

##
# Wrap WasatchDevice in a non-blocking interface run in a separate
# thread, using multiprocess.pipes to exchange data (SpectrometerSettings,
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
#       - spawns a thread running the continuous_poll() method of the _same_
#         WasatchDeviceWrapper instance
#           - the WDW in the MainProcess then waits (blocks) while waiting for a
#             single SpectrometerSettings object to be returned via a pipe from the
#             child thread.  This doesn't block the GUI, because this whole sequence
#             is occuring in a background tick event on the bus_timer QTimer.
#           - the WDW in the child threadis running continuous_poll()
#               - instantiates a WasatchDevice to access the actual hardware spectrometer
#                 over USB (this object will only ever be referenced within this thread)
#               - then calls WasatchDevice.connect()
#                   - WasatchDevice then instantiates an internal FeatureIdentificationDevice
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
# - if a hardware error occurs in the spectrometer thread, it sends a poison
#   pill upstream (a False value where a Reading is expected), then self-destructs
#   (Controller is expected to drop the spectrometer from the GUI)
# - if the GUI is closing, poison-pills (None values where ControlObjects are expected)
#   are sent downstream to each spectrometer thread, telling them to terminate themselves
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
# With regard to the "immediacy" of commands like laser_enable, note that 
# spectrometer threads are internally single-threaded: the continuous_poll()
# function BLOCKS on WasatchDevice.acquire_data(), meaning that even if new
# laser_enable commands get pushed into the downstream command pipe, 
# continuous_poll won't check for them until the end of the current acquisition.
#
# What we'd really like is two threads running in the child, one handling
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

    # ##########################################################################
    #                                                                          #
    #                             Parent Thread                                #
    #                                                                          #
    # ##########################################################################

    ##
    # Instantiated by Controller.connect_new(), if-and-only-if a WasatchBus
    # reports a DeviceID which has not already connected to the GUI.  The DeviceID
    # is "unique and relevant" to the bus which reported it, but neither the
    # bus class nor instance is passed in to this object.  If the DeviceID looks like
    # "USB:VID:PID:bus:addr", then it is presumably USB.  Future DeviceID formats 
    # could include "FILE:/path/to/dir", etc.  However, device_id is just
    # a string scalar to this class, and actually parsing / using it should be
    # entirely encapsulated within WasatchDevice and lower using DeviceID.
    def __init__(self, device_id, log_level):
        self.device_id = device_id
        self.log_level = log_level

        self.settings_queue = False
        self.response_queue = False
        self.message_queue = False
        self.command_queue = False

        self.settings_queue = Queue() # spectrometer -> GUI (SpectrometerSettings, one-time)
        self.response_queue = Queue() # spectrometer -> GUI (Readings)
        self.message_queue  = Queue() # spectrometer -> GUI (StatusMessages)
        self.command_queue  = Queue() # GUI -> spectrometer (ControlObjects)

        self.connected    = False
        self.closing      = False   # Don't permit new acquires during close
        self.poller       = None    # a handle to the child thread
        self.thread       = False
        self.is_ocean     = '0x2457' in str(device_id)
        self.is_andor     = '0x136e' in str(device_id)
        self.mock         = 'test' in str(device_id)
        self.is_ble       = isinstance(device_id.device_type, BLEDevice)

        # this will contain a populated SpectrometerSettings object from the
        # WasatchDevice, for relay to the instantiating Controller
        self.settings     = None

        self.previous_reading = None

    ##
    # Create a low level device object with the specified identifier, kick off
    # the child thread to attempt to read from it.
    #
    # Called by Controller.connect_new() immediately after instantiation
    #
    # Spawn a child thread running the continuous_poll() method on THIS
    # object instance.  
    #
    # The two threads are coupled via the 3 queues:
    #
    # @par settings_queue
    #
    # Lets the child (WasatchDevice) send a single, one-time copy
    # of a populated SpectrometerSettings object back through the
    # Wrapper to the calling Controller.  This is the primary way the
    # Controller knows what kind of spectrometer it has connected to,
    # what hardware features and EEPROM settings are applied etc.
    #
    # Thereafter both WasatchDevice and Controller will maintain
    # their own copies of SpectrometerSettings, and they are not
    # automatically synchronized (is there a clever way to do this?).
    # They may well drift out-of-sync regarding state, although the
    # command_queue helps keep them somewhat in sync.
    #
    # @par command_queue
    #
    # The Controller will send ControlObject instances (basically
    # (name, value) pairs) through the Wrapper to the WasatchDevice
    # to set attributes or commands on the WasatchDevice spectrometer.
    # These may be volatile hardware settings (laser power, integration
    # time), meta-commands to the WasatchDevice class (scan averaging),
    # or EEPROM updates.
    #
    # @par response_queue
    #
    # The WasatchDevice will stream a continuous series of Reading
    # instances back through the Wrapper to the Controller.  These
    # each contain a newly read spectrum, as well as metadata about
    # the spectrometer at the time the spectrum was taken (integration
    # time, laser power), plus additional readings from the spectrometer
    # (detector and laser temperature, secondary ADC).
    #
    def connect(self):

        # instantiate thread
        self.wrapper_worker = WrapperWorker(
            device_id      = self.device_id,
            command_queue  = self.command_queue,  # Main --> child
            response_queue = self.response_queue, # Main <-- child \
            settings_queue = self.settings_queue, # Main <-- child / consolidate into SpectrometerMessage?
            message_queue  = self.message_queue,
            is_ocean       = self.is_ocean,
            is_andor       = self.is_andor,
            is_ble         = self.is_ble)
        log.debug("device wrapper: Instance created for worker")

        self.wrapper_worker.setDaemon(True)
        log.debug("deivce wrapper: Initiating wrapper thread")

        self.wrapper_worker.start()

        # If something goes wrong, we won't want to kill the current thread (this
        # function runs within MainProcess), but we will want to kill the spawned 
        # child, and ensure 'self' (the current WasatchDeviceWrapper instance) is 
        # ready for clean destruction (otherwise we'd be leaking resources 
        # continuously).
        kill_myself = False

        # expect to read a single post-initialization SpectrometerSettings object off the queue
        time_start = datetime.datetime.now()
        settings_timeout_sec = 15
        if self.is_ble:
            settings_timeout_sec += 10
        self.settings = None
        log.debug("connect: blocking on settings_queue (waiting on child thread to send SpectrometerSettings)")
        while True:
            if not self.settings_queue.empty():
                self.settings = self.settings_queue.get_nowait()
                break

            if (datetime.datetime.now() - time_start).total_seconds() > settings_timeout_sec:
                log.error("connect: gave up waiting for SpectrometerSettings")
                kill_myself = True
                break
            else:
                log.debug("connect: still waiting for SpectrometerSettings")
                time.sleep(0.1)

        if self.settings is None:
            log.error("connect: received poison-pill from child thread")
            kill_myself = True

        if kill_myself:
            # Apparently something failed in initialization of the device, and it
            # never succeeded in sending a SpectrometerSettings object. Do our best to
            # kill the thread

            # MZ: should this bit be merged with disconnect()?
            self.settings = None
            self.closing = True

            log.warn("connect: releasing thread")
            return False

        # AttributeError: 'AutoProxy[Queue]' object has no attribute 'close'
        del self.settings_queue

        log.info("connect: received SpectrometerSettings from child")

        # After we return True, the Controller will then take a handle to our received
        # settings object, and will keep a reference to this Wrapper object for sending
        # commands and reading spectra.
        log.debug("connect: succeeded")
        self.connected = True

        return True

    def disconnect(self):
        # send poison pill to the child
        self.closing = True
        log.debug("disconnect: sending poison pill downstream")
        try:
            self.command_queue.put(None) 
        except:
            pass
        try:
            self.wrapper_worker.join()
        except Exception as exc:
            log.critical("disconnect: Cannot terminate thread", exc_info=1)

        time.sleep(0.1)
        log.debug("disconnect: done")
        self.thread = None

        self.connected = False

        # MZ: why do we recreate these?
        self.settings_queue = Queue()
        self.response_queue = Queue()
        self.message_queue  = Queue()
        self.command_queue  = Queue()

        return True

    ##
    # Similar to acquire_data, this method is called by the Controller in
    # MainProcess to dequeue a StatusMessage from the spectrometer child
    # thread, if one is available.
    def acquire_status_message(self):
        if self.closing or not self.connected:
            return None

        if not self.message_queue.empty():
            return self.message_queue.get_nowait()

    ##
    # This method is called by the Controller in MainProcess.  It checks
    # the response_queue it shares with the child thread to see if any
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
    # - False = Poison pill (shutdown child)
    # - None or True = Keepalive (no-op)
    # - Reading = measured data
    #
    # @note it is not clear that measurement modes other than
    #       ACQUISITION_MODE_KEEP_COMPLETE have been well-tested,
    #       especially in the context of multiple spectrometers,
    #       BatchCollection etc.
    def acquire_data(self, mode=None):
        if self.closing or not self.connected:
            log.critical("acquire_data: closing (sending poison-pill upstream)")
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

        # MZ: this would be a possible place to revert logging from DEBUG to
        # whatever was passed (self.log_level), especially if this is the
        # first reading returned by this thread.

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

            log.debug("change_setting: %s => %s", setting, value)
            control_object = ControlObject(setting, value)

            self.command_queue.put(control_object)

            return
        except Exception as e:
            log.error(f"found an error of {e}")

