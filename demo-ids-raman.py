#!/usr/bin/env python
################################################################################
#                              demo-ids-raman.py                               #
################################################################################
#                                                                              #
#  DESCRIPTION:  Simple cmd-line demo to control an XS spectrometer using IDS  #
#                detector and 220250 Rev4 laser driver.                        #
#                                                                              #
#  NOTES: This is for the fairly unusual case of having an XS bench built with #
#         an IDS camera (providing its own USB3 port for comms and control),   #
#         plus a 220250 Rev4 PCB for use as a standalone laser driver (but w/o #
#         an IMX385 detector connected).                                       #
#                                                                              #
################################################################################

# DEPENDENCIES: pywin32, psutil, seabreeze, pyftdi, crcmod, bleak

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
from wasatch                      import applog
from wasatch.DeviceID             import DeviceID
from wasatch.IDSDevice            import IDSDevice
from wasatch.WasatchBus           import WasatchBus
from wasatch.WasatchDevice        import WasatchDevice

log = logging.getLogger(__name__)

class WasatchDemo:

    ############################################################################
    #                                                                          #
    #                               Lifecycle                                  #
    #                                                                          #
    ############################################################################

    def __init__(self):
        self.bus     = None
        self.camera_device = None
        self.laser_device = None
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
        parser.add_argument("--integration-time-ms", type=int, default=1000,   help="integration time (ms, default 10)")
        parser.add_argument("--delay-ms",            type=int, default=1000,   help="delay between integrations (ms, default 1000)")
        parser.add_argument("--outfile",             type=str, default=None,   help="output filename (e.g. path/to/spectra.csv)")
        parser.add_argument("--max",                 type=int, default=0,      help="max spectra to acquire (default 0, unlimited)")

        args = parser.parse_args()
        args.log_level = args.log_level.upper()

        return args
        
    ############################################################################
    #                                                                          #
    #                              USB Devices                                 #
    #                                                                          #
    ############################################################################

    def connect(self):
        """ Connect to all discoverable Wasatch spectrometers.  """

        ########################################################################
        # Look for laser driver board
        ########################################################################

        if self.bus is None:
            self.bus = WasatchBus()
        if not self.bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return 

        # look for laser driver on WasatchBus -- we won't see the camera here,
        # because IDSPeak devices are not automatically considered for inclusion
        # by WasatchBus (although they could be)
        for device_id in self.bus.device_ids:
            log.debug("connect: trying to connect to %s", device_id)
            device = WasatchDevice(device_id)
            ok = device.connect()
            if not ok:
                log.critical("connect: can't connect to %s", device_id)
                continue

            log.debug(f"connect: connected to {device_id} ({device})")
            if device.settings is None:
                log.error("can't have device without SpectrometerSettings")
                continue

            print(f"connected to {device.settings.full_model()} {device.settings.eeprom.serial_number} with {device.settings.pixels()} pixels " +
                  f"from ({device.settings.wavelengths[0]:.2f}, {device.settings.wavelengths[-1]:.2f}nm)" +
                  (   f" ({device.settings.wavenumbers[0]:.2f}, {device.settings.wavenumbers[-1]:.2f}cm⁻¹)" if device.settings.has_excitation() else "") +
                  f" (Microcontroller {device.settings.microcontroller_firmware_version}," +
                  f" FPGA {device.settings.fpga_firmware_version})")

            self.laser_device = device
            break
        if self.laser_device is None:
            log.critical("Could not find laser driver, exitting")
            return False

        ########################################################################
        # now look for an IDS camera
        ########################################################################

        try:
            device_id = DeviceID(label="IDSPeak")
            device = IDSDevice(device_id=device_id)
            if device.connect():
                log.info("Successfully connected to IDSPeak camera")
                self.camera_device = device
            else:
                log.error("Failed to connecting to IDSCamera")
        except:
            log.critical("Exception connecting to IDS camera", exc_info=1)

        if self.camera_device is None:
            log.critical("Could not find IDS camera, exitting")
            return False

        return True

    ############################################################################
    #                                                                          #
    #                               Run-Time Loop                              #
    #                                                                          #
    ############################################################################

    def run(self):
        log.info("Wasatch.PY %s IDS Raman Demo", wasatch.__version__)

        # apply initial settings
        self.camera_device.set_integration_time_ms(self.args.integration_time_ms)

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

            # take a spectrum 
            log.debug(f"calling attempt_reading")
            self.attempt_reading(self.camera_device)

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
            reading_response = device.acquire_data()
        except:
            log.critical("attempt_reading caught exception", exc_info=1)
            self.exiting = True
            return

        if reading_response.keep_alive:
            log.debug(f"ignoring keepalive from {device}")
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

    def process_reading(self, reading):
        if (self.exiting or 
            (self.args.max > 0 and self.reading_count >= self.args.max)):
            return

        settings = self.camera_device.settings

        self.reading_count += 1
        spectrum = reading.spectrum

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
        if demo.laser_device is not None:
            demo.laser_device.disconnect()
            time.sleep(1)
        if demo.camera_device is not None:
            demo.camera_device.disconnect()
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
