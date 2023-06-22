"""
This is a simple script showing how to connect and control a Wasatch Photonics 
Raman spectrometer using Wasatch.PY.  

In particular, it walks the user through a short process to optimize the working
distance by checking the height of a specific expected Raman peak (the 801.3cm⁻¹
peak of cyclohexane, in this case) matches a prescribed threshold.
"""

import sys
import time
import wasatch
import numpy as np
import scipy.signal

from wasatch.WasatchBus    import WasatchBus
from wasatch.WasatchDevice import WasatchDevice
from wasatch.RealUSBDevice import RealUSBDevice

LASER_WARMUP_SEC    = 10
EXPECTED_PEAK       = 801.3             # cyclohexane (cm⁻¹)
EXPECTED_COUNTS     = 4000 
PEAK_TOLERANCE_CM   = 5                 # allow peaks to move by as much as 5cm⁻¹
TEMPFILE            = "spectrum.csv"    # for debugging

class Workflow:

    def __init__(self, integ_time_ms, laser_power_mW):
        self.integ_time_ms = integ_time_ms
        self.laser_power_mW = laser_power_mW

    def connect(self): # -> bool 
        bus = WasatchBus(use_sim=False)
        if not bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return False

        device_id = bus.device_ids[0]
        print(f"connecting to {device_id}")
        device_id.device_type = RealUSBDevice(device_id)
        device = WasatchDevice(device_id)
        ok = device.connect()
        if not ok:
            print("can't connect to %s", device_id)
            return False

        # take convenience handles to SpectrometerSettings and FeatureIdentificationDevice 
        self.settings = device.settings
        self.fid = device.hardware
        if self.settings.wavelengths is None:
            print("script requires Raman spectrometer")
            return False

        print("connected to %s %s with %d pixels (%.2f, %.2fnm) (%.2f, %.2fcm¹)" % (
            self.settings.eeprom.model,
            self.settings.eeprom.serial_number,
            self.settings.pixels(),
            self.settings.wavelengths[0],
            self.settings.wavelengths[-1],
            self.settings.wavenumbers[0],
            self.settings.wavenumbers[-1]))

        self.fid.set_laser_power_high_resolution(True)

        return True

    def optimize_working_distance(self):
        print(f"setting integration time to {self.integ_time_ms}ms")
        self.fid.set_integration_time_ms(self.integ_time_ms)

        print(f"setting laser power to {self.laser_power_mW}mW")
        self.fid.set_laser_power_mW(self.laser_power_mW)

        done = False
        while not done:
            done = self.test_working_distance()

    def get_spectrum(self):
        response = self.fid.get_line()
        if response and response.data:
            spectrum = response.data.spectrum

            # debugging
            with open(TEMPFILE, "w") as outfile:
                outfile.write("\n".join([f"{x:0.2f}" for x in spectrum]))

            return np.asarray(spectrum)

    def test_working_distance(self): # -> bool 
        print("-" * 50)
        print("Please insert calibration sample and press <Enter> (ctrl-C to exit)...", end='')
        try:
            input()
        except:
            print("Program exiting")
            sys.exit(1)

        print("Reading dark spectrum")
        dark = self.get_spectrum()
        if dark is None:
            print("failed to take dark")
            return False

        print("Enabling laser")
        self.fid.set_laser_enable(True)

        print(f"Waiting {LASER_WARMUP_SEC}sec for laser to warmup (required for MML)")
        time.sleep(LASER_WARMUP_SEC)

        print("Taking sample spectrum")
        sample = self.get_spectrum()
        if sample is None:
            print("failed to take sample")
            return False

        print("Disabling laser")
        self.fid.set_laser_enable(False)

        # generate dark-corrected measurement
        measurement = sample - dark
        print(f"dark:        {dark}...")
        print(f"sample:      {sample}...")
        print(f"measurement: {measurement}...")

        # find pixel indices of peaks in the dark-corrected measurement
        # (tune find_peaks arguments as applicable to your setup)
        peak_pixels = scipy.signal.find_peaks(measurement, height=10000)[0]
        print(f"peak pixels:      {peak_pixels}")

        # see if our "calibration peak" is in the list
        peak_pixel = None
        for pixel in peak_pixels:
            peak_cm = self.settings.wavenumbers[pixel]
            if abs(EXPECTED_PEAK - peak_cm) <= PEAK_TOLERANCE_CM:
                print(f"found expected {EXPECTED_PEAK}cm⁻¹ at pixel {pixel} ({peak_cm:0.2f}cm⁻¹)")
                peak_pixel = pixel
                break

        if peak_pixel is None:
            print(f"Failed to find {EXPECTED_PEAK}cm⁻¹ peak in sample: adjust working distance")
            return False

        # see if we've achieved the required intensity
        counts = measurement[peak_pixel]
        if counts < EXPECTED_COUNTS:
            print(f"Failed {EXPECTED_PEAK}cm⁻¹ peak counts too low ({counts} < {EXPECTED_COUNTS}): adjust working distance")
            return False
        
        print(f"Success! {EXPECTED_PEAK}cm⁻¹ peak found with {counts} counts.")
        return True

################################################################################
# main()
################################################################################

workflow = Workflow(integ_time_ms=1000, laser_power_mW=25)
if not workflow.connect():
    sys.exit(1)

workflow.optimize_working_distance()
