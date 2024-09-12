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
from wasatch.TakeOneRequest       import TakeOneRequest
from wasatch.AutoRamanRequest     import AutoRamanRequest

log = logging.getLogger(__name__)

class Fixture:

    def __init__(self):
        self.bus     = None
        self.device  = None
        self.outfile = None
        self.reading_count = 0

        self.parse_args()

        self.logger = applog.MainLogger("DEBUG" if self.args.debug else "INFO")

    def parse_args(self):
        parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument("--debug",          action="store_true",          help="logging level")
        parser.add_argument("--outfile",        type=str,      default=None,  help="output filename (e.g. path/to/spectra.csv)")
        parser.add_argument("--max-ms",         type=int,      default=10000, help="maximum measurement time (ms)")
        parser.add_argument("--start-integ-ms", type=int,      default=100,   help="initial integration time (ms)")
        parser.add_argument("--start-gain-db",  type=float,    default=0,     help="initial gain (dB)")
        parser.add_argument("--max-integ-ms",   type=int,      default=2000,  help="maximum integration time (ms)")
        parser.add_argument("--min-integ-ms",   type=int,      default=10,    help="minimum integration time (ms)")
        parser.add_argument("--max-gain-db",    type=float,    default=32,    help="maximum gain (dB)")
        parser.add_argument("--min-gain-db",    type=float,    default=0,     help="minimum gain (dB)")
        parser.add_argument("--target-counts",  type=int,      default=45000, help="target counts")
        parser.add_argument("--max-counts",     type=int,      default=50000, help="max counts")
        parser.add_argument("--min-counts",     type=int,      default=40000, help="min counts")
        parser.add_argument("--max-factor",     type=float,    default=5,     help="max scaling factor")
        parser.add_argument("--drop-factor",    type=float,    default=0.5,   help="factor when scaling down")
        parser.add_argument("--saturation",     type=int,      default=65000, help="counts")

        self.args = parser.parse_args()
        
    def connect(self):
        """ Connect to all discoverable Wasatch spectrometers.  """

        self.bus = WasatchBus()
        if not self.bus.device_ids:
            print("No Wasatch USB spectrometers found.")
            return 

        for device_id in self.bus.device_ids:
            device = WasatchDevice(device_id)
            if not device.connect():
                log.critical("connect: can't connect to %s", device_id)
                continue

            settings = device.settings
            print(f"connected to {settings.full_model()} {settings.eeprom.serial_number} with {settings.pixels()} pixels " +
                  f"from ({settings.wavelengths[0]:.2f}, {settings.wavelengths[-1]:.2f}nm)" +
                  (   f" ({settings.wavenumbers[0]:.2f}, {settings.wavenumbers[-1]:.2f}cm⁻¹)" if settings.has_excitation() else "") +
                  f" (Microcontroller {device.settings.microcontroller_firmware_version}," +
                  f" FPGA {settings.fpga_firmware_version})")

            self.device = device
            return True

    def run(self):
        start_time = datetime.datetime.now()

        # instantiate an AutoRamanRequest, passing in the configured options
        arr = AutoRamanRequest(max_ms         = self.args.max_ms,
                               start_integ_ms = self.args.start_integ_ms,
                               start_gain_db  = self.args.start_gain_db,
                               max_integ_ms   = self.args.max_integ_ms,
                               min_integ_ms   = self.args.min_integ_ms,
                               max_gain_db    = self.args.max_gain_db,
                               min_gain_db    = self.args.min_gain_db,
                               target_counts  = self.args.target_counts,
                               max_counts     = self.args.max_counts,
                               min_counts     = self.args.min_counts,
                               max_factor     = self.args.max_factor,
                               drop_factor    = self.args.drop_factor,
                               saturation     = self.args.saturation)
        log.debug(f"instantiated {arr}")

        # add the AutoRamanRequest to a TakeOneRequest
        tor = TakeOneRequest(auto_raman_request=arr)
        log.debug(f"instantiated {tor}")

        # send the TakeOneRequest to the driver
        self.device.change_setting("take_one_request", tor)
        log.debug("sent TakeOneRequest to spectrometer")

        # block on completion of the TakeOneRequest (ignore keepalives)
        while True:
            log.debug("calling WasatchDevice.acquire_data")
            reading = self.device.acquire_data().data
            log.debug(f"WasatchDevice.acquire_data returned {reading}")
            if reading is not None:
                break

            time.sleep(1)

        elapsed_sec = (datetime.datetime.now() - start_time).total_seconds()
        print(f"Auto-Raman measurement completed in {elapsed_sec:.2f} sec")

        # process Reading
        settings = self.device.settings

        # ASCII-Art
        print("\n".join(wasatch.utils.ascii_spectrum(reading.spectrum, rows=20, cols=80, x_axis=settings.wavenumbers, x_unit="cm⁻¹")))

        if self.outfile:
            self.outfile.write("{datetime.datetime.now()}, {aar}, {', '.join([f'.2f' for x in reading.spectrum])}\n")

# main()
fixture = Fixture()
if fixture.connect():
    fixture.run()
