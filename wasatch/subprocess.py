""" Long-polling multiprocessing wrappers. 

    From ENLIGHTEN's standpoint (one Wasatch.PY user), here's what's going on:

    1. MainProcess creates a Controller.bus_timer which on timeout (tick) calls
        Controller.update_connections()
    2. Controller.update_connections() calls Controller.connect_new()
    3. connect_new(): if we're not already connected to a spectrometer, yet
        bus.device_1 is not "disconnected" (implying something was found on the
        bus), then connect_new() instantiates a WasatchDeviceWrapper and then
        calls connect() on it
    4. WasatchDeviceWrapper.connect() forks a child process running the
        continuous_poll() method of the same WasatchDeviceWrapper instance,
        then waits on hw_details to be returned via a pipe (queue).

       [-- at this point, the same WasatchDeviceWrapper instance is being 
           accessed by two processes...be careful! --]

    5. continuous_poll() instantiantes a WasatchDevice.  This object will only
        ever be referenced within the subprocess.
    6. continuous_poll() calls WasatchDevice.connect() (exits on failure)
        6.a WasatchDevice instantiates a Sim, FID or SP based on UID
        6.b if FID, WasatchDevice.connect() loads the EEPROM
    7. continuous_poll() populates hw_details object (contains bits read earlier 
        from EEPROM), then feeds it back to MainProcess
"""

import time
import Queue
import common
import logging
import multiprocessing

from . import applog, devices, utils

log = logging.getLogger(__name__)

class HardwareDetails(object):
    """ Simple data structure to host the details read from the EEPROM
        (or defaults). Must be defined at the top level in a module in
        order to be pickleable (support serialization). """

    name = "hwdetails"

