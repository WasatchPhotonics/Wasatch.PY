#!/usr/bin/env python -u
################################################################################
#                               laser-test.py
################################################################################
# 
#  DESCRIPTION:  Simple hysteresis test of laser using photodiode.
#
################################################################################

import traceback
import argparse
import datetime
import pexpect
import time
import sys
import re
import os

from pexpect.popen_spawn import PopenSpawn

prompt = "wp>"
success = "1"

def read_temperature(child):
    child.sendline("get_laser_temperature_raw")
    child.expect(prompt)
    raw = int(child.before.strip())

    child.sendline("get_laser_temperature_degC")
    child.expect(prompt)
    degC = float(child.before.strip())

    return (raw, degC)

def read_photodiode(child):
    child.sendline("get_secondary_adc_raw")
    child.expect(prompt)
    raw = int(child.before.strip())

    child.sendline("get_secondary_adc_calibrated")
    child.expect(prompt)
    mW = child.before.strip()

    return (raw, mW)

# command-line args
parser = argparse.ArgumentParser(description="Laser Hysteresis Test")
parser.add_argument("--passes", type=int, default=2, help="number of hysteresis passes")
parser.add_argument("--delay-ms", type=int, default=1000, help="delay between measurements")
parser.add_argument("--readings", type=int, default=20, help="readings per laser power")
parser.add_argument("--reverse", action="store_true", help="measure photodiode BEFORE temperature")
args = parser.parse_args()

# configure logging
logfile = open("laser-test.log", "w")

# spawn the shell process
child = PopenSpawn("python -u ./wasatch-shell.py --log-level debug", logfile=logfile, timeout=10, maxread=65535, encoding='utf-8')

# confirm the script launches correctly
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

print("Successfully enumerated spectrometer")

# used by photodiode to READ laser power in mW
child.sendline("has_linearity_coeffs")
child.expect(prompt)
has_linearity_coeffs = re.match("1", child.before)

# used to SET laser power in mW
child.sendline("has_laser_power_calibration")
child.expect(prompt)
has_laser_power_calibration = re.match("1", child.before)

# set integration time as convenient way to control timing
child.sendline("set_integration_time_ms %d" % args.delay_ms)
child.expect(prompt)

child.sendline("set_laser_enable true")
child.expect(prompt)

# perform multiple hysteresis passes
for pass_count in range(args.passes):

    # perform a hysteresis pass over the laser, ramping it down from 100% to 1% and up again.
    percentages = [ 100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100 ]

    for laser_power_perc in percentages:
        child.sendline("set_laser_power_perc %d" % laser_power_perc)
        child.expect(prompt)

        # take multiple readings as the laser stabilizes
        for reading_count in range(args.readings):

            # throwaway spectrum for timing purposes and to keep the spectrometer
            # command pipeline moving (could probably just use sleep)
            child.sendline("get_spectrum_pretty")
            child.expect(prompt)

            pd_raw = 0
            pd_mW = "NA"

            # MZ: I don't understand this: if laser temperature is read BEFORE photodiode
            # I can SEE the laser flicker on my test unit (which lacks a photodiode).  
            # Actually, it flickers a bit under Windows either way.
            if args.reverse:
                (temp_raw, temp_degC) = read_temperature(child) # flickers?
                if has_linearity_coeffs:
                    (pd_raw, pd_mW) = read_photodiode(child)
            else:
                if has_linearity_coeffs:
                    (pd_raw, pd_mW) = read_photodiode(child)
                (temp_raw, temp_degC) = read_temperature(child)

            print("%s reading: pass %d laser_power_perc %3d reading %2d photodiode_raw %4d photodiode_mW %s temp_raw %4d temp_degC %8.2f" % (
                datetime.datetime.now(),
                pass_count,
                laser_power_perc,
                reading_count,
                pd_raw,
                pd_mW,
                temp_raw,
                temp_degC))

child.sendline("set_laser_enable false")
child.expect(prompt)

child.sendline("close")
child.expect(pexpect.EOF)
