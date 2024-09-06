import time
import math
import numpy as np
import logging

from datetime import datetime

from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest  import SpectrometerRequest
from .Reading              import Reading

log = logging.getLogger(__name__)

class AutoRaman:
    """
    This class encapsulates Dieter's Auto-Raman algorithm, which optimizes 
    integration time (and gain on XS series spectrometers) to achieve a
    target window of counts, then uses the configured measurement time
    to maximize scan averaging at with those acquisition parameters.

    As calling software will not necessarily expect the configured "default" 
    integration time and gain to change, the class restores those to previous
    levels after a measurement.

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

    """

    INTER_SPECTRA_DELAY_MS = 50

    def __init__(self, wasatch_device):
        self.wasatch_device = wasatch_device

    def from_db_to_linear(self, x):
        return 10 ** (x / 20.0)

    def from_linear_to_db(self, x):
        return 20 * math.log(x, 10)

    def inter_spectrum_delay(self):
        time.sleep(self.INTER_SPECTRA_DELAY_MS / 1000)

    def measure(self, auto_raman_request):
        """
        @returns a Reading wrapped in a SpectrometerResponse
        """
        if auto_raman_request is None:
            log.error("measure requires AutoRamanRequest")
            return None

        # cache initial state
        self.start_time = datetime.now()
        initial_int_time = self.wasatch_device.settings.state.integration_time_ms
        initial_gain_db = self.wasatch_device.settings.state.gain_db
        log.debug(f"get_auto_spectrum: caching initial state ({initial_int_time}ms, {initial_gain_db}dB)")

        # generate auto-Raman measurement
        reading = self.get_auto_spectrum(auto_raman_request)

        # for now, don't restore initial state -- send optimized values back in 
        # Reading so ENLIGHTEN can update GUI appropriately
        if False:
            log.debug(f"get_auto_spectrum: restoring initial state")
            self.set_integration_time_ms(initial_int_time)
            self.set_gain_db(initial_gain_db)

        return SpectrometerResponse(data=reading)

    def get_avg_spectrum(self, int_time, gain_db, num_avg, dummy=True):
        """ Takes a single throwaway, then averages num_avg spectra """

        self.set_integration_time_ms(int_time)
        self.set_gain_db(gain_db)
        self.inter_spectrum_delay()

        # perform one throwaway
        if dummy:
            throwaway = self.get_spectrum()

        sum_spectrum = np.zeros(self.wasatch_device.settings.pixels())
        for _ in range(num_avg):
            spectrum = np.array(self.get_spectrum())
            sum_spectrum = sum_spectrum + spectrum
            self.inter_spectrum_delay()

        return sum_spectrum / num_avg

    def get_auto_spectrum(self, request):
        """
        @returns a Reading with specturm, dark and averaged_count populated
        """
        log.debug(f"get_auto_spectrum: start (max_ms {request.max_ms})")

        int_time = request.start_integ_ms
        gain_db = request.start_gain_db

        gain_linear = self.from_db_to_linear(gain_db)
        min_gain_linear = self.from_db_to_linear(request.min_gain_db)
        max_gain_linear = self.from_db_to_linear(request.max_gain_db)

        num_avg = 1

        # get one Raman spectrum to start (no dark)
        log.debug(f"taking initial spectrum (integ {int_time}, gain {gain_db})")
        self.set_laser_enable(True)
        spectrum = self.get_avg_spectrum(int_time, gain_db, num_avg=1)

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
                int_time *= scale_factor

                # check int time does not exceed maximum
                if int_time > request.max_integ_ms:
                    # however much int time exceeds the max, transfer scaling to gain
                    gain_linear *= int_time / request.max_integ_ms
                    int_time = request.max_integ_ms

            elif scale_factor < 1.0:

                # if saturating, accelerate drop. do not use signal factor
                if max_signal >= request.saturation:
                    scale_factor = request.drop_factor

                # decrease gain first (INCREASE int time first implies DECREASE int time last)
                gain_linear *= scale_factor

                # check we did not drop below min gain
                if gain_linear < min_gain_linear:
                    # dump the overshoot into decreasing int time
                    int_time *= gain_linear / min_gain_linear
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
                        if self.wasatch_device.settings.is_xs() and (gain_db < request.max_gain_db):
                            gain_db += 0.1 
                        else: 
                            quit_loop = True
                else:
                    # was supposed to shrink

                    # prefer to shrink gain
                    if self.wasatch_device.settings.is_xs() and (gain_db > request.min_gain_db):
                        gain_db -= 0.1 
                    else:
                        # fail-over to shrinking integration
                        if int_time > request.min_integ_ms:
                            int_time -= 1
                        else:
                            quit_loop = True

            log.debug(f"integ now {int_time}, gain now {gain_db} (linear {gain_linear:.4f})")

            log.debug(f"Taking spectrum #{loop_count}")
            spectrum = self.get_avg_spectrum(int_time, gain_db, num_avg=1)
            max_signal = spectrum.max()

            if max_signal < request.max_counts and max_signal > request.min_counts:
                log.debug("===> achieved window")
                quit_loop = True
            elif max_signal < request.min_counts and int_time >= request.max_integ_ms and gain_db >= request.max_gain_db:
                log.debug("can't achieve window within acquisition parameter limits")
                quit_loop = True

        # decide on number of averages - all times in ms
        # include dark + signal

        num_avg = math.floor(request.max_ms / (2 * int_time))
        num_avg = max(1, num_avg)
        expected_ms = num_avg * 2 * int_time
        log.debug(f"based on max_ms {request.max_ms} and int_time {int_time} ms, computed num_avg {num_avg} (expected_ms {expected_ms})")

        # now get the spectrum with the chosen parameters
        # take two averaged spectra here: avg signal first, then turn laser off, then take dark
        # (this saves the laser warm up)

        # 1. signal - laser is still on
        log.debug(f"taking {num_avg} averaged Raman spectra")
        new_spectrum = self.get_avg_spectrum(int_time, gain_db, num_avg, dummy=False)

        # 2. turn laser off
        self.set_laser_enable(False)

        # 3. take dark
        log.debug(f"taking {num_avg} averaged darks")
        new_dark = self.get_avg_spectrum(int_time, gain_db, num_avg, dummy=False)

        # correct signal minus dark
        spectrum = new_spectrum - new_dark

        reading = Reading()
        reading.spectrum = spectrum
        reading.dark = new_dark
        reading.averaged_count = num_avg
        reading.new_integration_time_ms = int_time
        reading.new_gain_db = gain_db

        log.debug("done")
        return reading

    ############################################################################
    # wrappers over stupidly complicated WasatchDevice interface
    ############################################################################

    def set_laser_enable(self, flag):
        self.wasatch_device.hardware.handle_requests([SpectrometerRequest('set_laser_enable', args=[flag])])

    def set_integration_time_ms(self, ms):
        if ms != self.wasatch_device.settings.state.integration_time_ms:
            self.wasatch_device.hardware.handle_requests([SpectrometerRequest('set_integration_time_ms', args=[ms])])

    def set_gain_db(self, db):
        if self.wasatch_device.settings.is_xs():
            if db != self.wasatch_device.settings.state.gain_db:
                self.wasatch_device.hardware.handle_requests([SpectrometerRequest('set_detector_gain', args=[db])])

    def get_spectrum(self):
        result = self.wasatch_device.hardware.handle_requests([SpectrometerRequest("get_line")])[0]
        if result is None or isinstance(result, bool) or result.error_msg != '':
            raise(Exception(f"get_spectrum returned {result}"))
        spectrum = result.data.spectrum
        self.inter_spectrum_delay()
        return np.array(spectrum)
