import re
import os
import gc
import time
import queue
import psutil
import logging
import datetime
import multiprocessing

# import numpy # memory leaks?

from configparser import ConfigParser

from . import utils

from .FeatureIdentificationDevice import FeatureIdentificationDevice
from .SpectrometerSettings        import SpectrometerSettings
from .BalanceAcquisition          import BalanceAcquisition
from .SpectrometerState           import SpectrometerState
from .FileSpectrometer            import FileSpectrometer
from .ControlObject               import ControlObject
from .WasatchBus                  import WasatchBus
from .DeviceID                    import DeviceID
from .Reading                     import Reading

log = logging.getLogger(__name__)

##
# A WasatchDevice encapsulates and wraps a Wasatch spectrometer in a blocking
# interface.  It will normally wrap one of the following:
#
# - a FeatureIdentificationDevice (modern FID spectrometer)
# - a FileSpectrometer (filesystem gateway to virtual/simulated spectrometer)
#
# ENLIGHTEN does not instantiate WasatchDevices directly, but instead uses
# a WasatchDeviceWrapper to access a single WasatchDevice in a single subprocess.
# Other users of Wasatch.PY may of course instantiate a WasatchDevice directly,
# and can consider it roughly equivalent (though differently structured) to a
# WasatchNET.Spectrometer.  (Arguably wasatch.FeatureIdentificationDevice is the
# closer analog to WasatchNET.Spectrometer, as Wasatch.NET does not have anything
# like FileSpectrometer.)
class WasatchDevice(object):

    ##
    # @param device_id      a DeviceID instance OR string label thereof
    # @param message_queue  if provided, used to send status back to caller
    def __init__(self, device_id, message_queue=None):

        # if passed a string representation of a DeviceID, deserialize it 
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.device_id      = device_id
        self.message_queue  = message_queue

        self.connected = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. Although a logical queue, has nothing to do with multiprocessing.
        self.command_queue = []

        # Enable for "immediate mode" by clients like WasatchShell (by default,
        # inbound commands are queued and executed at beginning of next acquire_data;
        # this runs them as they arrive).
        self.immediate_mode = False

        # Enable this to skip extra metadata in Readings (detector temperature,
        # laser temperature, photodiode, battery etc)
        self.bare_readings = False

        self.settings = SpectrometerSettings()

        # Any particular reason these aren't in FeatureIdentificationDevice?
        # I guess because they could theoretically apply to a FileSpectrometer, etc?
        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0
        self.take_one               = False

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()

    # ######################################################################## #
    #                                                                          #
    #                               Connection                                 #
    #                                                                          #
    # ######################################################################## #

    ## Attempt low level connection to the specified DeviceID
    def connect(self):
        if self.device_id.is_usb():
            log.debug("trying to connect to USB device")
            if self.connect_feature_identification():
                log.debug("Connected to FeatureIdentificationDevice")
                self.connected = True
                self.initialize_settings()
                return True
        elif self.device_id.is_file():
            log.debug("trying to connect to FILE device")
            if self.connect_file_spectrometer():
                log.debug("connected to FileSpectrometer")
                self.connected = True
                self.initialize_settings()
                return True
        else:
            log.critical("unsupported DeviceID protocol: %s", device_id)

        log.debug("Can't connect to %s", self.device_id)
        return False

    def disconnect(self):
        log.debug("WasatchDevice.disconnect: calling hardware disconnect")
        try:
            self.hardware.disconnect()
        except Exception as exc:
            log.critical("Issue disconnecting hardware", exc_info=1)

        time.sleep(0.1)

        self.connected = False
        return True

    def connect_file_spectrometer(self):
        dev = FileSpectrometer(self.device_id)
        if dev.connect():
            self.hardware = dev
            return True

    ## Given a specified universal identifier, attempt to connect to the device using FID protocol.
    # @todo merge with the hardcoded list in DeviceFinderUSB
    def connect_feature_identification(self):
        FID_list = ["1000", "2000", "4000"] # hex

        # check to see if valid FID PID
        pid_hex = self.device_id.get_pid_hex()
        if not pid_hex in FID_list:
            log.debug("connect_feature_identification: device_id %s PID %s not in FID list %s", self.device_id, pid_hex, FID_list)
            return False

        dev = None
        try:
            log.debug("connect_fid: instantiating FID with device_id %s pid %s", self.device_id, pid_hex)
            dev = FeatureIdentificationDevice(device_id=self.device_id, message_queue=self.message_queue)
            log.debug("connect_fid: instantiated")

            try:
                log.debug("connect_fid: connecting")
                ok = dev.connect()
                log.debug("connect_fid: connected")
            except Exception as exc:
                log.critical("connect_feature_identification: %s", exc, exc_info=1)
                return False

            if not ok:
                log.critical("Low level failure in device connect")
                return False

            self.hardware = dev

        except Exception as exc:
            log.critical("Problem connecting to: %s", self.device_id, exc_info=1)
            return False

        log.debug("Connected to FeatureIdentificationDevice %s", self.device_id)
        return True

    def initialize_settings(self):
        if not self.connected:
            return

        self.settings = self.hardware.settings

        # generic post-initialization stuff for both SP and FID (and now
        # FileSpectrometer) - we probably need an ABC for this
        self.hardware.get_microcontroller_firmware_version()
        self.hardware.get_fpga_firmware_version()
        self.hardware.get_integration_time_ms()
        self.hardware.get_detector_gain()

        # could read the defaults for these ss.state volatiles from FID/SP too:
        #
        # self.tec_setpoint_degC
        # self.high_gain_mode_enabled
        # self.triggering_enabled
        # self.laser_enabled
        # self.laser_power
        # self.ccd_offset

        self.settings.update_wavecal()
        self.settings.update_raman_intensity_factors()
        self.settings.dump()

        # SiG-VIS kludge
        if self.settings.eeprom.model == "ENG-SV-DEFAULT":
            log.critical("enabling bare_readings for %s", self.settings.eeprom.model)
            self.bare_readings = True

    # ######################################################################## #
    #                                                                          #
    #                               Acquisition                                #
    #                                                                          #
    # ######################################################################## #


    ##
    # Process all enqueued settings, then read actual data (spectrum and
    # temperatures) from the device.
    #
    # This function is called by WasatchDeviceWrapper.continuous_poll.
    #
    # Somewhat confusingly, this function can return any of the following:
    #
    # @return False     a poison-pill sent upstream to device shutdown
    # @return True      keepalive
    # @return None      keepalive
    # @return Reading   a partial or complete spectrometer Reading (may itself 
    #                   have Reading.failure set other than None)
    #
    # @see Controller.acquire_reading
    def acquire_data(self):
        log.debug("Device acquire_data")

        self.monitor_memory()

        if self.hardware.shutdown_requested:
            return False 

        # process queued commands, and find out if we've been asked to read a
        # spectrum
        needs_acquisition = self.process_commands()
        if not (needs_acquisition or self.settings.state.free_running_mode):
            return None

        # if we don't yet have an integration time, nothing to do
        if self.settings.state.integration_time_ms <= 0:
            log.debug("skipping acquire_data because no integration_time_ms")
            return None

        # note that right now, all we return are Readings (encapsulating both
        # spectra and temperatures).  If we disable spectra (turn off
        # free_running_mode), then ENLIGHTEN stops receiving temperatures as
        # well.  In the future perhaps we should return multiple object types
        # (Acquisitions, Temperatures, etc)
        return self.acquire_spectrum()

    ##
    # Generate one Reading from the spectrometer, including one 
    # optionally-averaged spectrum, device temperatures and other hardware
    # status.
    #
    # This is normally called by acquire_data when that function decides it is 
    # time to perform an acquisition.
    #
    # @par Scan Averaging
    #
    # IF the driver is in free-running mode, AND performing scan averaging,
    # THEN scan averaging is NOT encapsulated within a single call to this 
    # function.  Instead, we let ths spectrometer run in free-running mode, 
    # collecting individual spectra as per normal, and returning each "partial"
    # readings while "building up" to the final averaged measurement.  
    # 
    # That is to say, if scan averaging is set to 10, then this function will
    # get called 10 times, as ticked by the regular free-running timers, before
    # the fully averaged spectrum is returned.  A total of 10 (not 11) spectra
    # will be generated and sent upstream: the first 9 "partial" (unaveraged) 
    # reads, and the final 10th spectrum which will contain the average of all
    # 10 measurements.  
    #
    # This gives the user-facing GUI an opportunity to update the "collected 
    # X-of-Y" readout on-screen, and potentially even graph the traces of 
    # in-process partial readings.
    #
    # HOWEVER, this doesn't make as much sense if we're not in free-running mode,
    # i.e. the subprocess has been slaved to explicit control by the Controller
    # (likely a feature object like BatchCollection), and is collecting exactly 
    # those measurements we're being commanded, as they're commanded.  
    #
    # THEREFORE, if the driver IS NOT in free-running mode, then we ONLY return
    # the final averaged spectrum as one atomic operation.
    #
    # In this case, if scan averaging is set to 10, then A SINGLE CALL to this
    # function will "block" while the full 10 measurements are made, and then
    # a single, fully-averaged spectrum will be returned upstream.
    #
    # @return a Reading object
    #
    def acquire_spectrum(self):
        averaging_enabled = (self.settings.state.scans_to_average > 1)

        ########################################################################
        # Batch Collection silliness
        ########################################################################
        
        # We could move this up into ENLIGHTEN.BatchCollection: have it enable
        # the laser, wait a bit, and then send the "acquire" command.  But since
        # WasatchDeviceWrapper.continuous_poll ticks at its own interval, that
        # would introduce timing interference, and different acquisitions would
        # realistically end up with different warm-up times for lasers (all "at
        # least" the configured time, but some longer than others).  Instead,
        # for now I'm putting this delay here, so it will be exactly the same
        # (given sleep()'s precision) for each acquisition.  For true precision
        # this should all go into the firmware anyway.

        dark_reading = None
        if self.settings.state.acquisition_take_dark_enable:
            log.debug("taking internal dark")
            dark_reading = self.take_one_averaged_reading()
            if isinstance(dark_reading, bool):
                return dark_reading
            log.debug("done taking internal dark")

        auto_enable_laser = self.settings.state.acquisition_laser_trigger_enable # and not self.settings.state.free_running_mode
        log.debug("acquire_spectrum: auto_enable_laser = %s", auto_enable_laser)
        if auto_enable_laser:
            log.debug("acquire_spectum: enabling laser, then sleeping %d ms", self.settings.state.acquisition_laser_trigger_delay_ms)
            self.hardware.set_laser_enable(True)
            if self.hardware.shutdown_requested: 
                return False

            time.sleep(self.settings.state.acquisition_laser_trigger_delay_ms / 1000.0)

        ########################################################################
        # Take a Reading (possibly averaged)
        ########################################################################

        # IMX sensors are free-running, so make sure we collect one full 
        # integration after turning on the laser
        self.perform_optional_throwaways()

        log.debug("taking averaged reading")
        reading = self.take_one_averaged_reading()
        if isinstance(reading, bool):
            return reading

        # don't perform dark subtraction, but pass the dark measurement along
        # with the averaged reading
        if dark_reading is not None:
            log.debug("attaching dark to reading")
            reading.dark = dark_reading.spectrum

        ########################################################################
        # provide early exit-ramp if we've been asked to return bare Readings 
        # (just averaged spectra with corrected bad pixels, no metadata)
        ########################################################################

        def disable_laser(force=False):
            if force or auto_enable_laser:
                log.debug("acquire_spectrum: disabling laser post-acquisition")
                self.hardware.set_laser_enable(False)
            return False # for convenience

        if self.bare_readings:
            disable_laser()
            return reading

        ########################################################################
        # We're done with the (possibly-averaged) spectrum, so we'd like to now
        # disable the automatically-enabled laser, if it was engaged; but before
        # we can do that that, we should take any requested measurements of the
        # laser temperature and photodiode, as those would obviously be invalid-
        # ated if we took them AFTER the laser was off.  
        ########################################################################

        # only read laser temperature if we have a laser 
        if self.settings.eeprom.has_laser:
            try:
                count = 2 if self.settings.state.secondary_adc_enabled else 1
                for throwaway in range(count):
                    reading.laser_temperature_raw  = self.hardware.get_laser_temperature_raw()
                    if self.hardware.shutdown_requested: 
                        return disable_laser(force=True)

                reading.laser_temperature_degC = self.hardware.get_laser_temperature_degC(reading.laser_temperature_raw)
                if self.hardware.shutdown_requested: 
                    return disable_laser(force=True)

                if not auto_enable_laser:
                    reading.laser_enabled = self.hardware.get_laser_enabled()
                if self.hardware.shutdown_requested: 
                    return disable_laser(force=True)

            except Exception as exc:
                log.debug("Error reading laser temperature", exc_info=1)

        # read secondary ADC if requested
        if self.settings.state.secondary_adc_enabled:
            try:
                self.hardware.select_adc(1)
                if self.hardware.shutdown_requested: 
                    return disable_laser(force=True)

                for throwaway in range(2):
                    reading.secondary_adc_raw = self.hardware.get_secondary_adc_raw()
                    if self.hardware.shutdown_requested: 
                        return disable_laser(force=True)

                reading.secondary_adc_calibrated = self.hardware.get_secondary_adc_calibrated(reading.secondary_adc_raw)
                self.hardware.select_adc(0)
                if self.hardware.shutdown_requested: 
                    return disable_laser(force=True)

            except Exception as exc:
                log.debug("Error reading secondary ADC", exc_info=1)

        ########################################################################
        # we've read the laser temperature and photodiode, so we can now safely 
        # disable the laser (if we're the one who enabled it)
        ########################################################################

        disable_laser()

        ########################################################################
        # finish collecting any metadata that doesn't require the laser
        ########################################################################

        # read detector temperature if applicable 
        if self.settings.eeprom.has_cooling:
            try:
                reading.detector_temperature_raw  = self.hardware.get_detector_temperature_raw()
                if self.hardware.shutdown_requested: 
                    return False

                reading.detector_temperature_degC = self.hardware.get_detector_temperature_degC(reading.detector_temperature_raw)
                if self.hardware.shutdown_requested: 
                    return False

            except Exception as exc:
                log.debug("Error reading detector temperature", exc_info=1)

        # read ambient temperature if applicable 
        if self.settings.is_gen15():
            try:
                reading.ambient_temperature_degC = self.hardware.get_ambient_temperature_degC()
            except Exception as exc:
                log.debug("Error reading ambient temperature", exc_info=1)

        # read battery every 10sec
        if self.settings.eeprom.has_battery:
            if self.settings.state.battery_timestamp is None or (datetime.datetime.now() >= self.settings.state.battery_timestamp + datetime.timedelta(seconds=10)):
                reading.battery_raw = self.hardware.get_battery_state_raw()
                if self.hardware.shutdown_requested: 
                    return False

                reading.battery_percentage = self.hardware.get_battery_percentage()
                if self.hardware.shutdown_requested: 
                    return False

                reading.battery_charging = self.hardware.get_battery_charging()
                if self.hardware.shutdown_requested: 
                    return False

                log.debug("battery: level %.2f%% (%s)", reading.battery_percentage, "charging" if reading.battery_charging else "not charging")

        # log.debug("device.acquire_spectrum: returning %s", reading)
        return reading

    ##
    # It's unclear how many throwaways are really needed for a stable Raman spectrum, and whether they're 
    # really based on number of integrations (sensor stabilization) or time (laser warmup); I suspect
    # both.  Also note the potential need for sensor warm-up, but I think that's handled inside FW.
    #
    # Optimal would probably be something like "As many integrations as it takes to span 2sec, but not
    # fewer than two."
    def perform_optional_throwaways(self):
        if self.settings.is_micro() and self.take_one:
            count = 2
            readout_ms = 5
            while count * (self.settings.state.integration_time_ms + readout_ms) < 2000:
                count += 1
            for i in range(count):
                log.debug("performing optional throwaway %d of %d before ramanMicro TakeOne", i, count)
                spectrum_and_row = self.hardware.get_line()
    ## 
    # @returns Reading on success, true or false on "stop processing" conditions
    def take_one_averaged_reading(self):

        # Okay, let's talk about averaging.  Normally we don't perform averaging
        # as a blocking batch process inside Wasatch.PY.  However, ENLIGHTEN's
        # BatchCollection requirements pulled this architecture in weird 
        # directions and we tried to accommodate responsively rather than refactor
        # each time requirements changed...hence the current strangeness.
        # 
        # Normally this process (the background process dedicated to a forked
        # WasatchDeviceWrapper object) is in "free-running" mode, taking spectra
        # just as fast as it can in an endless loop, feeding them back to the
        # consumer (ENLIGHTEN) over a multiprocess pipe.  To keep that pipeline
        # "moving," generally we don't do heavy blocking operations down here
        # in the background thread.
        #
        # However, the use-case for BatchCollection was very specific: to
        # take an averaged series of darks, then enable the laser, wait for the
        # laser to warmup, take an averaged series of dark-corrected measurements,
        # and return the average as one spectrum.  
        #
        # That is VERY different from what this code was originally written to 
        # do.  There are cleaner ways to do this, but I haven't gone back to
        # tidy things up.

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

            # TODO...just include a copy of SpectrometerState? something to think 
            # about. That would actually provide a reason to roll all the 
            # temperature etc readouts into the SpectrometerState class...
            reading.integration_time_ms = self.settings.state.integration_time_ms
            reading.laser_power         = self.settings.state.laser_power
            reading.laser_power_in_mW   = self.settings.state.laser_power_in_mW
            reading.laser_enabled       = self.settings.state.laser_enabled  

            # Are we reading one spectrum (normal mode, or "slow" area scan), or
            # doing a batch-read of a whole frame ("fast" area scan)?
            #
            # It's a bit confusing that this is INSIDE the scan averaging loop...
            # there is NO use-case for "averaged" area scan.  We should move this
            # up and out of take_one_averaged_reading().
            if self.settings.state.area_scan_enabled and self.settings.state.area_scan_fast:

                # collect a whole frame of area scan data
                reading.area_scan_data = []
                try:
                    rows = self.settings.eeprom.active_pixels_vertical
                    log.debug("trying to read a fast area scan frame of %d rows", rows)
                    for i in range(rows):
                        spectrum_and_row = self.hardware.get_line(trigger=(i==0))
                        if self.hardware.shutdown_requested: 
                            return False
                        elif spectrum_and_row.spectrum is None:
                            log.debug("device.take_one_averaged_spectrum: get_line None, sending keepalive for now (area scan fast)")
                            return True

                        # mimic "slow" results to minimize downstream fuss
                        reading.spectrum = spectrum_and_row.spectrum 
                        reading.area_scan_row_count = spectrum_and_row.row
                        reading.timestamp_complete  = datetime.datetime.now()

                        # accumulate the frame here
                        reading.area_scan_data.append(spectrum_and_row.spectrum)
                        log.debug("device.take_one_averaged_reading(area scan fast): got %s ... (row %d)", 
                            reading.spectrum[0:9], reading.area_scan_row_count)

                except Exception as exc:
                    log.critical("Error reading hardware data", exc_info=1)
                    reading.spectrum = None
                    reading.failure = str(exc)

            else:

                # collect ONE spectrum (it's in a while loop because we may have
                # to wait for an external trigger, and must wait through a series
                # of timeouts)
                externally_triggered = self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_EXTERNAL
                try:
                    while True:
                        spectrum_and_row = self.hardware.get_line()
                        if self.hardware.shutdown_requested: 
                            return False

                        if spectrum_and_row.spectrum is None:
                            # FeatureIdentificationDevice can return None when waiting
                            # on an external trigger.  FileSpectrometer can as well if 
                            # there is no new spectrum to read.
                            log.debug("device.take_one_averaged_spectrum: get_line None, sending keepalive for now")
                            return True
                        else:
                            break

                    reading.spectrum            = spectrum_and_row.spectrum
                    reading.area_scan_row_count = spectrum_and_row.row
                    reading.timestamp_complete  = datetime.datetime.now()

                    log.debug("device.take_one_averaged_reading: got %s ... (row %d)", reading.spectrum[0:9], reading.area_scan_row_count)
                except Exception as exc:
                    # if we got the timeout after switching from externally triggered back to internal, let it ride
                    if externally_triggered:
                        log.debug("caught exception from get_line while externally triggered...sending keepalive")
                        return True

                    log.critical("Error reading hardware data", exc_info=1)
                    reading.spectrum = None
                    reading.failure = str(exc)

            ####################################################################
            # Aggregate scan averaging
            ####################################################################

            # It's important to note here that the scan averaging "sum" buffer
            # here (self.summed_spectra) and counter (self.sum_count) are 
            # attributes of this WasatchDevice object: they are not part of the
            # Reading being sent back to the caller.
            #
            # The current architecture in Wasatch.PY, which is a little different
            # than other Wasatch drivers, is that even when scan averaging is
            # enabled, EVERY SPECTRUM will be flowed back to the caller.  
            #
            # This is because early versions of ENLIGHTEN showed the "individual" 
            # (pre-averaged) spectra as a faint grey trace in the background, 
            # updating with each integration, showing the higher noise level of 
            # "raw" spectra while an on-screen counter showed the incrementing
            # tally of summed spectra in the buffer.
            #
            # Then, when the FINAL raw spectrum had been read and the complete
            # average could be computed, that averaged spectrum was returned
            # with a special flag in the Reading object to indicate "fully
            # averaged."
            #
            # We no longer show the "background" spectral trace in ENLIGHTEN,
            # so this is kind of wasting some intra-process bandwidth, but we
            # do still increment the on-screen tally as feedback while the
            # averaging is taking place, so the in-process Reading messages
            # sent during an averaged collection are not entirely wasted.
            #
            # Still, this is not the way we would have designed a Python driver
            # "from a blank sheet," and our other driver architectures show that.

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
                    reading.spectrum = []
                    for i in range(len(self.summed_spectra)):
                        reading.spectrum.append(self.summed_spectra[i] / self.sum_count)
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

        # log.debug("device.take_one_averaged_reading: returning %s", reading)
        return reading

    def monitor_memory(self):
        now = datetime.datetime.now()
        if (now - self.last_memory_check).total_seconds() < 5:
            return

        self.last_memory_check = now
        size_in_bytes = psutil.Process(self.process_id).memory_info().rss
        log.info("monitor_memory: PID %d memory = %d bytes", self.process_id, size_in_bytes)

        if False:
            for i in [0, 1, 2, 1, 0]:
                gc.collect(i)

    ##
    # Process every entry on the incoming command (settings) queue, writing each 
    # to the device.  
    #
    # Essentially this iterates through all the (setting, value) pairs we've
    # received through change_setting() which have not yet been processed, and
    # 
    #
    # Note that WasatchDeviceWrapper.continuous_poll "de-dupes" commands on
    # receipt from ENLIGHTEN, so the command stream arising from that source
    # should already be optimized and minimal.  Commands injected manually by
    # calling WasatchDevice.change_setting() do not receive this treatment.
    #
    # In the normal multi-process (ENLIGHTEN) workflow, this function is called 
    # at the beginning of acquire_data, itself ticked regularly by 
    # WasatchDeviceWrapper.continuous_poll.
    def process_commands(self):
        control_object = "throwaway"
        retval = False
        log.debug("process_commands: processing")
        # while control_object != None:
        while len(self.command_queue) > 0:
            # try:
            # control_object = self.command_queue.get_nowait()

            control_object = self.command_queue.pop(0)
            log.debug("process_commands: %s", control_object)

            # is this a command used by WasatchDevice itself, and not
            # passed down to FeatureIdentificationDevice?
            if control_object.setting.lower() == "acquire":
                log.debug("process_commands: acquire found")
                retval = True
            else:
                # send setting downstream to be processed by the spectrometer HAL
                # (probably FeatureIdentificationDevice)
                self.hardware.write_setting(control_object)

            if control_object.setting == "free_running_mode" and not self.hardware.settings.state.free_running_mode:
                # we just LEFT free-running mode (went on "pause"), so toss any 
                # queued for the caller (ENLIGHTEN)
                log.debug("exited free-running mode")

        return retval

    # ######################################################################## #
    #                                                                          #
    #                             BalanceAcquisition                           #
    #                                                                          #
    # ######################################################################## #

    def balance_acquisition(self, 
            device                  = None, 
            mode                    = None, 
            intensity               = 45000, 
            threshold               = 2500, 
            pixel                   = None, 
            max_integration_time_ms = 5000, 
            max_tries               = 20):
        balancer = BalanceAcquisition(
            device                  = self,
            mode                    = mode, 
            intensity               = intensity, 
            threshold               = threshold, 
            pixel                   = pixel, 
            max_integration_time_ms = max_integration_time_ms, 
            max_tries               = max_tries)
        return balancer.balance()

    # ######################################################################## #
    #                                                                          #
    #                             Hardware Control                             #
    #                                                                          #
    # ######################################################################## #

    ##
    # Processes an incoming (setting, value) pair.
    #
    # Some settings are processed internally within this function, if the 
    # functionality they are controlling is implemented by WasatchDevice.
    # This includes scan averaging, and anything related to scan averaging
    # (such as "take one" behavior).
    #
    # Most tuples are queued to be sent downstream to the connected hardware
    # (usually FeatureIdentificationDevice) at the start of the next 
    # acquisition.
    #
    # Some hardware settings (those involving triggering or the laser) are 
    # sent downstream immediately, rather than waiting for the next "scheduled"
    # settings update.
    #
    # ENLIGHTEN commands to WasatchDeviceWrapper are sent here by 
    # WasatchDeviceWrapper.continuous_poll.
    #
    # @param setting (Input) which setting to change
    # @param value   (Input) the new value of the setting (required, but can 
    #                be None or "anything" for commands like "acquire" which
    #                don't use the argument).
    # @param allow_immediate 
    def change_setting(self, setting, value, allow_immediate=True):
        control_object = ControlObject(setting, value)
        log.debug("WasatchDevice.change_setting: %s", control_object)

        # Since scan averaging lives in WasatchDevice, handle commands which affect
        # averaging at this level
        if control_object.setting == "scans_to_average":
            self.sum_count = 0
            self.settings.state.scans_to_average = int(value) 
            return
        elif control_object.setting == "reset_scan_averaging":
            self.sum_count = 0
            return
        elif control_object.setting == "take_one":
            self.take_one = True
            self.change_setting("free_running_mode", True)
            return
        elif control_object.setting == "cancel_take_one":
            self.sum_count = 0
            self.take_one = False
            self.change_setting("free_running_mode", False)
            return

        self.command_queue.append(control_object)
        log.debug("change_setting: queued %s", control_object)

        # always process trigger_source commands promptly (can't wait for end of
        # acquisition which may never come)
        if (allow_immediate and self.immediate_mode) or re.search(r"trigger|laser", setting):
            log.debug("immediately processing %s", control_object)
            self.process_commands()
