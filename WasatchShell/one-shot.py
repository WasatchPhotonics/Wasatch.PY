#!/usr/bin/env python -u

import argparse
import pexpect
import sys

from pexpect.popen_spawn import PopenSpawn

# constants
prompt = "wp>"
success = "1"
filename = "one-shot.csv"
max_timeout_sec = 10

# command-line arguments
parser = argparse.ArgumentParser(description="One-Shot measurement")
parser.add_argument("--integration-time-ms", type=int, default=100)
parser.add_argument("--scans-to-average", type=int, default=1)
parser.add_argument("--wavelength", type=float, default=None)
parser.add_argument("--wavenumber", type=float, default=None)
parser.add_argument("--laser", action="store_true", help="fire the laser")
args = parser.parse_args()

# initialize test
logfile = open("one-shot.log", "w")
child = PopenSpawn("python -u ./wasatch-shell.py --log-level debug", logfile=logfile, timeout=max_timeout_sec, maxread=65535, encoding='utf-8')
child.expect("wasatch-shell version")
child.expect(prompt)

# open the spectrometer
child.sendline("open")
try:
    child.expect(success)
    child.expect(prompt)
except pexpect.exceptions.TIMEOUT:
    print("ERROR: No spectrometers found")
    sys.exit(1)

# configure the measurement
child.sendline("set_integration_time_ms %d" % args.integration_time_ms)
child.expect(prompt)

child.sendline("set_scans_to_average %d" % args.scans_to_average)
child.expect(prompt)

if args.wavelength is not None:
    child.sendline("set_interpolated_x_axis_nm %f %f 0" % (args.wavelength, args.wavelength))
    child.expect(prompt)
elif args.wavenumber is not None:
    child.sendline("set_interpolated_x_axis_cm %f %f 0" % (args.wavenumber, args.wavenumber))
    child.expect(prompt)

if args.laser:
    child.sendline("set_laser_enable on")
    child.expect(prompt)

# take the measurement
child.sendline("get_spectrum_save %s" % filename)
child.expect(prompt)

# shutdown
if args.laser:
    child.sendline("set_laser_enable off")
    child.expect(prompt)
child.sendline("set_laser_enable off")

child.sendline("close")
child.expect(pexpect.EOF)

# display to stdout
with open(filename, "r") as f:
    print(f.read())
