import re
import os
import time
import numpy as np
import psutil
import logging
import datetime
import threading
from queue import Queue
from typing import Any

from .FeatureIdentificationDevice import FeatureIdentificationDevice
from .SpectrometerSettings        import SpectrometerSettings
from .SpectrometerResponse        import SpectrometerResponse
from .SpectrometerRequest         import SpectrometerRequest
from .SpectrometerResponse        import ErrorLevel
from .InterfaceDevice             import InterfaceDevice
from .BalanceAcquisition          import BalanceAcquisition
from .SpectrometerState           import SpectrometerState
from .ControlObject               import ControlObject
from .AutoRaman                   import AutoRaman
from .DeviceID                    import DeviceID
from .Reading                     import Reading

log = logging.getLogger(__name__)

class WasatchDevice(InterfaceDevice):
    """
    This is the top-level interface for controlling and communicating with
    Wasatch Photonics USB 2.0 spectrometers using the FeatureInterfaceDevice 
    (FID) protocol as defined in ENG-0001.

    This class is essentially a clunky wrapper over FeatureInterfaceDevice, 
    providing the higher-level InterfaceDevice interface making WasatchDevice a 
    peer to AndorDevice, OceanDevice, TCPDevice, IDSDevice etc.

    Several points will stand out:

    1. WasatchDevice should probably be renamed to FIDDevice, because Wasatch 
       Photonics is also the manufacturer for spectrometers using AndorDevice,
       TCPDevice, IDSDevice etc.

    2. This class could be simplified considerably, and possibly merged with
       FeatureIdentificationDevice.

    ENLIGHTEN does not instantiate WasatchDevices directly, but instead uses
    a WasatchDeviceWrapper to access a single WasatchDevice in a dedicated child
    thread.  Other users of Wasatch.PY may of course instantiate a WasatchDevice 
    directly, or go straight to a FeatureInterfaceDevice.

    Main things this "wrapper" provides:

    - Implements software-based, library-based scan averaging (badly). Although
      ARM-based spectrometers can do this in firmware, FX2-based models have to
      do it in software. Between Wasatch.PY and ENLIGHTEN, it seems preferable
      to have this in the Wasatch.PY library, where it is easier for customers
      to use.

    - Distinguishes between normal, averaged, AutoRaman and AreaScan  
      acquisitions.

    @par History

    This class exists because it used to wrap TWO types of spectrometers:

    - FeatureIdentificationDevices, which follow the ENG-0001 API and
      have EEPROMs
    - StrokerDevices, which didn't have EEPROMs and didn't obey ENG-0001.

    I don't know if we have an ENG document specifying the protocol that the old
    Stroker electronics used. It was already deprecated when I started, and was 
    rapidly removed from Wasatch.PY. Which kind of made this class superfluous.

    This class (and all who inherit InterfaceDevice) use a somewhat clunky
    data-passing and error-handling mechanism via SpectrometerRequest and
    SpectrometerResponse objects. That could probably be streamlined.

    Part of the legacy "ugly-isms" in this class date back to when ENLIGHTEN
    was multi-process rather than multi-threaded, and all data flows between
    Wasatch.PY and ENLIGHTEN were via pickled queues :-(
    """

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        """
        @param device_id      a DeviceID instance OR string label thereof
        @param message_queue  if provided, used to send status back to caller
        @param alert_queue    if provided, used to receive hints and realtime interrupts from caller
        """
        # if passed a string representation of a DeviceID, deserialize it
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.device_id      = device_id
        self.message_queue  = message_queue # outgoing notifications to ENLIGHTEN
        self.alert_queue    = alert_queue   # incoming alerts from ENLIGHTEN 

        self.lock = threading.Lock()

        self.connected = False
        self.hardware = None                # FeatureIdentificationDevice

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
        self.take_one_request       = None
        self.last_complete_acquisition = None

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0

        self.process_f = self._init_process_funcs()

        self.auto_raman = AutoRaman(self)

    # ######################################################################## #
    #                                                                          #
    #                               Connection                                 #
    #                                                                          #
    # ######################################################################## #

    def connect(self):
        """ Attempt low level connection to the specified DeviceID """
        if self.device_id.is_usb() or self.device_id.is_mock():
            log.debug(f"trying to connect to {'USB' if self.device_id.is_usb() else 'Mock'}")
            result = self.connect_feature_identification()
            if result.data:
                log.debug("Connected to FeatureIdentificationDevice")
                self.connected = True
                self.initialize_settings()
                return SpectrometerResponse(True)
            else:
                log.debug("Failed to connect to FeatureIdentificationDevice")
                return result
        else:
            log.critical("unsupported DeviceID protocol: %s", self.device_id)
        log.debug("Can't connect to %s", self.device_id)
        return SpectrometerResponse(False)

    def disconnect(self):
        log.debug("WasatchDevice.disconnect: calling hardware disconnect")
        try:
            req = SpectrometerRequest("disconnect")
            self.hardware.handle_requests([req])
        except:
            log.critical("Issue disconnecting hardware", exc_info=1)

        time.sleep(0.1)

        self.connected = False
        return True

    ## Given a specified universal identifier, attempt to connect to the device using FID protocol.
    # @todo merge with the hardcoded list in DeviceFinderUSB
    def connect_feature_identification(self):
        # check to see if valid FID PID
        pid_hex = self.device_id.get_pid_hex()
        if pid_hex not in ["1000", "2000", "4000"]:
            log.debug(f"connect_feature_identification: device_id {self.device_id} has invalid PID {pid_hex}")
            return SpectrometerResponse(False)

        dev = None
        try:
            log.debug(f"connect_fid: instantiating FID with device_id {self.device_id}, pid {pid_hex}")
            dev = FeatureIdentificationDevice(device_id=self.device_id, message_queue=self.message_queue, alert_queue=self.alert_queue)
            log.debug("connect_fid: instantiated")

            log.debug("connect_fid: calling dev.connect")
            response = dev.connect()
            log.debug("connect_fid: back from dev.connect")

            if not response.data:
                log.critical("Low level failure in device connect")
                return response

            self.hardware = dev
        except:
            log.critical(f"Problem connecting to {self.device_id}", exc_info=1)
            return SpectrometerResponse(False)

        log.debug(f"Connected to FeatureIdentificationDevice {self.device_id}")
        return SpectrometerResponse(True)

    def initialize_settings(self):
        if not self.connected:
            return

        # WasatchDevice and FID share same SpectrometerSettings
        self.settings = self.hardware.settings

        req_int = SpectrometerRequest('get_integration_time_ms')
        req_gain = SpectrometerRequest('get_detector_gain')
        reqs = [req_int, req_gain]
        self.hardware.handle_requests(reqs) 

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

    def acquire_data(self):
        """
        Process all enqueued settings, then read actual data (spectrum and
        temperatures) from the device.

        ENLIGHTEN calls this function via WrapperWorker.run().

        @see Controller.acquire_reading
        """
        log.debug("acquire_data: start")

        if self.hardware.shutdown_requested:
            log.critical("acquire_data: hardware shutdown requested")
            return SpectrometerResponse(False, poison_pill=True)

        # process queued commands, and find out if we've been asked to read a
        # spectrum
        self.process_commands()

        # if we don't yet have an integration time, nothing to do
        if self.settings.state.integration_time_ms <= 0:
            log.debug("skipping acquire_data because no integration_time_ms")
            return SpectrometerResponse(None)

        # if not self.hardware.is_sensor_stable():
        #     # technically, we could do all the other stuff in acquire_spectrum 
        #     # (read battery, laser temperature, ambient temperature, detector 
        #     # temperature etc), not bothering for now
        #     log.debug("declining to read spectra while stabilizing") 
        #     return SpectrometerResponse(None)

        # note that right now, all we return are Readings (encapsulating both
        # spectra and temperatures).  If we disable spectra, ENLIGHTEN stops 
        # receiving temperatures as well.  In the future perhaps we should return
        # multiple object types (Acquisitions, Temperatures, etc)
        if self.settings.state.area_scan_enabled:
            return self.acquire_area_scan()
        else:
            return self.acquire_spectrum()

    ##
    # Generate one Reading from the spectrometer, including one
    # optionally-averaged spectrum, device temperatures and other hardware
    # status.
    #
    # This is normally called by self.acquire_data when that function decides it
    # is time to perform an acquisition.
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
    # @return a Reading wrapped in a SpectrometerResponse
    #
    def acquire_spectrum(self):
        """
        @returns a SpectrometerResponse(data=Reading)
        """
        if self.take_one_request and self.take_one_request.auto_raman_request:
            return self.acquire_spectrum_auto_raman()
        else:
            return self.acquire_spectrum_standard()

    def acquire_spectrum_auto_raman(self):
        """
        @returns a SpectrometerResponse(data=Reading)
        @todo fold-in a lot of the post-reading sensor measurements 
              (temperature, interlock etc) provided by acquire_spectrum_standard
        """
        log.debug("WasatchDevice.acquire_spectrum_auto_raman: calling AutoRaman.measure")
        spectrometer_response = self.auto_raman.measure(self.take_one_request.auto_raman_request)
        reading = spectrometer_response.data
        log.debug(f"WasatchDevice.acquire_spectrum_auto_raman: received {reading}")

        # return the completed TakeOneRequest and clear our internal handle
        #
        # Note that Auto-Raman doesn't currently support "fast BatchCollection" 
        # with TakeOneRequest.readings_target. I think that's okay, because that's
        # not really what Auto-Raman is for.
        reading.take_one_request = self.take_one_request
        self.take_one_request = None

        return spectrometer_response

    def acquire_spectrum_standard(self):
        """
        @returns a SpectrometerResponse(data=Reading)
        """
        acquire_response = SpectrometerResponse()

        tor = self.take_one_request

        self.perform_optional_throwaways()

        ########################################################################
        # Batch Collection silliness
        ########################################################################

        # Note that BatchCollection's "auto-enable laser" is not nearly as
        # involved as the Auto-Raman performed by acquire_spectrum_auto_raman.
        # BatchCollection uses TakeOneRequest.enable_laser_before, 
        # .disable_laser_after, .take_dark, .laser_warmup_ms and .scans_to_average
        # to take an averaged, dark-corrected Raman measurement WITH FIXED 
        # ACQUISITION PARAMETERS (integration time, gain). It does not perform 
        # any optimization of integration time or gain. 
        #
        # By implication then, we should simplify this by changing 
        # BatchCollection to use AutoRamanRequest, adding a checkbox to 
        # BatchCollection to allow # optimization; if unchecked, min/max integ 
        # and gain will equal start integ/gain, and no optimization will occur. 
        # Then we can remove all the auto laser stuff from the following code.
        #
        # This has been captured in https://github.com/WasatchPhotonics/ENLIGHTEN/issues/474

        auto_enable_laser = tor is not None and tor.enable_laser_before 
        # log.debug("acquire_spectrum: auto_enable_laser = %s", auto_enable_laser)

        dark_reading = SpectrometerResponse()
        if auto_enable_laser and tor.take_dark:
            log.debug(f"AUTO-RAMAN ==> taking internal dark")

            # disable laser if it was on
            if self.settings.state.laser_enabled:
                log.debug("AUTO-RAMAN ==> disabling laser for internal dark")
                self.hardware.handle_requests([SpectrometerRequest('set_laser_enable', args=[False])])
                time.sleep(1) 

            dark_reading = self.take_one_averaged_reading(label="internal dark")
            if dark_reading.poison_pill or dark_reading.error_msg:
                log.debug(f"internal dark error {dark_reading}")
                acquire_response.poison_pill = True
                return acquire_response

        if auto_enable_laser:
            log.debug(f"AUTO-RAMAN ==> acquire_spectum: enabling laser")
            req = SpectrometerRequest('set_laser_enable', args=[True])
            self.hardware.handle_requests([req])
            if self.hardware.shutdown_requested:
                log.debug(f"auto_enable_laser shutdown requested")
                acquire_response.poison_pill = True
                return acquire_response

            if tor:
                log.debug(f"AUTO-RAMAN ==> acquire_spectum: sleeping {tor.laser_warmup_ms}ms for laser to warmup")
                time.sleep(tor.laser_warmup_ms / 1000.0)

            self.perform_optional_throwaways()

        ########################################################################
        # Take a Reading (possibly averaged)
        ########################################################################

        log.debug("taking averaged reading")
        take_one_response = self.take_one_averaged_reading(label="sample (possibly Raman)")
        reading = take_one_response.data
        if take_one_response.poison_pill:
            log.debug(f"floating up take_one_averaged_reading poison pill {take_one_response}")
            return take_one_response
        if take_one_response.keep_alive:
            log.debug(f"floating up keep alive")
            return take_one_response
        if take_one_response.data is None:
            log.debug(f"Received a none reading, floating it up {take_one_response}")
            return take_one_response

        # don't perform dark subtraction, but pass the dark measurement along
        # with the averaged reading
        if dark_reading.data is not None:
            reading.dark = dark_reading.data.spectrum
            log.debug(f"attaching dark to reading (mean {np.mean(reading.dark)}, min {min(reading.dark)}, max {max(reading.dark)})")
            log.debug(f"reading spectrum          (mean {np.mean(reading.spectrum)}, min {min(reading.spectrum)}, max {max(reading.spectrum)})")

        ########################################################################
        # provide early exit-ramp if we've been asked to return bare Readings
        # (just averaged spectra with corrected bad pixels, no metadata)
        ########################################################################

        def disable_laser(shutdown=False, label=None):
            if shutdown or auto_enable_laser:
                log.debug(f"acquire_spectrum.disable_laser: shutdown {shutdown}, auto_enable_laser {auto_enable_laser}, label {label}")
                req = SpectrometerRequest('set_laser_enable', args=[False])
                self.hardware.handle_requests([req])
                acquire_response.poison_pill = shutdown
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
                # MZ: we might want to do these for auto_enable_laser as well...
                func_attr = [ ('get_laser_enabled', 'laser_enabled'),
                              ('can_laser_fire',    'laser_can_fire'),
                              ('is_laser_firing',   'laser_is_firing') ]
                if self.settings.is_xs() and self.settings.eeprom.sig_laser_tec:
                    func_attr.append( ('get_laser_tec_mode', 'laser_tec_enabled') )
                    func_attr.append( ('get_ambient_temperature_degC', 'ambient_temperature_degC') )

                for (func, attr) in func_attr:
                    req = SpectrometerRequest(func)
                    res = self.hardware.handle_requests([req])[0]
                    if res is None:
                        log.debug(f"WasatchDevice.acquire_spectrum: ignoring None {func} response")
                    else:
                        if res.error_msg != '':
                            return res
                        value = res.data
                        # log.debug(f"WasatchDevice.acquire_spectrum: storing {attr} = {value}")
                        setattr(reading, attr, value)

                if self.hardware.shutdown_requested:
                    return disable_laser(shutdown=True, label=f"loading laser attributes")

                # laser temperature
                count = 2 if self.settings.state.secondary_adc_enabled else 1
                for throwaway in range(count):
                    req = SpectrometerRequest('get_laser_temperature_raw')
                    res = self.hardware.handle_requests([req])[0]
                    if res.error_msg != '':
                        return res
                    reading.laser_temperature_raw  = res.data
                    if self.hardware.shutdown_requested:
                        return disable_laser(shuttdown=True, label=f"reading laser temperature (throwaway {throwaway} of {count})")

                req = SpectrometerRequest('get_laser_temperature_degC', args=[reading.laser_temperature_raw])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.laser_temperature_degC = res.data
                if self.hardware.shutdown_requested:
                    return disable_laser(shutdown=True, label=f"reading laser temperature")

            except:
                log.debug("Error reading laser temperature", exc_info=1)

        # read secondary ADC if requested
        if self.settings.state.secondary_adc_enabled:
            try:
                req = SpectrometerRequest("select_adc", args=[1])
                self.hardware.handle_requests([req])
                if self.hardware.shutdown_requested:
                    return disable_laser(shutdown=True, label="select_adc[1]")

                for throwaway in range(2):
                    req = SpectrometerRequest("get_secondary_adc_raw")
                    res = self.hardware.handle_requests([req])[0]
                    if res.error_msg != '':
                        return res
                    reading.secondary_adc_raw = res.data
                    if self.hardware.shutdown_requested:
                        return disable_laser(shutdown=True, label="get_secondary_adc_raw")

                req = SpectrometerRequest("get_secondary_adc_calibrated", args =[reading.secondary_adc_raw])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.secondary_adc_calibrated = res.data 
                req = SpectrometerRequest("select_adc", args=[0])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                if self.hardware.shutdown_requested:
                    return disable_laser(shutdown=True, label="select_adc[0]")

            except:
                log.debug("Error reading secondary ADC", exc_info=1)

        ########################################################################
        # we've read the laser temperature and photodiode, so we can now safely
        # disable the laser (if we're the one who enabled it)
        ########################################################################

        disable_laser(label="clean exit")

        ########################################################################
        # finish collecting any metadata that doesn't require the laser
        ########################################################################

        # read detector temperature if applicable
        if self.settings.eeprom.has_cooling:
            try:
                req = SpectrometerRequest("get_detector_temperature_raw")
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.detector_temperature_raw  = res.data
                if self.hardware.shutdown_requested:
                    log.debug("detector_temperature_raw shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

                req = SpectrometerRequest("get_detector_temperature_degC", args=[reading.detector_temperature_raw])
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.detector_temperature_degC = res.data
                if self.hardware.shutdown_requested:
                    log.debug("detector_temperature_degC shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

            except:
                log.debug("Error reading detector temperature", exc_info=1)

        # read ambient temperature if applicable
        if self.settings.is_gen15():
            try:
                # reading.ambient_temperature_degC = self.hardware.get_ambient_temperature_degC()
                pass
            except:
                log.debug("Error reading ambient temperature", exc_info=1)

        # read battery every 5sec
        if self.settings.eeprom.has_battery:
            if self.settings.state.battery_timestamp is None or (datetime.datetime.now() - self.settings.state.battery_timestamp).total_seconds() > 5:

                # note that the following 3 requests should actually only generate 
                # one USB transaction as raw is cached internally
                req = SpectrometerRequest("get_battery_state_raw")
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                   return res
                reading.battery_raw = res.data
                if self.hardware.shutdown_requested:
                    log.debug("battery_raw shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

                req = SpectrometerRequest("get_battery_percentage")
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                   return res
                reading.battery_percentage = res.data
                if self.hardware.shutdown_requested:
                    log.debug("battery_perc shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response
                self.last_battery_percentage = reading.battery_percentage

                req = SpectrometerRequest("get_battery_percentage")
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res
                reading.battery_charging = res.data
                if self.hardware.shutdown_requested:
                    log.debug("battery_charging shutdown")
                    acquire_response.poison_pill = True
                    return acquire_response

                log.debug("battery: %.2f%% (%s)", reading.battery_percentage, "charging" if reading.battery_charging else "discharging")
            else:
                if reading is not None:
                    reading.battery_percentage = self.last_battery_percentage

        if auto_enable_laser:
            log.debug(f"AUTO-RAMAN ==> done")

        if tor:
            reading.take_one_request = tor

            # was this a "fast BatchCollection" with a streaming multi-reading TakeOneRequest?
            if tor.readings_target:
                tor.readings_current += 1

            if not tor.readings_target or tor.readings_current >= tor.readings_target:
                log.debug(f"completed {tor}")
                self.take_one_request = None

        log.debug("device.acquire_spectrum: returning %s", reading)
        acquire_response.data = reading
        self.last_complete_acquisition = datetime.datetime.now()
        return acquire_response

    ##
    # It's unclear how many throwaways are really needed for a stable Raman 
    # spectrum, and whether they're really based on number of integrations 
    # (sensor stabilization) or time (laser warmup); I suspect both.  Also note 
    # the potential need for sensor warm-up, but I think that's handled inside FW.
    #
    # Optimal would probably be something like "As many integrations as it takes
    # to span 2sec, but not fewer than two."
    #
    # These are NOT the same throwaways added to smooth spectra over changes to
    # integration time and gain. These are separate throwaways potentially 
    # required when waking the sensor from sleep. However, I'm using the same
    # mechanism for tracking (self.remaining_throwaways) for commonality.
    #
    # @todo This shouldn't be required at all if we're in free-running mode, or 
    #       if it's been less than a second since the last acquisition.
    # @returns SpectrometerResponse IFF error occurred (normally None)
    def perform_optional_throwaways(self):
        if self.settings.is_micro() and self.take_one_request:
            count = 2
            readout_ms = 5

            # Assume that if we FINISHED the last measurement less than a second 
            # ago, the sensor probably has not gone to sleep and doesn't need SW-
            # driven warmups.
            elapsed_sec_since_last_acquisition = (datetime.datetime.now() - self.last_complete_acquisition).total_seconds()
            if self.last_complete_acquisition is None or elapsed_sec_since_last_acquisition > 1:
                # for now, default to 2sec worth of acquisitions
                while count * (self.settings.state.integration_time_ms + readout_ms) < 2000:
                    count += 1

            self.hardware.remaining_throwaways = count
            while self.hardware.remaining_throwaways > 0:
                log.debug(f"more than a second since last measurement, so performing wake-up throwaways ({self.hardware.remaining_throwaways - 1} remaining)")
                req = SpectrometerRequest("get_spectrum")
                res = self.hardware.handle_requests([req])[0]
                if res.error_msg != '':
                    return res

    def take_one_averaged_reading(self, label=None):
        """
        Okay, let's talk about averaging.  We only perform averaging as a 
        blocking call in Wasatch.PY when given a TakeOneRequest with non-zero 
        scans_to_average, typically from ENLIGHTEN's TakeOneFeature (perhaps via
        VCRControls.step, possibly with AutoRaman, or BatchCollection).

        Otherwise, normally this thread (the background thread owned by a 
        WasatchDeviceWrapper object and running WrapperWorker.run) is in "free-
        running" mode, taking spectra just as fast as it can in an endless loop,
        feeding them back to the consumer (ENLIGHTEN) over a queue. To keep that 
        pipeline "moving," generally we don't do heavy blocking operations down 
        here in the background thread.
        
        The new AutoRaman feature makes increased using of scan averaging,
        and again kind of wants to be encapsulated down here. And by implication,
        all TakeOneRequests should really support encapsulated, atomic averaging.
        So the current design is that ATOMIC scans_to_average comes from 
        TakeOneRequest, ENLIGHTEN-based averaging in SpectrometerState.

        @returns Reading on success, true or false on "stop processing" conditions
        """
        take_one_response = SpectrometerResponse()

        if self.take_one_request:
            scans_to_average = self.take_one_request.scans_to_average   # how many (for completion test / division)
            loop_count = scans_to_average                               # how many to take NOW
            sum_locally = scans_to_average > 1                          # whether we should be summing at all
            self.sum_count = 0                                          # whether we should reset previous sums
        else:
            scans_to_average = self.settings.state.scans_to_average     # how many (for completion test / division)
            sum_locally = scans_to_average > 1                          # whether we should be summing at all
            loop_count = 1                                              # how many to take NOW

        if False:
            log.debug(f"take_one_averaged_reading[{label}]:")
            log.debug(f"  self.sum_count        = {self.sum_count}")
            log.debug(f"  scans_to_average      = {scans_to_average}")
            log.debug(f"  loop_count            = {loop_count}")
            log.debug(f"  sum_locally           = {sum_locally}")
            log.debug(f"  remaining_throwaways  = {self.hardware.remaining_throwaways}")

        # clear any pending throwaways
        while self.hardware.remaining_throwaways > 0:
            log.debug(f"clearing stabilization throwaway ({self.hardware.remaining_throwaways - 1} remaining)")
            req = SpectrometerRequest("get_spectrum")
            res = self.hardware.handle_requests([req])[0]
            if res.error_msg != '':
                return res

        # either take one measurement (normal), or a bunch (sum_locally)
        reading = None
        for loop_index in range(loop_count):

            # log.debug(f"take_one_averaged_reading: loop_index {loop_index+1} of {loop_count}")

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

            # collect ONE spectrum (it's in a while loop because we may have
            # to wait for an external trigger, and must wait through a series
            # of timeouts)
            externally_triggered = self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_EXTERNAL
            try:
                while True:
                    req = SpectrometerRequest("get_spectrum")
                    res = self.hardware.handle_requests([req])[0]
                    if res.error_msg != '':
                        return res

                    # @todo get rid of spectrum_and_row...get_spectrum() can go back to only returning spectrum
                    spectrum_and_row = res.data
                    if res.poison_pill:
                        # float up poison
                        take_one_response.transfer_response(res)
                        return take_one_response
                    if res.keep_alive:
                        # float up keep alive
                        take_one_response.transfer_response(res)
                        return take_one_response
                    if isinstance(spectrum_and_row, bool):
                        # get_spectrum returned a poison-pill, so flow it upstream
                        take_one_response.poison_pill = True
                        return take_one_response

                    if self.hardware.shutdown_requested:
                        take_one_response.poison_pill = True
                        return take_one_response

                    if spectrum_and_row is None or spectrum_and_row.spectrum is None:
                        # FeatureIdentificationDevice can return None when waiting
                        # on an external trigger.  
                        log.debug("take_one_averaged_reading: get_spectrum None, sending keepalive for now")
                        take_one_response.transfer_response(res)
                        return take_one_response
                    else:
                        break

                reading.spectrum            = spectrum_and_row.spectrum
                reading.timestamp_complete  = datetime.datetime.now()

                log.debug(f"take_one_averaged_reading: got {reading.spectrum[0:9]}")
            except Exception as exc:
                # if we got the timeout after switching from externally triggered back to internal, let it ride
                take_one_response.error_msg = exc
                take_one_response.error_lvl = ErrorLevel.medium
                take_one_response.keep_alive = True
                if externally_triggered:
                    log.debug("caught exception from get_spectrum while externally triggered...sending keepalive")
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
            #
            # @todo: since we're actually passing every pre-averaged spectrum
            # back to ENLIGHTEN, for non-TakeOneRequest averaging, I feel like
            # we should probably perform the summation in ENLIGHTEN too (for
            # non-TakeOneRequest averages).

            if not reading.failure:
                # log.debug("take_one_averaged_reading: not failure")
                if sum_locally:
                    log.debug("take_one_averaged_reading: summing locally")
                    if self.sum_count == 0:
                        log.debug("take_one_averaged_reading: first spectrum (initializing)")
                        self.summed_spectra = [float(i) for i in reading.spectrum]
                    else:
                        log.debug("take_one_averaged_reading: adding to previous")
                        for i in range(len(self.summed_spectra)):
                            self.summed_spectra[i] += reading.spectrum[i]
                    self.sum_count += 1
                    log.debug("take_one_averaged_reading: summed_spectra : %s ...", self.summed_spectra[0:9])

            # count spectra
            self.session_reading_count += 1
            reading.session_count = self.session_reading_count
            reading.sum_count = self.sum_count
            log.debug(f"take_one_averaged_reading: reading.sum_count now {reading.sum_count}, session_count {reading.session_count}")

            # have we completed the averaged reading?
            if sum_locally: 
                log.debug(f"take_one_averaged_reading: checking for completion self.sum_count {self.sum_count} >?= scans_to_average {scans_to_average}")
                if self.sum_count >= scans_to_average:
                    reading.spectrum = [ x / self.sum_count for x in self.summed_spectra ]
                    log.debug("take_one_averaged_reading: averaged_spectrum : %s ...", reading.spectrum[0:9])
                    reading.averaged = True

                    # reset for next average
                    self.summed_spectra = None
                    self.sum_count = 0
            else:
                # if averaging isn't enabled...then a single reading is the
                # "averaged" final measurement (check reading.sum_count to confirm)
                reading.averaged = True

        log.debug("device.take_one_averaged_reading: returning %s", reading)
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
    # received through change_setting() which have not yet been processed, and <-- MZ: incomplete sentence
    #
    #
    # Note that WrapperWorker.run "de-dupes" commands on
    # receipt from ENLIGHTEN, so the command stream arising from that source
    # should already be optimized and minimal.  Commands injected manually by
    # calling WasatchDevice.change_setting() do not receive this treatment.
    #
    # In the normal multithreaded (ENLIGHTEN) workflow, this function is called
    # at the beginning of acquire_data, itself ticked regularly by
    # WrapperWorker.run.
    def process_commands(self):
        control_object = "throwaway"
        retval = False
        log.debug("process_commands: processing")
        while len(self.command_queue) > 0:
            control_object = self.command_queue.pop(0)
            log.debug("process_commands: %s", control_object)

            # is this a command used by WasatchDevice itself, and not
            # passed down to FeatureIdentificationDevice?
            if control_object.setting.lower() == "acquire":
                # MZ: does this ever happen? It looks like the current
                #     request sent down from WrapperWorker is "acquire_data"
                #     not "acquire"...
                log.debug("process_commands: acquire found")
                retval = True
            else:
                # send setting downstream to be processed by the spectrometer HAL
                # (probably FeatureIdentificationDevice)
                req = SpectrometerRequest(control_object.setting, args=[control_object.value])
                self.hardware.handle_requests([req])

        return retval

    def _init_process_funcs(self): # -> dict[str, Callable[..., Any]] 
        process_f = {}

        process_f["connect"] = self.connect
        process_f["disconnect"] = self.disconnect
        process_f["acquire_data"] = self.acquire_data

        return process_f

    # ######################################################################## #
    #                                                                          #
    #                                Area Scan                                 #
    #                                                                          #
    # ######################################################################## #

    def acquire_area_scan(self):
        """
        FeatureIdentificationDevice.get_area_scan returns a Reading because it
        really needs to return both the AreaScanImage (whatever form that may 
        take) and the vertically-binned spectrum (for live display).

        @returns a SpectrometerResponse(data=Reading)
        """
        log.debug("acquire_area_scan: start")
        reading = self.hardware.get_area_scan()

        self.session_reading_count += 1
        reading.session_count = self.session_reading_count
        response = SpectrometerResponse(data=reading)

        log.debug(f"acquire_area_scan: returning response {response}")
        return response

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
    def change_setting(self, setting: str, value: Any, allow_immediate: bool = True): # -> None 
        log.debug(f"WasatchDevice.change_setting: {setting} -> {value}")

        # Since scan averaging lives in WasatchDevice, handle commands which affect
        # averaging at this level
        if setting == "scans_to_average":
            self.sum_count = 0
            self.settings.state.scans_to_average = int(value)
            return
        elif setting == "reset_scan_averaging":
            self.sum_count = 0
            return
        elif setting == "take_one_request":
            self.sum_count = 0
            self.take_one_request = value
            return
        elif setting == "cancel_take_one":
            self.sum_count = 0
            self.take_one_request = None
            return

        control_object = ControlObject(setting, value)
        self.command_queue.append(control_object)
        log.debug("change_setting: queued %s", control_object)

        # always process trigger_source commands promptly (can't wait for end of
        # acquisition which may never come)
        if (allow_immediate and self.immediate_mode) or re.search(r"trigger|laser", setting):
            log.debug("immediately processing %s", control_object)
            self.process_commands()

    # override handle_requests. I didn't see a good way to overwrite change_setting without
    # having to redo that pass through.
    def handle_requests(self, requests: list[SpectrometerRequest]): # -> list[SpectrometerResponse] 
        responses = []
        for request in requests:
            try:
                cmd = request.cmd
                proc_func = self.process_f.get(cmd, None)
                if proc_func is None:
                    try:
                        self.change_setting(cmd, *request.args, **request.kwargs)
                    except Exception as e:
                        log.error(f"error {e} with trying to set setting {cmd} with args and kwargs {request.args} and {request.kwargs}", exc_info=1)
                        return []
                elif request.args == [] and request.kwargs == {}:
                    responses.append(proc_func())
                else:
                    responses.append(proc_func(*request.args, **request.kwargs))
            except Exception as e:
                log.error(f"error in handling request {request} of {e}", exc_info=1)
                responses.append(SpectrometerResponse(error_msg="error processing cmd", error_lvl=ErrorLevel.medium))
        return responses
