import time
import numpy
import Queue
import logging
import datetime
import multiprocessing

from ConfigParser import ConfigParser

from . import simulation_protocol
from . import utils

from FeatureIdentificationDevice import FeatureIdentificationDevice
from SpectrometerSettings        import SpectrometerSettings
from BalanceAcquisition          import BalanceAcquisition
from SpectrometerState           import SpectrometerState
from FileSpectrometer            import FileSpectrometer
from ControlObject               import ControlObject
from WasatchBus                  import WasatchBus
from DeviceID                    import DeviceID
from Reading                     import Reading

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
    def __init__(self, device_id, message_queue=None, response_queue=None):

        # if passed a string representation of a DeviceID, deserialize it 
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.device_id      = device_id
        self.message_queue  = message_queue
        self.response_queue = response_queue

        self.connected = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. It's not clear why we're using a multiprocessing.Queue inside
        # WasatchDevice (all the multiprocessing communications are encapsulated
        # within WasatchDeviceWrapper), or in fact why WasatchDevice.command_queue
        # is needed at all -- we already have WasatchDeviceWrapper.command_queue,
        # which is already deduped within continuous_poll(), so why not just have
        # continuous_poll call WasatchDevice.hardware.write_setting(control_obj)
        # directly?  Still, probably(?) not hurting anything.  May be leftover
        # from pre-WasatchDeviceWrapper days, when ENLIGHTEN had a blocking
        # interface to the spectrometer.
        #
        # Update: this IS hurting things, as "immediate" commands are queued and
        # then immediately intended for processing...yet the queue still reports
        # as empty! :-(
        #
        # self.command_queue = multiprocessing.Queue()
        self.command_queue = []

        # Enable for "immediate mode" by clients like WasatchShell (by default,
        # inbound commands are queued and executed at beginning of next acquire_data;
        # this runs them as they arrive).
        self.immediate_mode = False

        # Enable this to skip extra metadata in Readings (detector temperature,
        # laser temperature, photodiode, battery etc)
        self.bare_readings = False

        self.settings = SpectrometerSettings()

        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0

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
                log.info("Connected to FeatureIdentificationDevice")
                self.connected = True
                self.initialize_settings()
                return True
        elif self.device_id.is_file():
            log.debug("trying to connect to FILE device")
            if self.connect_file_spectrometer():
                log.info("connected to FileSpectrometer")
                self.connected = True
                self.initialize_settings()
                return True
        else:
            log.critical("unsupported DeviceID protocol: %s", device_id)

        log.debug("Can't connect to %s", self.device_id)
        return False

    def disconnect(self):
        log.info("WasatchDevice.disconnect: calling hardware disconnect")
        try:
            self.hardware.disconnect()
        except Exception as exc:
            log.critical("Issue disconnecting hardware", exc_info=1)

        time.sleep(0.1)
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
            log.debug("connect_fid: Attempt connection to device_id %s pid %s", self.device_id, pid_hex)
            dev = FeatureIdentificationDevice(device_id=self.device_id, message_queue=self.message_queue)

            try:
                ok = dev.connect()
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

        log.info("Connected to FeatureIdentificationDevice %s", self.device_id)
        return True

    def initialize_settings(self):
        if self.connected == False:
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
    # If a spectrometer has bad_pixels configured in the EEPROM, then average 
    # over them in the driver.
    #
    # Even though we're doing spatial averaging here, we truncate the result to
    # a uint16 rather than return a float.  That's because most pixels normally
    # return uint16, and it looks weird if a few scattered pixels are floats.
    # When we're doing scan-averaging, ALL the pixels are returned as floats,
    # which is okay.
    #
    # @note assumes bad_pixels is previously sorted
    def correct_bad_pixels(self, spectrum):

        if self.settings is None or \
                self.settings.eeprom is None or \
                self.settings.eeprom.bad_pixels is None or \
                len(self.settings.eeprom.bad_pixels) == 0:
            return

        if spectrum is None or len(spectrum) == 0:
            return

        pixels = len(spectrum)
        bad_pixels = self.settings.eeprom.bad_pixels

        # iterate over each bad pixel
        i = 0
        while i < len(bad_pixels):

            bad_pix = bad_pixels[i]

            if bad_pix == 0:
                # handle the left edge
                next_good = bad_pix + 1
                while next_good in bad_pixels and next_good < pixels:
                    next_good += 1
                    i += 1
                if next_good < pixels:
                    for j in range(next_good):
                        spectrum[j] = spectrum[next_good]
            else:

                # find previous good pixel
                prev_good = bad_pix - 1
                while prev_good in bad_pixels and prev_good >= 0:
                    prev_good -= 1

                if prev_good >= 0:
                    # find next good pixel
                    next_good = bad_pix + 1
                    while next_good in bad_pixels and next_good < pixels:
                        next_good += 1
                        i += 1

                    if next_good < pixels:
                        # for now, draw a line between previous and next_good pixels
                        # TODO: consider some kind of curve-fit
                        delta = float(spectrum[next_good] - spectrum[prev_good])
                        rng   = next_good - prev_good
                        step  = delta / rng
                        for j in range(rng - 1):
                            spectrum[prev_good + j + 1] = int(spectrum[prev_good] + round(step * (j + 1), 0))
                    else:
                        # we ran off the high end, so copy-right
                        for j in range(bad_pix, pixels):
                            spectrum[j] = spectrum[prev_good]

            # advance to next bad pixel
            i += 1

    ##
    # Until support for even/odd InGaAs gain and offset have been added to the 
    # firmware, apply the correction in software.
    #
    # @todo delete this function when firmware has been updated!
    def correct_ingaas_gain_and_offset(self, reading):
        if not self.settings.is_InGaAs():
            return

        # if even and odd pixels have the same settings, there's no point in doing anything
        if self.settings.eeprom.detector_gain_odd   == self.settings.eeprom.detector_gain and \
           self.settings.eeprom.detector_offset_odd == self.settings.eeprom.detector_offset:
            return

        log.debug("rescaling InGaAs odd pixels from even gain %.2f, offset %d to odd gain %.2f, offset %d",
            self.settings.eeprom.detector_gain,
            self.settings.eeprom.detector_offset,
            self.settings.eeprom.detector_gain_odd,
            self.settings.eeprom.detector_offset_odd)

        # iterate over the ODD pixels of the spectrum
        spectrum = reading.spectrum
        for i in range(1, len(spectrum), 2):

            # back-out the incorrectly applied "even" gain and offset
            old = float(spectrum[i])
            raw = (old - self.settings.eeprom.detector_offset) / self.settings.eeprom.detector_gain

            # apply the correct "odd" gain and offset
            new = (raw * self.settings.eeprom.detector_gain_odd) + self.settings.eeprom.detector_offset_odd

            # convert back to uint16 so the spectrum is all of one type
            spectrum[i] = int(round(max(0, min(new, 0xffff))))

            if i < 5:
                log.debug("  pixel %4d: old %.2f raw %.2f new %.2f final %5d", i, old, raw, new, spectrum[i])

    ##
    # Process all enqueued settings, then read actual data (spectrum and
    # temperatures) from the device.
    #
    # Somewhat confusingly, this function can return any of the following:
    #
    # @return False     a poison-pill sent upstream (requested device shutdown)
    # @return True      keepalive
    # @return None      keepalive
    # @return Reading   a partial or complete spectrometer Reading (may itself 
    #                   have Reading.failure set other than None)
    #
    # @see Controller.acquire_reading
    def acquire_data(self):

        log.debug("Device acquire_data")

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
    # Encapsulates the act of acquisition from the decision-making of whether 
    # same is needed.  Called directly by acquire_data, above.  
    #
    # @par Scan Averaging
    #
    # If the driver is in free-running mode, AND performing scan averaging,
    # THEN we return piecemeal partial readings while "building up" to the
    # final averaged measurement.  This gives the GUI an opportunity to update
    # the "collected X of Y" readout on-screen, and in earlier versions, 
    # supported a faint background trace of in-process partial readings.
    #
    # However, this doesn't make as much sense if we're not in free-running mode,
    # i.e. the subprocess has been slaved to explicit control by the Controller
    # (likely a feature object like BatchCollection), and is collecting exactly 
    # those measurements we're being commanded, as they're commanded.  
    #
    # Therefore, if the driver isn't in free-running mode, then we only return
    # the final averaged spectrum as an atomic operation.
    #
    # @par Precision
    #
    #     - we always return temperatures for live GUI updates
    #     - we always perform bad pixel correction
    #     - currently returning INTEGRAL averages (including bad_pixel)
    #
    # Complication: historically WasatchDevice returns every spectrum up to
    # WasatchDeviceWrapper, even mid-averaging partial reports.  Originally
    # this allowed ENLIGHTEN to display a light-gray trace line showing the
    # pre-averaged spectra as they were collected, rather than waiting on the
    # long averaged updates.  This can also allow the temperature and
    # metadata readings to continue to update during long averaged collections.
    #
    def acquire_spectrum(self):
        averaging_enabled = (self.settings.state.scans_to_average > 1)

        # for Batch Collection
        #
        # We could move this up into ENLIGHTEN.BatchCollection: have it enable
        # the laser, wait a bit, and then send the "acquire" command.  But since
        # WasatchDeviceWrapper.continuous_poll ticks at its own interval, that
        # would introduce timing interference, and different acquisitions would
        # realistically end up with different warm-up times for lasers (all "at
        # least" the configured time, but some longer than others).  Instead,
        # for now I'm putting this delay here, so it will be exactly the same
        # (given sleep()'s precision) for each acquisition.  For true precision
        # this should all go into the firmware anyway.
        auto_enable_laser = self.settings.state.acquisition_laser_trigger_enable and not self.settings.state.free_running_mode
        log.debug("acquire_spectrum: auto_enable_laser = %s", auto_enable_laser)
        if auto_enable_laser:
            log.debug("acquire_spectum: enabling laser, then sleeping %d ms", self.settings.state.acquisition_laser_trigger_delay_ms)
            self.hardware.set_laser_enable(True)
            if self.hardware.shutdown_requested: 
                return False

            time.sleep(self.settings.state.acquisition_laser_trigger_delay_ms / 1000.0)

        if averaging_enabled and not self.settings.state.free_running_mode:
            # collect the entire averaged spectrum at once (added for BatchCollection with laser delay))
            loop_count = self.settings.state.scans_to_average
            self.sum_count = 0
        else:
            # we're in free-running mode
            loop_count = 1

        for loop_index in range(0, loop_count):

            # start a new reading
            reading = Reading(self.device_id)

            # TODO...just include a copy of SpectrometerState? something to think about.
            # That would actually provide a reason to roll all the temperature etc readouts
            # into the SpectrometerState class...
            reading.integration_time_ms = self.settings.state.integration_time_ms
            reading.laser_enabled       = self.settings.state.laser_enabled
            reading.laser_power         = self.settings.state.laser_power
            reading.laser_power_in_mW   = self.settings.state.laser_power_in_mW

            # collect next spectrum
            externally_triggered = self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_EXTERNAL
            try:
                while True:
                    (reading.spectrum, reading.area_scan_row_count) = self.hardware.get_line()
                    if self.hardware.shutdown_requested: 
                        return False

                    if reading.spectrum is None:
                        # FeatureIdentificationDevice can return None when waiting
                        # on an external trigger.  FileSpectrometer can as well if 
                        # there is no new spectrum to read.
                        log.debug("device.acquire_data: get_line None, sending keepalive for now")
                        return True
                    else:
                        break

                log.debug("device.acquire_data: got %s ...", reading.spectrum[0:9])
            except Exception as exc:
                # if we got the timeout after switching from externally triggered back to internal, let it ride
                if externally_triggered:
                    log.debug("caught exception from get_line while externally triggered...sending keepalive")
                    return True

                log.critical("Error reading hardware data", exc_info=1)
                reading.failure = str(exc)

            if not reading.failure:
                # InGaAs even/odd kludge
                self.correct_ingaas_gain_and_offset(reading)

                # bad pixel correction
                if self.settings.state.bad_pixel_mode == SpectrometerState.BAD_PIXEL_MODE_AVERAGE:
                    self.correct_bad_pixels(reading.spectrum)
                reading.spectrum = list(reading.spectrum)

                log.debug("device.acquire_data: after bad_pixel correction: %s ...", reading.spectrum[0:9])

                # update summed spectrum
                if averaging_enabled:
                    if self.sum_count == 0:
                        self.summed_spectra = list(numpy.array([float(i) for i in reading.spectrum]))
                    else:
                        log.debug("device.acquire_data: summing spectra")
                        self.summed_spectra = numpy.add(self.summed_spectra, reading.spectrum)
                    self.sum_count += 1
                    log.debug("device.acquire_data: summed_spectra : %s ...", self.summed_spectra[0:9])

            # count spectra
            self.session_reading_count += 1
            reading.session_count = self.session_reading_count
            reading.sum_count = self.sum_count

            # have we completed the averaged reading?
            if averaging_enabled:
                if self.sum_count >= self.settings.state.scans_to_average:
                    # if we wanted to send the averaged spectrum as ints, use numpy.ndarray.astype(int)
                    reading.spectrum = numpy.divide(self.summed_spectra, self.sum_count).tolist()
                    log.debug("device.acquire_data: averaged_spectrum : %s ...", reading.spectrum[0:9])
                    reading.averaged = True

                    # reset for next average
                    self.summed_spectra = None
                    self.sum_count = 0

        # end of loop_index

        ########################################################################
        # provide early exit-ramp if we've been asked to return bare Readings 
        # (just averaged spectra with corrected bad pixels, no metadata)
        ########################################################################

        def disable_laser():
            if auto_enable_laser:
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
                        return disable_laser()

                reading.laser_temperature_degC = self.hardware.get_laser_temperature_degC(reading.laser_temperature_raw)
                if self.hardware.shutdown_requested: 
                    return disable_laser()
            except Exception as exc:
                log.debug("Error reading laser temperature", exc_info=1)

        # read secondary ADC if requested
        if self.settings.state.secondary_adc_enabled:
            try:
                self.hardware.select_adc(1)
                if self.hardware.shutdown_requested: 
                    return disable_laser()

                for throwaway in range(2):
                    reading.secondary_adc_raw = self.hardware.get_secondary_adc_raw()
                    if self.hardware.shutdown_requested: 
                        return disable_laser()

                reading.secondary_adc_calibrated = self.hardware.get_secondary_adc_calibrated(reading.secondary_adc_raw)
                self.hardware.select_adc(0)
                if self.hardware.shutdown_requested: 
                    return disable_laser()

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

        # read detector temperature if applicable (should we do this for Ambient as well?)
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
            if reading.detector_temperature_raw is None:
                return reading

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

        return reading

    ##
    # Process every entry on the settings queue, writing each to the device.  As
    # long as these were inserted by WasatchDeviceWrapper.continuous_poll, they
    # should be already de-dupped.
    #
    # I'm not sure where this is going, but I need a way to trigger acquisitions
    # through software.  An initial cautious approach is to make this function
    # return True if a queued command requested an acquisition.
    #
    # Called by acquire_data, ergo subprocess
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
                self.hardware.write_setting(control_object)

            if control_object.setting == "free_running_mode" and not self.hardware.settings.state.free_running_mode:
                log.debug("exited free-running mode, so clearing response queue")
                self.clear_response_queue()

            # except Queue.Empty:
            #     log.debug("process_commands: empty")
            #     break
            # except Exception as exc:
            #     log.critical("process_commands: error dequeuing or writing control object", exc_info=1)
            #     raise
        return retval

    def clear_response_queue(self):
        while not self.response_queue.empty():
            log.debug("clearing response queue: throwing away Reading")
            self.response_queue.get()

    # ######################################################################## #
    #                                                                          #
    #                             BalanceAcquisition                           #
    #                                                                          #
    # ######################################################################## #

    def balance_acquisition(self, mode=None, intensity=45000, threshold=2500, pixel=None):
        balancer = BalanceAcquisition(mode, intensity, threshold, pixel, self)
        balancer.balance()

    # ######################################################################## #
    #                                                                          #
    #                             Hardware Control                             #
    #                                                                          #
    # ######################################################################## #

    ##
    # Add the specified setting and value to the local control queue.
    #
    # Called by subprocess.continuous_poll
    def change_setting(self, setting, value, allow_immediate=True):
        control_object = ControlObject(setting, value)
        log.debug("WasatchDevice.change_setting: %s", control_object)

        if control_object.setting == "scans_to_average":
            self.sum_count = 0

        # try:
        # self.command_queue.put(control_object)
        self.command_queue.append(control_object)
        log.debug("change_setting: queued %s", control_object)
        # except Exception as exc:
        #     log.critical("WasatchDevice.change_setting: failed to enqueue %s",
        #         control_object, exc_info=1)

        # always process trigger_source commands promptly (can't wait for end of
        # acquisition which may never come)
        if (allow_immediate and self.immediate_mode) or (setting in ["trigger_source", "laser_enable"]):
            log.debug("immediately processing %s", control_object)
            self.process_commands()
