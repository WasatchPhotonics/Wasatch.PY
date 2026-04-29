import os
import time
import math
import numpy as np
import logging

from datetime import datetime

from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest  import SpectrometerRequest
from .Reading              import Reading

from . import utils

log = logging.getLogger(__name__)

class OptimizedAcquisitionParameters:
    def __init__(self, int_time, gain_db, optimized_spectrum=None):
        self.int_time = int_time
        self.gain_db = gain_db

        # if optimization was performed, save the final "optimal" sample for re-
        # use in the averaged Raman spectrum
        self.optimized_spectrum = optimized_spectrum

class AutoRaman:
    """
    This class encapsulates Dieter's Auto-Raman algorithm, which optimizes 
    integration time (and gain on XS series spectrometers) to achieve a
    target window of counts, then uses the configured measurement time
    to maximize scan averaging at with those acquisition parameters.

    As calling software will not necessarily expect the configured "default" 
    integration time and gain to change, the class restores those to previous
    levels after a measurement.

    @par Auto-Collection mode

    Auto-Collection mode was added to support STARVIS sensors with their
    much-deeper dynamic range. Essentially this skips the "optimization"
    step of balancing integration time and gain, and simply uses 
    AutoRamanRequest.max_integ_ms. Gain is left at whatever is already 
    configured.

    @par InterfaceDevice handle

    AutoRaman needs an InterfaceDevice to request spectra, fire the laser etc.
    The provided InterfaceDevice should support the following features:

    - device_id
    - settings
    - queue_message()
    - check_alert()
    - handle_requests()
        - sensor
            * get_spectrum([auto_raman_params])
            * set_integration_time_ms
            * set_detector_gain
        - laser
            * can_laser_fire
            * is_laser_firing
            * set_laser_enable
            * get_laser_tec_mode
            * get_ambient_temperature_degC
            * get_laser_warning_delay_sec
            * set_laser_warning_delay_sec

    @par Design Considerations

    Mark made the following changes from Dieter's original algo:

    - don't take throwaways during averaged signal or dark collections, 
      as acquisition parameters aren't changing and sensor should be
      stable
    - don't include laser warning delay when computing num_avg, since
      laser is already enabled and firing

    Mark also made the following decisions regarding ENLIGHTEN integration:

    - return the optimized integration time and gain back to ENLIGHTEN 
      in the Reading object so they will be the new GUI settings if the 
      user simply hits "Play" to resume free-running spectra.
    - ENLIGHTEN will override the init_int_time and init_gain defaults
      in AutoRamanRequest so that if the user hits "Auto-Raman Measurement"
      a second time, the previous settings will be used as the new starting
      point, and (ideally) the initial spectrum will determine that no further 
      optimization is required.

    Points to consider:
    
    - consider rolling the "last optimization" measurement directly into the
      averaged sample spectra (since it had its own throwaway and presumably
      represents a "stable" reading). This could potentially allow for one more
      averaged dark, depending on max_ms.
    - ENLIGHTEN's laser button should still be able to turn the laser OFF at
      any point.
    """

    INTER_SPECTRA_DELAY_MS = 100

    def __init__(self, idevice, auto_collection_mode=False):
        self.idevice = idevice
        self.auto_collection_mode = auto_collection_mode

        self.progress_count = 0
        self.progress_total = 0
        self.optimizing = False

    def measure(self, auto_raman_request):
        if auto_raman_request.onboard and not self.auto_collection_mode:
            return self.measure_firmware(auto_raman_request)
        else:
            return self.measure_software(auto_raman_request)
            
    ############################################################################
    #                                                                          #
    #                          Firmware Implementation                         #
    #                                                                          #
    ############################################################################

    def measure_firmware(self, auto_raman_request):

        # marshall the parameters into a binary payload
        params = auto_raman_request.serialize()
        log.debug(f"measure_firmware: params {utils.to_hex(params)}")

        # generate a request for FID to execute (note this could be a BLEDevice in the future)
        req = SpectrometerRequest("get_spectrum", kwargs={"auto_raman_params": params})
        log.debug(f"measure_firmware: req {req}")

        # perform the measurement -- all the work occurs here
        self.idevice.queue_message("progress_bar", -1)
        result = self.idevice.handle_requests([req])[0]
        self.idevice.queue_message("progress_bar", 100)

        # process the result
        if result is None or isinstance(result, bool) or result.error_msg != '':
            raise(Exception(f"get_spectrum returned {result}"))
        spectrum = result.data.spectrum

        reading = Reading(self.idevice.device_id)
        reading.spectrum                = np.array(spectrum)
        reading.dark                    = None
        reading.averaged                = True
        reading.sum_count               = 1         # todo: get_scans_to_average()
        reading.new_integration_time_ms = None      # todo: get_integration_time_ms()
        reading.new_gain_db             = None      # todo: get_detector_gain()
        reading.laser_enabled           = True
        reading.laser_power_perc = self.idevice.settings.state.laser_power_perc
        reading.laser_power_mW = self.idevice.settings.state.laser_power_mW

        # fill-out some otherwise missing attributes from WasatchDevice.acquire_spectrum_standard
        setting_to_attr = [ ('can_laser_fire',    'laser_can_fire'),
                            ('is_laser_firing',   'laser_is_firing') ]
        if self.settings.is_xs() and self.settings.eeprom.sig_laser_tec:
            setting_to_attr.extend( [ ('get_laser_tec_mode', 'laser_tec_enabled'),
                                      ('get_ambient_temperature_degC', 'ambient_temperature_degC') ] )
        for (setting, attr_name) in setting_to_attr:
            request = SpectrometerRequest(setting)
            result = self.self.idevice.handle_requests([request])[0]
            if result is not None:
                setattr(reading, attr_name, result.data)

        return SpectrometerResponse(data=reading)

    ############################################################################
    #                                                                          #
    #                          Software Implementation                         #
    #                                                                          #
    ############################################################################

    def from_db_to_linear(self, x):
        return 10 ** (x / 20.0)

    def from_linear_to_db(self, x):
        return 20 * math.log(x, 10)

    def inter_spectrum_delay(self):
        time.sleep(self.INTER_SPECTRA_DELAY_MS / 1000)

    def measure_software(self, auto_raman_request):
        """
        @returns a Reading wrapped in a SpectrometerResponse
        """
        if auto_raman_request is None:
            log.error("measure requires AutoRamanRequest")
            return None

        self.settings = self.idevice.settings

        self.optimizing = True

        log.debug(f"measure: auto_raman_request {auto_raman_request}")
        self.bump_progress_bar()

        # cache initial state
        self.start_time = datetime.now()
        initial_laser_warning_delay_sec = self.settings.state.laser_warning_delay_sec

        # apply requested laser warning delay
        if self.settings.is_xs():
            self.idevice.handle_requests([
                SpectrometerRequest('set_laser_warning_delay_sec', args=[ 
                    auto_raman_request.laser_warning_delay_sec 
                ])
            ])

        ########################################################################
        # generate auto-Raman measurement
        ########################################################################

        reading = self.get_auto_spectrum(auto_raman_request)
        if reading.spectrum is None:
            log.debug("looks like Auto-Raman measurement was cancelled")
            self.set_laser_enable(False)

        # restore previous laser warning delay
        if self.settings.is_xs():
            self.idevice.handle_requests([
                SpectrometerRequest('set_laser_warning_delay_sec', args=[ 
                    initial_laser_warning_delay_sec
                ])
            ])

        return SpectrometerResponse(data=reading)

    def bump_progress_bar(self):
        if self.optimizing:
            self.idevice.queue_message("progress_bar", -1)
        else:
            self.progress_count += 1
            self.idevice.queue_message("progress_bar", 100 * (self.progress_count / self.progress_total))

    def get_avg_spectrum(self, int_time, gain_db, num_avg=1, throwaway=True, first=None, label="unknown"):
        """ Takes a single throwaway, then averages num_avg spectra """

        if self.idevice.check_alert("auto_raman_cancel"):
            return

        self.set_integration_time_ms(int_time)
        self.set_gain_db(gain_db)
        self.inter_spectrum_delay()

        # perform one throwaway
        if throwaway:
            throwaway = self.get_spectrum()
            self.save(throwaway, f"{label} throwaway")

        if self.idevice.check_alert("auto_raman_cancel"):
            return

        if first is None:
            sum_spectrum = np.zeros(self.settings.pixels())
            start = 0
        else:
            # we were given the "first" spectrum for use in the average
            sum_spectrum = first
            start = 1

        for i in range(start, num_avg):
            self.bump_progress_bar()

            spectrum = np.array(self.get_spectrum())
            self.save(spectrum, f"{label} {i+1}/{num_avg}")

            if self.idevice.check_alert("auto_raman_cancel"):
                return

            sum_spectrum += spectrum
            self.inter_spectrum_delay()

        return sum_spectrum / num_avg

    def save(self, spectrum, label=None):
        """ Save each spectrum in row-ordered CSV if debug environment variable enabled """
        if "WASATCH_SAVE_AUTO_RAMAN" in os.environ:
            with open("auto-raman-debug.csv", "a") as outfile:
                now = datetime.now().strftime('%F %T.%f')[:-3]
                values = ", ".join([f"{v:.2f}" for v in spectrum])
                outfile.write(f"{now}, {label}, {values}\n")

    def enable_and_warmup_laser(self):
        # enable the laser and wait for it to fire
        self.set_laser_enable(True)
        self.idevice.queue_message("laser_firing_indicators", "firing")

        # arguably, we could use auto_raman_request.laser_warning_delay_sec here,
        # not sure why we're re-reading what we just set in measure_software
        warning_delay_sec = self.get_laser_warning_delay_sec()
        if warning_delay_sec > 0:
            self.idevice.queue_message("marquee_info", f"waiting {warning_delay_sec}sec for laser to fire")
            time.sleep(warning_delay_sec)

        laser_warmup_sec = self.settings.eeprom.laser_warmup_sec
        if laser_warmup_sec > 0:
            self.idevice.queue_message("marquee_info", f"waiting {laser_warmup_sec}sec for laser to stabilize")
            time.sleep(laser_warmup_sec)

    def optimize_acquisition_parameters(self, request):
        """ Assumes laser already enabled and warmed-up """

        log.debug("optimizing acquisition parameters")

        # starting point
        int_time = request.start_integ_ms
        gain_db = request.start_gain_db

        # linearize gain scale
        gain_linear = self.from_db_to_linear(gain_db)
        min_gain_linear = self.from_db_to_linear(request.min_gain_db)
        max_gain_linear = self.from_db_to_linear(request.max_gain_db)

        # take initial Raman measurement with start params
        log.debug(f"taking initial spectrum (integ {int_time}, gain {gain_db})")
        self.idevice.queue_message("marquee_info", "optimizing acquisition parameters")
        spectrum = self.get_avg_spectrum(int_time, gain_db, label="initial", throwaway=True)
        if spectrum is None:
            return

        max_signal = spectrum.max()

        # integration/gain scaling
        quit_loop = False
        loop_count = 0
        while not quit_loop:

            loop_count += 1
            scale_factor = request.target_counts / max_signal
            log.debug(f"loop {loop_count}: counts {max_signal}, scale {scale_factor:.2f}")

            # We distribute scaling among integration time and linear gain
            #
            # mode: int time first
            #
            # if too small:
            # 1. increase int time from start to max
            # 2. increase gain from start to max
            #
            # if too large:
            # 1. decrease gain to min
            # 2. decrease int time

            # in mode 'int time first':
            # - we will first increase integration time - this will give best quality spectrum
            # - if the integration time is at the maximum, we will increase gain to reach expected
            #   signal levels

            prev_integration_time = int_time
            prev_gain_db = gain_db

            if scale_factor > 1.0:

                # do not grow too fast
                if scale_factor > request.max_factor:
                    scale_factor = request.max_factor
                
                # increase int time first
                log.debug(f"optimize_acquisition_parameters: scaling int_time {int_time} UP by scale_factor {scale_factor:.2f}")
                int_time *= scale_factor

                # check int time does not exceed maximum
                if int_time > request.max_integ_ms:
                    # however much int time exceeds the max, transfer scaling to gain
                    gain_factor = int_time / request.max_integ_ms
                    log.debug(f"optimize_acquisition_parameters: scaling gain_linear {gain_linear} UP by gain_factor {gain_factor:.2f}")
                    gain_linear *= gain_factor
                    int_time = request.max_integ_ms

            elif scale_factor < 1.0:

                # if saturating, accelerate drop. do not use signal factor
                if max_signal >= request.saturation:
                    scale_factor = request.drop_factor

                # decrease gain first (INCREASE int time first implies DECREASE int time last)
                log.debug(f"optimize_acquisition_parameters: scaling gain_linear {gain_linear:.2f} DOWN by scale_factor {scale_factor:.2f}")
                gain_linear *= scale_factor

                # check we did not drop below min gain
                if gain_linear < min_gain_linear:
                    # dump the overshoot into decreasing int time
                    int_factor = gain_linear / min_gain_linear
                    log.debug(f"optimize_acquisition_parameters: scaling int_time {int_time} DOWN by int_factor {int_factor:.2f}")
                    int_time *= int_factor
                    gain_linear = min_gain_linear

            # gain is rounded to 0.1 dB
            gain_db = round(self.from_linear_to_db(gain_linear), 1)
            gain_db = min(request.max_gain_db, max(gain_db, request.min_gain_db))

            # integration time is integral (ms)
            int_time = round(int_time)

            if (int_time == prev_integration_time) and (gain_db == prev_gain_db):
                # nothing has changed - rounding problem?
                # Here we do not distinguish between int time forst and gain first, the changes are very small
                if scale_factor > 1.0:
                    # was supposed to increase

                    # prefer to increase integration time
                    if int_time < request.max_integ_ms:
                        int_time += 1 
                    else:
                        # failover to increasing gain
                        if self.settings.is_xs() and (gain_db < request.max_gain_db):
                            gain_db += 0.1 
                        else: 
                            quit_loop = True
                else:
                    # was supposed to shrink

                    # prefer to shrink gain
                    if self.settings.is_xs() and (gain_db > request.min_gain_db):
                        gain_db -= 0.1 
                    else:
                        # fail-over to shrinking integration
                        if int_time > request.min_integ_ms:
                            int_time -= 1
                        else:
                            quit_loop = True

            log.debug(f"optimize_acquisition_parameters: integ now {int_time}, gain now {gain_db:.1f} (linear {gain_linear:.4f})")

            log.debug(f"optimize_acquisition_parameters: Taking spectrum #{loop_count}")
            self.idevice.queue_message("marquee_info", "optimizing acquisition parameters")
            spectrum = self.get_avg_spectrum(int_time, gain_db, label="optimizing", throwaway=True)
            if spectrum is None:
                return

            max_signal = spectrum.max()

            if max_signal < request.max_counts and max_signal > request.min_counts:
                log.debug(f"optimize_acquisition_parameters: ===> achieved window (max_signal {max_signal} in range ({request.min_counts}, {request.max_counts}))")
                quit_loop = True
            elif max_signal < request.min_counts and int_time >= request.max_integ_ms and gain_db >= request.max_gain_db:
                log.debug("optimize_acquisition_parameters: can't achieve window within acquisition parameter limits")
                quit_loop = True
        
        return OptimizedAcquisitionParameters(int_time=int_time, gain_db=gain_db, optimized_spectrum=spectrum)

    def get_auto_spectrum(self, request):
        """
        This is the "Auto-Raman algorithm." Everything that matters happens here.

        @returns a Reading with spectrum, dark and sum_count populated
        """
        log.debug(f"get_auto_spectrum: start (max_ms {request.max_ms})")

        reading = Reading(self.idevice.device_id)

        ########################################################################
        # Optimize Acquisition Parameters
        ########################################################################

        self.enable_and_warmup_laser()

        if self.auto_collection_mode:
            opt_params = OptimizedAcquisitionParameters(int_time = request.max_integ_ms, 
                                                        gain_db  = device.settings.state.gain_db)
        else:
            opt_params = self.optimize_acquisition_parameters(request)
            if not opt_params:
                log.error("unable to optimize acquisition parameters")
                return reading

        ########################################################################
        # Generate Measurement Plan
        ########################################################################

        # decide on number of averages - all times in ms
        # include dark + signal

        self.optimizing = False
        total = math.floor(request.max_ms / opt_params.int_time)   # total number of sample + dark spectra we have time for
        num_avg = math.ceil((total + 1) / 2)            # how many darks to collect
        num_avg = max(1, num_avg)
        num_avg = min(num_avg, request.max_avg)
        self.progress_count = 0
        self.progress_total = 2 * num_avg - 1           # darks + remaining samples
        expected_ms = opt_params.int_time * self.progress_total 
        log.debug(f"based on max_ms {request.max_ms} and int_time {opt_params.int_time} ms, computed num_avg {num_avg} (expected_ms {expected_ms})")

        ########################################################################
        # collect remaining Raman samples
        ########################################################################

        # now get the spectrum with the chosen parameters
        # take two averaged spectra here: avg signal first, then turn laser off, then take dark
        # (this saves the laser warm up)

        # 1. signal - laser is still on
        self.idevice.queue_message("marquee_info", f"averaging {num_avg} Raman spectra at {opt_params.int_time}ms")
        avg_sample = self.get_avg_spectrum(opt_params.int_time, 
                                           opt_params.gain_db, 
                                           num_avg=num_avg, 
                                           throwaway=False, 
                                           first=opt_params.optimized_spectrum, 
                                           label="signal")
        if avg_sample is None:
            return reading
        self.save(avg_sample, "averaged sample")

        ########################################################################
        # collect darks
        ########################################################################

        # 2. turn laser off
        self.set_laser_enable(False)
        self.idevice.queue_message("laser_firing_indicators", "not firing")

        # 3. take dark
        self.idevice.queue_message("marquee_info", f"averaging {num_avg} dark spectra at {opt_params.int_time}ms")
        avg_dark = self.get_avg_spectrum(opt_params.int_time, 
                                         opt_params.gain_db, 
                                         num_avg=num_avg, 
                                         throwaway=True, 
                                         label="dark")
        if avg_dark is None:
            return reading
        self.save(avg_dark, "averaged dark")

        # note that we don't actually perform dark subtraction here -- we return
        # both the averaged Raman sample and the averaged dark, so that the 
        # caller can decide when / how to perform dark subtraction

        reading.spectrum = avg_sample
        reading.dark = avg_dark
        reading.averaged = True
        reading.sum_count = num_avg
        reading.new_integration_time_ms = opt_params.int_time
        reading.new_gain_db = opt_params.gain_db
        reading.laser_enabled = True
        reading.laser_power_perc = self.idevice.settings.state.laser_power_perc
        reading.laser_power_mW = self.idevice.settings.state.laser_power_mW

        ########################################################################
        # fill-in miscellaneous spectrometer state
        ########################################################################

        # fill-out some otherwise missing attributes from WasatchDevice.acquire_spectrum_standard
        setting_to_attr = [ ('can_laser_fire',    'laser_can_fire'),
                            ('is_laser_firing',   'laser_is_firing') ]
        if self.settings.is_xs() and self.settings.eeprom.sig_laser_tec:
            setting_to_attr.extend( [ ('get_laser_tec_mode', 'laser_tec_enabled'),
                                      ('get_ambient_temperature_degC', 'ambient_temperature_degC') ] )
        for (setting, attr_name) in setting_to_attr:
            request = SpectrometerRequest(setting)
            result = self.idevice.handle_requests([request])[0]
            if result is not None and not result.error_msg:
                setattr(reading, attr_name, result.data)

        log.debug("done")
        return reading

    ############################################################################
    # wrappers over stupidly complicated WasatchDevice interface
    ############################################################################

    def get_laser_warning_delay_sec(self):
        response = self.idevice.handle_requests([SpectrometerRequest('get_laser_warning_delay_sec')])[0]
        log.debug(f"get_laser_warning_delay_sec: response {response}")
        if response is None or response.data is None:
            return 5
        return response.data

    def set_laser_enable(self, flag):
        self.idevice.handle_requests([SpectrometerRequest('set_laser_enable', args=[flag])])

    def set_integration_time_ms(self, ms):
        if ms != self.settings.state.integration_time_ms:
            self.idevice.handle_requests([SpectrometerRequest('set_integration_time_ms', args=[ms])])

    def set_gain_db(self, db):
        if self.settings.is_xs():
            if abs(db - self.settings.state.gain_db) > 0.05:
                self.idevice.handle_requests([SpectrometerRequest('set_detector_gain', args=[db])])

    def get_spectrum(self):
        result = self.idevice.handle_requests([SpectrometerRequest("get_spectrum")])[0]
        if result is None or isinstance(result, bool) or result.error_msg != '':
            raise(Exception(f"get_spectrum returned {result}"))
        spectrum = result.data.spectrum
        self.inter_spectrum_delay()
        return np.array(spectrum)
