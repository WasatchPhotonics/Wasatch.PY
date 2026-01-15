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
import numpy as np
import signal
import logging
import argparse

from datetime import datetime
from time import sleep
from PIL import Image, ImageStat  # for image normalization

import wasatch
from wasatch                      import applog
from wasatch.DeviceID             import DeviceID
from wasatch.IDSDevice            import IDSDevice
from wasatch.WasatchBus           import WasatchBus
from wasatch.WasatchDevice        import WasatchDevice

log = logging.getLogger(__name__)
demo = None

class WasatchDemo:

    def __init__(self):
        self.camera_device = None   # a wasatch.IDSDevice wrapping a wasatch.IDSCamera (as 'camera')
        self.laser_device = None    # a wasatch.WasatchDevice wrapping a wasatch.FeatureIdentificationDevice (as 'hardware')
        self.reading_count = 0
        self.exiting = False

        self.args = self.parse_args()

        print(f"Wasatch.PY {wasatch.__version__} IDS Raman Demo")

    def parse_args(self):
        parser = argparse.ArgumentParser(description="Simple demo to acquire spectra from command-line interface")
        parser.add_argument("--log-level",           type=str, default="INFO", help="logging level", choices=["DEBUG","INFO","WARNING","ERROR","CRITICAL","NEVER"])
        parser.add_argument("--integration-time-ms", type=int, default=1000,   help="integration time (ms, default 10)")
        parser.add_argument("--take-dark",           action="store_true",      help="first measurement is used for dark subtraction")
        parser.add_argument("--enable-laser",        action="store_true",      help="fire laser (after optional dark)")
        parser.add_argument("--max",                 type=int, default=0,      help="max spectra to acquire (default 0, unlimited)")
        parser.add_argument("--save-spectra",        action="store_true",      help="save vertically-binned spectra to row-ordered CSV")
        parser.add_argument("--save-png",            action="store_true",      help="save PNG of each image frame")
        parser.add_argument("--save-data",           action="store_true",      help="save 2D array of each image frame")
        parser.add_argument("--normalize",           action="store_true",      help="normalize PNG image")
        parser.add_argument("--save-dir",            type=str, default=".",    help="directory to save files")
        parser.add_argument("--prefix",              type=str, default="ids-raman", help="filename prefix")

        args = parser.parse_args()
        applog.MainLogger(args.log_level)

        return args
        
    def connect(self):

        ########################################################################
        # Look for laser driver board
        ########################################################################

        bus = WasatchBus()
        if not bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return 

        # look for laser driver on WasatchBus -- we won't see the camera here,
        # because IDSPeak devices are not automatically considered for inclusion
        # by WasatchBus (although they could be)
        for device_id in bus.device_ids:
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
            log.critical("could not find laser driver, exitting")
            return 

        ########################################################################
        # now look for an IDS camera
        ########################################################################

        try:
            device_id = DeviceID(label="IDSPeak")
            device = IDSDevice(device_id=device_id, scratch_dir=".", consumer_deletes_area_scan_image=True)
            if device.connect():
                self.camera_device = device
            else:
                log.error("failed connecting to IDSCamera")
        except:
            log.critical("exception connecting to IDS camera", exc_info=1)
            return

        if self.camera_device is None:
            log.critical("could not find IDS camera, exitting")
            return

        device   = self.camera_device
        camera   = device.camera
        settings = device.settings
        eeprom   = settings.eeprom

        device.set_area_scan_enable(True)

        # copy the laser excitation over to the camera "virtual EEPROM" so we can 
        # compute wavenumbers
        eeprom.excitation_nm = self.laser_device.settings.eeprom.excitation_nm
        settings.update_wavecal()
        print(f"connected to IDSPeak camera {eeprom.model} {eeprom.serial_number} with {camera.width}x{camera.height} pixels " +
               f"from ({settings.wavelengths[0]:.2f}, {settings.wavelengths[-1]:.2f}nm)" +
               (   f" ({settings.wavenumbers[0]:.2f}, {settings.wavenumbers[-1]:.2f}cm⁻¹)" if settings.has_excitation() else ""))

        return True

    ############################################################################
    #                                                                          #
    #                               Run-Time Loop                              #
    #                                                                          #
    ############################################################################

    def run(self):
        if self.args.save_dir != ".":
            os.makedirs(self.args.save_dir, exist_ok=True)

        self.camera_device.set_integration_time_ms(self.args.integration_time_ms)

        # initialize outfile if saving spectra
        outfile = None
        if self.args.save_spectra:
            filename = os.path.join(self.args.save_dir, self.args.prefix + "-spectra.csv")
            outfile = open(filename, "w")
            outfile.write("pixels,      0, " + ", ".join([f"{x}" for x in range(self.camera_device.settings.pixels())]) + "\n")
            outfile.write("wavelengths, 0, " + ", ".join([f"{x:.2f}" for x in self.camera_device.settings.wavelengths]) + "\n")
            outfile.write("wavenumbers, 0, " + ", ".join([f"{x:.2f}" for x in self.camera_device.settings.wavenumbers]) + "\n")
            outfile.write("time,    count, spectrum\n")

        # read spectra until user presses Control-Break
        while not self.exiting and (self.args.max == 0 or self.reading_count < self.args.max):

            ####################################################################
            # check to fire laser
            ####################################################################

            # on the first reading (or the second, if taking dark), enable the 
            # laser if requested
            if (self.args.enable_laser and (self.reading_count == 0 or (self.args.take_dark and self.reading_count == 1))):
                print("enabling laser")
                self.laser_device.hardware.set_laser_enable(True)

            ####################################################################
            # read image
            ####################################################################

            if self.exiting:
                break

            response = self.camera_device.acquire_data()
            if response.keep_alive:
                log.debug(f"ignoring keepalive from {device}")
                continue

            if isinstance(response.data, bool):
                if response.data:
                    log.debug("received poison-pill, exiting")
                    break
                else:
                    log.debug("no reading available")
                    continue

            if response.data.failure:
                log.debug("reading indicated failure")
                break

            if self.exiting:
                break

            self.reading_count += 1

            reading = response.data
            spectrum = reading.spectrum
            settings = self.camera_device.settings
            asi = reading.area_scan_image
            ts = reading.timestamp.strftime("%Y%m%d-%H%M%S-%f")

            # print some stats as we go
            spectrum_min = np.amin(spectrum)
            spectrum_max = np.amax(spectrum)
            spectrum_avg = np.mean(spectrum)
            spectrum_std = np.std (spectrum)
            print(f"{ts}: {self.reading_count:4d} : min {spectrum_min:8.2f}, max {spectrum_max:8.2f}, avg {spectrum_avg:8.2f}, stdev {spectrum_std:8.2f}")

            # append to row-ordered file
            if outfile:
                values = ",".join(format(x, ".2f") for x in spectrum)
                outfile.write(f"{reading.timestamp}, {self.reading_count}, {values}\n")

            # save PNG
            if self.args.save_png and asi.pathname_png:
                filename = os.path.join(self.args.save_dir, f"{self.args.prefix}-{self.reading_count:04d}.png")
                log.debug(f"renaming {asi.pathname_png} -> {filename}")
                os.replace(asi.pathname_png, filename)
                print(f"\tsaved {filename}")

                # normalize PNG (just the image file, not the raw 2D data)
                if self.args.normalize:
                    img = Image.open(filename).convert('L')
                    stat = ImageStat.Stat(img)
                    mean_brightness = stat.mean[0]
                    img_array = np.array(img)
                    normalized_img_array = img_array / mean_brightness * 100 
                    normalized_img = Image.fromarray(np.uint8(normalized_img_array))
                    normalized_img.save(filename)
                    print(f"\tnormalized {filename}")

            # save area scan data
            if self.args.save_data and asi.data is not None:
                filename = os.path.join(self.args.save_dir, f"{self.args.prefix}-{self.reading_count:04d}.csv")
                np.savetxt(filename, asi.data, fmt='%d', delimiter=",")
                print(f"\tsaved {filename}")

            if self.args.take_dark and self.reading_count == 1:
                print("\tsaving dark")
                self.camera_device.camera.set_dark_asi(asi)

################################################################################
# main()
################################################################################

def signal_handler(signal, frame):
    print('\rInterrupted by Ctrl-C...shutting down', end=' ')
    shutdown()

def shutdown():
    if demo:
        demo.exiting = True
        if demo.laser_device is not None:
            demo.laser_device.hardware.set_laser_enable(False)
            demo.laser_device.disconnect()

        if demo.camera_device is not None:
            demo.camera_device.disconnect()
            sleep(1)

    log.debug(None)
    applog.explicit_log_close()
    sys.exit()

if __name__ == "__main__":
    # add a signal-handler to catch ctrl-C interrupts, so we can cleanly close 
    # down all connected devices
    signal.signal(signal.SIGINT, signal_handler)

    demo = WasatchDemo()
    if demo.connect():
        # Note that on Windows, Control-Break (SIGBREAK) differs from 
        # Control-C (SIGINT); see https://stackoverflow.com/a/1364199
        log.debug("Press Control-Break to interrupt...")
        try:
            demo.run()
        except:
            # catch exception so shutdown will run and disable laser
            log.error("Caught exception during demo", exc_info=1)

    shutdown()
