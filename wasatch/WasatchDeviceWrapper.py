import time
import random
import logging
import datetime

from queue import Queue

from .SpectrometerResponse import SpectrometerResponse
from .ControlObject        import ControlObject
from .WrapperWorker        import WrapperWorker

log = logging.getLogger(__name__)

##
# Wrap WasatchDevice in a non-blocking interface run in a separate
# thread, using multiprocess.pipes to exchange data (SpectrometerSettings,
# Readings and StatusMessages) for multiprocessing-safe device communications
# and acquisition under Windows and Qt.
#
# @todo This document is full of references to continuous_poll(). That method 
#       no longer exists, and has been replaced by WrapperWorker.run()
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
class WasatchDeviceWrapper:

    ACQUISITION_MODE_KEEP_ALL      = 0 # don't drop frames
    ACQUISITION_MODE_LATEST        = 1 # only grab most-recent frame (allow dropping)
    ACQUISITION_MODE_KEEP_COMPLETE = 2 # generally grab most-recent frame (allow dropping),
                                       # except don't drop FULLY AVERAGED frames (summation
                                       # contributors can be skipped).  If the most-recent
                                       # frame IS a partial summation contributor, then send it on.

    # ##########################################################################
    #                                                                          #
    #                             Parent Thread                                #
    #                                                                          #
    # ##########################################################################

    ##
    # Instantiated by Controller.connect_new(), if-and-only-if a WasatchBus
    # reports a DeviceID which has not already connected to the GUI. The DeviceID
    # is "unique and relevant" to the bus which reported it, but neither the
    # bus class nor instance is passed in to this object. If the DeviceID looks like
    # "USB:VID:PID:bus:addr", then it is presumably USB. Future DeviceID formats 
    # could include "FILE:/path/to/dir", etc. However, device_id is just
    # a string scalar to this class, and actually parsing / using it should be
    # entirely encapsulated within WasatchDevice and lower using DeviceID.
    def __init__(self, device_id, log_level, callback=None):
        self.device_id = device_id
        self.log_level = log_level
        self.callback = callback

        self.settings_queue = Queue() # spectrometer -> GUI (SpectrometerSettings, one-time)
        self.response_queue = Queue() # spectrometer -> GUI (Readings)
        self.message_queue  = Queue() # spectrometer -> GUI (StatusMessages)
        self.command_queue  = Queue() # GUI -> spectrometer (ControlObjects)
        self.alert_queue    = Queue() # GUI -> spectrometer (ControlObjects)

        self.connected    = False
        self.closing      = False   # Don't permit new acquires during close
        self.poller       = None    # a handle to the child thread
        self.is_ocean     = '0x2457' in str(device_id)
        self.is_andor     = '0x136e' in str(device_id)
        self.is_spi       = '0x0403' in str(device_id)
        self.is_ids       = 'IDSPeak' in str(device_id)
        self.mock         = 'MOCK' in str(device_id).upper()
        self.is_ble       = 'BLE' in str(device_id)
        self.is_tcp       = 'TCP' in str(device_id)
        self.wrapper_worker = None
        self.connect_start_time = datetime.datetime(year=datetime.MAXYEAR, month=1, day=1)

        # this will contain a populated SpectrometerSettings object from the
        # WasatchDevice, for relay to the instantiating Controller
        self.settings     = None

        self.previous_reading = None

        self.reset_tries = 0

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
        self.closing = False # needed if doing reset and closing previously was True
        self.wrapper_worker = WrapperWorker(
            device_id      = self.device_id,
            command_queue  = self.command_queue,  # Main --> child (dedupable single-threaded spectrometer commands)
            alert_queue    = self.alert_queue,    # Main --> child (real-time hints and interrupts relating to ongoing commands)
            response_queue = self.response_queue, # Main <-- child \
            settings_queue = self.settings_queue, # Main <-- child  | consolidate into 
            message_queue  = self.message_queue,  # Main <-- child /  SpectrometerMessage?
            is_ocean       = self.is_ocean,
            is_andor       = self.is_andor,
            is_spi         = self.is_spi,
            is_ble         = self.is_ble,
            is_tcp         = self.is_tcp,
            is_ids         = self.is_ids,
            log_level      = self.log_level,
            callback       = self.callback)
        log.debug("device wrapper: Instance created for worker")

        self.wrapper_worker.daemon = True

        log.debug("deivce wrapper: starting WrapperWorker thread")
        self.wrapper_worker.start()

        # expect to read a single post-initialization SpectrometerSettings object off the queue
        self.connect_start_time = datetime.datetime.now()
        self.settings = None
        log.debug("connect: setup connection, returning to controller for settings polling")

        if self.callback:
            self.wait_for_settings()

        return True

    def wait_for_settings(self):
        while True:
            if self.poll_settings():
                break
            if (datetime.datetime.now() - self.connect_start_time).total_seconds() > 2:
                raise Exception("Failed to read SpectrometerSettings")
            time.sleep(0.1)

    def poll_settings(self): 
        """ 
        @returns SpectrometerResponse(True) on success, (False) otherwise 
        @note this doesn't have a timeout -- that's in enlighten.Controller.check_ready_initialize
        """
        log.debug("polling device settings")
        if not self.settings_queue.empty():
            result = self.settings_queue.get_nowait()
            if result is None: 
                log.critical("poll_settings: failed to retrieve device settings (got None, shouldn't happen)")
                return SpectrometerResponse(False, error_msg="Failed to retrieve device settings")

            if result.data:
                log.info(f"got spectrometer settings for device")
                self.connected = True
                self.settings = result.data
                self.connect_start_time = datetime.datetime(year=datetime.MAXYEAR, month=1, day=1)
                self.settings.state.dump("WasatchDeviceWrapper.poll_settings")
                return SpectrometerResponse(True)
            else:
                log.critical("got error response instead of settings from connection request")
                return result
        else:
            log.debug("settings still not obtained, returning")
            return None

    def reset(self):
        if "USB" in str(self.device_id):
            self.reset_tries += 1
            self.command_queue.put(ControlObject("reset", None))

    def disconnect(self):
        # send poison pill to the child
        self.closing = True
        log.debug("disconnect: sending poison pill downstream")
        try:
            self.command_queue.put(None) 
        except:
            pass
        time.sleep(0.1)
        log.debug("disconnect: done")
        del self.wrapper_worker

        self.connected = False

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

    def send_alert(self, setting, value):
        self.alert_queue.put(ControlObject(setting, value))

    ##
    # This method is called by the Controller.  It checks the response_queue it 
    # shares with the child thread to see if any Reading objects have been queued
    # from the spectrometer to the GUI.
    #
    # It is the upstream interface's job to decide how to process the potentially
    # voluminous amount of data returned from the device. get_last by default 
    # will make sure the queue is cleared, then return the most recent reading 
    # from the device.
    #
    # @note it is not clear that measurement modes other than
    #       ACQUISITION_MODE_KEEP_COMPLETE have been well-tested,
    #       especially in the context of multiple spectrometers,
    #       BatchCollection etc.
    def acquire_data(self, mode=None): # -> SpectrometerResponse 
        if self.closing or not self.connected:
            log.critical(f"acquire_data: closing {self.closing} (sending poison-pill upstream) and connected {self.connected}")
            return SpectrometerResponse(False, poison_pill=True)

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
    def get_final_item(self, keep_averaged=False): # -> SpectrometerResponse 
        last_response = SpectrometerResponse()
        last_averaged_response = SpectrometerResponse()
        dequeue_count = 0

        while True:
            # without waiting (don't block), just get the first item off the
            # queue if there is one
            wrapper_response = SpectrometerResponse()
            if not self.response_queue.empty():
                wrapper_response = self.response_queue.get_nowait()
            else:
                # If there is nothing more to read, then we've emptied the queue
                # log.debug("get_final has nothing more to read, sending up readings")
                break

            # If we come across a keep_alive, ignore it for the moment.
            # for now continue cleaning-out the queue.
            if wrapper_response.keep_alive or wrapper_response.data is None:
                # If that keep alive is associated with an error though float it up
                if wrapper_response.keep_alive and wrapper_response.error_msg:
                    last_response = wrapper_response
                    break
                log.debug("get_final_item: ignoring keepalive")
                continue

            # If we come across a poison-pill, flow that up immediately --
            # game-over, we're done
            if wrapper_response.poison_pill:
                log.critical("get_final_item: poison-pill!")
                return wrapper_response

            # apparently we read a Reading
            log.debug(f"get_final_item: read Reading {wrapper_response.data}")
            last_response = wrapper_response
            dequeue_count += 1

            # Was this the final spectrum in an averaged sequence?
            #
            # If so, grab a reference, but DON'T flow it up yet...there
            # may be a NEWER fully-averaged spectrum later on.
            #
            # It is the purpose of this function ("get FINAL item...")
            # to PURGE THE QUEUE -- we are not intending to leave any
            # values in the queue (None, bool, or Readings of any kind.
            if keep_averaged and wrapper_response.data.averaged:
                # log.debug("keeping last_response as last_averaged_response")
                last_averaged_response = wrapper_response

        if last_response.data is None:
            log.debug("wrapper worker floating up keep alive last reading")
            # apparently we didn't read anything...just pass up a keepalive
            last_averaged_response = None
            last_response.keep_alive = True
            return last_response

        # apparently we read at least some readings.  For interest, how how many
        # readings did we throw away (not return up to ENLIGHTEN)?
        if dequeue_count > 1:
            log.debug("discarded %d readings", dequeue_count - 1)

        # if we're doing averaging, and we found one or more averaged readings,
        # return the latest of those
        if last_averaged_response.data is not None:
            # log.debug("returning latest averaged reading")
            last_response = None
            return last_averaged_response

        # We've had every opportunity short-cut the process: we could have
        # returned potential poison pills or completed averages, but found
        # none of those.  Yet apparently we did read some normal readings.
        # Return the latest of those.

        # log.debug("returning last_response")
        return last_response

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
    def change_setting(self, setting, value=None):
        try:
            if not self.connected:
                return

            log.debug("change_setting: %s => %s", setting, value)
            control_object = ControlObject(setting, value)

            self.command_queue.put(control_object)

            return
        except Exception as e:
            log.error(f"found an error of {e}")
