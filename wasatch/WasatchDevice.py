""" Higher level abstractions for devices and communication buses.  Allows for
    the wrapping of simulation devices and real hardware devices simultaneously.

    TODO: split into single-class files.
"""

import time
import numpy
import Queue
import logging
import multiprocessing

from ConfigParser import ConfigParser

from . import simulation_protocol
from . import common
from . import utils

from FeatureIdentificationDevice import FeatureIdentificationDevice
from StrokerProtocolDevice       import StrokerProtocolDevice
from ControlObject               import ControlObject
from WasatchBus                  import WasatchBus
from Reading                     import Reading

log = logging.getLogger(__name__)

class WasatchDevice(object):
    """ Provide an interface to the actual libusb bus.  The summary object is
        used in order to pass just a simple python object on the multiprocessing
        queue instead of an entire device object. Passing the entire device
        object caused problems on Windows.

        MZ: some of these methods are called from MainProcess, others by
        subprocess. """

    def __init__(self, uid=None, bus_order=0, tolerant=True):
        super(WasatchDevice, self).__init__()
        log.debug("%s setup", self.__class__.__name__)

        self.uid = uid
        self.bus_order = bus_order
        self.tolerant = tolerant
        self.connected = False

        self.settings = multiprocessing.Queue()
        self.backend_error = 0

        control_object = ControlObject("integration", 10)
        self.settings.put(control_object)

        # non-FID devices won't have all these fields, so provide defaults
        # (MZ: hardcodes)

        self.serial_number          = "pre-init"
        self.sw_code                = "pre-init"
        self.int_time               = "pre-init"
        self.fpga_rev               = "pre-init"
        self.ccd_gain               = "pre-init"
        self.model                  = 785
        self.calibration_by         = None
        self.calibration_date       = None
        self.baud_rate              = 0
        self.has_cooling            = False
        self.has_battery            = False
        self.has_laser              = False
        self.slit_size              = 10
        self.excitation             = 785
        self.wavelength_coeff_0     = 1 # 7.48485E+02   # 1
        self.wavelength_coeff_1     = 1 # 2.47398E-01   # 1
        self.wavelength_coeff_2     = 0 # 1.45487E-05   # 0
        self.wavelength_coeff_3     = 0 # -4.65241E-08  # 0
        self.detector_tec_setpoint_degC = 10     # MZ: used at this layer?
        self.degC_to_dac_coeff_0    = 4258.1
        self.degC_to_dac_coeff_1    = -159.1
        self.degC_to_dac_coeff_2    = -0.952
        self.tec_r298               = 10000
        self.tec_beta               = 3450
        self.tmax                   = 0.0
        self.tmin                   = 0.0
        self.adc_to_degC_coeff_0    = -108264.69890926 # the "new" 7031 detector readback coefficients (MZ: why 7031?)
        self.adc_to_degC_coeff_1    = 140.86099686
        self.adc_to_degC_coeff_2    = -0.0580806
        self.detector               = "NA"
        self.pixels                 = 1024
        self.pixel_height           = 1
        self.max_integration        = 10000
        self.min_integration        = 1     # MZ: I'm not sure any of our spectrometers can do this
        self.bad_pixels             = []

        self.summary                = None
        self.wavelengths            = []
        self.wavenumbers            = []
        self.fpga_options           = None

        self.integration            = self.min_integration
        self.reading                = None

        self.summed_spectra         = None
        self.sum_count              = 0

    def connect(self):
        """ Attempt low level connection to the device specified in init.  """
        # MZ: hardcode
        if self.uid == "0x24aa:0x0512":
            log.info("Connected to simulation device")
            self.hardware = simulation_protocol.SimulateMaterial()
            self.connected = True
            self.populate_summary()
            return True

        result = self.connect_feature_identification()
        if result == True:
            log.info("Connected to feature identification device")
            self.connected = True
            self.load_eeprom_settings()
            self.populate_summary()
            return True

        result = self.connect_stroker_protocol()
        if result == True:
            log.info("Connected to stroker protocol device")
            self.connected = True
            self.populate_summary()
            return True

        log.debug("Can't find FID or SP class device")

        return False

    def connect_stroker_protocol(self):
        """ Given a specified universal identifier, attempt to connect to the device using stroker protocol. """
        FID_list = ["0x1000", "0x2000", "0x3000", "0x4000"]

        if self.uid == None:
            log.debug("No specified UID for stroker protocol connect")
            return False

        if any(fid in self.uid for fid in FID_list):
            log.debug("Compatible feature ID not found")
            return False

        if self.backend_error >= 1:
            if self.backend_error == 2:
                log.warn("Don't attempt to connect with no backend")
            return

        # MZ: what is self.uid?
        dev = None
        try:
            bus_pid = self.uid[7:]
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

    def connect_feature_identification(self):
        """ Given a specified universal identifier, attempt to connect to the 
            device using feature identification firmware. """
        FID_list = ["0x1000", "0x2000", "0x3000", "0x4000"]

        if self.uid == None:
            log.debug("No specified UID for feature id connect")
            return False

        if not any(fid in self.uid for fid in FID_list):
            log.debug("Compatible feature ID not found")
            return False

        if self.backend_error >= 1:
            if self.backend_error == 2:
                log.warn("Don't attempt to connect with no backend")
            return

        dev = None
        try:
            bus_pid = self.uid[7:]
            log.debug("connect_fid: Attempt connection to bus_pid %s (bus_order %d)", bus_pid, self.bus_order)

            dev = FeatureIdentificationDevice(pid=bus_pid, bus_order=self.bus_order)
            result = False

            try:
                result = dev.connect()
            except Exception as exc:

                # MZ: what is this? we retry with bus_order 0, PID 0x2000?
                log.critical("Connect level exception: %s", exc)
                dev = FeatureIdentificationDevice(pid="0x2000", bus_order=0)
                try:
                    result = dev.connect()
                except Exception as exc:
                    log.critical("SECOND Level exception: %s", exc)

            if result != True:
                log.critical("Low level failure in device connect")
                return False
            self.hardware = dev

        except Exception as exc:
            log.critical("Problem connecting to: %s", self.uid, exc_info=1)
            return False

        log.info("Connected to FeatureIdentificationDevice %s", self.uid)
        return True

    def populate_summary(self):
        """ generate name-value ASCII summary, populate wavelengths/numbers """
        # MZ: why wasn't this a dict?

        if self.connected == False:
            log.critical("Can't print summary, no connection")
            # MZ: return?

        dev = self.hardware

        try:
            self.serial_number = dev.get_serial_number()
            self.sw_code       = dev.get_standard_software_code()
            self.int_time      = dev.get_integration_time()
            self.fpga_rev      = dev.get_fpga_revision()
            self.ccd_gain      = dev.get_ccd_gain()
            self.model         = dev.get_model_number()

            # compare vs EEPROM
            sensor_pixels = dev.get_sensor_line_length()
            if sensor_pixels != self.pixels:
                log.warn("Pixel count mismatch: EEPROM %d overidden by sensor_line_length %d",
                    self.pixels, sensor_pixels)
                self.pixels = sensor_pixels

            self.update_wavelengths()

        except Exception as exc:
            log.critical("Problem getting basic details", exc_info=1)

        # NJH 20170118 UnicodeDecodeError on fpga_rev get from a "Known
        # working" unit. First time this error appeared. Why here in the
        # variable population but not above in the log prints? Data as
        # of 2017-04-11 13:40 seems to indicate this was a user error,
        # where I had it connected to a windows virtual machine at the
        # same time. It was a race condition or analagous.
        try:
            self.summary  = "Serial:   %s\n" % self.serial_number   # MZ: note "serial" HAS to be first (control.py splits it by index)
            self.summary += "Firmware: %s\n" % self.sw_code
            self.summary += "Int Time: %s\n" % self.int_time
            self.summary += "FPGA:     %s\n" % self.fpga_rev
            self.summary += "Gain:     %s\n" % self.ccd_gain
            self.summary += "Model:    %s\n" % self.model

            log.info("Serial:   %s" % self.serial_number)
            log.info("Firmware: %s" % self.sw_code)
            log.info("Int Time: %s" % self.int_time)
            log.info("FPGA:     %s" % self.fpga_rev)
            log.info("Gain:     %s" % self.ccd_gain)
            log.info("Model:    %s" % self.model)
        except Exception as exc:
            log.critical("Problem populating summary", exc_info=1)

        log.debug("Device Summary:\n%s" % self.summary)

    def load_eeprom_settings(self):
        self.model               = self.hardware.model              
        self.serial_number       = self.hardware.serial_number      
        self.baud_rate           = self.hardware.baud_rate          
        self.has_cooling         = self.hardware.has_cooling        
        self.has_battery         = self.hardware.has_battery        
        self.has_laser           = self.hardware.has_laser          
        self.excitation          = self.hardware.excitation         
        self.slit_size           = self.hardware.slit_size          
                                                           
        self.wavelength_coeff_0  = self.hardware.wavelength_coeff_0 
        self.wavelength_coeff_1  = self.hardware.wavelength_coeff_1 
        self.wavelength_coeff_2  = self.hardware.wavelength_coeff_2 
        self.wavelength_coeff_3  = self.hardware.wavelength_coeff_3 
        self.degC_to_dac_coeff_0 = self.hardware.degC_to_dac_coeff_0
        self.degC_to_dac_coeff_1 = self.hardware.degC_to_dac_coeff_1
        self.degC_to_dac_coeff_2 = self.hardware.degC_to_dac_coeff_2
        self.adc_to_degC_coeff_0 = self.hardware.adc_to_degC_coeff_0
        self.adc_to_degC_coeff_1 = self.hardware.adc_to_degC_coeff_1
        self.adc_to_degC_coeff_2 = self.hardware.adc_to_degC_coeff_2
        self.tmax                = self.hardware.tmax               
        self.tmin                = self.hardware.tmin               
        self.tec_r298            = self.hardware.tec_r298           
        self.tec_beta            = self.hardware.tec_beta           
        self.calibration_date    = self.hardware.calibration_date   
        self.calibration_by      = self.hardware.calibration_by     
                                                           
        self.detector            = self.hardware.detector           
        self.pixels              = self.hardware.pixels             
        self.pixel_height        = self.hardware.pixel_height       
        self.min_integration     = self.hardware.min_integration    
        self.max_integration     = self.hardware.max_integration    

        self.bad_pixels          = self.hardware.bad_pixels

        # not really EEPROM, but go ahead
        self.fpga_options        = self.hardware.fpga_options

    def disconnect(self):
        log.info("WasatchDevice.disconnect: calling hardware disconnect")
        try:
            self.hardware.disconnect()
            # MZ: should this not update the bus, which is checked by control.connect_new?
        except Exception as exc:
            log.critical("Issue disconnecting hardware", exc_info=1)

        time.sleep(0.1)
        return True

    # Assumes bad_pixels is a sorted array (possibly empty)
    def correct_bad_pixels(self, spectrum):

        if not self.bad_pixels:
            return

        if not spectrum:
            return

        pixels = len(spectrum)

        # iterate over each bad pixel
        i = 0
        while i < len(self.bad_pixels):

            bad_pix = self.bad_pixels[i]

            if bad_pix == 0:
                # handle the left edge
                next_good = bad_pix + 1
                while next_good in self.bad_pixels and next_good < pixels:
                    next_good += 1
                    i += 1
                if next_good < pixels:
                    for j in range(next_good):
                        spectrum[j] = spectrum[next_good]
            else:

                # find previous good pixel
                prev_good = bad_pix - 1
                while prev_good in self.bad_pixels and prev_good >= 0:
                    prev_good -= 1

                if prev_good >= 0:
                    # find next good pixel
                    next_good = bad_pix + 1
                    while next_good in self.bad_pixels and next_good < pixels:
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

    # MZ: This is akin to getSpectrum() in other drivers.  Can be called directly
    #     in a blocking interface, or from subprocess in non-blocking queued architecture.
    #     Note that subprocess.acquire_data() takes a common.acquisition_mode argument,
    #     where this does not.
    def acquire_data(self):
        """ Process all enqueued settings, then read actual data from the device.

            Notes:
                - we always return raw readings, even during scan averaging (.spectrum)
                - if averaging was enabled and we're complete, then we return the
                  averaged "complete" INSTEAD OF the raw
                - we always return temperatures for live GUI updates
                - we always perform bad pixel correction

                - currently returning INTEGRAL averages (including bad_pixel)
        """

        log.debug("Device acquire_data")

        # yes, allow settings to change in the midst of a long average; this way
        # they can turn off laser, disable averaging etc
        self.process_settings()

        averaging_enabled = (self.hardware.scans_to_average > 1)

        # start a new reading
        reading = Reading()
        reading.integration  = self.hardware.integration
        reading.laser_status = self.hardware.laser_status

        # collect next spectrum
        try:
            reading.spectrum = self.hardware.get_line()

            log.debug("device.acquire_data: got %s ...", reading.spectrum[0:9])

            # bad pixel correction
            if self.hardware.bad_pixel_mode == common.bad_pixel_mode_average:
                self.correct_bad_pixels(reading.spectrum)

            log.debug("device.acquire_data: after bad_pixel correction: %s ...", reading.spectrum[0:9])

            # update summed spectrum
            if averaging_enabled:
                if self.sum_count == 0:
                    self.summed_spectra = numpy.array([float(i) for i in reading.spectrum])
                else:
                    log.debug("device.acquire_data: summing spectra")
                    self.summed_spectra = numpy.add(self.summed_spectra, reading.spectrum)
                self.sum_count += 1
                log.debug("device.acquire_data: summed_spectra : %s ...", self.summed_spectra[0:9])

        except Exception as exc:
            log.critical("Error reading hardware data", exc_info=1)
            reading.failure = exc

        # pass this upstream for GUI display
        reading.sum_count = self.sum_count

        # read detector temperature if applicable (should we do this for Ambient as well?)
        if True or self.hardware.has_cooling:
            try:
                reading.detector_temperature_raw  = self.hardware.get_detector_temperature_raw()
                reading.detector_temperature_degC = self.hardware.get_detector_temperature_degC(reading.detector_temperature_raw)
            except Exception as exc:
                if not self.tolerant:
                    log.critical("Error reading detector temperature", exc_info=1)
                    reading.failure = exc
                else:
                    log.debug("Error reading detector temperature", exc_info=1)

        # only read laser temperature if we have a laser
        if self.hardware.has_laser:
            try:
                reading.laser_temperature_raw  = self.hardware.get_laser_temperature_raw()
                reading.laser_temperature_degC = self.hardware.get_laser_temperature_degC(reading.laser_temperature_raw)
            except Exception as exc:
                if self.tolerant:
                    log.debug("Error reading laser temperature", exc_info=1)
                else:
                    log.critical("Error reading laser temperature", exc_info=1)
                    reading.failure = exc

        # read secondary ADC if requested
        if self.hardware.secondary_adc_enabled:
            try:
                self.hardware.select_adc(1)
                reading.secondary_adc_raw = self.hardware.get_secondary_adc_raw()
                reading.secondary_adc_calibrated = self.hardware.get_secondary_adc_calibrated(reading.secondary_adc_raw)
                self.hardware.select_adc(0)
            except Exception as exc:
                if self.tolerant:
                    log.debug("Error reading secondary ADC", exc_info=1)
                else:
                    log.critical("Error reading secondary ADC", exc_info=1)
                    reading.failure = exc

        # have we completed the averaged reading?
        if averaging_enabled:
            if self.sum_count >= self.hardware.scans_to_average:
                # if we wanted to send the averaged spectrum as ints, use numpy.ndarray.astype(int)
                reading.spectrum = numpy.divide(self.summed_spectra, self.sum_count).tolist()
                log.debug("device.acquire_data: averaged_spectrum : %s ...", reading.spectrum[0:9])
                reading.averaged = True

                # reset for next average
                self.summed_spectra = None
                self.sum_count = 0

        return reading

    # MZ: called by acquire_data, ergo subprocess
    def process_settings(self):
        """ Process every entry on the settings queue, write them to the
            device. Failures when writing settings are collected by this
            exception handler. """

        log.debug("process_settings: start")
        control_object = "throwaway"
        while control_object != None:
            try:
                control_object = self.settings.get_nowait()
                log.debug("process_settings: %s -> %s", control_object.setting, control_object.value)
                self.hardware.write_setting(control_object)
            except Queue.Empty:
                log.debug("process_settings: queue empty")
                control_object = None
            except Exception as exc:
                log.critical("process_settings: error dequeuing or writing control object", exc_info=1)
                raise

        log.debug("process_settings: done")

    def update_wavelengths(self):
        self.wavelengths = utils.generate_wavelengths(self.pixels,
                                                      self.wavelength_coeff_0,
                                                      self.wavelength_coeff_1,
                                                      self.wavelength_coeff_2,
                                                      self.wavelength_coeff_3)
        if self.excitation > 0:
            self.wavenumbers = utils.generate_wavenumbers(self.excitation, self.wavelengths)
        else:
            log.debug("No excitation defined")
            self.wavenumbers = None

    # called by subprocess.continuous_poll
    def change_setting(self, setting, value):
        """ Add the specified setting and value to the local control queue. """
        log.debug("WasatchDevice.change_setting: %s -> %s", setting, value)
        control_object = ControlObject(setting, value)

        if control_object.setting == "scans_to_average":
            self.reading = None

        try:
            self.settings.put(control_object)
        except Exception as exc:
            log.critical("WasatchDevice.change_setting: can't enqueue %s -> %s",
                setting, value, exc_info=1)
