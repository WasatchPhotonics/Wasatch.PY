import re
import os
import time
import queue
import psutil
import logging
import datetime
import threading

from configparser import ConfigParser

from . import utils

from .FeatureIdentificationDevice import FeatureIdentificationDevice
from .SpectrometerSettings        import SpectrometerSettings
from .SpectrometerResponse        import SpectrometerResponse
from .SpectrometerRequest         import SpectrometerRequest
from .SpectrometerResponse        import ErrorLevel
from .BalanceAcquisition          import BalanceAcquisition
from .SpectrometerState           import SpectrometerState
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
#
# ENLIGHTEN does not instantiate WasatchDevices directly, but instead uses
# a WasatchDeviceWrapper to access a single WasatchDevice in a dedicated child 
# thread.  Other users of Wasatch.PY may of course instantiate a WasatchDevice 
# directly.
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

        self.lock = threading.Lock()

        self.connected = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. Although a logical queue, has nothing to do with multiprocessing.
        self.command_queue = []

        # Enable for "immediate mode" by clients like WasatchShell (by default,
        # inbound commands are queued and executed at beginning of next acquire_data;
        # this runs them as they arrive).
        self.immediate_mode = False

        self.settings = SpectrometerSettings()

        # Any particular reason these aren't in FeatureIdentificationDevice?
        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0
        self.take_one               = False

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0

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
        else:
            log.critical("unsupported DeviceID protocol: %s", device_id)

        log.debug("Can't connect to %s", self.device_id)
        return False

    def disconnect(self):
        log.debug("WasatchDevice.disconnect: calling hardware disconnect")
        try:
            req = SpectrometerRequest("disconnect")
            self.hardware.handle_request([req])
        except Exception as exc:
            log.critical("Issue disconnecting hardware", exc_info=1)

        time.sleep(0.1)

        self.connected = False
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

        # generic post-initialization stuff 
        req_fw_v = SpectrometerRequest('get_microcontroller_firmware_version')
        req_fpga_v = SpectrometerRequest('get_fpga_firmware_version')
        req_int = SpectrometerRequest('get_integration_time_ms')
        req_gain = SpectrometerRequest('get_detector_gain')# note we don't pass update_session_eeprom, so this doesn't really do anything
        reqs = [req_fw_v, req_fpga_v, req_int, req_gain]
        self.hardware.handle_request(reqs) 
        # could read the defaults for these ss.state volatiles from FID too:
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
        log.debug("acquire_data: start")

        self.monitor_memory()

        if self.hardware.shutdown_requested:
            log.critical("acquire_data: hardware shutdown requested")
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
        acquire_response = SpectrometerResponse()


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

        dark_reading = SpectrometerResponse()
        if self.settings.state.acquisition_take_dark_enable:
            log.debug("taking internal dark")
            dark_reading = self.take_one_averaged_reading()
            if dark_reading.poison_pill or dark_reading.error_msg:
                log.debug(f"dark reading was bool {dark_reading}")
                return dark_reading
            log.debug("done taking internal dark")

        auto_enable_laser = self.settings.state.acquisition_laser_trigger_enable # and not self.settings.state.free_running_mode
        log.debug("acquire_spectrum: auto_enable_laser = %s", auto_enable_laser)
        if auto_enable_laser:
            log.debug("acquire_spectum: enabling laser, then sleeping %d ms", self.settings.state.acquisition_laser_trigger_delay_ms)
            req = SpectrometerRequest('set_laser_enable', args=[True])
            self.hardware.handle_request([req])
            if self.hardware.shutdown_requested:
                log.debug(f"auto_enable_laser shutdown requested")
                acquire_response.poison_pill = True
                return acquire_response

            time.sleep(self.settings.state.acquisition_laser_trigger_delay_ms / 1000.0)

        ########################################################################
        # Take a Reading (possibly averaged)
        ########################################################################

        # IMX sensors are free-running, so make sure we collect one full
        # integration after turning on the laser
        self.perform_optional_throwaways()

        log.debug("taking averaged reading")
        take_one_response = self.take_one_averaged_reading()
        reading = take_one_response.data
        if take_one_response.poison_pill:
            log.debug(f"take_one_averaged_reading floating poison pill {take_one_response}")
            return take_one_response
        if take_one_response.keep_alive:
            log.debug(f"floating up keep alive")
            return take_one_response
        if take_one_response.data == None:
            log.debug(f"Received a none reading, floating it up {take_one_response}")
            return take_one_response

        # don't perform dark subtraction, but pass the dark measurement along
        # with the averaged reading
        if dark_reading.data is not None:
            log.debug("attaching dark to reading")
            reading.dark = dark_reading.data.spectrum

        ########################################################################
        # provide early exit-ramp if we've been asked to return bare Readings
        # (just averaged spectra with corrected bad pixels, no metadata)
        ########################################################################

        def disable_laser(force=False):
            if force or auto_enable_laser:
                log.debug("acquire_spectrum: disabling laser post-acquisition")
                req = SpectrometerRequest('set_laser_enable', args=[False])
                self.hardware.handle_request([req])
                acquire_response.poison_pill = True
            return acquire_response # for convenience

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
                    req = SpectrometerRequest('get_laser_temperature_raw')
                    res = self.hardware.handle_request([req])
                    if res.error_msg != '':
                        return res
                    reading.laser_temperature_raw  = res.data
                    if self.hardware.shutdown_requested:
                        return disable_laser(force=True)


                req = SpectrometerRequest('get_laser_temperature_degC', args=[reading.laser_temperature_raw])
                res = self.hardware.handle_request([req])
                if res.error_msg != '':
                    return res
                reading.laser_temperature_degC = res.data
                if self.hardware.shutdown_requested:
                    return disable_laser(force=True)

                if not auto_enable_laser:
                    req_en = SpectrometerRequest("get_laser_enabled")
                    req_can = SpectrometerRequest("can_laser_fire")
                    req_is = SpectrometerRequest("is_laser_firing")
                    reqs = [req_en, req_can, req_is]
                    self.hardware.handle_requests(reqs)

                if self.hardware.shutdown_requested:
                    return disable_laser(force=True)

            except Exception as exc:
                log.debug("Error reading laser temperature", exc_info=1)

        # read secondary ADC if requested
        if self.settings.state.secondary_adc_enabled:
            try:
                req = SpectrometerRequest("select_adc", args=[1])
                self.hardware.handle_requests([req])
                if self.hardware.shutdown_requested:
                    return disable_laser(force=True)

                for throwaway in range(2):
                    req = SpectrometerRequest("get_secondary_adc_raw")
                    res = self.hardware.handle_requests([req])
                    if res.error_msg != '':
                        return res
                    reading.secondary_adc_raw = res.data
                    if self.hardware.shutdown_requested:
                        return disable_laser(force=True)

                req = SpectrometerRequest("get_secondary_adc_calibrated", args =[reading.secondary_adc_raw])
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                    return res
                reading.secondary_adc_calibrated = res.data 
                req = SpectrometerRequest("select_adc", args=[0])
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                    return res
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
                req = SpectrometerRequest("get_detector_temperature_raw")
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                    return res
                reading.detector_temperature_raw  = res.data
                if self.hardware.shutdown_requested:
                    log.debug("detector_temperature_raw shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

                req = SpectrometerRequest("get_detector_temperature_raw", args=[reading.detector_temperature_raw])
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                    return res
                reading.detector_temperature_degC = res.data
                if self.hardware.shutdown_requested:
                    log.debug("detector_temperature_degC shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

            except Exception as exc:
                log.debug("Error reading detector temperature", exc_info=1)

        # read ambient temperature if applicable
        if self.settings.is_gen15():
            try:
                # reading.ambient_temperature_degC = self.hardware.get_ambient_temperature_degC()
                pass
            except Exception as exc:
                log.debug("Error reading ambient temperature", exc_info=1)

        # read battery every 10sec
        if self.settings.eeprom.has_battery:
            if self.settings.state.battery_timestamp is None or (datetime.datetime.now() >= self.settings.state.battery_timestamp + datetime.timedelta(seconds=10)):
                req = SpectrometerRequest("get_battery_state_raw")
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                   return res
                reading.battery_raw = res.data
                if self.hardware.shutdown_requested:
                    log.debug("battery_raw shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

                req = SpectrometerRequest("get_battery_percentage")
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                   return res
                reading.battery_percentage = res.data
                if self.hardware.shutdown_requested:
                    log.debug("battery_perc shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response
                self.last_battery_percentage = reading.battery_percentage

                req = SpectrometerRequest("get_battery_percentage")
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                    return res
                reading.battery_charging = res.data
                if self.hardware.shutdown_requested:
                    log.debug("battery_charging shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

                log.debug("battery: level %.2f%% (%s)", reading.battery_percentage, "charging" if reading.battery_charging else "not charging")
            else:
                if reading is not None:
                    reading.battery_percentage = self.last_battery_percentage

        # log.debug("device.acquire_spectrum: returning %s", reading)
        acquire_response.data = reading
        return acquire_response

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
                req = SpectrometerRequest("get_line")
                res = self.hardware.handle_requests([req])
                if res.error_msg != '':
                    return res
                spectrum_and_row = res.data
    ##
    # @returns Reading on success, true or false on "stop processing" conditions
    def take_one_averaged_reading(self):
        take_one_response = SpectrometerResponse()

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
            reading.laser_power_perc    = self.settings.state.laser_power_perc
            reading.laser_power_mW      = self.settings.state.laser_power_mW
            reading.laser_enabled       = self.settings.state.laser_enabled

            # Are we reading one spectrum (normal mode, or "slow" area scan), or
            # doing a batch-read of a whole frame ("fast" area scan)?
            #
            # It's a bit confusing that this is INSIDE the scan averaging loop...
            # there is NO use-case for "averaged" area scan.  We should move this
            # up and out of take_one_averaged_reading().
            if self.settings.state.area_scan_enabled and self.settings.state.area_scan_fast:

                # collect a whole frame of area scan data
                with self.lock:
                    reading.area_scan_data = []
                    try:
                        rows = self.settings.eeprom.active_pixels_vertical
                        first = True
                        log.debug("trying to read a fast area scan frame of %d rows", rows)
                        #for i in range(rows):
                        row_data = {}
                        while True:
                            log.debug(f"trying to read fast area scan row")
                            req = SpectrometerRequest("get_line",kwargs={"trigger":first})
                            res = self.hardware.handle_requests([req])
                            if res.error_msg != '':
                                return res
                            response = res.data
                            spectrum_and_row = response.data
                            first = False
                            if response.poison_pill:
                                # get_line returned a poison-pill, so we're not 
                                # getting any more in this frame...give up and move on
                                # return False
                                take_one_response.transfer_response(response)
                                log.debug(f"get_line returned {spectrum_and_row}, breaking")
                                break
                            elif response.keep_alive:
                                take_one_response.transfer_response(response)
                                log.debug(f"get_line returned keep alive, passing up")
                                return take_one_response
                            elif self.hardware.shutdown_requested:
                                take_one_response.transfer_response(response)
                                return take_one_response
                            elif spectrum_and_row.spectrum is None:
                                log.debug("device.take_one_averaged_spectrum: get_line None, sending keepalive for now (area scan fast)")
                                take_one_response.transfer_response(response)
                                return take_one_response

                            # mimic "slow" results to minimize downstream fuss
                            spectrum = spectrum_and_row.spectrum
                            row = spectrum_and_row.row

                            reading.spectrum = spectrum
                            row_data[row] = spectrum
                            reading.timestamp_complete  = datetime.datetime.now()
                            log.debug("device.take_one_averaged_reading(area scan fast): got %s ... (row %d) (min %d)",
                                spectrum[0:9], row, min(reading.spectrum))

                        reading.area_scan_data = []
                        reading.area_scan_row_count = -1
                        for row in sorted(row_data.keys()):
                            reading.area_scan_data.append(row_data[row])
                            reading.area_scan_row_count = row

                    except Exception as exc:
                        log.critical("Error reading hardware data", exc_info=1)
                        reading.spectrum = None
                        reading.failure = str(exc)
                        take_one_response.error_msg = exc
                        take_one_response.error_lvl = ErrorLevel.medium
                        take_one_response.keep_alive = True
                        return take_one_response

            else:

                # collect ONE spectrum (it's in a while loop because we may have
                # to wait for an external trigger, and must wait through a series
                # of timeouts)
                externally_triggered = self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_EXTERNAL
                try:
                    while True:
                        req = SpectrometerRequest("get_line")
                        res = self.hardware.handle_requests([req])
                        if res.error_msg != '':
                            return res
                        spectrum_and_row = res.data
                        if response.poison_pill:
                            # float up poison
                            take_one_response.transfer_response(response)
                            return take_one_response
                        if response.keep_alive:
                            # float up keep alive
                            take_one_response.transfer_response(response)
                            return take_one_response
                        if isinstance(spectrum_and_row, bool):
                            # get_line returned a poison-pill, so flow it upstream
                            take_one_response.poison_pill = True
                            return take_one_response

                        if self.hardware.shutdown_requested:
                            take_one_response.poison_pill = True
                            return take_one_response

                        if spectrum_and_row is None or spectrum_and_row.spectrum is None:
                            # FeatureIdentificationDevice can return None when waiting
                            # on an external trigger.  
                            log.debug("device.take_one_averaged_spectrum: get_line None, sending keepalive for now")
                            take_one_response.transfer_response(response)
                            return take_one_response
                        else:
                            break

                    reading.spectrum            = spectrum_and_row.spectrum
                    reading.area_scan_row_count = spectrum_and_row.row
                    reading.timestamp_complete  = datetime.datetime.now()

                    log.debug("device.take_one_averaged_reading: got %s ... (row %d)", reading.spectrum[0:9], reading.area_scan_row_count)
                except Exception as exc:
                    # if we got the timeout after switching from externally triggered back to internal, let it ride
                    take_one_response.error_msg = exc
                    take_one_response.error_lvl = ErrorLevel.medium
                    take_one_response.keep_alive = True
                    if externally_triggered:
                        log.debug("caught exception from get_line while externally triggered...sending keepalive")
                        return take_one_response

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
                    reading.spectrum = [ x / self.sum_count for x in self.summed_spectra ]
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

        log.debug("device.take_one_averaged_reading: returning %s", reading)
        # reading.dump_area_scan()
        take_one_response.data = reading
        return take_one_response

    def monitor_memory(self):
        now = datetime.datetime.now()
        if (now - self.last_memory_check).total_seconds() < 5:
            return

        self.last_memory_check = now
        size_in_bytes = psutil.Process(self.process_id).memory_info().rss
        log.info("monitor_memory: PID %d memory = %d bytes", self.process_id, size_in_bytes)

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
                req = SpectrometerRequest(control_object.setting, args=[control_object.value])
                self.hardware.handle_requests([req])

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
