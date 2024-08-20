#!/usr/bin/env python
################################################################################
#                                   demo.py                                    #
################################################################################
#                                                                              #
#  DESCRIPTION:  Simple cmd-line demo to confirm that Wasatch.PY is working    #
#                and can connect to and control a spectrometer.                #
#                                                                              #
#  INVOCATION:   $ python -u demo.py                                           #
#                                                                              #
################################################################################

import os
import re
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

class WasatchDemo:

    ############################################################################
    #                                                                          #
    #                               Lifecycle                                  #
    #                                                                          #
    ############################################################################

    def __init__(self):
        self.bus     = None
        self.devices = {}
        self.logger  = None
        self.outfile = None
        self.exiting = False
        self.reading_count = 0

        self.args = self.parse_args()

        if self.args.log_level != "NEVER":
            self.logger = applog.MainLogger(self.args.log_level)
        log.info("Wasatch.PY version %s", wasatch.__version__)

    ############################################################################
    #                                                                          #
    #                             Command-Line Args                            #
    #                                                                          #
    ############################################################################

    def parse_args(self):
        parser = argparse.ArgumentParser(description="Simple demo to acquire spectra from command-line interface")
        parser.add_argument("--log-level",           type=str, default="INFO", help="logging level", choices=["DEBUG","INFO","WARNING","ERROR","CRITICAL","NEVER"])
        parser.add_argument("--integration-time-ms", type=int, default=10,     help="integration time (ms, default 10)")
        parser.add_argument("--scans-to-average",    type=int, default=1,      help="scans to average (default 1)")
        parser.add_argument("--boxcar-half-width",   type=int, default=0,      help="boxcar half-width (default 0)")
        parser.add_argument("--delay-ms",            type=int, default=1000,   help="delay between integrations (ms, default 1000)")
        parser.add_argument("--outfile",             type=str, default=None,   help="output filename (e.g. path/to/spectra.csv)")
        parser.add_argument("--max",                 type=int, default=0,      help="max spectra to acquire (default 0, unlimited)")
        parser.add_argument("--non-blocking",        action="store_true",      help="non-blocking USB interface (WasatchDeviceWrapper instead of WasatchDevice)")
        parser.add_argument("--ascii-art",           action="store_true",      help="graph spectra in ASCII")
        parser.add_argument("--version",             action="store_true",      help="display Wasatch.PY version and exit")

        args = parser.parse_args()
        if args.version:
            print("Wasatch.PY %s" % wasatch.__version__)
            sys.exit(0)

        args.log_level = args.log_level.upper()

        return args
        
    ############################################################################
    #                                                                          #
    #                              USB Devices                                 #
    #                                                                          #
    ############################################################################

    def connect(self):
        """ Connect to all discoverable Wasatch spectrometers.  """

        # lazy-load a USB bus
        if self.bus is None:
            log.debug("instantiating WasatchBus")
            self.bus = WasatchBus()

        if not self.bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return 

        for device_id in self.bus.device_ids:
            log.debug("connect: trying to connect to %s", device_id)

            if self.args.non_blocking:
                # Non-blocking means we instantiate a WasatchDeviceWrapper (WDW)
                # The WDW incapsulates its own threading.Thread (WrapperWorker)
                # which continuously acquires spectra free-running mode.
                log.debug("instantiating WasatchDeviceWrapper (non-blocking)")
                device = WasatchDeviceWrapper(
                    device_id = device_id,
                    log_level = self.args.log_level,
                    callback = self.WrapperWorker_callback)
            else:
                # We're going to place blocking calls to spectrometers, so we can
                # instantiate a plain WasatchDevice (no need for WDW or WrapperWorker)
                log.debug("instantiating WasatchDevice (blocking)")
                device = WasatchDevice(device_id)

            ok = device.connect()
            if not ok:
                log.critical("connect: can't connect to %s", device_id)
                continue

            log.debug("connect: device connected")
            if device.settings is None:
                log.error("can't have device without SpectrometerSettings")
                continue

            print(f"connected to {device.settings.full_model()} {device.settings.eeprom.serial_number} with {device.settings.pixels()} pixels " +
                  f"from ({device.settings.wavelengths[0]:.2f}, {device.settings.wavelengths[-1]:.2f}nm)" +
                  (   f" ({device.settings.wavenumbers[0]:.2f}, {device.settings.wavenumbers[-1]:.2f}cm⁻¹)" if device.settings.has_excitation() else "") +
                  f" (Microcontroller {device.settings.microcontroller_firmware_version}," +
                  f" FPGA {device.settings.fpga_firmware_version})")

            self.devices[str(device.device_id)] = device

        return len(self.devices) > 0

    ############################################################################
    #                                                                          #
    #                               Run-Time Loop                              #
    #                                                                          #
    ############################################################################

    def run(self):
        log.info("Wasatch.PY %s Demo", wasatch.__version__)

        # apply initial settings
        for device_id, device in self.devices.items():
            device.change_setting("integration_time_ms", self.args.integration_time_ms)
            device.change_setting("scans_to_average", self.args.scans_to_average)

        # initialize outfile if one was specified
        if self.args.outfile:
            try:
                self.outfile = open(self.args.outfile, "w")
                self.outfile.write("time,temp,serial,spectrum\n")
            except:
                log.error("Error initializing %s", self.args.outfile)
                self.outfile = None

        # read spectra until user presses Control-Break
        while not self.exiting:
            start_time = datetime.datetime.now()

            if not self.args.non_blocking:
                # if not non-blocking (i.e., blocking calls to Wasatch.PY), call 
                # device.acquire_data() directly -- this will block on each 
                # spectrometer in turn until we have collected one spectrum from each
                # connected spectromter
                for device_id, device in self.devices.items():
                    self.attempt_reading(device)
            else:
                # if we are non-blocking (meaning each spectrometer is free-
                # running in its own background thread, then we don't need to do
                # anything here -- spectra will magically "show up" at 
                # WasatchDeviceWrapper_callback without us having to do anything,
                # and all we need to here is check whether we've collected enough
                # spectra to shutdown
                pass

            end_time = datetime.datetime.now()

            if self.args.max > 0 and self.reading_count >= self.args.max:
                log.debug("max spectra reached, exiting")
                self.exiting = True
            else:
                # compute how much longer we should wait before the next "tick"
                reading_time_ms = int((end_time - start_time).microseconds / 1000)
                sleep_ms = self.args.delay_ms - reading_time_ms
                if sleep_ms > 0:
                    log.debug("sleeping %d ms (%d ms already passed)", sleep_ms, reading_time_ms)
                    try:
                        time.sleep(float(sleep_ms) / 1000)
                    except:
                        self.exiting = True

        log.debug("WasatchDemo.run exiting")

    def attempt_reading(self, device):
        try:
            reading_response = self.acquire_reading(device)
        except:
            log.critical("attempt_reading caught exception", exc_info=1)
            self.exiting = True
            return

        if isinstance(reading_response.data, bool):
            if reading_response.data:
                log.debug("received poison-pill, exiting")
                self.exiting = True
                return
            else:
                log.debug("no reading available")
                return

        if reading_response.data.failure:
            self.exiting = True
            return

        self.process_reading(reading_response.data)

    def acquire_reading(self, device):
        # We want the demo to effectively block on new scans, so keep polling the
        # background thread until a reading is ready.  In other apps, we could do
        # other things (like respond to GUI events) if device.acquire_data() was
        # None (meaning the next spectrum wasn't yet ready).
        while True:
            reading = device.acquire_data()
            if reading is None:
                log.debug("waiting on next reading")
            else:
                return reading

    def WrapperWorker_callback(self, response):
        if response is None:
            log.error("WrapperWorker_callback received null SpectrometerResponse")
            return

        reading = response.data        
        if reading is None:
            log.error("WrapperWorker_callback received null Reading")
            return

        self.process_reading(reading)

    def process_reading(self, reading):
        if (self.exiting or 
           (self.args.scans_to_average > 1 and not reading.averaged) or
           (self.args.max > 0 and self.reading_count >= self.args.max)):
            return

        device_id = reading.device_id
        log.debug(f"received reading from device_id {device_id}")
        device = self.devices[str(reading.device_id)]
        settings = device.settings

        self.reading_count += 1

        if self.args.boxcar_half_width > 0:
            spectrum = utils.apply_boxcar(reading.spectrum, self.args.boxcar_half_width)
        else:
            spectrum = reading.spectrum

        if self.args.ascii_art:
            print("\n".join(wasatch.utils.ascii_spectrum(spectrum, rows=20, cols=80, x_axis=settings.wavelengths, x_unit="nm")))
        else:
            spectrum_min = numpy.amin(spectrum)
            spectrum_max = numpy.amax(spectrum)
            spectrum_avg = numpy.mean(spectrum)
            spectrum_std = numpy.std (spectrum)

            print("%s: %s %4d  Detector: %5.2f degC  Min: %8.2f  Max: %8.2f  Avg: %8.2f  StdDev: %8.2f" % (
                reading.timestamp,
                settings.eeprom.serial_number,
                self.reading_count,
                reading.detector_temperature_degC,
                spectrum_min,
                spectrum_max,
                spectrum_avg,
                spectrum_std))
            log.debug("%s", str(reading))

        if self.outfile:
            self.outfile.write("%s,%.2f,%s,%s\n" % (datetime.datetime.now(),
                                                 reading.detector_temperature_degC,
                                                 settings.eeprom.serial_number,
                                                 ",".join(format(x, ".2f") for x in spectrum)))

################################################################################
# main()
################################################################################

def signal_handler(signal, frame):
    print('\rInterrupted by Ctrl-C...shutting down', end=' ')
    clean_shutdown()

def clean_shutdown():
    log.debug("Exiting")
    if demo:
        if demo.args.non_blocking and len(demo.devices):
            for device_id, device in demo.devices.items():
                log.debug(f"closing {device_id}")
                device.disconnect()
                time.sleep(1)

        if demo.logger:
            log.debug("closing logger")
            log.debug(None)
            demo.logger.close()
            time.sleep(1)
            applog.explicit_log_close()
    sys.exit()

demo = None
if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    demo = WasatchDemo()
    if demo.connect():
        # Note that on Windows, Control-Break (SIGBREAK) differs from 
        # Control-C (SIGINT); see https://stackoverflow.com/a/1364199
        log.debug("Press Control-Break to interrupt...")
        demo.run()

    clean_shutdown()
