import platform
import datetime
import logging
import random
import copy
import math
import os
import re

from random import randint
from time   import sleep

from . import utils

from .SpectrometerSettings import SpectrometerSettings
from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest  import SpectrometerRequest
from .SpectrometerResponse import ErrorLevel
from .SpectrometerState    import SpectrometerState
from .InterfaceDevice      import InterfaceDevice
from .DetectorRegions      import DetectorRegions
from .StatusMessage        import StatusMessage
from .RealUSBDevice        import RealUSBDevice
from .MockUSBDevice        import MockUSBDevice
from .DetectorROI          import DetectorROI
from .PollStatus           import PollStatus
from .EEPROM               import EEPROM
from .IMX385               import IMX385

log = logging.getLogger(__name__)

MICROSEC_TO_SEC = 0.000001
UNINITIALIZED_TEMPERATURE_DEG_C = -999

class SpectrumAndRow:
    def __init__(self, spectrum=None, row=-1):
        self.spectrum = None
        self.row = row

        if spectrum is not None:
            self.spectrum = spectrum.copy()

class FeatureIdentificationDevice(InterfaceDevice):
    """
    This is the basic implementation of our FeatureIdentificationDevice (FID)
    spectrometer USB API as defined in ENG-0001.
    This class is roughly comparable to Wasatch.NET's Spectrometer.cs.
    
    This class is normally not accessed directly, but through the higher-level
    abstraction WasatchDevice.
    
    @see ENG-0001
    ##########################################################################
    This class adopts the external device interface structure
    This invlovles receiving a request through the handle_request function
    A request is processed based on the key in the request
    The processing function passes the commands to the requested device
    Once it recevies a response from the connected device it then passes that
    back up the chain
    @verbatim
                               Enlighten Request
                                       |
                                handle_requests
                                       |
                                 ------------
                                /   /  |  \  \ 
             { get_laser status, acquire, set_laser_watchdog, etc....}
                                \   \  |  /  /
                                 ------------
                                       |
                                   _send_code
    @endverbatim
    ############################################################################
    """

    # ##########################################################################
    # Lifecycle
    # ##########################################################################

    def __init__(self, device_id: str, message_queue: list = None):
        """
        Instantiate a FeatureIdentificationDevice with from the given device_id.
        @param device_id [in] device ID ("USB:0x24aa:0x1000:1:24")
        @param message_queue [out] if provided, provides an outbound (from FID)
        queue for writing StatusMessage objects upstream
        """
        super().__init__()
        self.device_id = device_id
        self.message_queue = message_queue

        self.device = None
        if "MOCK" in str(device_id).upper():
            self.device_type = MockUSBDevice(device_id.name,
                                             device_id.directory,
                                             device_id.overrides,
                                             device_id.spectra_options)
        else:
            self.device_type = RealUSBDevice(device_id)

        self.last_usb_timestamp = None

        self.laser_temperature_invalid = False
        self.ccd_temperature_invalid = False

        self.settings = SpectrometerSettings(device_id)
        self.eeprom_backup = None

        # ######################################################################
        # these are "driver state" within FeatureIdentificationDevice, and don't
        # really relate to the spectrometer hardware
        # ######################################################################

        self.detector_tec_setpoint_has_been_set = False
        self.last_applied_laser_power = 0.0 # last power level APPLIED to laser, either by turning off (0) or on 
        self.next_applied_laser_power = None # power level to be applied NEXT time the laser is enabled

        self.raise_exceptions = False
        self.inject_random_errors = False
        self.random_error_perc = 0.001   # 0.1%
        self.allow_default_gain_reset = True

        self.connected = False
        self.connecting = False
        self.shutdown_requested = False

        self.last_spectrum = None
        self.prev_pixels = None

        # in case of I2C collisions within the spectrometer, e.g. due to battery-LED status
        self.retry_enabled = True
        self.retry_ms = 5
        self.retry_max = 3

        self.process_f = self._init_process_funcs()
        self.imx385 = IMX385()

    def handle_requests(self, requests: list[SpectrometerRequest]):
        """
        @todo consider making 'requests' an object, and dynamically checking to 
              see if it is a single SpectrometerRequest or a list[SpectrometerRequest];
              if the former, only return a single SpectrometerResponse.
        """
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
                responses.append(SpectrometerResponse(error_msg="error processing cmd", error_lvl=ErrorLevel.medium))
        return responses

    def connect(self, retries=0):
        """
        Connect to the device and initialize basic settings.
        @warning this causes a problem in non-blocking mode (WasatchDeviceWrapper)
        on MacOS
        """
        self.connecting = True

        # ######################################################################
        # USB Connection
        # ######################################################################

        # Generate a fresh listing of USB devices with the requested VID and PID.
        # Note that this is NOT how WasatchBus traverses the list.  It actually
        # calls usb.busses(), then iterates over bus.devices, but that's because
        # it doesn't know what PIDs it might be looking for.  We know, so just
        # narrow down the search to those devices.

        log.info(f"FID.connect: asked to connect to device_type {self.device_type}")
        log.info(f"FID.connect: calling device_type.find, looking for VID 0x{self.device_id.vid:04x} and PID 0x{self.device_id.pid:04x}")
        devices = self.device_type.find(find_all=True, idVendor=self.device_id.vid, idProduct=self.device_id.pid)
        log.info(f"FID.connect: found devices {devices}")
        dev_list = list(devices) # convert from array

        device = None
        log.info(f"searching for specified device in dev_list {dev_list}")
        for dev in dev_list:
            if dev.bus != self.device_id.bus:
                log.debug("FID.connect: rejecting device (bus %d != requested %d)", dev.bus, self.device_id.bus)
            elif dev.address != self.device_id.address:
                log.debug("FID.connect: rejecting device (address %d != requested %d)", dev.address, self.device_id.address)
            else:
                device = dev
                break

        if device is None:
            log.debug(f"FID.connect: unable to find DeviceID {self.device_id}")
            self.connecting = False
            return SpectrometerResponse(data=False, error_msg=f"unable to find DeviceID {self.device_id}")
        else:
            log.debug("FID.connect: matched DeviceID %s", str(self.device_id))

        if os.name != "posix":
            log.debug("on Windows, so NOT setting configuration and claiming interface")
        elif "macOS" in platform.platform():
            log.debug("on MacOS, so NOT setting configuration and claiming interface")
        # elif "raspberrypi" in str(os.uname()):
        #     log.debug("on Raspberry Pi, so NOT setting configuration and claiming interface")
        else:
            log.debug("on posix, so setting configuration and claiming interface")

            # in the following, return SpectrometerResponse objects rather than 
            # raising exceptions so the user will have a more-useful error 
            # message to report or use in troubleshooting (exception still gets
            # logged)
            try:
                log.debug("setting configuration")
                self.device_type.set_configuration(device)
            except Exception as exc:
                #####################################################################################################################
                # This additional if statement is present for the Raspberry Pi. There is an issue with resource busy errors.
                # Adding dev.reset() solves this. See https://stackoverflow.com/questions/29345325/raspberry-pyusb-gets-resource-busy
                #####################################################################################################################
                if "Resource busy" in str(exc) and retries <= 3:
                    log.warn("Hardware Failure in setConfiguration. Resource busy error. Attempting to reattach driver by reset.")
                    self.device_type.reset(dev)
                    sleep_ms = 10 ** retries # 10^3 ms = 1sec max delay
                    sleep(sleep_ms / 1000.0) 
                    return self.connect(retries=retries+1) 

                self.connecting = False
                msg = f"Hardware Failure in setConfiguration, giving up after {retries} retries"
                log.critical(msg, exc_info=1)
                return SpectrometerResponse(False, error_msg=msg)

            try:
                log.debug("claiming interface")
                self.device_type.claim_interface(device, 0)
            except:
                self.connecting = False
                msg = "Hardware Failure in claimInterface"
                log.critical(msg, exc_info=1)
                return SpectrometerResponse(False, error_msg=msg)

        self.device = device

        return self._post_connect()

    def _post_connect(self):
        """
        Perform additional setup after instantiating FID device.
        Split-out from physical / bus connect() to simplify MockSpectrometer.
        """

        # grab firmware versions early
        self.get_microcontroller_firmware_version()
        self.get_fpga_firmware_version()
        self.get_microcontroller_serial_number()

        # issue: BL652 may not be fully booted if this was a hotplug. We could
        #        of course re-poll if None, but the initial SpectrometerSettings
        #        will already have been used to populate the EEPROMEditor. What
        #        we really need to do is send a "change" in this setting upstream
        #        via the MessageQueue, and have a listener in ENLIGHTEN/caller
        #        for such updates. The way we mainly do this now is by adding
        #        fields to Reading (like temperature, battery, laser interlock
        #        etc), since most dynamic (uncommanded) changes in spectrometer 
        #        state are usually measurement-related. It would be a little 
        #        weird to add a "dynamic firmware version" to Reading, implying
        #        that firmware versions might suddenly change mid-runtime...
        self.get_ble_firmware_version()

        # ######################################################################
        # model-specific settings
        # ######################################################################

        log.debug("model-specific settings")

        # default high-gain mode for InGaAs
        if self.settings.is_ingaas():
            self.set_high_gain_mode_enable(True)

        # ######################################################################
        # EEPROM
        # ######################################################################

        log.debug("reading EEPROM")

        result = self._read_eeprom()
        if not result.data:
            log.error(f"failed to read EEPROM, got error message of {result.error_msg}")
            self.connecting = False
            return result

        # ######################################################################
        # Automatically disable laser at connection
        # ######################################################################

        if self.settings.eeprom.has_laser:
            self.set_laser_enable(False)

        # ######################################################################
        # TEC Setpoint
        # ######################################################################

        if self.settings.is_xs():
            
            if self.settings.eeprom.sig_laser_tec:

                # sanity-check for reasonable setpoint range (raw 12-bit)
                if 700 <= self.settings.eeprom.startup_temp_degC <= 900:
                    log.debug("initializing XS laser TEC setpoint")

                    # kludge: for now, use the detector TEC startup setpoint for laser
                    self.settings.state.laser_tec_setpoint = self.settings.eeprom.startup_temp_degC
                    self.set_laser_temperature_setpoint_raw(self.settings.state.laser_tec_setpoint)

                    # this should be the default in firmware, but set anyway
                    log.debug("initializing XS laser TEC mode -> AUTO")
                    self.set_laser_tec_mode("AUTO")
                else:
                    # don't set anything if default setpoint looks way off
                    log.error(f"laser TEC setpoint looks invalid: {self.settings.eeprom.startup_temp_degC}")

            self.get_laser_warning_delay_sec()

        # ######################################################################
        # Detector TEC
        # ######################################################################
            
        if not self.settings.is_xs():

            # X/XM models don't require runtime configuration of the laser TEC
            # (it's set via pots on 110280 (SML) or 110613 (MML)), but do need
            # to initialize the detector TEC for R and C units.
            if self.settings.eeprom.has_cooling:

                degC = None
                eeprom = self.settings.eeprom

                if eeprom.min_temp_degC <= eeprom.startup_temp_degC <= eeprom.max_temp_degC:
                    degC = eeprom.startup_temp_degC
                elif re.match(r"7031|10141|9214", eeprom.detector):
                    degC = -15
                elif re.match(r"16011|11511|11850|13971", eeprom.detector):
                    degC = 10

                if degC is not None:
                    # TEC doesn't do anything unless you give it a temperature first
                    log.debug(f"setting detector TEC setpoint to {degC} deg C")
                    self.detector_tec_setpoint_degC = degC
                    self.set_detector_tec_setpoint_degC(self.detector_tec_setpoint_degC)

                    log.debug("enabling detector TEC")
                    self.detector_tec_setpoint_has_been_set = True
                    self.set_tec_enable(True)

        # ######################################################################
        # FPGA
        # ######################################################################

        log.debug("reading FPGA compilation options")
        self._read_fpga_compilation_options()

        log.debug("configuring FPGA")

        # automatically push EEPROM values to the FPGA (on modern EEPROMs)
        # (this will work on Series-XS as well, even if we subsequently track its gain
        #  somewhat differently as state.gain_db)
        if self.settings.eeprom.format >= 4:
            log.debug("sending gain/offset to FPGA")
            self.set_detector_gain      (self.settings.eeprom.detector_gain)
            self.set_detector_offset    (self.settings.eeprom.detector_offset)
            self.set_detector_gain_odd  (self.settings.eeprom.detector_gain_odd)
            self.set_detector_offset_odd(self.settings.eeprom.detector_offset_odd)

        # initialize state.gain_db from EEPROM startup value
        self.settings.state.gain_db = self.settings.eeprom.detector_gain

        if self.settings.is_micro():
            roi = self.settings.get_vertical_roi()
            if roi is not None:
                self.set_vertical_binning(roi)

        self.settings.init_regions()        

        # ######################################################################
        # post-connection defaults
        # ######################################################################

        # default to internal triggering
        self.set_trigger_source(SpectrometerState.TRIGGER_SOURCE_INTERNAL)

        if self.settings.is_xs():

            if self.settings.eeprom.has_laser:
                has_sml = self.settings.eeprom.has_sml()
                has_tec = self.settings.eeprom.sig_laser_tec

                # laser watchdog
                sec = self.settings.eeprom.laser_watchdog_sec
                if sec <= 0 and has_sml and not has_tec:
                    sec = EEPROM.DEFAULT_LASER_WATCHDOG_SEC
                    log.debug(f"declining to disable laser watchdog for SML w/o TEC, defaulting to {sec}sec")
                log.debug(f"post-connect: initializing laser watchdog to {sec}sec")
                self.set_laser_watchdog_sec(sec)

        self.set_integration_time_ms(self.settings.eeprom.startup_integration_time_ms)

        # # for now, enable Gen 1.5 accessory connector by default
        # if self.settings.is_gen15():
        #     log.debug("enabling Gen 1.5 accessory connector")
        #     self.set_accessory_enable(True)

        # ######################################################################
        # Done
        # ######################################################################

        log.debug("connection successful")
        self.connected = True
        self.connecting = False

        self.settings.state.dump("FID.post_connect")

        return SpectrometerResponse(self.connected)

    def disconnect(self):
        if self.last_applied_laser_power:
            log.debug("fid.disconnect: disabling laser")
            self._set_laser_enable_immediate(False)

        self.connected = False

        log.critical("fid.disconnect: releasing interface")
        try:
            #result = self.device_type.release_interface(self.device, 0)
            try:
                self.device_type.reset(self.device)
            except:
                log.error("Couldn't reset device")
        except:
            log.warn("Failure in release interface", exc_info=1)
            raise
        return SpectrometerResponse(True)

    def _schedule_disconnect(self, exc):
        """
        Something in the driver has caused it to request the controlling
        application to close the peripheral.  The next time
        WasatchDevice.acquire_data is called, it will pass a "poison pill" back
        up the response queue.
        Alternately, non-ENLIGHTEN callers can set "raise_exceptions" -> True for
        in-process exception-handling.
        """
        if self.raise_exceptions:
            log.critical("_schedule_disconnect: raising exception %s", exc)
            raise exc
        else:
            log.critical("requesting shutdown due to exception %s", exc)
            self.shutdown_requested = True

    def reset(self, *args):
        log.debug("FID performing device reset")
        #self.device_type.release_interface(self.device, 0)
        self.device_type.reset(self.device)
        log.debug(f"freed interface")
        '''
        pyusb_devices = list(self.device_type.find(find_all=True, 
                                              idVendor=self.device_id.vid, 
                                              idProduct=self.device_id.pid,
                                              custom_match = lambda d: d.address == self.device_id.address and d.bus == self.device_id.bus))
        # a couple notes,
        # When you search how to do a reset by enable/disable on windows what comes up is devcon
        # devcon links to pnputil as the recommended tool to use and comes preinstalled on windows
        # pnputil needs the instance ID, which is pretty well hidden in pyusb
        # I had to look at the full configuration string and saw that the way to get the instance id was the following
        # using _try_get_string https://github.com/pyusb/pyusb/blob/master/usb/core.py#L1221
        # so techincally we shouldn't do this by the _ meaning it should be private, but it's the only way I see
        device_instance_id = f'USB\VID_{self.device_id.vid:04X}&PID_{self.device_id.pid:04X}\{usb.core._try_get_string(pyusb_devices[0], pyusb_devices[0].iSerialNumber)}'
        log.debug(f"In reset and restart trying to reset instance id {device_instance_id}")
        subprocess.run(["pnputil", r"/reboot", r"/disable-device", device_instance_id])
        subprocess.run(["pnputil", r"/reboot", r"/enable-device", device_instance_id])
'''
    # ##########################################################################
    # Utility Methods
    # ##########################################################################

  
    def _to40bit(self, val):
        """
        Laser modulation and continuous-strobe commands take arguments in micro-
        seconds as 40-bit values, where the least-significant 16 bits are passed
        as wValue, the next-significant 16 as wIndex, and the most-significant
        as a single byte of payload.  This function takes an unsigned integral
        value (presumably microseconds) and returns a tuple of wValue, wIndex
        and a buffer to pass as payload.
        """
        lsw = val & 0xffff
        msw = (val >> 16) & 0xffff
        buf = [ (val >> 32) & 0xff ] + [0] * 7
        return (lsw, msw, buf)

    def _wait_for_usb_available(self):
        """
        Wait until any enforced USB packet intervals have elapsed. This does
        nothing in most cases - the function is normally a no-op.
        However, if the application has defined min/max_usb_interval_ms (say
        (20, 50ms), then pick a random delay in the defined window (e.g. 37ms)
        and sleep until it has been at least that long since the last USB
        exchange.
        The purpose of this function was to wring-out some early ARM micro-
        controllers with apparent timing issues under high-speed USB 2.0, to see
        if communications issues disappeared if we enforced a communication
        latency from the software side.
        """
        if self.settings.state.max_usb_interval_ms <= 0:
            return

        if self.last_usb_timestamp is not None:
            delay_ms = randint(self.settings.state.min_usb_interval_ms, self.settings.state.max_usb_interval_ms)
            next_usb_timestamp = self.last_usb_timestamp + datetime.timedelta(milliseconds=delay_ms)
            now = datetime.datetime.now()
            if now < next_usb_timestamp:
                sleep_sec = (next_usb_timestamp - now).total_seconds()
                log.debug("fid: sleeping %.3f sec to enforce %d ms USB interval", sleep_sec, delay_ms)
                sleep(sleep_sec)
        self.last_usb_timestamp = datetime.datetime.now()

    def _check_for_random_error(self):
        """
        This function is provided to simulate random USB communication errors
        during regression testing, and is normally a no-op.
        """
        if not self.inject_random_errors:
            return False

        if random.random() <= self.random_error_perc:
            log.critical("Randomly-injected error")
            self._schedule_disconnect(Exception("Randomly-injected error"))
            return True
        return False

    ##
    # Until support for even/odd InGaAs gain and offset have been added to the
    # firmware, apply the correction in software.
    def _correct_ingaas_gain_and_offset(self, spectrum: list[float]):
        if not self.settings.is_ingaas() or self.settings.eeprom.hardware_even_odd:
            return False

        # if even and odd pixels have the same settings, there's no point in doing anything
        if self.settings.eeprom.detector_gain_odd   == self.settings.eeprom.detector_gain and \
           self.settings.eeprom.detector_offset_odd == self.settings.eeprom.detector_offset:
            return False

        if 0 == self.settings.eeprom.detector_gain:
            log.debug("declining to attempt division by zero")
            return

        log.debug("rescaling InGaAs odd pixels from even gain %.4f, offset %d to odd gain %.4f, offset %d",
            self.settings.eeprom.detector_gain,
            self.settings.eeprom.detector_offset,
            self.settings.eeprom.detector_gain_odd,
            self.settings.eeprom.detector_offset_odd)

        log.debug("before: %d, %d, %d, %d, %d", spectrum[0], spectrum[1], spectrum[2], spectrum[3], spectrum[4])

        # iterate over the ODD pixels of the spectrum
        for i in range(1, len(spectrum), 2):

            # back-out the incorrectly applied "even" gain and offset
            old = float(spectrum[i])
            raw = (old - self.settings.eeprom.detector_offset) / self.settings.eeprom.detector_gain

            # apply the correct "odd" gain and offset
            spectrum[i] = (raw * self.settings.eeprom.detector_gain_odd) + self.settings.eeprom.detector_offset_odd

            if i < 5 or i > len(spectrum) - 5:
                log.debug("  pixel %4d: old %.2f raw %.2f new %.2f", i, old, raw, spectrum[i])

        log.debug("after: %d, %d, %d, %d, %d", spectrum[0], spectrum[1], spectrum[2], spectrum[3], spectrum[4])

        return True

    def _apply_horizontal_binning(self, spectrum: list[float]):
        if not self.settings.eeprom.horiz_binning_enabled:
            return spectrum

        mode = self.settings.eeprom.horiz_binning_mode
        if mode == IMX385.BIN_2X2:
            return self.imx385.bin_2x2(spectrum)
        elif mode == IMX385.CORRECT_SSC:
            return self.imx385.correct_ssc(spectrum, self.settings.wavelengths)
        elif mode == IMX385.CORRECT_SSC_BIN_2X2:
            spectrum = self.imx385.correct_ssc(spectrum, self.settings.wavelengths)
            return self.imx385.bin_2x2(spectrum)
        elif mode == IMX385.BIN_4X2:
            return self.imx385.bin_4x2(spectrum)
        else:
            # there may be legacy units in the field where this byte is 
            # uninitialized to 0xff...treat as 0x00 for now
            log.error("invalid horizontal binning mode {mode}...defaulting to bin_2x2")
            return self.imx385.bin_2x2(spectrum)

    def _correct_bad_pixels(self, spectrum: list[float]):
        """
        If a spectrometer has bad_pixels configured in the EEPROM, then average
        over them in the driver.
        Note this function modifies the passed array in-place, rather than
        returning a modified copy.
        @note assumes bad_pixels is previously sorted
        """

        if self.settings is None or \
                self.settings.eeprom is None or \
                self.settings.eeprom.bad_pixels is None or \
                len(self.settings.eeprom.bad_pixels) == 0 or \
                self.settings.state.detector_regions is not None:
            return False

        if spectrum is None or len(spectrum) == 0:
            return False

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

    def _send_code(self, 
                   bRequest: int, 
                   wValue: int = 0, 
                   wIndex: int = 0, 
                   data_or_wLength: int = None, 
                   label: str = "", 
                   dry_run: bool = False, 
                   retry_on_error: bool = False, 
                   success_result: int = 0x00) -> SpectrometerResponse:
        if self.shutdown_requested or (not self.connected and not self.connecting):
            log.debug("_send_code: not attempting because not connected")
            return SpectrometerResponse(False)

        prefix = "" if not label else ("%s: " % label)
        result = None

        if data_or_wLength is None:
            if self.settings.is_arm():
                data_or_wLength = [0] * 8
            else:
                data_or_wLength = 0

        log.debug("%s_send_code: request 0x%02x value 0x%04x index 0x%04x data/len %s",
            prefix, bRequest, wValue, wIndex, data_or_wLength)

        if dry_run:
            return SpectrometerResponse(keep_alive=True)

        if self._check_for_random_error():
            return SpectrometerResponse(poison_pill=False)

        retry_count = 0
        while True:
            try:
                self._wait_for_usb_available()
                result = self.device_type.ctrl_transfer(self.device,
                                                        0x40,        # HOST_TO_DEVICE
                                                        bRequest,
                                                        wValue,
                                                        wIndex,
                                                        data_or_wLength) # add TIMEOUT_MS parameter?
            except Exception as exc:
                log.critical("Hardware Failure FID Send Code Problem with ctrl transfer", exc_info=1)
                self._schedule_disconnect(exc)
                return SpectrometerResponse(poison_pill=True)

            log.debug("%s_send_code: request 0x%02x value 0x%04x index 0x%04x data/len %s: result %s",
                prefix, bRequest, wValue, wIndex, data_or_wLength, result)

            if not retry_on_error:
                return SpectrometerResponse(keep_alive=True)

            # retry logic enabled, so compare result to expected
            matched_expected = True
            if len(success_result) < len(result):
                matched_expected = False
            else:
                for i in range(len(success_result)):
                    if result[i] != success_result[i]:
                        matched_expected = False
                        break

            if matched_expected:
                return SpectrometerResponse(keep_alive=True)

            # apparently it didn't match expected
            retry_count += 1
            if retry_count > self.retry_max:
                log.error("giving up after %d retries", retry_count)
                return SpectrometerResponse(poison_pill=True)

            # try again
            log.error("retrying (attempt %d)", retry_count + 1)

    ## @note weird that so few calls to this function override the default wLength
    # @todo consider adding retry logic as well
    def _get_code(self, 
                  bRequest: int, 
                  wValue: int = 0, 
                  wIndex: int = 0, 
                  wLength: int = 64, 
                  label: str = "", 
                  msb_len: int = None, 
                  lsb_len: int = None) -> SpectrometerResponse:
        prefix = "" if not label else ("%s: " % label)
        result = None

        if self.shutdown_requested or (not self.connected and not self.connecting):
            log.debug("_get_code: not attempting because not connected")
            return SpectrometerResponse()

        if self._check_for_random_error():
            log.debug("random error")
            return SpectrometerResponse(poison_pill=True)

        try:
            self._wait_for_usb_available()
            result = self.device_type.ctrl_transfer(self.device,
                                               0xc0,        # DEVICE_TO_HOST
                                               bRequest,
                                               wValue,
                                               wIndex,
                                               wLength)
        except Exception as exc:
            log.critical(f"Hardware Failure FID Get Code Problem with ctrl transfer (bRequest 0x{bRequest:02x}, wValue 0x{wValue:04x}, wIndex 0x{wIndex:04x}, label {label})", exc_info=1)
            self._schedule_disconnect(exc)
            return SpectrometerResponse(poison_pill=True)

        result_hex = " ".join([f"{v:02x}" for v in result])
        log.debug("%s_get_code: request 0x%02x value 0x%04x index 0x%04x = [%s]",
            prefix, bRequest, wValue, wIndex, result_hex)

        if result is None:
            log.critical("_get_code[%s, %s]: received null", label, self.device_id)
            self._schedule_disconnect(f"_get_code[{label}] received NULL")
            return SpectrometerResponse(keep_alive=True)

        # demarshall or return raw array
        value = 0
        if msb_len is not None:
            for i in range(msb_len):
                value = value << 8 | result[i]
            return SpectrometerResponse(data=value)
        elif lsb_len is not None:
            for i in range(lsb_len):
                if i < len(result):
                    value = (result[i] << (8 * i)) | value
            return SpectrometerResponse(data=value)
        else:
            return SpectrometerResponse(data=result)

    def get_upper_code(self, 
                       wValue: int, 
                       wIndex: int = 0, 
                       wLength: int = 64, 
                       label: str = "", 
                       msb_len: int = None, 
                       lsb_len: int = None) -> SpectrometerResponse:
        return self._get_code(0xff, wValue, wIndex, wLength, label=label, msb_len=msb_len, lsb_len=lsb_len)

    # ##########################################################################
    # initialization
    # ##########################################################################

    def _read_eeprom(self):
        buffers = []
        for page in range(EEPROM.MAX_PAGES):
            buf = None
            try:
                response = self.get_upper_code(0x01, page, label="GET_MODEL_CONFIG(%d)" % page)
                buf = response.data
                if response.error_lvl != ErrorLevel.ok:
                    return response
            except:
                log.error("exception reading upper_code 0x01 with page %d", page, exc_info=1)
            if buf is None:
                msg = "unable to read EEPROM (null buf)"
                log.error(msg)
                return SpectrometerResponse(False, error_lvl=ErrorLevel.medium, error_msg=msg)
            elif len(buf) < 64:
                msg = f"unable to read EEPROM received buf of {buf} and len {len(buf)}"
                log.error(msg)
                return SpectrometerResponse(False, error_lvl=ErrorLevel.medium, error_msg=msg)
            buffers.append(buf)

        flat_buffers_all_ones = True
        for page in buffers:
            for byte in page:
                flat_buffers_all_ones = flat_buffers_all_ones and (byte == 0xFF)

        if flat_buffers_all_ones:
            return SpectrometerResponse(data=False, error_msg="Saw all Fs for EEPROM. Check EEPROM Programmed.", error_lvl=ErrorLevel.low)
        return SpectrometerResponse(data=self.settings.eeprom.parse(buffers))

    def has_linearity_coeffs(self):
        """
        At least one linearity coeff is other than 0 or -1 (and no NaN).

        Public because used by wasatch-shell.
        """
        if self.settings.eeprom.linearity_coeffs:
            for c in self.settings.eeprom.linearity_coeffs:
                if math.isnan(c):
                    return False
            for c in self.settings.eeprom.linearity_coeffs:
                if c != 0 and c != -1:
                    return True
        return False

    def _read_fpga_compilation_options(self):
        if self.settings.is_arm():
            log.debug("ARM spectrometers no longer supporting FPGA compilation options")
            return
            
        response = self.get_upper_code(0x04, label="READ_COMPILATION_OPTIONS", lsb_len=3)
        word = response.data
        self.settings.fpga_options.parse(word)

    # ##########################################################################
    # Accessors
    # ##########################################################################

    # @todo test endian order (in and out)
    def get_battery_register(self, reg: int):
        reg = reg & 0xffff
        return self.get_upper_code(0x14, wIndex=reg, label="GET_BATTERY_REG", msb_len=2)

    def get_battery_state_raw(self):
        """Retrieves the raw battery reading and then caches it for 1 sec"""
        now = datetime.datetime.now()
        if self.settings.state.battery_timestamp is not None and (now - self.settings.state.battery_timestamp).total_seconds() < 1:
            return SpectrometerResponse(data=self.settings.state.battery_raw)

        self.settings.state.battery_timestamp = now
        response = self.get_upper_code(0x13, label="GET_BATTERY_STATE", msb_len=3)
        self.settings.state.battery_raw = response.data

        log.debug(f"battery_state_raw: 0x{self.settings.state.battery_raw:04x}")
        return SpectrometerResponse(data=self.settings.state.battery_raw)

    def get_battery_percentage(self):
        response = self.get_battery_state_raw()
        word = response.data
        lsb = (word >> 16) & 0xff
        msb = (word >>  8) & 0xff
        perc = msb + (1.0 * lsb / 256.0)
        log.debug("battery_perc: %.2f%%", perc)
        return SpectrometerResponse(data=perc)

    def get_battery_charging(self):
        res = self.get_battery_state_raw()
        word = res.data
        charging = (0 != (word & 0xff))
        return SpectrometerResponse(data=charging)

    def get_integration_time_ms(self):
        response = self._get_code(0xbf, label="GET_INTEGRATION_TIME_MS", lsb_len=3)
        ms = response.data

        if self.settings.state.integration_time_ms > 0:
            log.debug(f"GET_INTEGRATION_TIME_MS: now {ms}")
            self.settings.state.integration_time_ms = ms
        else:
            log.debug("declining to initialize session integration_time_ms from spectrometer")

        return SpectrometerResponse(data=ms)

    def set_dfu_enable(self):
        """
        Puts ARM-based spectrometers into Device Firmware Update (DFU) mode.
        @warning reflashing spectrometer firmware without specific instruction and
        support from Wasatch Photonics will void your warranty
        """
        if not self.settings.is_arm():
            msg = "DFU mode only supported for ARM-based spectrometers"
            log.error(msg)
            return SpectrometerResponse(error_lvl=ErrorLevel.low, error_msg=msg, keep_alive=True)

        result = self._send_code(0xfe, label="SET_DFU_ENABLE")

        self.queue_message("marquee_info", "%s in DFU mode" % self.settings.eeprom.serial_number)

        self._schedule_disconnect(Exception("DFU Mode"))
        return SpectrometerResponse(data=result)

    def set_detector_offset(self, value: int):
        word = utils.clamp_to_int16(value)
        self.settings.eeprom.detector_offset = word
        # log.debug("value %d (%s) = 0x%04x (%s)", value, format(value, 'b'), word, format(word, 'b'))
        return self._send_code(0xb6, word, label="SET_DETECTOR_OFFSET")

    def set_detector_offset_odd(self, value: int):
        if not self.settings.is_ingaas():
            log.debug("SET_DETECTOR_OFFSET_ODD only supported on InGaAs")
            return SpectrometerResponse(keep_alive=True, error_lvl=ErrorLevel.low)

        word = utils.clamp_to_int16(value)
        self.settings.eeprom.detector_offset_odd = word

        return self._send_code(0x9c, word, label="SET_DETECTOR_OFFSET_ODD")

    def get_detector_gain(self, update_session_eeprom: bool = False):
        """
        Read the device stored gain.  Convert from binary "half-precision" float.
        - 1st byte (LSB) is binary encoded: bit 0 = 1/2, bit 1 = 1/4, bit 2 = 1/8 etc.
        - 2nd byte (MSB) is the integral part to the left of the decimal
        E.g., 231 dec == 0x01e7 == 1.90234375
        """
        res = self._get_code(0xc5, label="GET_DETECTOR_GAIN")

        result = res.data

        if result is None:
            msg = "GET_DETECTOR_GAIN returned NULL!"
            log.error(msg)
            return SpectrometerResponse(error_lvl=ErrorLevel.medium, error_msg=msg, keep_alive=True)

        lsb = result[0] # little-endian, OPPOSITE of set_detector_gain
        msb = result[1]

        raw = (msb << 8) | lsb

        gain = msb + lsb / 256.0
        log.debug("get_detector_gain: %f (raw 0x%04x) (session eeprom %f)" % (
            gain, raw, self.settings.eeprom.detector_gain))

        if update_session_eeprom:
            self.settings.eeprom.detector_gain = gain

        if self.settings.is_micro():
            self.settings.state.gain_db = gain

        return SpectrometerResponse(data=gain)

    def get_detector_gain_odd(self, update_session_eeprom: bool = False):
        if not self.settings.is_ingaas():
            log.debug("GET_DETECTOR_GAIN_ODD only supported on InGaAs")
            return SpectrometerResponse(data=self.settings.eeprom.detector_gain_odd)

        result = self._get_code(0x9f, label="GET_DETECTOR_GAIN_ODD")

        # not reversing this for XS, because we don't currently have an XS-NIR
        lsb = result[0] # LSB-MSB
        msb = result[1]
        raw = (msb << 8) | lsb

        gain = msb + lsb / 256.0
        log.debug("get_detector_gain_odd: %f (0x%04x) (session eeprom %f)" % (
            gain, raw, self.settings.eeprom.detector_gain_odd))
        if update_session_eeprom:
            self.settings.eeprom.detector_gain_odd = gain
        return SpectrometerResponse(data=gain)

    ##
    # Note that this is used for detector types, including:
    #
    # - Hamamatsu silicon (S16010-*, S16011-*, etc)
    # - Hamamatsu InGaAs (G9214 etc)
    # - Sony IMX (IMX385 etc)
    #
    # It is important to understand that the UNIT of this value changes between
    # Hamamatsu and IMX detectors, but the DATATYPE does not.
    #
    # Reasonable gain levels for Hamamatsu are a floating-point scalar, literally
    # used to scale (gain) the signal, and are usually in the range (0.8 .. 1.2)
    # or thereabouts.
    #
    # Reasonable levels for IMX sensors are in dB and vary by detector, but are
    # usually in the range (0.0 .. 31.0), with exactly 0.1dB precision.  The
    # spectrometer's FW will round to the nearest setting (1.23 will be rounded
    # to 1.2).  IMX sensors switch from "analog gain" to "digital gain" above
    # a given threshold...on the IMX123, analog gain is 0.0 - 31.0, and digital
    # is 31.1 - 72.0 (I think).
    #
    # @see https://wasatchphotonics.com/api/Wasatch.NET/class_wasatch_n_e_t_1_1_funky_float.html
    # @todo we should probably track runtime hardware gain in SpectrometerState,
    #       not EEPROM
    def set_detector_gain(self, gain: float):
        if not self.is_sensor_stable():
            log.error("declining to change sensor settings while stabilizing")
            return

        raw = self.settings.eeprom.float_to_uint16(gain)

        # MZ: note that we SEND gain MSB-LSB, but we READ gain LSB-MSB?!
        log.debug("Send Detector Gain: 0x%04x (%s)", raw, gain)
        result = self._send_code(0xb7, raw, label="SET_DETECTOR_GAIN")

        self.require_throwaway(gain != self.settings.eeprom.detector_gain)
        self.settings.eeprom.detector_gain = gain

        if self.settings.is_xs():
            self.queue_message("marquee_info", "sensor is stabilizing (gain)")
            self.settings.state.gain_db = gain

        return result

    def set_detector_gain_odd(self, gain: float):
        if not self.settings.is_ingaas():
            log.debug("SET_DETECTOR_GAIN_ODD only supported on InGaAs")
            return SpectrometerResponse(error_lvl=ErrorLevel.low, error_msg="SET_DETECTOR_GAIN_ODD only supported on InGaAs")

        raw = self.settings.eeprom.float_to_uint16(gain)

        # MZ: note that we SEND gain MSB-LSB, but we READ gain LSB-MSB?!
        log.debug("Send Detector Gain Odd: 0x%04x (%s)", raw, gain)
        self.settings.eeprom.detector_gain_odd = gain
        return self._send_code(0x9d, raw, label="SET_DETECTOR_GAIN_ODD")

    ##
    # Historically, this opcode moved around a bit.  At one point it was 0xeb
    # (and is now again), which conflicts with CF_SELECT).  At other times it
    # was 0xe9, which conflicted with LASER_RAMP_ENABLE.  This seems to be what
    # we're standardizing on henceforth.
    def set_area_scan_enable(self, flag: bool):
        if self.settings.is_ingaas():
            log.error("area scan is not supported on InGaAs detectors (single line array)")
            return SpectrometerResponse(error_lvl=ErrorLevel.low, error_msg="area scan is not supported on InGaAs detectors (single line array)")

        value = 1 if flag else 0
        self.settings.state.area_scan_enabled = flag
        return self._send_code(0xeb, value, label="SET_AREA_SCAN_ENABLE")

    def get_sensor_line_length(self):
        value = self.get_upper_code(0x03, label="GET_LINE_LENGTH", lsb_len=2)
        if value != self.settings.eeprom.actual_pixels_horizontal:
            log.error("GET_LINE_LENGTH opcode result %d != EEPROM.actual_pixels_horizontal %d (using opcode result)",
                value, self.settings.eeprom.actual_pixels_horizontal)
        return SpectrometerResponse(data=value)

    def get_microcontroller_firmware_version(self):
        if self.settings.microcontroller_firmware_version is not None:
            return SpectrometerResponse(data=self.settings.microcontroller_firmware_version)

        res = self._get_code(0xc0, label="GET_CODE_REVISION")
        result = res.data
        version = "?.?.?.?"
        if result is not None and len(result) >= 4:
            version = "%d.%d.%d.%d" % (result[3], result[2], result[1], result[0]) # MSB-LSB

        self.settings.microcontroller_firmware_version = version
        return SpectrometerResponse(data=version)

    def get_fpga_firmware_version(self):
        if self.settings.fpga_firmware_version is not None:
            return SpectrometerResponse(data=self.settings.fpga_firmware_version)

        s = ""
        res = self._get_code(0xb4, wLength=7, label="GET_FPGA_REV")
        result = res.data
        if result is not None:
            for i in range(len(result)):
                c = result[i]
                if 0x20 <= c < 0x7f: # visible ASCII
                    s += chr(c)

        self.settings.fpga_firmware_version = s
        return SpectrometerResponse(data=s)

    def get_microcontroller_serial_number(self):
        return None # disabling until we work out another Beta SiG conflict

        if not self.settings.is_arm():
            log.debug("GET_MICROCONTROLLER_SERIAL_NUMBER requires ARM")
            return None

        if not self.settings.supports_feature("microcontroller_serial_number"):
            log.debug("GET_MICROCONTROLLER_SERIAL_NUMBER not supported on this firmware")
            return None

        result = self._get_code(0xff, wValue=0x2c, wLength=12, label="GET_MICROCONTROLLER_SERIAL_NUMBER")
        if result is None:
            log.error("GET_MICROCONTROLLER_SERIAL_NUMBER returned None")
            return None

        data = result.data
        if data is None or len(data) != 12:
            log.error("GET_MICROCONTROLLER_SERIAL_NUMBER expected 12 bytes (received {len(data)})")
            return None

        des = "".join(f"{v:02x}" for v in data) # Device Electronic Signature
        self.settings.microcontroller_serial_number = des
        return SpectrometerResponse(data=des)

    def get_ble_firmware_version(self):
        if not self.settings.is_arm():
            log.debug("GET_BLE_FIRMWARE_VERSION requires ARM")
            return None

        result = self._get_code(0xff, wValue=0x2d, wLength=32, label="GET_BLE_FIRMWARE_VERSION")
        if result is None:
            return None

        data = result.data
        if data is None:
            return None

        s = ""
        for c in data:
            if c == 0:
                break
            s += chr(c)
        self.settings.ble_firmware_version = s
        return SpectrometerResponse(data=s)

    def apply_edc(self, spectrum):
        """
        Use optically black pixels for electrical dark correction (EDC).

        @par IMX385LQR-C datasheet (p8)

        @verbatim
        Pixels    Count Description
        0-3       4     OB side ignored area                <-- using this
        4-7       4     Effective pixel side ignored area
        8-15      8     Effective margin for color processing
        16-1935   1920  Recording pixel area
        1936-1944 9     Effective margin for color processing
        1945-1948 4     Effective pixel side ignored area
        1949-1951 3     Dummy
        @endverbatim

        @note that this is called BEFORE horizontal binning, so should still work
              even with BIN_4X2

        @todo we might want to make buffer length configurable, either in spectra
              or by time (consider 10ms vs 1sec integration time)
        """            
        if not self.settings.state.edc_enabled:
            return spectrum

        if not self.settings.is_imx():
            return spectrum

        if len(spectrum) != 1952:
            log.error("IMX EDC hard-coded to expect 1952px")
            return spectrum

        # this averages electrical dark over SPACE
        dark_pixels = spectrum[0:4]
        avg_dark = round(sum(dark_pixels) / len(dark_pixels), 2)

        return [ v - avg_dark for v in spectrum ]

    def get_line(self, trigger: bool = True):
        """
        Send "acquire", then immediately read the bulk endpoint(s).
        Probably the most important method in this class, more commonly called
        "getSpectrum" in most drivers.
        @param trigger (Input) send an initial ACQUIRE
        @returns tuple of (spectrum[], area_scan_row_count) for success
        @returns None when it times-out while waiting for an external trigger
                 (interpret as, "didn't find any fish this time, try again in a bit")
        @returns False (bool) when it times-out or encounters an exception
                 when NOT in external-triggered mode
        @throws exception on timeout (unless external triggering enabled)
        """
        response = SpectrometerResponse()

        if not self.is_sensor_stable():
            response.error_msg = "get_line: leaving sensor alone while stabilizing"
            response.error_lvl = ErrorLevel.low
            response.keep_alive = True
            log.debug(response.error_msg)
            self.queue_message("marquee_info", "sensor is stabilizing (FW)")
            return response

        ########################################################################
        # send the ACQUIRE
        ########################################################################

        # main use-case for NOT sending a trigger would be when reading
        # subsequent lines of data from area scan "fast" mode

        acquisition_timestamp = datetime.datetime.now()
        if trigger and self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_INTERNAL:
            # Only send ACQUIRE (internal SW trigger) if external HW trigger is disabled (default)
            log.debug("get_line: requesting spectrum")
            self._send_code(0xad, label="ACQUIRE_SPECTRUM")

        ########################################################################
        # prepare to read spectrum
        ########################################################################

        # this is the number of pixels we expect to read-out over USB
        if self.settings.is_imx385():
            # Currently, only the IMX385 uses horizontal binning, so for now 
            # limit code changes to that detector (even though they haven't been
            # shown to break other hardware)
            pixels = self.settings.eeprom.actual_pixels_horizontal
        else:
            pixels = self.settings.pixels()

        # when changing detector ROI, exactly ONE READ should be at the previous length
        # if self.prev_pixels is not None:
        #     log.debug(f"get_line: using one-time prev_pixels value of {self.prev_pixels} rather than {pixels}")
        #     pixels = self.prev_pixels
        #     self.prev_pixels = None

        # all models return spectra as [uint16]
        endpoints = [0x82]
        block_len_bytes = pixels * 2
        if pixels == 2048 and not self.settings.is_arm():
            endpoints = [0x82, 0x86]
            block_len_bytes = 2048 # 1024 pixels apiece from two endpoints

        if self.settings.is_micro():
            # we have no idea if Series-XS has to "wake up" the sensor, so wait
            # long enough for 20ms + 8 throwaway frames if need be (IMX385 datasheet p69)
            timeout_ms = self.settings.state.integration_time_ms * 8 + 500 * self.settings.num_connected_devices + 20
        else:
            timeout_ms = self.settings.state.integration_time_ms * 2 + 1000 * self.settings.num_connected_devices

        self._wait_for_usb_available()

        ########################################################################
        # read the data from bulk endpoint(s)
        ########################################################################

        spectrum = []
        errors = 0
        for endpoint in endpoints:
            data = None
            while data is None:
                try:
                    log.debug("waiting for %d bytes (timeout %dms)", block_len_bytes, timeout_ms)
                    data = self.device_type.read(self.device, endpoint, block_len_bytes, timeout=timeout_ms)
                    log.debug("read %d bytes", len(data))
                except Exception as exc:
                    if self.device_type is None:
                        log.error(f"No device_type")
                        response.error_msg = f"Encountered error on read"
                        response.error_lvl = ErrorLevel.high
                        response.poison_pill = True # unrecoverable
                        return response
                    elif self.settings.state.trigger_source == SpectrometerState.TRIGGER_SOURCE_EXTERNAL:
                        # we don't know how long we'll have to wait for the trigger, so
                        # just loop and hope
                        # log.debug("still waiting for external trigger")
                        pass
                    else:
                        errors += 1
                        log.error(f"Encountered error on read of {exc}")
                        if errors < 3:
                            log.error(f"ignoring error number {errors}")
                        else:
                            response.error_msg = "Encountered error on read"
                            response.error_lvl = ErrorLevel.high
                            return response

            # This is a convoluted way to iterate across the received bytes in 'data' as
            # two interleaved arrays, both only processing alternating bytes, but one (i)
            # starting at zero (even bytes) and the other (j) starting at 1 (odd bytes).
            subspectrum = [int(i | (j << 8)) for i, j in zip(data[::2], data[1::2])] # LSB-MSB

            spectrum.extend(subspectrum)

            # empirically determined need for 5ms delay when switching endpoints
            # on 2048px detectors during area scan
            if self.settings.state.area_scan_enabled and pixels == 2048: # and endpoint == 0x82:
                log.debug("sleeping 5ms between endpoints")
                sleep(0.005)

        # received a response, so decrement throwaways
        self.remaining_throwaways = max(0, self.remaining_throwaways - 1)

        ########################################################################
        # error-check the received spectrum
        ########################################################################

        log.debug("get_line: completed in %d ms (vs integration time %d ms)",
            round((datetime.datetime.now() - acquisition_timestamp).total_seconds() * 1000, 0),
            self.settings.state.integration_time_ms)

        log.debug("get_line: pixels %d, endpoints %s, block %d, spectrum %s ...",
            len(spectrum), endpoints, block_len_bytes, spectrum[0:9])

        if len(spectrum) != pixels:
            log.error("get_line read wrong number of pixels (expected %d, read %d)", pixels, len(spectrum))
            response.error_msg = f"get_line read wrong number of pixels (expected {pixels}, read {len(spectrum)})"
            response.error_lvl = ErrorLevel.low
            response.keep_alive = True
            return response

        ########################################################################
        #
        #                   post-process the spectrum
        #
        ########################################################################

        ########################################################################
        # Apply InGaAs even/odd gain/offset in software
        ########################################################################

        # (before x-axis inversion because this is where FPGA will do it)

        # this should be done in the FPGA, but older FW didn't have that
        # implemented, so fix in SW unless EEPROM indicates "already handled"
        if self.settings.is_ingaas() and not self.settings.eeprom.hardware_even_odd:
            self._correct_ingaas_gain_and_offset(spectrum)

        ########################################################################
        # Area Scan (rare)
        ########################################################################

        # (before x-axis inversion because line index at FPGA pixel 0)

        # If we're in area scan mode, use first pixel as row index (leave pixel 
        # in spectrum).  Do this before any horizontal averaging which might 
        # corrupt first pixel).  Note that InGaAs don't support DetectorRegions.
        area_scan_row_count = -1
        if self.settings.state.area_scan_enabled:
            area_scan_row_count = spectrum[0]

            # for i in range(4):
            #     spectrum[i] = spectrum[4] # KLUDGE: NRD-dual

            # Leave the row counter in place if we're in "Fast" Area Scan mode,
            # since downstream software can use it to assemble the final image.
            # ("Slow" Area Scan mode sent this value back as a separate field in
            # the Reading, but this isn't possible in Fast mode.  Just delete
            # Slow mode when Fast is widely deployed.)
            #
            if not self.settings.state.area_scan_fast:
                spectrum[0] = spectrum[1]

        ########################################################################
        # Start-of-Spectrum Marker (rare)
        ########################################################################

        # (before x-axis inversion because marker at FPGA pixel 0)

        # Check and track the "start of spectrum" marker.  This is very rare and
        # only for experimental units.  Currently Wasatch does not have any
        # "standard" spectrum framing data, although such would be useful. This
        # is only enabled in FPGAs when trying to debug rare timing issues. The
        # "Marker" is simply a pixel with the value 0xffff.  On FPGA FW where the
        # marker is enabled, spectral data is clamped to 0xfffe, meaning such
        # markers can ONLY appear as the first pixel in a spectrum.
        #
        # Would need updated to work with DetectorRegions.
        if self.settings.has_marker() and not self.settings.state.area_scan_enabled:
            marker = 0xffff
            if spectrum[0] == marker:
                # marker found where expected, so all is good (overwrite for a
                # clean graph)
                spectrum[0] = spectrum[1]
            else:
                # we DIDN'T find the marker where it was expected, so flag and
                # go hunting
                log.error("get_line: missing marker")
                for i in range(pixels):
                    if spectrum[i] == marker:
                        log.error("get_line: marker found at pixel %d", i)

        # consider skipping much of the following if in area scan mode

        ########################################################################
        # Stomp array ends
        ########################################################################

        # (before x-axis inversion because optically masked pixels are physical)

        # some detectors have "garbage" pixels at the front or end of every
        # spectrum (sync bytes and what-not)
        if self.settings.is_imx392() and self.settings.state.detector_regions is None:
            utils.stomp_first(spectrum, 3)
            utils.stomp_last (spectrum, 17)

        ########################################################################
        # Electrical Dark Correction (EDC, experimental)
        ########################################################################

        spectrum = self.apply_edc(spectrum)

        ########################################################################
        # Invert X-Axis
        ########################################################################

        # For benches where the detector is essentially rotated 180-deg from our
        # typical orientation with regard to the grating (e.g. InGaAs OEM
        # benches, where red wavelengths are diffracted toward pixel 0, and blue
        # wavelengths toward pixel 511).
        #
        # Note this simply performs a horizontal FLIP (mirror) of the vertically-
        # binned 1-D spectra, and is NOT sufficient to perform a genuine 180-
        # degree rotation of 2-D imaging mode; if "authentic" area scan is
        # desired, the caller would likewise need to reverse the display order of
        # the rows.
        #
        #           It is VITALLY IMPORTANT that all drivers agree
        #            on when to perform X-Axis inversion in the
        #                        Order of Operations.
        #
        if self.settings.eeprom.invert_x_axis and not self.settings.state.area_scan_enabled:
            spectrum.reverse()

        ########################################################################
        # ignore isolated "flat" spectra on SiG
        ########################################################################

        if self.settings.is_micro() and utils.all_same(spectrum):
            response.error_msg = "skipping flat spectrum"
            response.error_lvl = ErrorLevel.low
            response.keep_alive = True
            log.debug(response.error_msg)
            self.queue_message("marquee_info", "sensor is stabilizing (constant)")
            return response

        ########################################################################
        # Bad Pixel Correction
        ########################################################################

        # Note these are pre-horizontal binning...
        if self.settings.state.bad_pixel_mode == SpectrometerState.BAD_PIXEL_MODE_AVERAGE:
            self._correct_bad_pixels(spectrum)

        ########################################################################
        # Swap Alternating Pixels (very rare)
        ########################################################################

        # a prototype model output spectra with alternating pixels swapped, and
        # this was quicker than changing in firmware

        if self.settings.state.swap_alternating_pixels and self.settings.state.detector_regions is None:
            log.debug("swapping alternating pixels: spectrum = %s", spectrum[:10])
            corrected = []
            for a, b in zip(spectrum[0::2], spectrum[1::2]):
                corrected.extend([b, a])
            spectrum = corrected
            log.debug("swapped alternating pixels: spectrum = %s", spectrum[:10])

        ########################################################################
        # horizontal binning
        ########################################################################

        spectrum = self._apply_horizontal_binning(spectrum)

        # * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * 
        # Note: len(spectrum) may no longer == eeprom.actual_pixels_horizontal!
        # * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * 

        ########################################################################
        # Graph Alternating Pixels
        ########################################################################

        # When integrating new sensors, or testing "interleaved" detectors like
        # the InGaAs, sometimes we want to only look at "every other" pixel to
        # flatten-out irregularities in Bayer filters or photodiode arrays.
        # However, we don't want to disrupt the expected pixel-count, so just
        # average-over the skipped pixels.
        #
        # Not important enough to update for DetectorRegions
        if self.settings.state.graph_alternating_pixels:
            log.debug("applying graph_alternating_pixels")
            smoothed = []
            for i in range(len(spectrum)):
                if i % 2 == 0:
                    smoothed.append(spectrum[i])
                else:
                    if i + 1 < len(spectrum):
                        averaged = (spectrum[i-1] + spectrum[i+1]) / 2.0
                    else:
                        averaged = spectrum[i - 1]
                    smoothed.append(averaged)
            spectrum = smoothed

        # Somewhat oddly, we're currently returning a TUPLE of the spectrum and
        # the area scan row count.  When "Fast" Area Scan is more commonplace 
        # we'll change this back to just returning the spectrum array directly.
        response.data = SpectrumAndRow(spectrum, area_scan_row_count) 
        return response

    def require_throwaway(self, flag):
        if flag and self.settings.is_xs() and self.remaining_throwaways < 1:
            log.debug("queuing throwaways")
            self.remaining_throwaways += 1

    def set_integration_time_ms(self, ms: float):
        """
        Send the updated integration time in a control message to the device
        @warning disabled EEPROM range-checking by customer
             request; range limits in EEPROM are defined as 16-bit
             values, while integration time is actually a 24-bit value,
             such that the EEPROM is artificially limiting our range.

        @todo SiG needs to wait 20ms + 8 frames for stabilization.
        """
        if not self.is_sensor_stable():
            log.error("declining to change sensor settings while stabilizing")
            return

        ms = max(1, int(round(ms)))

        lsw =  ms        & 0xffff
        msw = (ms >> 16) & 0x00ff

        result = self._send_code(0xB2, lsw, msw, label="SET_INTEGRATION_TIME_MS")
        log.debug("SET_INTEGRATION_TIME_MS: now %d", ms)

        self.require_throwaway(ms != self.settings.state.integration_time_ms)
        self.settings.state.integration_time_ms = ms

        if self.settings.is_xs():
            self.queue_message("marquee_info", "sensor is stabilizing (int time)")

        return result

    def is_sensor_stable(self):
        if not (self.settings.is_imx() and self.settings.supports_feature("imx_stabilization")):
            return True

        self.get_poll_status()
        stable = PollStatus.IDLE == self.settings.state.poll_status
        # log.debug(f"is_sensor_stable: sensor {'IS' if stable else 'IS NOT'} stable (poll_status 0x{self.settings.state.poll_status:02x})")
        return stable

    def get_poll_status(self):
        status = PollStatus.IDLE # .UNDEFINED (kludge)
        result = SpectrometerResponse(data=status)

        if False and self.settings.is_imx() and self.settings.supports_feature("imx_stabilization"):
            result = self._get_code(0xd4, lsb_len=1, label="GET_POLL_STATUS")
            if result is not None:
                status = result.data
        self.settings.state.poll_status = status
        # log.debug(f"get_poll_status: status 0x{status:02x}")
        return result

    # ##########################################################################
    # ADCs / DACs
    # ##########################################################################

    # This is a little confusing, so:
    #
    # The "primary" ADC (ADC 0) is assumed to return laser temperature.
    #
    # The "secondary" ADC (ADC 1) may be wired to various other things for
    # legacy or OEM systems. For instance, it has been used with an integrated
    # photodiode for real-time measurement of laser output power (with or
    # without a calibration to convert the photodiode result to mW, hence
    # get_secondary_adc_calibrated).
    #
    # Most users will never need to explicitly call select_adc, and can simply
    # call get_laser_temperature_raw and/or get_laser_temperature_degC.

    def get_dac(self, dacIndex: int = 0):
        return self._get_code(0xd9, wIndex=dacIndex, label="GET_DAC", lsb_len=2)

    def select_adc(self, n: int):
        log.debug("select_adc -> %d", n)
        self.settings.state.selected_adc = n
        result = self._send_code(0xed, n, label="SELECT_ADC")
        self._get_code(0xd5, wLength=2, label="GET_ADC (throwaway)") # stabilization read
        return result

    def get_secondary_adc_calibrated(self, raw: float = None):
        if not self.has_linearity_coeffs():
            log.debug("secondary_adc_calibrated: no calibration")
            return SpectrometerResponse(data=None, error_lvl=ErrorLevel.low, error_msg="secondary_adc_calibrated: no calibration")

        if raw is None:
            response = self.get_secondary_adc_raw()
        if response.data is None:
            return response

        raw = float(response.data)

        # use the first 4 linearity coefficients as a 3rd-order polynomial
        calibrated = float(self.settings.eeprom.linearity_coeffs[0]) \
                   + float(self.settings.eeprom.linearity_coeffs[1]) * raw \
                   + float(self.settings.eeprom.linearity_coeffs[2]) * raw * raw \
                   + float(self.settings.eeprom.linearity_coeffs[3]) * raw * raw * raw
        log.debug("secondary_adc_calibrated: %f", calibrated)
        response.data = calibrated
        return response

    def get_secondary_adc_raw(self):
        # flip to secondary ADC if needed
        if self.settings.state.selected_adc is None or self.settings.state.selected_adc != 1:
            self.select_adc(1)

        result = self._get_code(0xd5, wLength=2, label="GET_ADC", lsb_len=2)
        log.debug("secondary_adc_raw: 0x%04x", result)
        return result

    # ##########################################################################
    # Laser Temperature
    # ##########################################################################

    def set_laser_tec_mode(self, mode):
        if not self.settings.is_xs():
            msg = "Laser TEC mode only applicable on XS"
            log.error(msg)
            return SpectrometerResponse(data=None, error_lvl=ErrorLevel.low, error_msg=msg)

        if isinstance(mode, str):
            mode = mode.upper()
            if mode == "OFF": 
                mode = 0
            elif mode == "ON":  
                mode = 1
            elif mode == "AUTO": 
                mode = 2
            elif mode == "AUTO_ON": 
                mode = 3
            else: 
                mode = 0

        mode = min(3, max(0, int(mode)))
        self.settings.state.laser_tec_mode = mode
        return self._send_code(0x84, mode, label="SET_LASER_TEC_MODE")

    def get_laser_tec_mode(self):
        """ 
        The current opcode (0x85) seems to be actually returning 
        GET_LASER_TEC_ENABLED, not GET_LASER_TEC_MODE. 
        """
        if not self.settings.is_xs():
            msg = "Laser TEC mode only applicable on XS"
            log.error(msg)
            return SpectrometerResponse(data=None, error_lvl=ErrorLevel.low, error_msg=msg)

        result = self._get_code(0x85, wLength=1, label="GET_LASER_TEC_MODE", lsb_len=1)
        if result is None or result.data < 0 or result.data > 3:
            msg = f"get_laser_tec_mode: invalid mode {result}"
            log.error(msg)
            return SpectrometerResponse(data=None, error_lvl=ErrorLevel.low, error_msg=msg)

        # mode = result.data
        # names = [ 'OFF', 'ON', 'AUTO', 'AUTO_ON' ]
        # log.debug(f"get_laser_tec_mode: mode {names[mode]}")
        # self.settings.state.laser_tec_mode = mode 

        enabled = 0 != result.data
        # log.debug(f"get_laser_tec_mode: enabled {enabled}")
        self.settings.state.laser_tec_enabled = enabled

        return result

    ##
    # Laser temperature conversion doesn't use EEPROM coeffs at all.
    #
    # Wasatch SML products use an IPS Wavelength-Stabilized TO-56
    # laser, which internally uses a Betatherm 10K3CG3 thermistor.
    #
    # @see https://www.ipslasers.com/data-sheets/SM-TO-56-Data-Sheet-IPS.pdf
    #
    # The official conversion from thermistor resistance (in ohms) to degC is:
    #
    # \verbatim
    # 1 / (   C1
    #       + C2 * ln(ohms)
    #       + C3 * pow(ln(ohms), 3)
    #     )
    # - 273.15
    #
    # Where: C1 = 0.00113
    #        C2 = 0.000234
    #        C3 = 8.78e-8
    # \endverbatim
    #
    # That said, on XS the IPS thermistor then goes through a MAX1978ETM-T
    # TEC IC which buffers the thermistor voltage. This requires the additional
    # empirically-determined static conversion captured below.
    #
    # Regrettably, the laser thermistor voltage on X series spectrometers
    # overflows the ADC range, so we don't currently have valid laser 
    # temperature read-out on X. 
    #
    # Also, on MML units using the Ondax OEM Module, the cable to the 220060 
    # does not seem to pass-through the thermistor pin, so we don't have
    # laser temperature read-out on those, either. (Nor photodiode...)
    #
    # @param raw    the value read from the thermistor's 12-bit ADC (can be
    #               uint12 or SpectrometerResponse)
    def get_laser_temperature_degC(self, raw=None):
        if isinstance(raw, SpectrometerResponse):
            raw = raw.data
        if raw is None:
            raw = self.get_laser_temperature_raw()

        if raw is None:
            msg="get_laser_temperature_degC: error reading raw laser temperature"
            log.error(msg)
            return SpectrometerResponse(error_lvl=ErrorLevel.low, error_msg=msg)

        if raw > 0xfff:
            msg = f"get_laser_temperature_degC: raw value 0x{raw:x} exceeds 12 bits"
            log.error(msg)
            return SpectrometerResponse(data=None, error_lvl=ErrorLevel.low, error_msg=msg)

        if raw == 0:
            msg = "get_laser_temperature_degC: can't take log of raw ADC value 0"
            # log.debug(msg)
            return SpectrometerResponse(data=None) # not propogating error_msg for now

        degC = 0
        try:
            if self.settings.is_xs():
                # conversion for 220250 Rev4 MAX1978ETM-T 3V3 buffer -> 12-bit DAC -> degC
                coeffs = [ 1.5712971947853123e+000,
                           1.4453391889061071e-002,
                          -1.8534086153440592e-006,
                           4.2553356470494626e-010 ]
                for i, coeff in enumerate(coeffs):
                    degC += coeff * pow(raw, i)
                    # log.debug(f"get_laser_temperature_degC: degC {degC:g} after adding {coeff} x (pow({raw}, {i}) = {pow(raw, i):g})")
            else:
                # when not using TEC IC
                voltage    = 2.5 * raw / 4096
                resistance = 21450.0 * voltage / (2.5 - voltage) # LB confirmed

                if resistance < 0:
                    msg="get_laser_temperature_degC: can't compute degC: raw = 0x%04x, voltage = %f, resistance = %f" % (
                        raw, voltage, resistance)
                    log.error(msg)
                    return SpectrometerResponse(data=None, error_level=ErrorLevel.low, error_msg=msg)

                logVal     = math.log(resistance / 10000.0)
                insideMain = logVal + 3977.0 / (25 + 273.0)
                degC       = 3977.0 / insideMain - 273.0

            log.debug("Laser temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        except:
            msg = "exception computing laser temperature"
            log.error(msg, exc_info=1)
            return SpectrometerResponse(data=None, error_level=ErrorLevel.low, error_msg=msg)

        return SpectrometerResponse(data=degC)

    # ##########################################################################
    # Detector Temperature
    # ##########################################################################

    ## @note big-endian, reverse of get_laser_temperature_raw
    def get_detector_temperature_raw(self):
        return self._get_code(0xd7, label="GET_CCD_TEMP", msb_len=2)

    def get_detector_temperature_degC(self, raw: float = None):
        if raw is None:
            raw = self.get_detector_temperature_raw().data

        if raw is None:
            return raw

        degC = self.settings.eeprom.adc_to_degC_coeffs[0]             \
             + self.settings.eeprom.adc_to_degC_coeffs[1] * raw       \
             + self.settings.eeprom.adc_to_degC_coeffs[2] * raw * raw
        log.debug("Detector temperature: %.2f deg C (0x%04x raw)" % (degC, raw))
        return SpectrometerResponse(data=degC)

    def set_detector_tec_setpoint_degC(self, degC: float):
        """
        Attempt to set the CCD cooler setpoint. Verify that it is within an
        acceptable range. Ideally this is to prevent condensation and other
        issues. This value is a default and is hugely dependent on the
        environmental conditions.
        """
        if not self.settings.eeprom.has_cooling:
            log.error("unable to control TEC: EEPROM reports no cooling")
            return SpectrometerResponse(data=False, error_lvl=ErrorLevel.low, error_msg="unable to control TEC: EEPROM reports no cooling")

        if degC < self.settings.eeprom.min_temp_degC:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f below min %f", degC, self.settings.eeprom.min_temp_degC)
            return SpectrometerResponse(data=False, error_lvl=ErrorLevel.low, error_msg="setpoint below minimum")

        if degC > self.settings.eeprom.max_temp_degC:
            log.critical("set_detector_tec_setpoint_degC: setpoint %f exceeds max %f", degC, self.settings.eeprom.max_temp_degC)
            return SpectrometerResponse(data=False, error_lvl=ErrorLevel.low, error_msg="setpoint beyond max")

        raw = int(round(self.settings.eeprom.degC_to_dac_coeffs[0]
                      + self.settings.eeprom.degC_to_dac_coeffs[1] * degC
                      + self.settings.eeprom.degC_to_dac_coeffs[2] * degC * degC))

        # ROUND (don't mask) to 12-bit DAC
        raw = max(0, min(raw, 0xfff))

        log.debug("Set CCD TEC Setpoint: %.2f deg C (raw ADC 0x%04x)", degC, raw)
        ok = self._send_code(0xd8, raw, label="SET_DETECTOR_TEC_SETPOINT")
        self.settings.state.tec_setpoint_degC = degC
        self.detector_tec_setpoint_has_been_set = True
        return ok

    def get_detector_tec_setpoint_degC(self):
        if self.detector_tec_setpoint_has_been_set:
            return SpectrometerResponse(self.settings.state.tec_setpoint_degC)
        log.error("Detector TEC setpoint has not yet been applied")
        return SpectrometerResponse(data=0.0)

    def get_detector_tec_setpoint_raw(self):
        return self.get_dac(0)

    ## @todo rename set_detector_tec_enable
    def set_tec_enable(self, flag: bool):
        if not self.settings.eeprom.has_cooling:
            log.debug("unable to control TEC: EEPROM reports no cooling")
            return SpectrometerResponse(data=False, error_msg="unable to control TEC: EEPROM reports no cooling")

        if not self.settings.eeprom.has_detector_tec_calibration():
            log.debug("unable to control TEC: EEPROM missing valid TEC calibration")
            return SpectrometerResponse(data=False, error_msg="unable to control TEC: EEPROM missing valid TEC calibration")

        value = 1 if flag else 0

        if not self.detector_tec_setpoint_has_been_set:

            # @todo should this not be eeprom.startup_temp_degC
            log.debug("defaulting TEC setpoint to min %s", self.settings.eeprom.min_temp_degC)
            self.set_detector_tec_setpoint_degC(self.settings.eeprom.min_temp_degC)

        log.debug("Send detector TEC enable: %s", value)
        ok = self._send_code(0xd6, value, label="SET_DETECTOR_TEC_ENABLE")
        if ok.data:
            self.settings.state.tec_enabled = flag
        return ok

    # ##########################################################################
    # Triggering
    # ##########################################################################

    def set_trigger_source(self, value: bool):
        """
        Set the source for incoming acquisition triggers.
        @param value either 0 for "internal" or 1 for "external"
        With internal triggering (the default), the spectrometer expects the
        USB host to explicitly send a START_ACQUISITION (ACQUIRE) opcode to
        begin each integration.  In external triggering, the spectrometer
        waits for the rising edge on a signal connected to a pin on the OEM
        accessory connector.
        Technically on ARM, the microcontroller is continuously monitoring
        both the external pin and listening for internal software opcodes.
        On the FX2 you need to explicitly place the microcontroller into
        external triggering mode to avail the feature.
        """
        self.settings.state.trigger_source = value
        log.debug("trigger_source now %s", value)

        # Don't send the opcode on ARM. See issue #2 on WasatchUSB project
        if self.settings.is_arm():
            return SpectrometerResponse(data=False)

        msb = 0
        lsb = value
        buf = [0] * 8

        # MZ: this is weird...we're sending the buffer on an FX2-only command
        return self._send_code(0xd2, lsb, msb, buf, label="SET_TRIGGER_SOURCE")

    # ##########################################################################
    # High-Gain Mode
    # ##########################################################################

    ##
    # CF_SELECT is configured using bit 2 of the FPGA configuration register
    # 0x12.  This bit can be set using vendor commands 0xeb to SET and 0xec
    # to GET.  Note that the set command is expecting a 5-byte unsigned
    # value, the highest byte of which we pass as part of an 8-byte buffer.
    # Not sure why.
    def set_high_gain_mode_enable(self, flag: bool):
        log.debug("Set high gain mode: %s", flag)
        if not self.settings.is_ingaas():
            log.debug("SET_HIGH_GAIN_MODE_ENABLE only supported on InGaAs")
            return SpectrometerResponse(data=False, error_msg="High Gain not support on this spectrometer")

        value = 1 if flag else 0

        # this is done automatically on ARM, but for this opcode we do it on FX2 as well
        buf = [0] * 8

        self.settings.state.high_gain_mode_enabled = flag
        return self._send_code(0xeb, wValue=value, wIndex=0, data_or_wLength=buf, label="SET_HIGH_GAIN_MODE_ENABLE")

    def get_high_gain_mode_enabled(self):
        if not self.settings.is_ingaas():
            log.debug("GET_HIGH_GAIN_MODE_ENABLE only supported on InGaAs")
            return SpectrometerResponse(self.settings.eeprom.high_gain_mode_enabled)

        self.settings.state.high_gain_mode_enabled = 0 != self._get_code(0xec, lsb_len=1, label="GET_HIGH_GAIN_MODE_ENABLED")
        return SpectrometerResponse(self.settings.state.high_gain_mode_enabled)

    ############################################################################
    # Laser commands
    ############################################################################

    def get_opt_laser_control(self):
        return self.get_upper_code(0x09, label="GET_OPT_LASER_CONTROL", msb_len=1)

    def get_opt_has_laser(self):
        value = self.get_upper_code(0x08, label="GET_OPT_HAS_LASER", msb_len=1).data
        available = 0 != value
        if available != self.settings.eeprom.has_laser:
            log.error("OPT_HAS_LASER opcode result %s != EEPROM has_laser %s (using opcode)",
                value, self.settings.eeprom.has_laser)
        return SpectrometerResponse(data=available)

    def get_laser_temperature_raw(self):
        """ @note little-endian, reverse of get_detector_temperature_raw """
        # flip to primary ADC if needed
        if self.settings.state.selected_adc is None or self.settings.state.selected_adc != 0:
            # MZ: unclear if this is needed on SiG
            self.select_adc(0)

        result = self._get_code(0xd5, wLength=2, label="GET_ADC", lsb_len=2)
        if not result.data:
            log.debug("Unable to read laser temperature")
            return SpectrometerResponse(0)
        result.data = result.data & 0xfff # clamp to 12-bit
        return result

    def set_selected_laser(self, value: int):
        """
        On spectrometers supporting two lasers, select the primary (0) or
        secondary (1).  Laser Enable, laser power etc should all then
        affect the currently-selected laser.
        @warning conflicts with GET_RAMAN_MODE_ENABLE
        """
        if not self.settings.eeprom.has_laser:
            msg = "no laser installed"
            log.error(msg)
            return SpectrometerResponse(data=False, error_msg=msg)

        n = 1 if value else 0
        log.debug("selecting laser %d", n)
        self.settings.state.selected_laser = n

        return self._send_code(bRequest        = 0xff,
                               wValue          = 0x15,
                               wIndex          = n,
                               data_or_wLength = [0] * 8,
                               label           = "SET_SELECTED_LASER")

    def get_selected_laser(self):
        return SpectrometerResponse(data=self.settings.state.selected_laser)

    def get_laser_enabled(self):
        if self.settings.is_xs():
            # MZ: get_laser_enabled not currently robust on SiG :-(
            return SpectrometerResponse(data=self.settings.state.laser_enabled)

        res = self._get_code(0xe2, label="GET_LASER_ENABLED", msb_len=1)
        flag = 0 != res.data

        log.debug(f"get_laser_enabled: {flag} (storing to {self.settings.state})")
        self.settings.state.laser_enabled = flag
        return SpectrometerResponse(data=flag)

    def set_laser_enable(self, flag: bool):
        """
        Turn the laser on or off.
        If laser power hasn't yet been externally configured, applies the default
        of full-power.  The new state will be applied immediately.
        @param flag (Input) bool (True turns laser on, False turns laser off)
        @returns whether the new state was applied
        """
        if not self.settings.eeprom.has_laser:
            msg = "no laser installed"
            log.error(msg)
            return SpectrometerResponse(data=False, error_msg=msg)

        # ARM seems to require that laser power be set before the laser is enabled
        if self.next_applied_laser_power is None:
            self.set_laser_power_perc(100.0)

        self.settings.state.laser_enabled = flag
        self._set_laser_enable_immediate(flag)
        return SpectrometerResponse(data=True)

    def _set_laser_enable_immediate(self, flag: bool):
        """
        The user has requested to update the laser firing state (on or off),
        so apply the new laser state to the spectrometer immediately.

        Because the ability to immediately disable a laser is a safety-related
        feature (noting that truly safety-critical capabilities should be
        implemented in hardware, and generally can't be robustly achieved through
        Python scripts), this function takes the unusual step of looping over
        multiple attempts to set the laser state until either the command succeeds,
        or 3 consecutive failures have occured.

        This behavior was added after a developmental, unreleased prototype was
        found to occasionally drop USB packets, and was therefore susceptible to
        inadvertently failing to disable the laser upon command.

        @private (as callers are recommended to use set_laser_enable)
        @param flag (Input) whether the laser should be on (true) or off (false)
        @returns true if new state was successfully applied
        """
        log.debug("Send laser enable: %s", flag)
        if flag:
            self.last_applied_laser_power = 0.0
        else:
            self.last_applied_laser_power = self.next_applied_laser_power

        self.set_strobe_enable(flag)

        if self.settings.is_xs():
            # Series-XS getLaserEnable doesn't provide immediate confirmation 
            # because it's comingled with laserWatchdogSec and probably 
            # laserDelaySec
            return SpectrometerResponse(data=True)

        # try to verify laserEnable worked
        tries = 0
        while True:
            res = self.get_laser_enabled()
            if flag == res.data:
                return SpectrometerResponse(data=True)
            tries += 1
            if tries > 3:
                log.critical("laser_enable %s command failed, giving up", flag)
                self.queue_message("marquee_error", "laser setting failed")
                return SpectrometerResponse(data=False, error_msg="laser setting failed", error_lvl=ErrorLevel.medium)
            else:
                log.error("laser_enable %s command failed, re-trying", flag)
                self.set_strobe_enable(flag)

    def set_laser_power_mW(self, mW_in: int):
        if mW_in is None or not self.settings.eeprom.has_laser_power_calibration():
            log.error("EEPROM doesn't have laser power calibration")
            self.settings.state.laser_power_mW = 0
            self.settings.state.laser_power_perc = 0
            self.settings.state.use_mW = False
            return SpectrometerResponse(data=False, error_msg="no laser power calibration")

        mW = min(self.settings.eeprom.max_laser_power_mW, max(self.settings.eeprom.min_laser_power_mW, mW_in))

        perc = self.settings.eeprom.laser_power_mW_to_percent(mW)
        log.debug("set_laser_power_mW: range (%.2f, %.2f), requested %.2f, approved %.2f, percent = %.2f",
            self.settings.eeprom.min_laser_power_mW,
            self.settings.eeprom.max_laser_power_mW,
            mW_in,
            mW,
            perc)
        self.settings.state.laser_power_mW = mW
        return self.set_laser_power_perc(perc, set_in_perc=False)

    ##
    # @brief Sets whether the PWM pulse period is 1000µs (high-resolution, default)
    # or 100µs (low-resolution, legacy).
    #
    # @param flag (Input) True for 1000µs PWM period (high-resolution, default), 
    #        False for 100µs (low-resolution)
    # 
    # Laser power is controlled via PWM (Pulse Width Modulation), essentially 
    # a square wave whose "duty cycle" (high vs low), expressed as a percentage,
    # represents the amount of time the laser is actually firing. If the duty
    # cycle is 33%, the laser will be firing 1/3 of the time, representing
    # approximately one-third of "full power".  
    # 
    # (It's not "exactly" one-third because the laser needs a moment to 
    # stablize when energized, and that "start-up" instability is increased 
    # when you're constantly pulsing the laser on-and-off.)
    # 
    # This function is used to determine the LENGTH (period) of that PWM
    # square wave.  Because the laser's PWM parameters are all specified in
    # microseconds (µs), a longer period allows greater precision (resolution)
    # in specifying the duty cycle.
    # 
    # Historically, Wasatch spectrometers used a PWM period of 100µs, meaning
    # the pulse width (time each wave was high) could only be set from 1-99µs,
    # giving an essential laser power resolution of 1%.  That behavior can
    # be restored by setting this value False.
    # 
    # More recently, we've increased the default PWM pulse period to 1000µs 
    # (1ms). Since the pulse width is still set in µs, that allows an
    # effective laser power resolution of 0.1%.  This is the new default,
    # and can be explicitly requested by setting this value to True.
    #
    # @warning Note that this function is provided for cases where the laser power 
    # is set as a percentage.  When setting laser power through milliWatts, using
    # the onboard laser power calibration, it is important to use the same
    # resolution as was in effect when the calibration was generated.  All Wasatch
    # laser power calibrations are generated in "high-resolution," so this 
    # function SHOULD NOT be set "False" (low-res) if setting laser power in mW.
    def set_laser_power_high_resolution(self, flag: bool):
        self.settings.state.laser_power_high_resolution = True if flag else False
        return SpectrometerResponse()

    def set_laser_power_require_modulation(self, flag: bool):
        self.settings.state.laser_power_require_modulation = True if flag else False
        return SpectrometerResponse()

    ##
    # @todo support floating-point value, as we have a 12-bit ADC and can provide
    # a bit more precision than 100 discrete steps (goal to support 0.1 - .125% resolution)
    def set_laser_power_perc(self, value_in: float, set_in_perc: bool = True):
        if not self.settings.eeprom.has_laser:
            msg = "no laser installed"
            log.error(msg)
            return SpectrometerResponse(data=False, error_msg=msg)

        value = float(max(0, min(100, value_in)))
        self.settings.state.laser_power_perc = value
        log.debug("set_laser_power_perc: range (0, 100), requested %.2f, applying %.2f", value_in, value)

        if set_in_perc:
            # apparently the laser power was explicitly commanded as a percentage
            # of full, and not "computed from mW" using a calibration, so clear
            # the mW setpoint
            self.settings.state.laser_power_mW = 0

        return self.set_laser_power_perc_immediate(value)

    ##
    # Sets laser power (modulated pulse width) which will be used next time the
    # laser is turned on (or changed immediately, if the laser is already enabled).
    # Laser power is determined by a combination of the pulse width,
    # period and modulation being enabled. There are many combinations of
    # these values that will produce a given percentage of the total laser
    # power through pulse width modulation. There is no 'get laser power'
    # control message on the device.
    #
    # Some of the goals of Enlighten are for it to be stable, and a reason
    # we have sales. During spectrometer builds, it was discovered that
    # the laser power settings were not implemented. During the
    # implementation process, it was discovered that the laser modulation,
    # pulse period and pulse width commands do not conform to
    # specification. Where you can set integration time 100 ms with the
    # command:
    #
    # device.ctrl_transfer(bmRequestType=device_to_host,
    #                      bRequest=0xDB,
    #                      wValue=100,
    #                      wIndex=0,
    #                      data_or_wLength=0)
    #
    # The laser pulse period must be set where the wValue and
    # data_or_wLength parameters are equal. So if you wanted a pulse
    # period of 100, you must specify the value in both places:
    #
    # ...
    #                      wValue=100,
    #                      data_or_wLength=100)
    # ...
    #
    # This in turn implies that the legacy firmware has a long masked
    # issue when reading the value to update from the data_or_wLength
    # parameter instead of the wValue field. This is only accurate for the
    # laser modulation related functions.
    #
    # This is backed up by the Dash v3 StrokerControl DLL implementation.
    # It was discovered that the StrokerControl DLL sets the wValue and
    # data or wLength parameters to the same value at every control
    # message write.
    #
    # The exciting takeaway here is that Enlighten is stable enough.
    # Turning the laser on with the data or wLength parameter not set
    # correctly will cause a hardware failure and complete device lockup
    # requiring a power cycle.
    #
    # fid:
    #     CRITICAL Hardware Failure FID Send Code Problem with
    #              ctrl transfer: [Errno None] 11
    #
    # Unlike Dash which may lockup and require killing the application,
    # Enlighten does not lock up. The Enlighten code base has now been
    # used to unmask an issue that has been lurking with our legacy
    # firmware for close to 6 years. We've detected this out of
    # specification area of the code before it can adversely impact a
    # customer. """
    #
    # As long as laser power is modulated using a period of 100us,
    # with a necessarily-integral pulse width of 1-99us, then it's
    # not physically possible to support fractional power levels.
    #
    # @todo talk to Jason about changing modulation PERIOD to longer
    #     value (200us? 400? 1000?), OR whether pulse WIDTH can be
    #     in smaller unit (500ns? 100ns?)
    def set_laser_power_perc_immediate(self, value: float):

        # laser can flicker if we're on the wrong ADC?

        # don't want anything weird when passing over USB
        value = float(max(0, min(100, value)))
        if value <= 0:
            return set_laser_enable(False)

        # If full power (and allowed), disable modulation and exit
        if value >= 100:
            if self.settings.state.laser_power_require_modulation:
                log.debug("100% power requested, yet laser modulation required, so not disabling modulation")
            else:
                log.debug("Turning off laser modulation (full power)")
                self.next_applied_laser_power = 100.0
                log.debug("next_applied_laser_power = 100.0")
                return self.set_mod_enable(False)

        period_us = 1000 if self.settings.state.laser_power_high_resolution else 100
        width_us = int(round(1.0 * value * period_us / 100.0, 0)) # note that value is in range (0, 100) not (0, 1)

        if width_us <= 0:
            return set_laser_enable(False)

        # pulse width can't be longer than period, or shorter than 1us
        width_us = max(1, min(width_us, period_us))

        # Change the pulse period.  Note that we're not parsing into 40-bit
        # because this implementation is hard-coded to either 100 or 1000us
        # (both fitting well within uint16)
        result = self.set_mod_period_us(period_us)
        # if result.data is None:
        #     log.critical("Hardware Failure to send laser mod. pulse period")
        #     return SpectrometerResponse(data=False, error_msg="failed to send laser mod")

        # Set the pulse width to the 0-100 percentage of power
        result = self.set_mod_width_us(width_us)
        # if result.data is None:
        #     log.critical("Hardware Failure to send pulse width")
        #     return SpectrometerResponse(data=False, error_msg="failed to send pulse width")

        # Enable modulation
        result = self.set_mod_enable(True)
        # if result.data is None:
        #     log.critical("Hardware Failure to send laser modulation")
        #     return SpectrometerResponse(data=False, error_msg="failed to send laser modulation")

        log.debug("Laser power set to: %d", value)

        self.next_applied_laser_power = value
        log.debug("next_applied_laser_power = %s", self.next_applied_laser_power)

        return result

    ##
    # @note never used, provided for OEM
    def get_laser_temperature_setpoint_raw(self):
        if not self.settings.eeprom.has_laser:
            msg = "no laser installed"
            log.error(msg)
            return SpectrometerResponse(data=None, error_msg=msg)

        # is this little-endian?
        lsb_len = 2 if self.settings.is_xs() else 1

        return self._get_code(0xe8, lsb_len=lsb_len, label="GET_LASER_TEC_SETPOINT") 

    def set_laser_temperature_setpoint_raw(self, value: int):
        log.debug(f"Send laser temperature setpoint raw: 0x{value:03x}")
        return self._send_code(0xe7, value, label="SET_LASER_TEC_SETPOINT")

    def set_laser_warning_delay_sec(self, value):
        if not self.settings.is_xs():
            log.error("laser warning delay only configurable on XS")
            return
        return self._send_code(0x8a, value, label="SET_LASER_WARNING_DELAY_SEC")

    def get_laser_warning_delay_sec(self):
        if not self.settings.is_xs():
            msg = "laser warning delay only configurable on XS"
            log.error(msg)
            return SpectrometerResponse(data=None, error_msg=msg)

        result = self._get_code(0x8b, lsb_len=1, label="GET_LASER_WARNING_DELAY_SEC")
        self.settings.state.laser_warning_delay_sec = result.data
        return result

    ############################################################################
    # digital pot on 220250 Rev4A+
    ############################################################################

    def set_laser_power_attenuator(self, value):
        cmd = "SET_LASER_ATTENUATOR"
        if not self.settings.is_xs():
            msg = f"{cmd} is only available on XS-Series with 220250 Rev4A+"
            log.debug(msg)
            return SpectrometerResponse(data=None, error_msg=msg)

        # digital potentiometer is 8-bit
        value = max(0, min(0xff, int(round(value))))

        return self._send_code(0x82, value, label=cmd)

    def get_laser_power_attenuator(self):
        cmd = "GET_LASER_ATTENUATOR"
        if not self.settings.is_xs():
            msg = f"{cmd} is only available on XS-Series with 220250 Rev4A+"
            log.debug(msg)
            return SpectrometerResponse(data=None, error_msg=msg)

        return self._get_code(0x83, msb_len=1, label=cmd)

    ############################################################################
    # laser interlock
    ############################################################################

    def get_laser_interlock(self):
        """ Legacy wrapper over can_laser_fire. """
        return self.can_laser_fire()

    def can_laser_fire(self):
        """
        @note only works on FX2-based spectrometers with FW >= 10.0.0.11
        @returns True if there is a laser and either the interlock is
         closed (in firing position), or there is no readable
         interlock
        """
        if not self.settings.eeprom.has_laser:
            msg = "no laser installed"
            log.error(msg)
            return SpectrometerResponse(data=False, error_msg=msg)

        if not self.settings.eeprom.has_interlock_feedback:
            # log.debug("CAN_LASER_FIRE requires has_interlock_feedback (defaulting True)")
            return SpectrometerResponse(data=True)

        res = self._get_code(0xef, label="CAN_LASER_FIRE", msb_len=1)
        res.data = 0 != res.data
        return res

    def is_laser_firing(self):
        """
        Check if the laser actually IS firing, independent of laser_enable or 
        can_laser_fire.

        @returns SpectrometerResponse
        """
        if not self.settings.eeprom.has_interlock_feedback:
            # log.debug("IS_LASER_FIRING requires has_interlock_feedback (defaulting to laser_enabled)")
            return self.get_laser_enabled()

        res = self.get_upper_code(0x0d, label="IS_LASER_FIRING", msb_len=1)
        res.data = 0 != res.data
        return res

    ############################################################################
    # (end of laser commands)
    ############################################################################

    def reset_fpga(self):
        log.debug("reset_Fpga: start")
        result = self._send_code(0xb5, label="RESET_FPGA")

        self.queue_message("marquee_error", "resetting FPGA")

        log.debug("reset_fpga: sleeping 3sec")
        sleep(3)

        log.debug("reset_fpga: re-applying FPGA settings")

        self.set_high_gain_mode_enable  (self.settings.state.high_gain_mode_enabled)
        self.set_integration_time_ms    (self.settings.state.integration_time_ms)
        self.set_detector_gain          (self.settings.eeprom.detector_gain)
        self.set_detector_offset        (self.settings.eeprom.detector_offset)
        self.set_detector_gain_odd      (self.settings.eeprom.detector_gain_odd)
        self.set_detector_offset_odd    (self.settings.eeprom.detector_offset_odd)

        self.queue_message("marquee_info", "FPGA reset")

        log.debug("reset_fpga: done")
        return result

    ##
    # Read the trigger source setting from the device.
    #
    # - 0 = internal
    # - 1 = external
    #
    # Use caution when interpreting the larger behavior of
    # the device as ARM and FX2 implementations differ as of 2017-08-02
    #
    # @note never called by ENLIGHTEN - provided for OEMs
    def get_trigger_source(self):
        value = self._get_code(0xd3, label="GET_TRIGGER_SOURCE", msb_len=1)
        self.settings.state.trigger_source = value.data
        return value

    ##
    # @note not called by ENLIGHTEN
    # @warning conflicts with GET_SELECTED_LASER
    def get_raman_mode_enabled_NOT_USED(self):
        res = self.get_upper_code(0x15, label="GET_RAMAN_MODE_ENABLED", msb_len=1)
        res.data = 0 != res.data
        return res

    ##
    # Enable "Raman mode" (automatic laser) in the spectrometer firmware.
    def set_raman_mode_enable_NOT_USED(self, flag: bool):
        if not self.settings.is_micro():
            log.debug("Raman mode only supported on Series-XS")
            return SpectrometerResponse(data=False, error_msg="raman mode not supported")

        return self._send_code(bRequest        = 0xff,
                               wValue          = 0x16,
                               wIndex          = 1 if flag else 0,
                               data_or_wLength = [0] * 8,
                               label           = "SET_RAMAN_MODE_ENABLE")

    def get_raman_delay_ms(self):
        res = self.get_upper_code(0x19, label="GET_RAMAN_DELAY_MS", msb_len=2)
        self.settings.state.raman_delay_ms = res.data
        return res

    def set_raman_delay_ms(self, ms: int):
        if not self.settings.is_micro():
            log.debug("Raman delay only supported on Series-XS")
            return SpectrometerResponse(data=False, error_msg="raman delay not supported")

        # send value as big-endian
        msb = (ms >> 8) & 0xff
        lsb =  ms       & 0xff
        value = (msb << 8) | lsb

        self.settings.state.raman_delay_ms = ms
        return self._send_code(bRequest        = 0xff,
                               wValue          = 0x20,
                               wIndex          = value,
                               data_or_wLength = [0] * 8,
                               label           = "SET_RAMAN_DELAY_MS")

    def get_laser_watchdog_sec(self):
        res = self.get_upper_code(0x17, label="GET_LASER_WATCHDOG_SEC", msb_len=2)
        self.settings.state.laser_watchdog_sec = res.data
        return res

    # @note until this change is mimick'd in the STM32 (and after that, until the FPGA
    #       logic itself is fixed), always send a DISABLE_LASER before changing the 
    #       watchdog period.
    def set_laser_watchdog_sec(self, sec):
        if not self.settings.is_micro():
            log.error("Laser watchdog only supported on Series-XS")
            return SpectrometerResponse(data=False, error_msg="laser watchdog not supported")

        # remove this call after the Series-XS ARM / FPGA watchdog are fixed
        # MZ: are they fixed? can I remove this?
        # self.set_laser_enable(False)

        # send value as big-endian
        msb = (sec >> 8) & 0xff
        lsb =  sec       & 0xff
        value = (msb << 8) | lsb

        self.settings.state.laser_watchdog_sec = sec
        return self._send_code(bRequest        = 0xff,
                               wValue          = 0x18,
                               wIndex          = value,
                               data_or_wLength = [0] * 8,
                               label           = "SET_LASER_WATCHDOG_SEC")

    def update_laser_watchdog(self):
        """
        Automatically set the laser watchdog long enough to handle the current
        integration time, assuming we have to perform 6 throwaways on the sensor
        in case it went to sleep.

        @note we are not currently using this function
        """
        if not self.settings.is_micro() or not self.settings.eeprom.has_laser:
            return SpectrometerResponse(data=False, error_msg="update laser watchdog not supported")

        int_ms = self.settings.state.integration_time_ms
        scans  = self.settings.state.scans_to_average
        
        throwaways_sec = int_ms * (8 + scans) / 1000.0
        watchdog_sec = int(max(10, throwaways_sec)) * 2
        log.debug(f"updating laser watchdog to {watchdog_sec} based on integration time {int_ms}ms and {scans} averaging")

        return self.set_laser_watchdog_sec(watchdog_sec)

    def set_vertical_binning(self, lines: tuple[int, int]):
        # check for legacy vis since they don't like vertical binning
        if self.settings.fpga_firmware_version == "000-008" and self.settings.microcontroller_firmware_version == "0.1.0.7":
            return SpectrometerResponse(data=False)
        if not self.settings.is_micro():
            log.debug("Vertical Binning only configurable on Series-XS")
            return SpectrometerResponse(data=False, error_msg="vertical binning not supported")

        try:
            start = lines[0]
            end   = lines[1]
        except:
            log.error("set_vertical_binning requires a tuple of (start, stop) lines")
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")

        if start < 0 or end < 0:
            log.error("set_vertical_binning requires a tuple of POSITIVE (start, stop) lines")
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")

        # enforce ascending order (also, note that stop line is "last line binned + 1", so stop must be > start)
        if start >= end:
            # (start, end) = (end, start)
            log.error("set_vertical_binning requires ascending order (ignoring %d, %d)", start, end)
            return SpectrometerResponse(data=False, error_msg="invalid start and stop lines")

        ok1 = self._send_code(bRequest        = 0xff,
                              wValue          = 0x21,
                              wIndex          = start,
                              data_or_wLength = [0] * 8,
                              label           = "SET_CCD_START_LINE")
        if ok1.error_msg != '':
            return ok1

        ok2 = self._send_code(bRequest        = 0xff,
                              wValue          = 0x23,
                              wIndex          = end,
                              data_or_wLength = [0] * 8,
                              label           = "SET_CCD_STOP_LINE")
        if ok2.error_msg != '':
            return ok2
        return SpectrometerResponse(data=ok1.data and ok2.data)

    ## 
    # @param mode: integral value 0-3
    #
    # \verbatim
    # mode  ADC (AD)   Pixel Width (OD)
    # b00   10-bit     10-bit
    # b01   10-bit     12-bit
    # b10   12-bit     10-bit
    # b11   12-bit     12-bit
    # \endverbatim
    def set_pixel_mode(self, mode: float):
        if not self.settings.is_micro():
            log.debug("Pixel Mode only configurable on Series-XS")
            return SpectrometerResponse(data=False, error_msg="pixel mode not supported")

        # we only care about the two least-significant bits
        mode = int(round(mode)) & 0x3 

        result = self._send_code(bRequest = 0xfd,
                                 wValue   = mode,
                                 label    = "SET_PIXEL_MODE")

        log.debug("waiting 1sec...")
        sleep(1)

        return result

    def clear_regions(self):
        x1 = self.settings.eeprom.active_pixels_horizontal
        y1 = self.settings.eeprom.active_pixels_vertical
        log.debug(f"resettings detector to full ({x1}, {y1}) extent")

        self.settings.state.region = None
        self.settings.update_wavecal()

        return self.set_detector_roi([0, 0, y1, 0, x1], store=False)

    def set_single_region(self, n: int):
        """
        This function uses the the multi-region feature to select just a single 
        pre-configured region at a time.  Whichever region is selected, that 
        region's parameters are written to "region 0" of the spectrometer, and
        the global wavecal is updated to use that region's calibration.
       
        @todo consider clear_region() function to restore physical ROI to 
            (0, active_vertical_pixels, 0, active_horizontal_pixels)
            (leave wavecal alone?)
        """
        if self.settings.state.detector_regions is None:
            log.debug(f"no detector regions configured")
            return SpectrometerResponse(data=False, error_msg="no regions configured")

        roi = self.settings.state.detector_regions.get_roi(n)
        if roi is None:
            log.debug(f"unconfigured region {n} (max {self.settings.eeprom.region_count}")
            return SpectrometerResponse(data=False, error_msg="unconfigured region")

        log.debug(f"set_single_region: applying region {n}: {roi}")
        self.settings.set_single_region(n)

        # send a "fake" ROI downstream, overriding to position 0
        self.set_detector_roi([0, roi.y0, roi.y1, roi.x0, roi.x1], store=False)
        return SpectrometerResponse()

    def set_detector_roi(self, args: list[float], store: bool = True):
        """
        Note this only sends the ROI downstream to the spectrometer (and stores
        it in DetectorRegions).  If you want to update the wavecal and store
        the "selected" region index, use set_region() instead (which calls this).
        
        You should use set_region() if you are selecting one of the standard
        regions already configured on the EEPROM.  You should use set_detector_roi()
        if you're making ad-hoc ROIs which aren't configured on the EEPROM.
        
        @param args: either a DetectorROI or a tuple of (region, y0, y1, x0, x1)
        """
        if not self.settings.is_micro():
            log.debug("Detector ROI only configurable on Series-XS")
            return SpectrometerResponse(data=False, error_msg="Detector ROI not configurable")

        if isinstance(args, DetectorROI):
            roi = args
            log.debug(f"passed DetectorROI: {roi}")
        else:
            # convert args to ROI
            log.debug(f"creating DetectorROI from args: {args}")

            if len(args) != 5:
                log.error(f"invalid detector roi args: {args}")
                return SpectrometerResponse(data=False, error_msg="invalid roi args")

            region = int(round(args[0]))
            y0     = int(round(args[1]))
            y1     = int(round(args[2]))
            x0     = int(round(args[3]))
            x1     = int(round(args[4]))

            if not (0 <= region <= 3 and
                    y0 < y1 and
                    x0 < x1 and
                    y0 >= 0 and
                    x0 >= 0 and
                    y1 <= self.settings.eeprom.active_pixels_horizontal and
                    x1 <= self.settings.eeprom.active_pixels_horizontal):
                log.error(f"invalid detector roi: {args}")
                return SpectrometerResponse(data=False, error_msg="invalid roi args")
            roi = DetectorROI(region, y0, y1, x0, x1)
            log.debug(f"created DetectorROI: {roi}")

        # determine previous total pixels
        self.prev_pixels = self.settings.pixels()
        log.debug(f"prev_pixels = {self.prev_pixels}")

        if store:
            if self.settings.state.detector_regions is None:
                log.debug("creating DetectorRegions")
                self.settings.state.detector_regions = DetectorRegions()

            # this is a no-op if it's already present and unchanged
            log.debug("saving DetectorROI in DetectorRegions")
            self.settings.state.detector_regions.add(roi)

        log.debug(f"total_pixels now {self.settings.pixels()}")

        buf = utils.uint16_to_little_endian([ roi.y0, roi.y1, roi.x0, roi.x1 ])
        log.debug("would send buf: %s", buf)

        result = self._send_code(bRequest        = 0xff,
                                 wValue          = 0x25,
                                 wIndex          = roi.region,
                                 data_or_wLength = buf,
                                 label           = "SET_DETECTOR_ROI")

        log.debug("waiting 1sec...")
        sleep(1)

        # Just in case, flows the updated DetectorRegions object upstream
        # so caller has access to it.
        if store:
            self.queue_message("detector_regions", self.settings.state.detector_regions)

        return result

    def get_fpga_configuration_register(self, label: str = ""):
        raw = self._get_code(0xb3, lsb_len=2, label="GET_FPGA_CONFIGURATION_REGISTER")
        log.debug(f"FPGA Configuration Register: 0x{raw:04x} ({label})")
        return raw

    # ##########################################################################
    #
    #                           Accessory Connector
    #
    # ##########################################################################

    # ##########################################################################
    # Accessory Enable
    # ##########################################################################

    ## @todo change opcode (conflicts with GET_DETECTOR_START_LINE)
    def set_accessory_enable(self, flag: bool):
        if not self.settings.is_gen15():
            log.debug("accessory requires Gen 1.5")
            return SpectrometerResponse(data=False, error_msg="requires gen1.5")
        value = 1 if flag else 0
        return self._send_code(bRequest        = 0x22,
                               wValue          = value,
                               wIndex          = 0,
                               data_or_wLength = [0] * 8,
                               label           = "SET_ACCESSORY_ENABLE")

    ## @todo find out opcode
    def get_discretes_enabled(self):
        if not self.settings.is_gen15():
            log.error("accessory requires Gen 1.5")
            return SpectrometerResponse(data=False, error_msg="requires gen1.5")
        # return self._get_code(0x37, label="GET_ACCESSORY_ENABLED", msb_len=1)

    # ##########################################################################
    # Fan
    # ##########################################################################

    def set_fan_enable(self, flag: bool):
        if not self.settings.is_gen15():
            log.debug("fan requires Gen 1.5")
            return SpectrometerResponse(data=False, error_msg="fan requires gen15.")
        value = 1 if flag else 0
        return self._send_code(bRequest        = 0x36,
                               wValue          = value,
                               wIndex          = 0,
                               data_or_wLength = [0] * 8,
                               label           = "SET_FAN_ENABLE")

    def get_fan_enabled(self):
        if not self.settings.is_gen15():
            log.error("fan requires Gen 1.5")
            return SpectrometerResponse(data=False, error_msg="fan requires gen1.5")
        return SpectrometerResponse(data=0 != self._get_code(0x37, label="GET_FAN_ENABLED", msb_len=1))

    # ##########################################################################
    # Lamp
    # ##########################################################################

    def set_lamp_enable(self, flag: bool):
        if not self.settings.is_gen15():
            log.debug("lamp requires Gen 1.5")
            return SpectrometerResponse(data=False, error_msg="lamp requires gen1.5")
        value = 1 if flag else 0
        return self._send_code(bRequest        = 0x32,
                               wValue          = value,
                               wIndex          = 0,
                               data_or_wLength = [0] * 8,
                               label           = "SET_LAMP_ENABLE")

    def get_lamp_enabled(self):
        if not self.settings.is_gen15():
            log.error("lamp requires Gen 1.5")
            return SpectrometerResponse(data=False, error_msg="lamp requires gen1.5")
        res = self._get_code(0x33, label="GET_LAMP_ENABLED", msb_len=1)
        res.data = 0 != res.data
        return res

    # ##########################################################################
    # Shutter
    # ##########################################################################

    def set_shutter_enable(self, flag: bool):
        if not (self.settings.is_gen15() and self.settings.eeprom.has_shutter):
            log.debug("shutter requires Gen 1.5 and has_shutter flag")
            return SpectrometerResponse(data=False, error_msg="shutter requires gen1.5")
        value = 1 if flag else 0
        return self._send_code(bRequest        = 0x30,
                               wValue          = value,
                               wIndex          = 0,
                               data_or_wLength = [0] * 8,
                               label           = "SET_SHUTTER_ENABLE")

    def get_shutter_enabled(self):
        if not (self.settings.is_gen15() and self.settings.eeprom.has_shutter):
            log.debug("shutter requires Gen 1.5 and has_shutter flag")
            return SpectrometerResponse(data=False, error_msg="shutter requires gen1.5")
        res = SpectrometerResponse(data=0 != self._get_code(0x31, label="GET_SHUTTER_ENABLED", msb_len=1))
        res.data = 0 != res.data
        return res 

    # ##########################################################################
    # Laser Modulation and Continuous Strobe
    # ##########################################################################

    def set_mod_enable(self, flag: bool):
        self.settings.state.mod_enabled = flag
        value = 1 if flag else 0
        return self._send_code(0xbd, value, label="SET_MOD_ENABLE")

    def get_mod_enabled(self):
        res = self._get_code(0xe3, label="GET_MOD_ENABLED", msb_len=1)
        if res.error_msg != '':
            return res
        flag = 0 != res.data
        self.settings.state.mod_enabled = flag
        return SpectrometerResponse(data=flag)

    def set_mod_period_us(self, us: float):
        self.settings.state.mod_period_us = us
        (lsw, msw, buf) = self._to40bit(us)
        return self._send_code(0xc7, lsw, msw, buf, label="SET_MOD_PERIOD")

    def get_mod_period_us(self):
        result = self._get_code(0xcb, label="GET_MOD_PERIOD", lsb_len=5)
        self.settings.state.mod_period_us = result
        return result

    def set_mod_width_us(self, us: float):
        self.settings.state.mod_width_us = us
        (lsw, msw, buf) = self._to40bit(us)
        return self._send_code(0xdb, lsw, msw, buf, label="SET_MOD_WIDTH")

    def get_mod_width_us(self):
        result = self._get_code(0xdc, label="GET_MOD_WIDTH", lsb_len=5)
        self.settings.state.mod_width_us = result.data
        return result

    def set_mod_delay_us(self, us: float):
        self.settings.state.mod_delay_us = us
        (lsw, msw, buf) = self._to40bit(us)
        return self._send_code(0xc6, lsw, msw, buf, label="SET_MOD_DELAY")

    def get_mod_delay_us(self):
        result = self._get_code(0xca, label="GET_MOD_DELAY", lsb_len=5)
        self.settings.state.mod_delay_us = result.data
        return result

    def set_mod_duration_us_NOT_USED(self, us: float):
        self.settings.state.mod_duration_us = us
        (lsw, msw, buf) = self._to40bit(us)
        return self._send_code(0xb9, lsw, msw, buf, label="SET_MOD_DURATION")

    def get_mod_duration_us(self):
        result = self._get_code(0xc3, label="GET_MOD_DURATION", lsb_len=5)
        self.settings.state.mod_duration_us = result.data
        return result

    ## this is a synonym for _set_laser_enable_immediate(), but without side-effects
    def set_strobe_enable(self, flag: bool):
        value = 1 if flag else 0
        return self._send_code(0xbe, value, label="SET_STROBE_ENABLE")

    ## a literal pass-through to get_laser_enabled()
    def get_strobe_enabled(self):
        return self.get_laser_enabled()

    # ##########################################################################
    # Ambient Temperature
    # ##########################################################################

    ## @see https://www.nxp.com/docs/en/data-sheet/LM75B.pdf
    def get_ambient_temperature_degC(self):
        if self.settings.is_gen15():
            return self.get_ambient_temperature_degC_gen15()
        elif self.settings.is_xs():
            return self.get_ambient_temperature_degC_arm()
        else:
            log.debug("ambient temperature requires XS or Gen 1.5")
            return 

    def get_ambient_temperature_degC_arm(self):
        if not self.settings.is_xs():
            msg = "ambient temperature ARM requires XS"
            log.debug(msg)
            return

        # MZ: just disable for now
        # return

        if self.settings.microcontroller_firmware_version == "1.0.2.9":
            msg = "ambient temperature ARM requires newer firmware"
            log.debug(msg)
            return

        result = self._get_code(0xff, 0x2a, label="GET_AMBIENT_TEMPERATURE_DEGC_ARM", msb_len=1)
        if result is None or result.data is None:
            msg = f"failed to read ambient temperature"
            log.error(msg)
            return SpectrometerResponse(error_msg=msg)
        degC = result.data

        log.debug("ambient temperature degC ARM: %s", degC)
        return SpectrometerResponse(data=degC)

    def get_ambient_temperature_degC_gen15(self):
        if not self.settings.is_gen15():
            msg = "ambient temperature Gen1.5 requires Gen 1.5"
            log.error(msg)
            return SpectrometerResponse(error_msg=msg)
            
        result = self._get_code(0x35, label="GET_AMBIENT_TEMPERATURE_GEN15", msb_len=2)
        if result is None or len(result) != 2:
            msg = f"failed to read ambient temperature"
            log.error(msg)
            return SpectrometerResponse(error_msg=msg)
        log.debug("ambient temperature raw: %s", result)

        raw = result.data
        raw = raw >> 5
        degC = 0.125 * utils.twos_comp(raw, 11)

        log.debug("parsed ambient temperature from raw %s to %.3f degC", result, degC)
        return SpectrometerResponse(data=degC)

    # ##########################################################################
    # added for wasatch-shell
    # ##########################################################################

    def get_tec_enabled(self):
        if not self.settings.eeprom.has_cooling:
            log.error("unable to control TEC: EEPROM reports no cooling")
            return SpectrometerResponse(data=False, error_msg="no cooling reported")
        res = self._get_code(0xda, label="GET_CCD_TEC_ENABLED", msb_len=1)
        res.data = 0 != res.data
        if res.error_msg != '':
            return res
        return res

    def get_actual_frames(self):
        return self._get_code(0xe4, label="GET_ACTUAL_FRAMES", lsb_len=2)

    def get_actual_integration_time_us(self):
        return self._get_code(0xdf, label="GET_ACTUAL_INTEGRATION_TIME_US", lsb_len=3)

    def get_detector_offset(self):
        value = self._get_code(0xc4, label="GET_DETECTOR_OFFSET", lsb_len=2)
        self.settings.eeprom.detector_offset = value.data
        return SpectrometerResponse(data=value)

    def get_detector_offset_odd(self):
        if not self.settings.is_ingaas():
            log.debug("GET_DETECTOR_OFFSET_ODD only supported on InGaAs")
            return SpectrometerResponse(data=self.settings.eeprom.detector_offset_odd)

        value = self._get_code(0x9e, label="GET_DETECTOR_OFFSET_ODD", lsb_len=2)
        if value.error_msg != '':
            return value
        self.settings.eeprom.detector_offset_odd = value.data
        return SpectrometerResponse(data=value)

    def get_ccd_sensing_threshold(self):
        return self._get_code(0xd1, label="GET_CCD_SENSING_THRESHOLD", lsb_len=2)

    def get_ccd_threshold_sensing_mode(self):
        return self._get_code(0xcf, label="GET_CCD_THRESHOLD_SENSING_MODE", msb_len=1)

    def get_external_trigger_output(self):
        return self._get_code(0xe1, label="GET_EXTERNAL_TRIGGER_OUTPUT", msb_len=1)

    def set_mod_linked_to_integration(self, flag: bool):
        value = 1 if flag else 0
        return self._send_code(0xdd, value, label="SET_MOD_LINKED_TO_INTEGRATION")

    def get_mod_linked_to_integration(self):
        res = self._get_code(0xde, label="GET_MOD_LINKED_TO_INTEGRATION", msb_len=1)
        if res.error_msg != '':
            return res
        res.data = 0 != res.data
        return res

    def get_selected_adc(self):
        value = self._get_code(0xee, label="GET_SELECTED_ADC", msb_len=1)
        if value.error_msg != '':
            return value
        if self.settings.state.selected_adc != value.data:
            log.error("GET_SELECTED_ADC %d != state.selected_adc %d", value, self.settings.state.selected_adc)
            self.settings.state.selected_adc = value.data
        return value

    def set_trigger_delay(self, half_us: float):
        """
        A confenurable delay from when an inbound trigger signal is
        received by the spectrometer, until the triggered acquisition actually starts.
        
        Default value is 0us.
        
        Unit is in 0.5 microseconds (500ns), so value of 25 would represent 12.5us.
        
        Value is 24bit, so max value is 16777216 (8.388608 sec).
        
        Like triggering, only currently supported on ARM.
        """
        if not self.settings.is_arm():
            log.error("SET_TRIGGER_DELAY only supported on ARM")
            return False
        lsw = half_us & 0xffff
        msb = (half_us >> 16) & 0xff
        return self._send_code(0xaa, wValue=lsw, wIndex=msb, label="SET_TRIGGER_DELAY")

    # not tested
    def get_trigger_delay(self):
        if not self.settings.is_arm():
            msg = "GET_TRIGGER_DELAY only supported on ARM"
            log.error(msg)
            return SpectrometerResponse(error_lvl=ErrorLevel.low, error_msg=msg)
        return self._get_code(0xe4, label="GET_TRIGGER_DELAY", lsb_len=3) # not sure about LSB

    def get_vr_continuous_ccd(self):
        res = self._get_code(0xcc, label="GET_VR_CONTINUOUS_CCD", msb_len=1)
        if res.error_msg != '':
            return res
        res.data = 0 != res.data
        return res

    def get_vr_num_frames(self):
        return self._get_code(0xcd, label="GET_VR_NUM_FRAMES", msb_len=1)

    def get_opt_actual_integration_time(self):
        res = self.get_upper_code(0x0b, label="GET_OPT_ACT_INT_TIME", msb_len=1) 
        if res.error_msg != '':
            return res
        res.data = 0 != res.data
        return res

    def get_opt_area_scan(self):
        res = self.get_upper_code(0x0a, label="GET_OPT_AREA_SCAN", msb_len=1)
        if res.error_msg != '':
            return res
        res.data = 0 != res.data
        return res

    def get_opt_cf_select(self):
        res = self.get_upper_code(0x07, label="GET_OPT_CF_SELECT", msb_len=1)
        if res.error_msg != '':
            return res
        res.data = 0 != res.data
        return res

    def get_opt_data_header_tab(self):
        return self.get_upper_code(0x06, label="GET_OPT_DATA_HEADER_TAB", msb_len=1)

    def get_opt_horizontal_binning(self):
        return self.get_upper_code(0x0c, label="GET_OPT_HORIZONTAL_BINNING", msb_len=1)

    def get_opt_integration_time_resolution(self):
        return self.get_upper_code(0x05, label="GET_OPT_INTEGRATION_TIME_RESOLUTION", msb_len=1)

    # ##########################################################################
    # Analog output
    # ##########################################################################

    ## @param value (Input) tuple of (bool enable, int mode)
    def set_analog_output_mode(self, value: tuple[bool, int]):
        if not self.settings.is_gen2():
            log.error("analog output only available on Gen2")
            return SpectrometerResponse(data=False, error_msg="analog output unsupported")

        wIndex = 0

        # parse enable and mode from value tuple
        try:
            if isinstance(value[0], bool):
                enable = value[0]
            else:
                enable = value[0] != 0

            mode = int(value[1])
            if (mode != 0 and mode != 1):
                log.error("invalid analog output mode 0x%02x, disabling", mode)
                enable = False
                mode = 0

            if enable:
                wIndex = 0x02 | mode # 0x02 sets the "enable" bit
                self.state.analog_out_enable = True
                self.state.analog_out_mode = mode
                self.state.analog_out_value = 0 if mode == 0 else 40 # 4mA default current in deci-mA
            else:
                wIndex = 0
                self.state.analog_out_enable = False
                self.state.analog_out_mode = 0
                self.state.analog_out_value = 0

        except:
            log.error("set_analog_output_mode takes tuple of (bool, int), disabling")
            wIndex = 0

        return self._send_code(bRequest  = 0xff,
                               wValue    = 0x11,
                               wIndex    = wIndex,
                               label     = "SET_ANALOG_OUT_MODE")

    def set_analog_output_value(self, value: int):
        if not self.settings.is_gen2():
            log.error("analog output only available on Gen2")
            return SpectrometerResponse(data=False, error_msg="analog output unsupported")

        # spectrometer should range-limit, but just to codify:
        if self.state.analog_out_mode == 0:
            # voltage (decivolts, range 0-50 (0-5V))
            if value < 0:
                value = 0
            if value > 50:
                value = 50
        elif self.state.analog_out_mode == 1:
            # current (deci-mA, range 40-200 (4-20mA))
            if value < 40:
                value = 40
            elif value > 200:
                value = 200
        else:
            log.error("invalid analog out mode %d, ignoring value", self.state.analog_out_mode)
            return SpectrometerResponse(data=False, error_msg="invalid mode")

        return self._send_code(bRequest  = 0xff,
                               wValue    = 0x12,
                               wIndex    = value,
                               label     = "SET_ANALOG_OUT_VALUE")

    def get_analog_output_state(self):
        if not self.settings.is_gen2():
            log.error("analog output only available on Gen2")
            return SpectrometerResponse(data=False, error_msg="analog output unsupported")

        res = self.get_upper_code(0x1a, wLength=3, label="GET_ANALOG_OUT_STATE")
        if res.error_msg != '':
            return res
        data = res.data
        if (data is None or len(data) != 3):
            log.error("invalid analog out state read: %s", data)
            return SpectrometerResponse(data=False, error_msg="invalid analog out state read")

        if data[0] != 0 and data[0] != 1:
            log.error("received invalid analog out enable: %d", data[0])
        if data[1] != 0 and data[1] != 1:
            log.error("received invalid analog out mode: %d", data[1])

        self.state.analog_out_enable = data[0] != 0
        self.state.analog_out_mode = data[1]
        self.state.analog_out_value = data[2] # no range-checking applied
        data = (self.state.analog_out_enable,
                self.state.analog_out_mode,
                self.state.analog_out_value)
        return SpectrometerResponse(data=data)

    # @returns decivolts
    def get_analog_input_value(self):
        if not self.settings.is_gen2():
            log.error("analog input only available on Gen2")
            return SpectrometerResponse(data=False, error_msg="analog input unsupported")
        return self.get_upper_code(0x1b, lsb_len=1, label="GET_ANALOG_IN_VALUE")

    # ##########################################################################
    # EEPROM Cruft
    # ##########################################################################

    def update_session_eeprom(self, pair: tuple[str, EEPROM]):
        """
        Given a (serial_number, EEPROM) pair, update this process's "session"
        EEPROM with just the EDITABLE fields of the passed EEPROM.
        """
        log.debug("fid.update_session_eeprom: %s updating EEPROM instance", self.settings.eeprom.serial_number)

        if not self.eeprom_backup:
            self.eeprom_backup = copy.deepcopy(self.settings.eeprom)

        self.settings.eeprom.update_editable(pair[1])
        return SpectrometerResponse(data=True)

    def replace_session_eeprom(self, pair: tuple[str, EEPROM]):
        """
        Given a (serial_number, EEPROM) pair, replace this process's "session"
        EEPROM with the passed EEPROM.
        """
        log.debug("fid.replace_session_eeprom: %s replacing EEPROM instance", self.settings.eeprom.serial_number)

        if not self.eeprom_backup:
            self.eeprom_backup = copy.deepcopy(self.settings.eeprom)

        self.settings.eeprom = pair[1]
        self.settings.eeprom.dump()
        return SpectrometerResponse()

    ## Actually store the current session EEPROM fields to the spectrometer.
    def write_eeprom(self):
        if not self.eeprom_backup:
            log.critical("expected to update or replace EEPROM object before write command")
            self.queue_message("marquee_error", "Failed to write EEPROM")
            return SpectrometerResponse(data=False, error_msg="failed to write eeprom")

        # backup contents of previous EEPROM in log
        log.debug("Original EEPROM contents")
        self.eeprom_backup.dump()
        log.debug("Original EEPROM buffers: %s", self.eeprom_backup.buffers)

        try:
            self.settings.eeprom.generate_write_buffers()
        except:
            log.critical("failed to render EEPROM write buffers", exc_info=1)
            self.queue_message("marquee_error", "Failed to write EEPROM")
            return SpectrometerResponse(data=False, error_msg="failed to generate eeprom")

        log.debug("Would write new buffers: %s", self.settings.eeprom.write_buffers)

        for page in range(EEPROM.MAX_PAGES):
            if self.settings.is_arm():
                log.debug("writing page %d: %s", page, self.settings.eeprom.write_buffers[page])
                self._send_code(bRequest        = 0xff, # second-tier
                               wValue          = 0x02,
                               wIndex          = page,
                               data_or_wLength = self.settings.eeprom.write_buffers[page],
                               label           = "WRITE_EEPROM")
            else:
                DATA_START = 0x3c00
                offset = DATA_START + page * 64
                log.debug("writing page %d at offset 0x%04x: %s", page, offset, self.settings.eeprom.write_buffers[page])
                self._send_code(bRequest        = 0xa2,   # dangerous
                               wValue          = offset, # arguably an index but hey
                               wIndex          = 0,
                               data_or_wLength = self.settings.eeprom.write_buffers[page],
                               label           = "WRITE_EEPROM")

        self.queue_message("marquee_info", "EEPROM successfully updated")

        # any value in doing this?
        self.settings.eeprom.buffers = self.settings.eeprom.write_buffers

        return SpectrometerResponse(data=True)

    # ##########################################################################
    # Interprocess Communications
    # ##########################################################################

    # @todo move string-to-enum converter to AppLog
    def set_log_level(self, s: str):
        lvl = logging.DEBUG if s == "DEBUG" else logging.INFO
        log.debug("fid.set_log_level: setting to %s", lvl)
        logging.getLogger().setLevel(lvl)
        return SpectrometerResponse()

    def queue_message(self, setting, value):
        """
        If an upstream queue is defined, send the name-value pair.  Does nothing
        if the caller hasn't provided a queue.

        "setting" is application (caller) dependent, but ENLIGHTEN currently uses
        "marquee_info" and "marquee_error".
        """
        if self.message_queue is None:
            return SpectrometerResponse(data=False)

        msg = StatusMessage(setting, value)
        try:
            self.message_queue.put(msg) # put_nowait(msg)
        except:
            log.error("failed to enqueue StatusMessage (%s, %s)", setting, value, exc_info=1)
            return SpectrometerResponse(data=False, error_msg="failed to enqueue messsage")
        return SpectrometerResponse(data=True)

    def _init_process_funcs(self):
        """
        Is it the expectation that all of these functions will return 
        SpectrometerResponse? If so, that should be made explicit.
        """
        process_f = {}

        for fn_name in [ 
                "can_laser_fire",
                "clear_regions",
                "connect",
                "disconnect",
                "get_actual_frames",
                "get_actual_integration_time_us",
                "get_ambient_temperature_degC",
                "get_analog_input_value",
                "get_analog_output_state",
                "get_battery_charging",
                "get_battery_percentage",
                "get_battery_register",
                "get_battery_state_raw",
                "get_ble_firmware_version",
                "get_ccd_sensing_threshold",
                "get_ccd_threshold_sensing_mode",
                "get_dac",
                "get_detector_gain",
                "get_detector_gain_odd",
                "get_detector_offset",
                "get_detector_offset_odd",
                "get_detector_tec_setpoint_degC",
                "get_detector_tec_setpoint_raw",
                "get_detector_temperature_degC",
                "get_detector_temperature_raw",
                "get_discretes_enabled",
                "get_external_trigger_output",
                "get_fan_enabled",
                "get_fpga_configuration_register",
                "get_fpga_firmware_version",
                "get_high_gain_mode_enabled",
                "get_integration_time_ms",
                "get_lamp_enabled",
                "get_laser_enabled",
                "get_laser_interlock",
                "get_laser_tec_mode",
                "get_laser_temperature_degC",
                "get_laser_temperature_raw",
                "get_laser_temperature_setpoint_raw",
                "get_laser_warning_delay_sec",
                "get_laser_watchdog_sec",
                "get_line",
                "get_microcontroller_firmware_version",
                "get_mod_delay_us",
                "get_mod_duration_us",
                "get_mod_enabled",
                "get_mod_period_us",
                "get_mod_width_us",
                "get_opt_actual_integration_time",
                "get_opt_area_scan",
                "get_opt_cf_select",
                "get_opt_data_header_tab",
                "get_opt_has_laser",
                "get_opt_horizontal_binning",
                "get_opt_integration_time_resolution",
                "get_opt_laser_control",
                "get_raman_delay_ms",
                "get_secondary_adc_calibrated",
                "get_secondary_adc_raw",
                "get_selected_adc",
                "get_selected_laser",
                "get_sensor_line_length",
                "get_shutter_enabled",
                "get_strobe_enabled",
                "get_tec_enabled",
                "get_trigger_delay",
                "get_trigger_source",
                "get_vr_continuous_ccd",
                "get_vr_num_frames",
                "is_laser_firing",
                "queue_message",
                "replace_session_eeprom",
                "reset_fpga",
                "select_adc",
                "set_accessory_enable",
                "set_analog_output_mode",
                "set_analog_output_value",
                "set_area_scan_enable",
                "set_detector_gain",
                "set_detector_gain_odd",
                "set_detector_offset",
                "set_detector_offset_odd",
                "set_detector_roi",
                "set_detector_tec_setpoint_degC",
                "set_dfu_enable",
                "set_fan_enable",
                "set_high_gain_mode_enable",
                "set_integration_time_ms",
                "set_lamp_enable",
                "set_laser_enable",
                "set_laser_power_high_resolution",
                "set_laser_power_mW",
                "set_laser_power_perc",
                "set_laser_power_perc_immediate",
                "set_laser_power_require_modulation",
                "set_laser_tec_mode",
                "set_laser_temperature_setpoint_raw",
                "set_laser_watchdog_sec",
                "set_log_level",
                "set_mod_delay_us",
                "set_mod_enable",
                "set_mod_linked_to_integration",
                "set_mod_period_us",
                "set_mod_width_us",
                "set_pixel_mode",
                "set_raman_delay_ms",
                "set_selected_laser",
                "set_shutter_enable",
                "set_single_region",
                "set_strobe_enable",
                "set_tec_enable",
                "set_trigger_delay",
                "set_trigger_source",
                "set_vertical_binning",
                "update_laser_watchdog",
                "update_session_eeprom",
                "write_eeprom",
            ]:
            process_f[fn_name] = getattr(self, fn_name)
    
        ##################################################################
        # What follows is the old init-lambdas that are squashed into process_f
        # Long term, the upstream requests should be changed to match the new format
        # This is an easy fix for the time being to make things behave
        ##################################################################
        # spectrometer control
        process_f["laser_enable"]                       = lambda x: self.set_laser_enable(bool(x))
        process_f["integration_time_ms"]                = lambda x: self.set_integration_time_ms(x)

        process_f["detector_tec_setpoint_degC"]         = lambda x: self.set_detector_tec_setpoint_degC(int(round(x)))
        process_f["detector_tec_enable"]                = lambda x: self.set_tec_enable(bool(x))
        process_f["detector_gain"]                      = lambda x: self.set_detector_gain(float(x))
        process_f["detector_offset"]                    = lambda x: self.set_detector_offset(int(round(x)))
        process_f["detector_gain_odd"]                  = lambda x: self.set_detector_gain_odd(float(x))
        process_f["detector_offset_odd"]                = lambda x: self.set_detector_offset_odd(int(round(x)))
        process_f["degC_to_dac_coeffs"]                 = lambda x: self.settings.eeprom.set("degC_to_dac_coeffs", x)

        process_f["laser_power_perc"]                   = lambda x: self.set_laser_power_perc(x)
        process_f["laser_power_mW"]                     = lambda x: self.set_laser_power_mW(x)
        process_f["laser_temperature_setpoint_raw"]     = lambda x: self.set_laser_temperature_setpoint_raw(int(round(x)))
        process_f["laser_power_attenuator"]             = lambda x: self.set_laser_power_attenuator(int(round(x)))
        process_f["laser_power_high_resolution"]        = lambda x: self.set_laser_power_high_resolution(x)
        process_f["laser_power_require_modulation"]     = lambda x: self.set_laser_power_require_modulation(x)
        process_f["selected_laser"]                     = lambda x: self.set_selected_laser(int(x))
        process_f["set_laser_warning_delay_sec"]        = lambda x: self.set_laser_warning_delay_sec(int(x))

        process_f["high_gain_mode_enable"]              = lambda x: self.set_high_gain_mode_enable(bool(x))
        process_f["trigger_source"]                     = lambda x: self.set_trigger_source(int(x))
        process_f["enable_secondary_adc"]               = lambda x: self.settings.state.set("secondary_adc_enabled", bool(x))
        process_f["area_scan_enable"]                   = lambda x: self.set_area_scan_enable(bool(x))
        process_f["area_scan_fast"]                     = lambda x: self.settings.state.set("area_scan_fast", bool(x))

        process_f["bad_pixel_mode"]                     = lambda x: self.settings.state.set("bad_pixel_mode", int(x))
        process_f["min_usb_interval_ms"]                = lambda x: self.settings.state.set("min_usb_interval_ms", int(round(x)))
        process_f["max_usb_interval_ms"]                = lambda x: self.settings.state.set("max_usb_interval_ms", int(round(x)))

        process_f["accessory_enable"]                   = lambda x: self.set_accessory_enable(bool(x))
        process_f["fan_enable"]                         = lambda x: self.set_fan_enable(bool(x))
        process_f["lamp_enable"]                        = lambda x: self.set_lamp_enable(bool(x))
        process_f["shutter_enable"]                     = lambda x: self.set_shutter_enable(bool(x))
        process_f["strobe_enable"]                      = lambda x: self.set_strobe_enable(bool(x))
        process_f["mod_enable"]                         = lambda x: self.set_mod_enable(bool(x))
        process_f["mod_period_us"]                      = lambda x: self.set_mod_period_us(int(round(x)))
        process_f["mod_width_us"]                       = lambda x: self.set_mod_width_us(int(round(x)))

        # BatchCollection
        process_f["take_one_request"]                   = lambda x: self.settings.state.set("take_one_request", x)

        # Series-XS
       #f["raman_mode_enable"]                          = lambda x: self.set_raman_mode_enable(bool(x))
        process_f["raman_delay_ms"]                     = lambda x: self.set_raman_delay_ms(int(round(x)))
        process_f["laser_watchdog_sec"]                 = lambda x: self.set_laser_watchdog_sec(int(round(x)))

        # regions
        process_f["vertical_binning"]                   = lambda x: self.set_vertical_binning(x)
        process_f["single_region"]                      = lambda x: self.set_single_region(int(round(x)))
        process_f["clear_regions"]                      = lambda x: self.clear_regions()
        process_f["detector_roi"]                       = lambda x: self.set_detector_roi(x)
        process_f["pixel_mode"]                         = lambda x: self.set_pixel_mode(x)

        # EEPROM updates
        process_f["update_eeprom"]                      = lambda x: self.update_session_eeprom(x)
        process_f["replace_eeprom"]                     = lambda x: self.replace_session_eeprom(x)
        process_f["write_eeprom"]                       = lambda x: self.write_eeprom()

        # manufacturing
        process_f["reset_fpga"]                         = lambda x: self.reset_fpga()
        process_f["dfu_enable"]                         = lambda x: self.set_dfu_enable()

        # legacy
        process_f["allow_default_gain_reset"]           = lambda x: setattr(self, "allow_default_gain_reset", bool(x))

        # experimental (R&D)
        process_f["graph_alternating_pixels"]           = lambda x: self.settings.state.set("graph_alternating_pixels", bool(x))
        process_f["swap_alternating_pixels"]            = lambda x: self.settings.state.set("swap_alternating_pixels", bool(x))
        process_f["edc_enable"]                         = lambda x: self.settings.state.set("edc_enabled", bool(x))
        process_f["invert_x_axis"]                      = lambda x: self.settings.eeprom.set("invert_x_axis", bool(x))
        process_f["horiz_binning_enable"]               = lambda x: self.settings.eeprom.set("horiz_binning_enabled", bool(x))
        process_f["wavenumber_correction"]              = lambda x: self.settings.set_wavenumber_correction(float(x))

        # heartbeats & connection data
        process_f["raise_exceptions"]                   = lambda x: setattr(self, "raise_exceptions", bool(x))
        process_f["log_level"]                          = lambda x: self.set_log_level(x)
        process_f["num_connected_devices"]              = lambda x: self.settings.set_num_connected_devices(int(x))
        process_f["subprocess_timeout_sec"]             = lambda x: None
        process_f["heartbeat"]                          = lambda x: None
        process_f["reset"]                              = self.reset

        return process_f
