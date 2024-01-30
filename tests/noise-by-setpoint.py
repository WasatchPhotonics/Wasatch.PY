#!/usr/bin/env python

import re
import os
import sys
import time
import numpy
import signal
import logging
import datetime
import argparse

import wasatch
from wasatch import utils
from wasatch import applog
from wasatch.WasatchBus           import WasatchBus
from wasatch.WasatchDevice        import WasatchDevice
from wasatch.WasatchDeviceWrapper import WasatchDeviceWrapper

log = logging.getLogger(__name__)

class Test:

    def __init__(self, argv=None):
        self.bus     = None
        self.device  = None
        self.logger  = None
        self.outfile = None
        self.exiting = False

        self.args = self.parse_args(argv)

        self.logger = applog.MainLogger(self.args.log_level)

    def parse_args(self, argv):
        parser = argparse.ArgumentParser(description="Simple test to acquire spectra from command-line interface")
        parser.add_argument("--log-level", type=str, default="info", help="logging level [debug,info,warning,error,critical]")
        parser.add_argument("--outfile", type=str, default=None, help="output filename (e.g. path/to/spectra.csv)")
        parser.add_argument("--stabilization-threshold", type=float, default=0.25, help="how close we have to be to declare stable temperature")
        parser.add_argument("--stabilization-counts", type=int, default=20, help="how many consecutive stable readings required")
        parser.add_argument("--count", type=int, default=100, help="how many acquisitions at each integration time")
        parser.add_argument("--integration-times-ms", type=str, default="1,10,100", help="comma-separated integration times (default '1,10,100')")

        # parse argv into dict
        args = parser.parse_args(argv[1:])

        # normalize log level
        if not re.match("^(debug|info|error|warning|critical)$", args.log_level.lower()):
            print("Invalid log level: %s (defaulting to INFO)" % args.log_level)
            args.log_level = "INFO"
        args.log_level = args.log_level.upper()

        # normalize integration times
        args.integration_times_ms = [int(x) for x in args.integration_times_ms.split(',')]

        return args
        
    def connect(self):
        self.bus = WasatchBus()
        if not self.bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return 

        device_id = self.bus.device_ids[0]
        device = WasatchDevice(device_id)
        if not device.connect():
            log.critical("connect: can't connect to %s", device_id)
            return

        self.device = device
        return device

    def run(self):
        log.info("NoiseBySetpoint (Wasatch.PY %s)", wasatch.version)

        if self.args.outfile:
            try:
                self.outfile = open(self.args.outfile, "w")
                self.outfile.write("time,temp,%s\n" % ",".join(format(x, ".2f") for x in self.device.settings.wavelengths))
            except:
                log.error("Error initializing %s", self.args.outfile)
                self.outfile = None

        for temp in range(self.device.settings.eeprom.min_temp_degC,
                          self.device.settings.eeprom.max_temp_degC + 1):
            self.stabilize(temp)

            for ms in self.args.integration_times_ms:
                spectra = []
                log.info("taking %d measurements at %dms", self.args.count, ms)
                self.device.change_setting("integration_time_ms", ms)
                for i in range(self.args.count):
                    while True:
                        reading = self.get_reading()
                        if reading or self.exiting:
                           break
                    if self.exiting:
                        break
                    spectra.append(reading.spectrum)

                    delta_temp = abs(temp - reading.detector_temperature_degC)
                    log.debug("reading: setpoint %-3d  integration %4dms  temperature %8.2f  delta %8.2f",
                        temp, ms, reading.detector_temperature_degC, delta_temp)

                    if self.outfile:
                        self.outfile.write("%s,%.2f,%s\n" % (
                            datetime.datetime.now(), 
                            reading.detector_temperature_degC,
                            ",".join(str(x) for x in reading.spectrum)))

                if self.exiting:
                    break

                (mean, noise) = self.process(spectra)
                print("report: setpoint %-3d  integration time %4dms   mean %8.2f  stdev %8.2f" % (
                    temp, ms, mean, noise))
                
            if self.exiting:
                break

    # probably a numpy one-liner for this...
    def process(self, spectra):
        # compute the stdev for each pixel over time, placing in new array
        count = len(spectra)
        pixels = len(spectra[0])
        stdevs = []
        for pix in range(pixels):
            col = []
            for spectrum in spectra:
                col.append(spectrum[pix])
            stdevs.append(numpy.std(col))
        
        # compute the average stdev across all pixels
        stdev = numpy.mean(stdevs)

        # compute the mean for all the spectra
        mean = numpy.mean(spectra)

        return (mean, stdev)

    def stabilize(self, temp):
        log.info("TEC setpoint -> %d", temp)
        self.device.change_setting("detector_tec_setpoint_degC", temp)
        self.device.change_setting("detector_tec_enable", True)

        log.info("waiting for temperature to stabilize")
        self.device.change_setting("integration_time_ms", self.device.settings.eeprom.min_integration_time_ms)

        stable_count = 0
        reading_count = 0
        while True:
            reading_count += 1
            reading = self.get_reading()
            if reading is None:
                return
            delta = abs(reading.detector_temperature_degC - temp)
            if delta <= self.args.stabilization_threshold:
                log.debug("temperature getting stable (value %.2f, delta %.2f)", reading.detector_temperature_degC, delta)
                stable_count += 1
            else:
                log.debug("temperature not stable (value %.2f, delta %.2f)", reading.detector_temperature_degC, delta)
                stable_count = 0
            if stable_count >= self.args.stabilization_counts:
                log.info("temperature stabilized after %d readings", reading_count)
                return 

            time.sleep(0.2)

    def get_reading(self):
        try:
            reading = self.device.acquire_data()
        except:
            self.exiting = True
            log.critical("error acquiring reading", exc_info=1)
            return

        if isinstance(reading, bool):
            if reading:
                log.critical("received poison-pill, exiting")
                self.exiting = True
                return 
            else:
                log.debug("no reading available")
                return
        return reading

################################################################################
# main()
################################################################################

if __name__ == "__main__":
    test = Test(sys.argv)
    if test.connect():
        test.run()
    test.logger.close()
