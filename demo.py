#!/usr/bin/env python

import os
import re
import sys
import time
import numpy
import signal
import logging
import datetime
import argparse
import multiprocessing

import wasatch
from wasatch import utils
from wasatch import applog
from wasatch.WasatchBus           import WasatchBus
from wasatch.WasatchDevice        import WasatchDevice
from wasatch.WasatchDeviceWrapper import WasatchDeviceWrapper

log = logging.getLogger(__name__)

class WasatchDemo(object):

    ############################################################################
    #                                                                          #
    #                               Lifecycle                                  #
    #                                                                          #
    ############################################################################

    def __init__(self, argv=None):
        self.bus     = None
        self.device  = None
        self.logger  = None
        self.outfile = None
        self.exiting = False

        self.args = self.parse_args(argv)

        self.logger = applog.MainLogger(self.args.log_level)

    ############################################################################
    #                                                                          #
    #                             Command-Line Args                            #
    #                                                                          #
    ############################################################################

    def parse_args(self, argv):
        parser = argparse.ArgumentParser(description="Simple demo to acquire spectra from command-line interface")
        parser.add_argument("-l", "--log-level",           type=str, default="INFO", help="logging level [DEBUG,INFO,WARNING,ERROR,CRITICAL]")
        parser.add_argument("-o", "--bus-order",           type=int, default=0,      help="usb device ordinal to connect")
        parser.add_argument("-i", "--integration-time-ms", type=int, default=10,     help="integration time (ms, default 10)")
        parser.add_argument("-s", "--scans-to-average",    type=int, default=1,      help="scans to average (default 1)")
        parser.add_argument("-w", "--boxcar-half-width",   type=int, default=0,      help="boxcar half-width (default 0)")
        parser.add_argument("-d", "--delay-ms",            type=int, default=1000,   help="delay between integrations (ms, default 1000)")
        parser.add_argument("-f", "--outfile",             type=str, default=None,   help="output filename (e.g. path/to/spectra.csv)")
        parser.add_argument("-m", "--max",                 type=int, default=0,      help="max spectra to acquire (default 0, unlimited)")
        parser.add_argument("-b", "--non-blocking",        action="store_true",      help="non-blocking USB interface")

        # parse argv into dict
        args = parser.parse_args(argv[1:])

        # normalize log level
        args.log_level = args.log_level.upper()
        if not re.match("^(DEBUG|INFO|ERROR|WARNING|CRITICAL)$", args.log_level):
            print "Invalid log level: %s (defaulting to INFO)" % args.log_level
            args.log_level = "INFO"

        return args
        
    ############################################################################
    #                                                                          #
    #                              USB Devices                                 #
    #                                                                          #
    ############################################################################

    def connect(self):
        """ If the current device is disconnected, and there is a new device, 
            attempt to connect to it. """

        # if we're already connected, nevermind
        if self.device is not None:
            return

        # lazy-load a USB bus
        if self.bus is None:
            log.debug("instantiating WasatchBus")
            self.bus = WasatchBus(use_sim = False)

        if not self.bus.devices:
            print("No Wasatch USB spectrometers found.")
            return 

        log.debug("connect: trying to connect to new device on bus 1")
        uid = self.bus.devices[0]

        if self.args.non_blocking:
            # this is still buggy on MacOS
            log.debug("instantiating WasatchDeviceWrapper (non-blocking)")
            device = WasatchDeviceWrapper(
                uid=uid,
                bus_order=self.args.bus_order,
                log_queue=self.logger.log_queue,
                log_level=self.args.log_level)
        else:
            log.debug("instantiating WasatchDevice (blocking)")
            device = WasatchDevice(uid, bus_order=self.args.bus_order)

        ok = device.connect()
        if not ok:
            log.critical("connect: can't connect to device on bus 1")
            return

        log.info("connect: device connected")

        self.device = device
        self.reading_count = 0

        return device

    ############################################################################
    #                                                                          #
    #                               Run-Time Loop                              #
    #                                                                          #
    ############################################################################

    def run(self):
        log.info("Wasatch.PY %s Demo", wasatch.version)

        # apply initial settings
        self.device.change_setting("integration_time_ms", self.args.integration_time_ms)
        self.device.change_setting("scans_to_average", self.args.scans_to_average)
        self.device.change_setting("detector_tec_enable", "1")

        # initialize outfile if one was specified
        if self.args.outfile:
            try:
                self.outfile = open(self.args.outfile, "w")
                self.outfile.write("time,temp,%s\n" % ",".join(format(x, ".2f") for x in self.device.settings.wavelengths))
            except:
                log.error("Error initializing %s", self.args.outfile)
                self.outfile = None

        # read spectra until user presses Control-Break
        while not self.exiting:
            start_time = datetime.datetime.now()
            self.attempt_reading()
            end_time = datetime.datetime.now()

            if self.args.max > 0 and self.reading_count >= self.args.max:
                log.info("max spectra reached, exiting")
                self.exiting = True
            else:
                # compute how much longer we should wait before the next reading
                reading_time_ms = int((end_time - start_time).microseconds / 1000)
                sleep_ms = self.args.delay_ms - reading_time_ms
                if sleep_ms > 0:
                    log.debug("sleeping %d ms (%d ms already passed)", sleep_ms, reading_time_ms)
                    try:
                        time.sleep(float(sleep_ms) / 1000)
                    except:
                        log.critical("WasatchDemo.run sleep() caught an exception", exc_info=1)
                        self.exiting = True

        log.info("WasatchDemo.run exiting")

    def attempt_reading(self):
        try:
            reading = self.acquire_reading()
        except Exception, exc:
            log.critical("attempt_reading caught exception", exc_info=1)
            self.exiting = True
            return

        self.reading_count += 1

        if reading.failure:
            log.critical("Hardware ERROR %s", reading.failure)
            log.critical("Device has been disconnected")
            self.device.disconnect()
            self.device = None
            raise Exception("disconnected")

        self.process_reading(reading)

    def acquire_reading(self):
        # We want the demo to effectively block on new scans, so keep
        # polling the subprocess until a reading is ready.  In other apps,
        # we could do other things (like respond to GUI events) if 
        # device.acquire_data() was None (meaning the next spectrum wasn't
        # yet ready).
        while True:
            reading = self.device.acquire_data()
            if reading:
                return reading
            else:
                log.debug("waiting on next reading")

    def process_reading(self, reading):
        if self.args.scans_to_average > 1 and not reading.averaged:
            return

        if self.args.boxcar_half_width > 0:
            spectrum = utils.apply_boxcar(reading.spectrum, self.args.boxcar_half_width)
        else:
            spectrum = reading.spectrum

        spectrum_min = numpy.amin(spectrum)
        spectrum_max = numpy.amax(spectrum)
        spectrum_avg = numpy.mean(spectrum)

        log.info("Reading: %4d  Detector: %5.2f degC  Min: %8.2f  Max: %8.2f  Avg: %8.2f",
            self.reading_count,
            reading.detector_temperature_degC,
            spectrum_min,
            spectrum_max,
            spectrum_avg)

        if self.outfile:
            self.outfile.write("%s,%.2f,%s\n" % (datetime.datetime.now(),
                                                 reading.detector_temperature_degC,
                                                 ",".join(format(x, ".2f") for x in spectrum)))

################################################################################
# main()
################################################################################

def signal_handler(signal, frame):
    log.critical('Interrupted by Ctrl-C')
    clean_shutdown()

def clean_shutdown():
    if demo:
        if demo.args and demo.args.non_blocking and demo.device:
            log.debug("closing background thread")
            demo.device.disconnect()

        if demo.logger:
            log.debug("closing logger")
            demo.logger.close()
    log.info("Exiting")
    sys.exit(0)

demo = None
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    demo = WasatchDemo(sys.argv)
    if demo.connect():
        # Note that on Windows, Control-Break (SIGBREAK) differs from 
        # Control-C (SIGINT); see https://stackoverflow.com/a/1364199
        log.info("Press Control-Break to interrupt...")
        demo.run()

    clean_shutdown()