class WasatchDeviceWrapper(object):

    """ Wrap WasatchDevice in a non-blocking interface run in a separate
        process. Use a summary queue to pass meta information about the
        device for multiprocessing-safe spectrometer settings and spectral 
        acquisition on Windows. """

    ############################################################################
    #                                                                          #
    #                                MainProcess                               #
    #                                                                          #
    ############################################################################

    # instantiated by Controller.connect_new()
    def __init__(self, 
                 uid=None, 
                 bus_order=0,
                 log_queue=None,
                 log_level=logging.DEBUG):

        log.debug("%s setup", self.__class__.__name__)

        self.uid        = uid
        self.bus_order  = bus_order
        self.log_level  = log_level

        self.log_queue        = log_queue
        self.command_queue    = multiprocessing.Queue()
        self.response_queue   = multiprocessing.Queue()
        self.hw_details_queue = multiprocessing.Queue()

        self.poller_wait  = 0.05    # MZ: update from hardware device at 20Hz
        self.acquire_sent = False   # Wait for an acquire to complete
        self.closing      = False   # Don't permit new requires during close
        self.poller       = None    # basically, "subprocess"
        self.hw_details   = None

    # called by Controller.connect_new()
    def connect(self):
        """ Create a low level device object with the specified
            identifier, kick off the subprocess to attempt to read from it. """

        if self.poller != None:
            log.critical("WasatchDeviceWrapper.connect: already polling, cannot connect")
            return False

        # fork a child process running the continuous_poll() method on THIS 
        # object instance.  Therefore, THIS Wrapper instance will be referenced 
        # from both processes
        args = (self.uid, 
                self.bus_order,
                self.log_queue,
                self.command_queue, 
                self.response_queue,
                self.hw_details_queue)
        self.poller = multiprocessing.Process(target=self.continuous_poll, args=args)
        self.poller.start()

        # Attempt to get the device summary information off of the queue
        # MZ: After forking the spectrometer communication loop into a separate thread,
        #     we assume that hardware details should be available within 3 seconds.
        try:
            self.hw_details = self.hw_details_queue.get(timeout=3)
            log.debug("WasatchDeviceWrapper.connect: hw_details: %s", self.hw_details.__dict__)
        except Exception as exc:
            log.warn("WasatchDeviceWrapper.connect: hw_details caught exception", exc_info=1)
            self.hw_details = None
            return False

        log.debug("WasatchDeviceWrapper.connect: succeeded")
        return True

    def disconnect(self):
        """ Add the poison pill to the command queue. """

        log.debug("WasatchDeviceWrapper.disconnect: start")
        self.command_queue.put(None)

        timeout_sec = 1
        log.debug("WasatchDeviceWrapper.disconnect: Poller join timeout: %s", timeout_sec)
        try:
            self.poller.join(timeout=timeout_sec)
        except NameError as exc:
            log.warn("WasatchDeviceWrapper.disconnect: Poller previously disconnected", exc_info=1)
        except Exception as exc:
            log.critical("WasatchDeviceWrapper.disconnect: Cannot join poller", exc_info=1)

        log.debug("Post poller join")

        try:
            self.poller.terminate()
            log.debug("WasatchDeviceWrapper.disconnect: poller terminated")
        except Exception as exc:
            log.critical("WasatchDeviceWrapper.disconnect: Cannot terminate poller", exc_info=1)

        self.closing = True
        time.sleep(0.1)
        log.debug("WasatchDeviceWrapper.disconnect: done")

        return True

    def acquire_data(self, mode=common.acquisition_mode_keep_complete):
        """ Don't use if queue.empty() for flow control on python 2.7 on
            windows, as it will hang. Use the catch of the queue empty exception as
            shown below instead.
            
            It is the upstream interface's job to decide how to process the
            potentially voluminous amount of data returned from the device.
            get_last by default will make sure the queue is cleared, then
            return the most recent reading from the device. """
        if self.closing:
            log.debug("WasatchDeviceWrapper.acquire_data: closing")
            return None

        # MZ: consider reading self.hw_details_queue.get_nowait() here, to allow
        #     asynchronous update of changed spectrometer settings?

        if mode == common.acquisition_mode_latest:
            return self.get_final_item()
        elif mode == common.acquisition_mode_keep_complete:
            return self.get_final_item(keep_averaged=True)

        # presumably mode == common.acquisition_mode_keep_all:

        # Get the oldest entry off of the queue, expect the user to be
        # able to acquire them upstream as fast as possible.

        # Note that these two calls to response_queue aren't synchronized
        reading = None
        qsize = self.response_queue.qsize()
        log.info("WasatchDeviceWrapper.acquire_data: qsize %s", qsize)
        try:
            reading = self.response_queue.get_nowait()
        except Queue.Empty:
            pass

        return reading

    def get_final_item(self, keep_averaged=False):
        """ Read from the response queue until empty (or we find an averaged item) """
        reading = None
        dequeue_count = 0
        while True:
            try:
                reading = self.response_queue.get_nowait()
            except Queue.Empty:
                break

            if reading is None:
                break

            dequeue_count += 1

            # was this the final spectrum in an averaged sequence?
            if keep_averaged and reading.averaged:
                break

        if reading is not None and dequeue_count > 1:
            log.debug("discarded %d spectra", dequeue_count - 1)
        return reading

    # called by MainProcess.Controller
    def change_setting(self, setting, value):
        """ Add the specified setting and value to the local control queue. """
        log.debug("WasatchDeviceWrapper.change_setting: %s => %s", setting, value)
        control_object = devices.ControlObject(setting, value)
        try:
            self.command_queue.put(control_object)
        except Exception as exc:
            log.critical("WasatchDeviceWrapper.change_setting: Problem enqueuing %s", setting, exc_info=1)

    def get_detail(self, field):
        tmp = self.hw_details
        if tmp is None:
            return "UNKNOWN-1"
        if hasattr(tmp, field):
            return getattr(tmp, field)
        return "UNKNOWN-2"

    ############################################################################
    #                                                                          #
    #                                 Subprocess                               #
    #                                                                          #
    ############################################################################

    def continuous_poll(self, 
                        uid, 
                        bus_order, 
                        log_queue, 
                        command_queue, 
                        response_queue, 
                        hw_details_queue):

        """ Continuously process with the simulated device. First setup
            the log queue handler. While waiting forever for the None poison
            pill on the command queue, continuously read from the device and
            post the results on the response queue. 
            
            MZ: This is essentially the main() loop in a forked process (not 
                thread).  Hopefully we can scale this to one per spectrometer.  
                All communications with the parent process are routed through
                one of the three queues (cmd inputs, response outputs, and
                a one-shot hw_details).
        """

        applog.process_log_configure(log_queue, self.log_level)
        log.info("WasatchDeviceWrapper.continuous_poll: start (uid %s, bus_order %d)", uid, bus_order)

        hardware = devices.WasatchDevice(uid, bus_order)
        ok = hardware.connect()
        if not ok:
            log.critical("WasatchDeviceWrapper.continuous_poll: Cannot connect")
            return False

        log.debug("WasatchDeviceWrapper.continuous_poll: connected to a spectrometer")

        # send the hardware details back to the GUI process
        # full of device details as well
        log.debug("WasatchDeviceWrapper.continuous_poll: creating hw_details")
        hw_details = self.build_hardware_details(hardware)

        log.debug("WasatchDeviceWrapper.continuous_poll: returning hw_details to GUI process")
        hw_details_queue.put(hw_details, timeout=1)

        # Read forever until the None poison pill is received
        log.debug("WasatchDeviceWrapper.continuous_poll: entering loop")
        while True:
            poison_pill = False
            queue_empty = False
            while queue_empty == False:
                try:
                    record = command_queue.get_nowait()
                    if record is None:
                        poison_pill = True
                    else:
                        log.debug("WasatchDeviceWrapper.continuous_poll: Processing command queue: %s", record.setting)
                        hardware.change_setting(record.setting, record.value)

                except Queue.Empty as exc:
                    log.debug("WasatchDeviceWrapper.continuous_poll: Queue empty, just wait")
                    queue_empty = True

            if poison_pill:
                log.debug("WasatchDeviceWrapper.continuous_poll: Exit command queue")
                break

            try:
                log.debug("WasatchDeviceWrapper.continuous_poll: acquiring data")
                reading = hardware.acquire_data()
            except ValueError as val_exc:
                log.critical("WasatchDeviceWrapper.continuous_poll: ValueError", exc_info=1)
            except Exception as exc:
                log.critical("WasatchDeviceWrapper.continuous_poll: Exception", exc_info=1)
                reading = devices.Reading()
                reading.failure = str(exc)

            log.debug("WasatchDeviceWrapper.continuous_poll: Spectrum is: %s", reading.spectrum[0:5])
            log.debug("WasatchDeviceWrapper.continuous_poll: Post acquire: %s", reading.laser_status)

            response_queue.put(reading, timeout=1)

            if reading.failure is not None:
                log.critical("WasatchDeviceWrapper.continuous_poll: Hardware level ERROR")
                break

            time.sleep(self.poller_wait)

        log.info("WasatchDeviceWrapper.continuous_poll: done")

    def build_hardware_details(self, hardware):
        """ Build a simple object that lists the second generation of
            hardware details as pulled from the device object (which is in
            turn pulled from EEPROM where available). """
        hw_details = HardwareDetails()

        hw_details.summary               = hardware.summary
        hw_details.model                 = hardware.model
        hw_details.serial_number         = hardware.serial_number
        hw_details.detector              = hardware.detector
        hw_details.degC_to_dac_coeff_0   = hardware.degC_to_dac_coeff_0
        hw_details.degC_to_dac_coeff_1   = hardware.degC_to_dac_coeff_1
        hw_details.degC_to_dac_coeff_2   = hardware.degC_to_dac_coeff_2
        hw_details.detector_tec_setpoint_degC = hardware.detector_tec_setpoint_degC
        hw_details.adc_to_degC_coeff_0   = hardware.adc_to_degC_coeff_0
        hw_details.adc_to_degC_coeff_1   = hardware.adc_to_degC_coeff_1
        hw_details.adc_to_degC_coeff_2   = hardware.adc_to_degC_coeff_2
        hw_details.tec_r298              = hardware.tec_r298
        hw_details.tec_beta              = hardware.tec_beta
        hw_details.tmax                  = hardware.tmax
        hw_details.tmin                  = hardware.tmin
        hw_details.excitation            = hardware.excitation
        hw_details.slit_size             = hardware.slit_size
        hw_details.bad_pixels            = hardware.bad_pixels
        hw_details.wavelength_coeff_0    = hardware.wavelength_coeff_0
        hw_details.wavelength_coeff_1    = hardware.wavelength_coeff_1
        hw_details.wavelength_coeff_2    = hardware.wavelength_coeff_2
        hw_details.wavelength_coeff_3    = hardware.wavelength_coeff_3
        hw_details.integration           = hardware.integration
        hw_details.wavelengths           = hardware.wavelengths
        hw_details.wavenumbers           = hardware.wavenumbers
        hw_details.min_integration_ms    = hardware.min_integration
        hw_details.max_integration_ms    = hardware.max_integration

        hw_details.laser_temperature_setpoint_raw = hardware.hardware.get_laser_temperature_setpoint_raw()

        return hw_details
