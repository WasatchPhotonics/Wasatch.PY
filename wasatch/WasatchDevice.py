import time
import numpy
import Queue
import logging
import multiprocessing

from ConfigParser import ConfigParser

from . import simulation_protocol
from . import utils

from FeatureIdentificationDevice import FeatureIdentificationDevice
from StrokerProtocolDevice       import StrokerProtocolDevice
from SpectrometerSettings        import SpectrometerSettings
from BalanceAcquisition          import BalanceAcquisition
from SpectrometerState           import SpectrometerState
from FileSpectrometer            import FileSpectrometer
from ControlObject               import ControlObject
from WasatchBus                  import WasatchBus
from Reading                     import Reading

log = logging.getLogger(__name__)

## 
# A WasatchDevice encapsulates and wraps a Wasatch spectrometer in a blocking
# interface.  It will normally wrap one of the following:
#
# - a FeatureIdentificationDevice (modern FID spectrometer)
# - a StrokerProtocolDevice (older SP-protocol spectrometer)
# - a FileSpectrometer (filesystem gateway to virtual/simulated spectrometer)
#
# ENLIGHTEN does not instantiate WasatchDevices directly, but instead uses
# a WasatchDeviceWrapper to access a single WasatchDevice in a single subprocess.
# Other users of Wasatch.PY may of course instantiate a WasatchDevice directly,
# and can consider it roughly equivalent (though differently structured) to a
# WasatchNET.Spectrometer.  (Arguably wasatch.FeatureIdentificationDevice is the
# closer analog to WasatchNET.Spectrometer, as Wasatch.NET does not have anything
# like StrokerProtocol or FileSpectrometer.)
class WasatchDevice(object):

    ## 
    # @param uid            (VID, PID) or FileSpectrometer directory
    # @param bus_order      integral USB bus sequence
    # @param message_queue  if provided, used to send status back to caller
    def __init__(self, uid, bus_order=0, message_queue=None):

        self.uid           = uid            
        self.bus_order     = bus_order      
        self.message_queue = message_queue 

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
        self.command_queue = multiprocessing.Queue() 

        self.settings = SpectrometerSettings()

        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0

    # ######################################################################## #
    #                                                                          #
    #                               Connection                                 #
    #                                                                          #
    # ######################################################################## #

    ## Attempt low level connection to the device specified in init.  
    def connect(self):

        if ("/" in self.uid or "\\" in self.uid) and self.connect_file_spectrometer():
            log.info("connected to FileSpectrometer")
            self.connected = True
            self.initialize_settings()
            return True

        if self.connect_feature_identification():
            log.info("Connected to FeatureIdentificationDevice")
            self.connected = True
            self.initialize_settings()
            return True

        if self.connect_stroker_protocol():
            log.info("Connected to StrokerProtocolDevice")
            self.connected = True
            self.initialize_settings()
            return True

        log.debug("Can't find FID or SP class device")

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
        dev = FileSpectrometer(self.uid)
        if dev.connect():
            self.hardware = dev
            return True

    ## Given a specified universal identifier, attempt to connect to the device using stroker protocol.
    def connect_stroker_protocol(self):
        FID_list = ["0x1000", "0x2000", "0x3000", "0x4000"]

        if self.uid == None:
            log.debug("No specified UID for stroker protocol connect")
            return False

        if any(fid in self.uid for fid in FID_list):
            log.debug("Compatible feature ID not found")
            return False

        dev = None
        try:
            bus_pid = self.uid[7:] # assumes UID="0xXXXX:0xYYYY" and we want "0xYYYY"
            log.info("Attempt connection to: %s", bus_pid)

            dev = StrokerProtocolDevice(pid=bus_pid)
            result = dev.connect()
            if result != True:
                log.critical("Low level failure in device connect")
                return False
            self.hardware = dev

        except Exception as exc:
            log.critical("Problem connecting to: %s", self.uid, exc_info=1)
            return False

        log.info("Connected to StrokerProtocolDevice %s", self.uid)
        return True

    ## Given a specified universal identifier, attempt to connect to the device using FID protocol. 
    def connect_feature_identification(self):
        FID_list = ["0x1000", "0x2000", "0x3000", "0x4000"]

        if self.uid == None:
            log.debug("No specified UID for feature id connect")
            return False

        if not any(fid in self.uid for fid in FID_list):
            log.debug("Compatible feature ID not found")
            return False

        dev = None
        try:
            bus_pid = self.uid[7:]
            log.debug("connect_fid: Attempt connection to bus_pid %s (bus_order %d)", bus_pid, self.bus_order)

            dev = FeatureIdentificationDevice(pid=bus_pid, bus_order=self.bus_order, message_queue=self.message_queue)

            try:
                ok = dev.connect()
            except Exception as exc:
                log.critical("connect_feature_identification: %s", exc)
                return False

            if not ok:
                log.critical("Low level failure in device connect")
                return False

            self.hardware = dev

        except Exception as exc:
            log.critical("Problem connecting to: %s", self.uid, exc_info=1)
            return False

        log.info("Connected to FeatureIdentificationDevice %s", self.uid)
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

    # ######################################################################## #
    #                                                                          #
    #                               Acquisition                                #
    #                                                                          #
    # ######################################################################## #

    ## Assumes bad_pixels is a sorted array (possibly empty)
    def correct_bad_pixels(self, spectrum):

        if not self.settings or not self.settings.eeprom or not self.settings.eeprom.bad_pixels:
            return

        if not spectrum:
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
                        # for now, draw a line between previous and next good pixels
                        # TODO: consider some kind of curve-fit
                        delta = float(spectrum[next_good] - spectrum[prev_good])
                        rng   = next_good - prev_good
                        step  = delta / rng
                        for j in range(rng - 1):
                            # MZ: examining performance
                            # spectrum[prev_good + j + 1] = spectrum[prev_good] + step * (j + 1)
                            spectrum[prev_good + j + 1] = spectrum[prev_good] + int(step * (j + 1))
                    else:
                        # we ran off the high end, so copy-right
                        for j in range(bad_pix, pixels):
                            spectrum[j] = spectrum[prev_good]

            # advance to next bad pixel
            i += 1

    def correct_ingaas_gain_and_offset(self, reading):
        #if not self.settings.is_InGaAs():
        #    return

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
            # back-out even gain and offset
            old = float(spectrum[i])
            raw = (old - self.settings.eeprom.detector_offset) / self.settings.eeprom.detector_gain
            new = (raw * self.settings.eeprom.detector_gain_odd) + self.settings.eeprom.detector_offset_odd
            spectrum[i] = round(new)

            # log.debug("pixel %4d: old %.2f raw %.2f new %.2f", i, old, raw, new)

    ##
    # Process all enqueued settings, then read actual data (spectrum and 
    # temperatures) from the device.
    #
    # Notes:
    #     - we always return raw readings, even during scan averaging (.spectrum)
    #     - if averaging was enabled and we're complete, then we return the
    #       averaged "complete" INSTEAD OF the raw
    #     - we always return temperatures for live GUI updates
    #     - we always perform bad pixel correction
    #     - currently returning INTEGRAL averages (including bad_pixel)
    def acquire_data(self):

        log.debug("Device acquire_data")

        # yes, allow settings to change in the midst of a long average; this way
        # they can turn off laser, disable averaging etc
        self.process_commands() 

        # if we don't yet have an integration time, nothing to do
        if self.settings.state.integration_time_ms <= 0:
            log.debug("skipping acquire_data because no integration_time_ms")
            return None

        averaging_enabled = (self.settings.state.scans_to_average > 1)

        # start a new reading
        reading = Reading()

        # TODO...just include a copy of SpectrometerState? something to think about
        # That would actually provide a reason to roll all the temperature etc readouts
        # into the SpectrometerState class...
        reading.integration_time_ms = self.settings.state.integration_time_ms
        reading.laser_enabled       = self.settings.state.laser_enabled
        reading.laser_power         = self.settings.state.laser_power
        reading.laser_power_in_mW   = self.settings.state.laser_power_in_mW

        # collect next spectrum
        try:
            while True:
                (reading.spectrum, reading.area_scan_row_count)  = self.hardware.get_line()
                if reading.spectrum is None:
                    # hardware devices (FID, SP) should never do this: for better or worse,
                    # they're blocked on a USB call.  FileSpectrometer can, though, if there
                    # is no new spectrum to read.  And sometimes 2048-pixel SP spectrometers
                    # will be unable to stitch together a complete spectrum
                    #
                    log.debug("device.acquire_data: get_line None, retrying")
                    pass
                else:
                    break

            log.debug("device.acquire_data: got %s ...", reading.spectrum[0:9])
        except Exception as exc:
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

        # read detector temperature if applicable (should we do this for Ambient as well?)
        if True or self.settings.eeprom.has_cooling:
            try:
                reading.detector_temperature_raw  = self.hardware.get_detector_temperature_raw()
                reading.detector_temperature_degC = self.hardware.get_detector_temperature_degC(reading.detector_temperature_raw)
            except Exception as exc:
                log.debug("Error reading detector temperature", exc_info=1)

        # only read laser temperature if we have a laser (how do we determine this for StrokerProtocol?)
        if self.settings.eeprom.has_laser:
            try:
                count = 2 if self.settings.state.secondary_adc_enabled else 1
                for throwaway in range(count):
                    reading.laser_temperature_raw  = self.hardware.get_laser_temperature_raw()
                reading.laser_temperature_degC = self.hardware.get_laser_temperature_degC(reading.laser_temperature_raw)
            except Exception as exc:
                log.debug("Error reading laser temperature", exc_info=1)

        # read secondary ADC if requested
        if self.settings.state.secondary_adc_enabled:
            try:
                self.hardware.select_adc(1)
                for throwaway in range(2):
                    reading.secondary_adc_raw = self.hardware.get_secondary_adc_raw()
                reading.secondary_adc_calibrated = self.hardware.get_secondary_adc_calibrated(reading.secondary_adc_raw)
                self.hardware.select_adc(0)
            except Exception as exc:
                log.debug("Error reading secondary ADC", exc_info=1)

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

        return reading

    ##
    # Process every entry on the settings queue, writing each to the 
    # device.
    #
    # Called by acquire_data, ergo subprocess 
    def process_commands(self):
        control_object = "throwaway"
        while control_object != None:
            try:
                control_object = self.command_queue.get_nowait()
                log.debug("process_commands: %s -> %s", control_object.setting, control_object.value)
                self.hardware.write_setting(control_object)
            except Queue.Empty:
                log.debug("process_commands: queue empty")
                control_object = None
            except Exception as exc:
                log.critical("process_commands: error dequeuing or writing control object", exc_info=1)
                raise

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
    def change_setting(self, setting, value):
        log.debug("WasatchDevice.change_setting: %s -> %s", setting, value)
        control_object = ControlObject(setting, value)

        if control_object.setting == "scans_to_average":
            self.sum_count = 0

        try:
            self.command_queue.put(control_object)
        except Exception as exc:
            log.critical("WasatchDevice.change_setting: can't enqueue %s -> %s",
                setting, value, exc_info=1)
