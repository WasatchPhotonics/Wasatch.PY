""" Simulated device components for demonstration program. Simple blocking calls
    with simulated delays for simulated spectrometer readings. Long-polling
    multiprocessing wrappers.
"""
import copy
import time
import logging
import numpy

log = logging.getLogger(__name__)

class SimulateMaterial(object):
    """ Read recorded spectrum from file, respond with noise on the
        baseline read from file. Default spectrum is pure noise from numpy.
        When the laser is on, it's the IPA csv entry (0). This is designed
        to simulate a vial of IPA in a holder. Changing the integration time
        scales just the noise on the spectrum. A integration time of 1 has
        1-10k counts of noise. An integration time of 10k has 0-1 counts of
        noise. """

    def __init__(self):
        super(SimulateMaterial, self).__init__()
        log.debug("%s setup", self.__class__.__name__)

        self.serial_number = "SIM-512"
        self.software_code = "SIM.0.0.1"
        self.fpga_revision = "SIM.022-007"

        self.pixels = 1024
        self.ccd_gain = 1.9

        self.max_integration = 10000 # ms
        self.min_integration = 1     # ms

        # For a simple inverse relationship of noise apperance
        self.integration = self.min_integration
        self.noise_level = self.max_integration - self.integration

        # Add to max integration to always show noise
        self.noise_margin = 100

        randint = numpy.random.randint
        startup_noise = randint(low=0, high=self.noise_level, size=self.pixels)
        self.startup_noise = startup_noise
        #print "All startup nosie: %s level %s" % (startup_noise, self.noise_level)

        self.initial_data = self.startup_noise

        self.ipa_data = numpy.asarray(self.load_raw_data()[0])
        self.signal_modifier = self.integration
        self.ipa_data = self.ipa_data / self.signal_modifier

        self.laser_status = "disable"
        self.detector_tec_setpoint_degC = 15.0
        self.detector_tec_enable = 0
        self.ccd_adc_setpoint = 2047 # Midway of a 12bit ADC

        # Defaults from Original (stroker-era) settings. These are known
        # to set ccd setpoints effectively for stroker 785 class units.
        self.original_degC_to_dac_coeff_0 = 3566.62
        self.original_degC_to_dac_coeff_1 = -143.543
        self.original_degC_to_dac_coeff_2 =   -0.324723

        self.degC_to_dac_coeff_0 = self.original_degC_to_dac_coeff_0
        self.degC_to_dac_coeff_1 = self.original_degC_to_dac_coeff_1
        self.degC_to_dac_coeff_2 = self.original_degC_to_dac_coeff_2

        self.model = "785"
        # Set default to zero bad pixels which is a list of -1's of
        # length 15
        self.bad_pixels = []
        for pixel in range(15):
            self.bad_pixels.append(-1)

    def read(self):
        """ Return the spectrum read from file. Add the noise. """
        randint = numpy.random.randint
        temp_data = randint(low=0, high=self.noise_level, size=self.pixels)

        if self.laser_status == "enable":
            temp_data += copy.copy(self.ipa_data)

        return temp_data

    def get_line(self):
        """ Wrap the read data simulation in a sleep-wait to ensure fidelity for 
            longer exposure integration times. """
        wait_interval = (1.0 * self.integration) / 1000.0
        log.debug("Waiting %sms", wait_interval)
        time.sleep(wait_interval)
        return self.read()

    def write_setting(self, record):
        """ Perform the specified setting such as simulating a laser enable, 
            changing the integration time, turning the cooler on etc. """

        log.debug("Changing %s to: %s", record.setting, record.value)
        if record.setting == "laser":
            self.laser_status = record.value

        elif record.setting == "integration":
            self.integration = int(record.value)
            log.debug("Set integration to: %s", self.integration)

            temp_margin = self.max_integration + self.noise_margin
            self.noise_level = temp_margin - self.integration
            log.debug("Set noise level to: %s", self.noise_level)

        elif record.setting == "detector_tec_setpoint_degC":
            self.detector_tec_setpoint_degC = int(record.value)

        elif record.setting == "degC_to_dac_coeffs":
            self.set_degC_to_dac_coeffs(record.value)

        elif record.setting == "detector_tec_enable":
            self.detector_tec_enable = int(record.value)

        else:
            log.critical("Unknown setting: %s", record.setting)
            return False

        return True

    def set_degC_to_dac_coeffs(self, coeffs):
        """ Temporary solution for modifying the CCD TEC setpoint
            calibration coefficients. These are used as part of a third
            order polynomial for transforming the setpoint temperature into
            an AD value. Expects a great deal of accuracy on part of the
            user, otherwise sets default. """

        degC_to_dac_coeff_0 = self.original_degC_to_dac_coeff_0
        degC_to_dac_coeff_1 = self.original_degC_to_dac_coeff_1
        degC_to_dac_coeff_2 = self.original_degC_to_dac_coeff_2
        try:
            (degC_to_dac_coeff_0, degC_to_dac_coeff_1, degC_to_dac_coeff_2) = coeffs.split(" ")
        except Exception as exc:
            log.critical("TEC Coeffs split failiure: %s", exc)
            log.critical("Setting original class coeffs")

        self.degC_to_dac_coeff_0 = float(degC_to_dac_coeff_0)
        self.degC_to_dac_coeff_1 = float(degC_to_dac_coeff_1)
        self.degC_to_dac_coeff_2 = float(degC_to_dac_coeff_2)
        log.info("Succesfully changed CCD TEC setpoint coefficients")

    def load_raw_data(self, filename=None):
        """ Apparently MS windows keeps some portion of the dictreader
            in place that prevents a clean exit in multiprocessing
            applications. This will only manifest when attempting to close
            the enlighten software. The temporary fix here is to load the
            file directly from disk, and manually slice the data required. """

        if filename == None:
            filename = "enlighten/assets/example_data/"
            filename += "Spectra_093016_785L_192.csv"

        log.info("Raw data load file: %s", filename)

	csv_data = []
	line_count = 0
	with open(filename, "r") as csv_file:
	    for line_data in csv_file:

	        if line_count > 1:
		    line_data = line_data.replace('"','')
	            commas = [x.strip() for x in line_data.split(",")]
	            ints = [int(x.strip()) for x in commas[17:]]
	            csv_data.append(ints)
	            #log.info("Strip: %s", csv_data)
		line_count += 1

	return csv_data

    def disconnect(self):
        """ Placeholder to log disconnect event. """
        log.info("Disconnect")
        return True

    def get_serial_number(self):
        return self.serial_number

    def get_standard_software_code(self):
        return self.software_code

    def get_integration_time(self):
        return self.integration

    def get_fpga_revision(self):
        return self.fpga_revision

    def get_ccd_gain(self):
        return self.ccd_gain

    def get_model_number(self):
        return self.model

    def get_detector_temperature_raw(self):
        """ Simulate a 12-bit AD """
        adc_wiggle = 100
        adc_min = self.ccd_adc_setpoint - adc_wiggle
        adc_max = self.ccd_adc_setpoint + adc_wiggle

        adc_value = numpy.random.uniform(low=adc_min, high=adc_max)
        log.debug("RAW adc: %s", adc_value)

        return adc_value

    def get_detector_temperature_degC(self, raw=0):
        """ Return randomized laser and detector temperature simulation within range. """
        detector_wiggle_degC = 2.0
        detector_min_degC    = self.detector_tec_setpoint_degC - detector_wiggle_degC
        detector_max_degC    = self.detector_tec_setpoint_degC + detector_wiggle_degC
        detector_temp_degC   = numpy.random.uniform(low=detector_min_degC, high=detector_max_degC)
        log.debug("CCD: %s", detector_temp_degC)
        return detector_temp_degC

    def get_laser_temperature_raw(self):
        return numpy.random.randint(4096)

    def get_laser_temperature_degC(self, raw=0):
        """ Return randomized laser and ccd temperature simulation within range. """
        laser_temp = numpy.random.uniform(low=35.0, high=45.0)
        log.debug("LASER: %s", laser_temp)
        return laser_temp

    def get_sensor_line_length(self):
        return self.pixels 
