import time
import math
import numpy as np
import logging

from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest  import SpectrometerRequest
from .Reading              import Reading

log = logging.getLogger(__name__)

class AutoRaman:

    INTER_SPECTRA_DELAY_MS = 50

    def __init__(self, wasatch_device):
        self.wasatch_device = wasatch_device

    def from_db_to_linear(self, x):
        return 10 ** (x / 20.0)

    def from_linear_to_db(self, x):
        return 20 * math.log(x, 10)

    def inter_spectrum_delay(self):
        time.sleep(self.INTER_SPECTRA_DELAY_MS / 1000)

    def measure(self, take_one_request):
        """
        @returns a Reading wrapped in a SpectrometerResponse
        """
        if take_one_request is None or take_one_request.auto_raman_request is None:
            log.error("measure requires AutoRamanRequest")
            return None

        # YOU ARE HERE
        reading = self.get_auto_spectrum(take_one_request.auto_raman_request)

        return SpectrometerResponse(data=reading)

    def get_avg_spectrum_with_dummy(self, int_time, gain_db, num_avg):
        """ Takes a single throwaway, then averages num_avg spectra """

        self.set_integration_time_ms(int_time)
        self.set_gain_db(gain_db)
        self.inter_spectrum_delay()

        # perform one throwaway
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

        int_time = request.start_integ_ms
        gain_db = request.start_gain_db

        gain_linear = self.from_db_to_linear(gain_db)
        min_gain_linear = self.from_db_to_linear(request.min_gain_db)
        max_gain_linear = self.from_db_to_linear(request.max_gain_db)

        num_avg = 1

        # get one Raman spectrum to start (no dark)
        log.debug(f"taking initial spectrum (integ {int_time}, gain {gain_db})")
        self.set_laser_enable(True)
        spectrum = self.get_avg_spectrum_with_dummy(int_time, gain_db, num_avg=1)

        max_signal = spectrum.max()

        # integration/gain scaling
        quit_loop = False
        loop_count = 0
        while not quit_loop:

            loop_count += 1
            scale_factor = request.target_counts / max_signal
            log.debug("loop {loop_count}: counts {max_signal}, scale {scale_factor}")

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
                        if gain_db < request.max_gain_db:
                            gain_db += 0.1 
                        else: 
                            quit_loop = True
                else:
                    # was supposed to shrink

                    # prefer to shrink gain
                    if gain_db > request.min_gain_db:
                        gain_db -= 0.1 
                    else:
                        # fail-over to shrinking integration
                        if int_time > request.min_integ_ms:
                            int_time -= 1
                        else:
                            quit_loop = True

            log.debug(f"integ now {int_time}, gain now {gain_db} (linear {gain_linear})")

            log.debug(f"Taking spectrum #{loop_count}")
            spectrum = self.get_avg_spectrum_with_dummy(int_time, gain_db, num_avg=1)
            max_signal = spectrum.max()

            if max_signal < request.max_counts and max_signal > request.min_counts:
                log.debug("achieved window")
                quit_loop = True
            elif max_signal < request.min_counts and int_time >= request.max_integ_ms and gain_db >= request.max_gain_db:
                log.debug("can't achieve window within acquisition parameter limits")
                quit_loop = True

        # decide on number of averages - all times in ms
        # include dark + signal + laser warm up
        # note: also include one dummy scan each (dark and signal)...

        num_avg = round((request.max_ms - request.laser_delay_ms) / (2 * int_time)) - 1
        num_avg = max(1, num_avg)

        # now get the spectrum with the chosen parameters
        # take two averaged spectra here: avg signal first, then turn laser off, then take dark
        # (this saves the laser warm up)

        # 1. signal - laser is still on
        log.debug("taking {num_avg} averaged Raman spectra")
        new_spectrum = self.get_avg_spectrum_with_dummy(int_time, gain_db, num_avg)

        # 2. turn laser off
        self.set_laser_enable(False)

        # 3. take dark
        log.debug("taking {num_avg} averaged darks")
        new_dark = self.get_avg_spectrum_with_dummy(int_time, gain_db, num_avg)

        # correct signal minus dark
        spectrum = new_spectrum - new_dark

        reading = Reading()
        reading.spectrum = spectrum
        reading.dark = new_dark
        reading.averaged_count = num_avg

        log.debug("done")
        return reading

    ############################################################################
    # wrappers over stupidly complicated WasatchDevice interface
    ############################################################################

    def set_laser_enable(self, flag):
        self.wasatch_device.hardware.handle_requests([SpectrometerRequest('set_laser_enable', args=[flag])])

    def set_integration_time_ms(self, ms):
        self.wasatch_device.hardware.handle_requests([SpectrometerRequest('set_integration_time_ms', args=[ms])])

    def set_gain_db(self, db):
        self.wasatch_device.hardware.handle_requests([SpectrometerRequest('set_detector_gain', args=[db])])

    def get_spectrum(self):
        res = self.wasatch_device.hardware.handle_requests([SpectrometerRequest("get_line")])[0]
        if res is None or isinstance(res, bool) or res.error_msg != '':
            raise res
        spectrum = res.data.spectrum
        self.inter_spectrum_delay()
        return np.array(spectrum)
