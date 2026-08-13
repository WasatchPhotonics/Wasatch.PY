import logging

from .SpectrometerResponse import SpectrometerResponse, ErrorLevel
from .SpectrometerRequest  import SpectrometerRequest
from .StatusMessage        import StatusMessage

log = logging.getLogger(__name__)

class InterfaceDevice:
    """
    An InterfaceDevice is the top-level interface to communicating with any 
    spectrometer supported by Wasatch.PY.

    # Sub-Classes

    These are the currently supported sub-classes at writing:

    - WasatchDevice: all Wasatch-manufactured USB spectrometers using VID 0x24aa
      and PID 0x1000 (FX2 with Hamamatsu CCD), 0x2000 (FX2 with Hamamamsu InGaAs)
      or 0x4000 (ARM, typically IMX CMOS).

        - FeatureInterfaceDevice (FID): a lower-level "hardware access layer" 
          (HAL) implementing the ENG-0001 USB protocol, used exclusively by 
          WasatchDevice. FID also inherits from InterfaceDevice for convenience. 
          Developers may choose to instantiate and communicate with FID directly,
          bypassing WasatchDevice, but will miss out on some automated higher-
          level functions (and sadly twisted logic) involving AutoRaman, scan 
          averaging, BatchCollections etc. 

    - BLEDevice: a Wasatch XS spectrometer communicating over Bluetooth®,
      implementing the ENG-0120 Wasatch BLE GATT profile.

    - AndorDevice: a Wasatch XL spectrometer using an Andor camera via Oxford 
      Instruments' SDK.

    - IDSDevice: a prototype spectrometer using an IDS camera via their IDSPeak
      SDK.

    - TCPDevice: a Wasatch Ethernet-capable spectrometer implementing the
      ENG-0196 WISP protocol.

    - SPIDevice: a Wasatch SPI-based spectrometer implementing the ENG-0150
      SPI spectrometer protocol.

    - OceanDevice: an experimental wrapper to communicate with Ocean Optics
      spectrometers using their SeaBreeze library.

    # Request Handling

    InterfaceDevices primarily communicate by receiving SpectrometerRequests
    and returning SpectrometerResponses. 

    A SpectrometerRequest is basically a command name coupled with a few 
    positional or keyword arguments (in other words, a deconstructed Python 
    function call).

    A SpectrometerResponse contains a .data object which could include a
    requested spectrum or spectrometer setting, as well as a few fields
    to indicate error conditions.
    
    """

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        """
        Any class that communicates to a spectrometer should inherit this class.
        It provides the common functions that avoid repeated code.

        @param device_id     either DeviceID or string equiv ("USB:0x24aa:0x1000:1:24")
        @param message_queue if provided, used to send status back to caller
        @param alert_queue   if provided, used to receive hints and realtime 
                             interrupts from caller, including cancellation of 
                             long-running tasks like AutoRaman

        @todo move scan averaging, TakeOneRequest logic upstream from 
              WasatchDevice, AndorDevice etc to here (simplify children of this 
              class while while increasing code-reuse and consistency across all
              InterfaceDevice variants)
        """
        self.device_id      = device_id
        self.message_queue  = message_queue # outgoing status back to ENLIGHTEN 
        self.alert_queue    = alert_queue   # incoming alerts from ENLIGHTEN 

        # if passed a string representation of a DeviceID, deserialize it
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        # all InterfaceDevices should populate this with the names of all supported requests
        self.process_f = {}

        # private (write via queue, read via check_alert())
        self._alerts = set() 

        self.remaining_throwaways = 0

        # just to catch early errors
        self._supported_message_queue_types = {
            "progress_bar",
            "firmware_log",
            "marquee_info",
            "marquee_error",
            "scan_averaging",
            "laser_firing_indicators",
            "received_ble_firmware_version"
        }

    ############################################################################
    # Request Handlers
    ############################################################################

    def change_setting(self, setting, value=None):
        """ 
        Convenience shortcut for old function name. Also, allows WasatchDevice
        to mimic WasatchDeviceWrapper interface.
        """ 
        return self.handle_cmd(setting, value)

    def handle_cmd(self, cmd, arg=None, force=False):
        """ 
        handle_cmd("get_laser_temperature_deg_c") 
            is a shortcut to 
        handle_request(SpectrometerRequest("get_laser_temperature_deg_c"))

        handle_cmd("select_adc", 1) 
            is a shortcut to
        handle_request(SpectrometerRequest("select_adc", args=[1]))

        handle_cmd("take_one_request", None, force=True) 
            is a shortcut to
        handle_request(SpectrometerRequest("select_adc", args=[None]))

        @param force is just there to allow users to forcibly pass None as an argument
        """
        if arg is None and not force:
            return self.handle_request(SpectrometerRequest(cmd))
        else:
            return self.handle_request(SpectrometerRequest(cmd, args=[arg]))

    def handle_request(self, request):
        """
        handle_request(SpectrometerRequest("get_laser_temperature_deg_c")) 
            is a shortcut to
        handle_requests([SpectrometerRequest("get_laser_temperature_deg_c")])[0]
        """
        if isinstance(request, list):
            log.error("handle_request received singular request of {len(request)} elements...just handling the first")
            request = request[0]

        responses = self.handle_requests( [ request ] )
        if responses is not None and len(responses) > 0:
            return responses[0]

    def handle_requests(self, requests):
        responses = []
        for request in requests:
            try:
                cmd = request.cmd
                proc_func = self.process_f.get(cmd, None)
                if proc_func is None:
                    responses.append(SpectrometerResponse(error_msg=f"unsupported cmd {request.cmd}", error_lvl=ErrorLevel.low))
                elif request.args == [] and request.kwargs == {}:
                    responses.append(proc_func())
                else:
                    responses.append(proc_func(*request.args, **request.kwargs))
            except Exception as e:
                log.error(f"error in handling request {request} of {e}", exc_info=1)
                self.queue_message("marquee_error", str(e))
                responses.append(SpectrometerResponse(error_msg=str(e), error_lvl=ErrorLevel.medium))
        return responses

    ############################################################################
    # Outgoing Sideline Messages
    ############################################################################

    # Some requests may involve long-running operations, and need to send 
    # information back to the caller while handling a large request (like an 
    # Auto-Raman measurement sending back progress indications).
    # This interface lets an InterfaceDevice push arbitrary StatusMessage key-
    # value pairs back up a Queue to the caller. 
    #
    # These messages do not represent the "official response" to a particular
    # request, but may contain useful information about spectrometer / operation
    # status.

    def queue_message(self, setting, value):
        """
        If an upstream queue is defined, send the name-value pair.  Does nothing
        if the caller hasn't provided a queue.

        @see enlighten.controller.process_status_message
        """
        if self.message_queue is None:
            return SpectrometerResponse(data=False)

        # warn on typos
        if setting not in self._supported_message_queue_types:
            log.error(f"possible error, {setting} not found in supported_message_queue_types")

        msg = StatusMessage(setting, value)
        try:
            self.message_queue.put(msg) 
            log.debug(f"queued: {msg}")
        except:
            log.error("failed to enqueue StatusMessage (%s, %s)", setting, value, exc_info=1)

    ############################################################################
    # Incoming Sideline Messages
    ############################################################################

    # Sometimes the caller (ENLIGHTEN etc) may need to notify the 
    # InterfaceDevice of real-time interrupts or alerts, like a cancellation of
    # a long-running operation. 
    #
    # Alerts are just (name, value) pairs, where if the value is "truthy,"
    # the named alert is raised by the caller, otherwise a previously-raised
    # alert is cleared.
    #
    # At writing, supported alerts are:
    #
    # - auto_raman_cancel

    def _refresh_alerts(self):
        """ 
        Retreive any fresh alerts from the caller (automatically called by
        check_alert).
        """
        if self.alert_queue is None:
            return

        if self.alert_queue.empty():
            return

        while not self.alert_queue.empty():
            alert = self.alert_queue.get_nowait()
            if alert is None:
                continue
            elif isinstance(alert, ControlObject):
                if alert.value:
                    log.debug(f"raised alert {alert.setting}")
                    self._alerts.add(alert.setting)
                else:
                    log.debug(f"cleared alert {alert.setting}")
                    self._alerts.discard(alert.setting)
            else:
                log.error(f"non-ControlObject found in alerts_queue: {alert}")

    def check_alert(self, name):
        """ 
        Check whether a specific alert has been raised.
        """
        log.debug(f"checking for alert {name}")
        self._refresh_alerts()
        if name in self._alerts:
            log.debug(f"found {name} (clearing)")
            self._alerts.remove(name)
            return True

    ############################################################################
    # common post-processing
    ############################################################################

    def correct_bad_pixels(self, spectrum):
        """
        If a spectrometer has bad_pixels configured in the EEPROM, then average
        over them in the driver.

        Note this function modifies the passed array in-place, rather than
        returning a modified copy.

        @note assumes bad_pixels is previously sorted
        @returns True if spectrum was modified, False if spectrum left unchanged
        """

        if self.settings is None or \
                self.settings.eeprom is None or \
                self.settings.eeprom.bad_pixels is None or \
                len(self.settings.eeprom.bad_pixels) == 0 or \
                self.settings.state.detector_regions is not None:
            log.debug("correct_bad_pixels: skipping[1]")
            return False

        if spectrum is None or len(spectrum) == 0:
            log.debug("correct_bad_pixels: skipping[2]")
            return False

        pixels = len(spectrum)
        bad_pixels = self.settings.eeprom.bad_pixels

        # iterate over each bad pixel
        i = 0
        log.debug(f"correct_bad_pixels: correcting {bad_pixels}")
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
                        # log.debug(f"correct_bad_pixels: bad_pix {bad_pix}, prev_good {prev_good} ({spectrum[prev_good]}), next_good {next_good} ({spectrum[next_good]}), delta {delta:.2f}, rng {rng}, step {step:.2f}")
                        for j in range(rng - 1):
                            new = spectrum[prev_good] + step * (j + 1)
                            spectrum[prev_good + j + 1] = new
                            # log.debug(f"correct_bad_pixels: stomping pix {prev_good + j + 1} with {new}")
                    else:
                        # we ran off the high end, so copy-right
                        for j in range(bad_pix, pixels):
                            spectrum[j] = spectrum[prev_good]

            # advance to next bad pixel
            i += 1
        return True

class InterfaceDeviceClassUnavailable(Exception):
    """
    InterfaceDevice objects can raise this to quietly indicate that they have 
    been deemed unavailable at runtime.

    This may happen if a particular InterfaceDevice subclass depends on a
    separate vendor SDK which has not been installed on the local system.
    """
    pass
