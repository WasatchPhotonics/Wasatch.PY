#!/usr/bin/env python

import sys
import logging
import argparse
import datetime

import numpy as np

from wasatch.WasatchBus    import WasatchBus
from wasatch.WasatchDevice import WasatchDevice

log = logging.getLogger(__name__)

class Fixture:
    def __init__(self, argv):
        self.args = self.parse_args(argv)
        log.setLevel(self.args.log_level)

        bus = WasatchBus()
        if not bus.device_ids:
            print("no spectrometers found")
            sys.exit(1)

        device_id = bus.device_ids[0]
        print("found %s" % device_id)

        device = WasatchDevice(device_id)
        if not device.connect().data:
            print("connection failed")
            sys.exit(1)

        print("connected to %s %s with %d pixels from (%.2f, %.2f)" % (
            device.settings.eeprom.model,
            device.settings.eeprom.serial_number,
            device.settings.pixels(),
            device.settings.wavelengths[0],
            device.settings.wavelengths[-1]))

        self.device = device        # WasatchDevice
        self.fid = device.hardware  # FeatureInterfaceDevice

    def parse_args(self, argv):
        parser = argparse.ArgumentParser(description="Collect dark spectra vs integration time")
        parser.add_argument("--outfile", type=str, default="spectra.csv", help="where to write spectra")
        parser.add_argument("--log-level", type=str, default="INFO", help="logging level [DEBUG,INFO,WARNING,ERROR,CRITICAL]")
        return parser.parse_args(argv)

    def run(self):

        # let TEC settle
        while True:
            now = datetime.datetime.now()
            temp = self.fid.get_detector_temperature_degC().data
            if "y" in input(f"{now} temperature {temp:+8.2f}C Start collection? (y/[n]) ").lower():
                break

        with open(self.args.outfile, "w") as outfile:
            outfile.write("time, temperature, integration time (ms), min, max, median, mean, stdev, rms, spectra\n")
            for ms in range(100, 5000 + 1, 100):
                now = datetime.datetime.now()
                print(f"{now} taking dark at {ms}ms")

                temp = self.fid.get_detector_temperature_degC().data
                self.fid.set_integration_time_ms(ms)

                spectrum = np.asarray(self.fid.get_line().data.spectrum, dtype=np.float32)
                values = ", ".join([ str(y) for y in spectrum ])

                lo = min(spectrum)
                hi = max(spectrum)
                median = np.median(spectrum)
                mean = np.mean(spectrum)

                stdev = np.std(spectrum)
                rms = np.sqrt(np.mean(spectrum**2))

                outfile.write(f"{now}, {temp:.2f}, {ms}, {lo}, {hi}, {median:.2f}, {mean:.2f}, {stdev:.5e}, {rms:.5e}, {values}\n")

        self.device.disconnect()

fixture = Fixture(sys.argv[1:])
fixture.run()
