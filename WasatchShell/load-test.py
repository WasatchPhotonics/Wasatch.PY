#!/usr/bin/env python -u
################################################################################
#                               load-test.py
################################################################################
# 
#  DESCRIPTION:  Allows user to "hammer" the spectrometer with a repeatable 
#                pattern of operations in an arbitrarily complex or heavy
#                load in order to ferret-out any underlying communication 
#                issues which only emit under conditions of duress.
#
#  INVOCATION:   $ ./load-test.py [outer_loop_count] [inner_loop_count]
#                  (value <= 0 means run indefinitely)
#
################################################################################

import traceback
import argparse
import platform
import datetime
import pexpect
import random
import time
import sys
import re
import os

from pexpect.popen_spawn import PopenSpawn

################################################################################
# constants
################################################################################

prompt = "wp>"
success = "1"
max_failures = 10

################################################################################
# command-line arguments
################################################################################

parser = argparse.ArgumentParser(description="Load test of Wasatch.PY function calls")
parser.add_argument("--outer-loop", type=int, default=5,  help="outer loop count (0 for inf)")
parser.add_argument("--inner-loop", type=int, default=10, help="inner loop count")
parser.add_argument("--seed", type=int, default=None, help="Monte Carlo seed")
parser.add_argument("--vis-only", action="store_true", help="only test WP-VIS features (no TEC, no laser)")
parser.add_argument("--script-file", type=str, help="file containing child.send() and child.expect() commands")
args = parser.parse_args()

################################################################################
# initialize test
################################################################################

# configure logging
logfile = open("load-test.log", "w")
logfile.write("settings: outer_loop %d, inner_loop %d, seed %s\n" % (args.outer_loop, args.inner_loop, args.seed))
random.seed(args.seed)

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

child.sendline("has_linearity_coeffs")
child.expect(prompt)
has_linearity_coeffs = re.match("1", child.before)

child.sendline("has_laser_power_calibration")
child.expect(prompt)
has_laser_power_calibration = re.match("1", child.before)

################################################################################
# run the test iterations
################################################################################

failure_count = 0
if args.script_file:
    if os.path.isfile(args.script_file):
        with open(args.script_file, "r") as f:
            print("running script (%s)" % args.script_file)
            linenum = 0
            for line in f:
                line = line.strip()
                print("running script line %5d: %s" % (linenum, line))
                exec(line)
                linenum += 1
    else:
        print("file not found: %s" % args.script_file)
else:

    outer_loop = 0
    while True:
        if args.outer_loop > 0:
            if outer_loop >= args.outer_loop:
                break
        outer_loop += 1

        # pick random draws for this pass
        integration_time_ms = random.randrange(10, 250)
        detector_tec_setpoint_degc = random.randrange(10, 15)
        laser_power_mw = random.randrange(10, 90)

        msg = "Pass %d of %d (time %s, integration_time_ms %d, detector_tec_setpoint_degc %d, laser_power_mw %d)" % (
            outer_loop, args.outer_loop, datetime.datetime.now(), integration_time_ms, detector_tec_setpoint_degc, laser_power_mw)
        logfile.write(msg + "\n")
        print(msg)

        # time.sleep(2)

        # loop over failures
        try:
            child.sendline("get_config_json")
            child.expect("wavelength_coeffs")
            child.expect(prompt)

            child.sendline("set_integration_time_ms %d" % integration_time_ms)
            child.expect(success)
            child.expect(prompt)

            if not args.vis_only:
                child.sendline("set_detector_tec_setpoint_degc %d" % detector_tec_setpoint_degc)
                child.expect(success)
                child.expect(prompt)

                child.sendline("set_tec_enable on")
                child.expect(success)
                child.expect(prompt)

                if has_laser_power_calibration:
                    child.sendline("set_laser_power_mw %d" % laser_power_mw)
                    child.expect(success)
                    child.expect(prompt)
                else:
                    child.sendline("set_laser_power_perc %d" % laser_power_mw)
                    child.expect(success)
                    child.expect(prompt)

                child.sendline("set_laser_enable on")
                child.expect(success)
                child.expect(prompt)

            for inner_loop in range(args.inner_loop):
                print("  Iteration %d of %d" % (inner_loop, args.inner_loop))
                
                child.sendline("get_detector_temperature_degc")
                child.expect(prompt)

                if not args.vis_only:
                    child.sendline("get_tec_enabled")
                    child.expect(success)
                    child.expect(prompt)

                child.sendline("get_integration_time_ms")
                child.expect(str(integration_time_ms))
                child.expect(prompt)

                if not args.vis_only:
                    child.sendline("get_laser_mod_duration")
                    child.expect(prompt)

                    child.sendline("get_laser_mod_pulse_delay")
                    child.expect(prompt)

                    # wait for laser_mod_period to be initialized
                    if inner_loop or outer_loop:
                        child.sendline("get_laser_mod_period")
                        child.expect("100")
                        child.expect(prompt)

                    child.sendline("get_laser_temperature_degc")
                    child.expect(prompt)

                child.sendline("get_actual_frames")
                child.expect(prompt)

                if not args.vis_only:
                    child.sendline("get_laser_mod_pulse_width")
                    child.expect(prompt)

                child.sendline("get_actual_integration_time_us")
                child.expect(prompt)

                child.sendline("get_external_trigger_output")
                child.expect(prompt)

                if not args.vis_only:
                    child.sendline("get_laser_enabled")
                    child.expect(success)
                    child.expect(prompt)

                    child.sendline("get_laser_mod_enabled")
                    child.expect(success)
                    child.expect(prompt)

                    child.sendline("get_laser_power_ramping_enabled")
                    child.expect(prompt)

                child.sendline("get_vr_num_frames")
                child.expect(prompt)

                child.sendline("get_spectrum")
                child.expect(prompt)

                if not args.vis_only:
                    child.sendline("get_laser_temperature_degc")
                    child.expect(prompt)

                child.sendline("get_selected_adc")
                child.expect("0")
                child.expect(prompt)

                if has_linearity_coeffs:
                    child.sendline("get_secondary_adc_calibrated")
                    child.expect(prompt)

                    child.sendline("get_selected_adc")
                    child.expect("1")
                    child.expect(prompt)

            if not args.vis_only:
                child.sendline("set_tec_enable off")
                child.expect(success)
                child.expect(prompt)

                child.sendline("set_laser_enable off")
                child.expect(success)
                child.expect(prompt)

        except Exception as ex:
            failure_count += 1
            logfile.write("load-test error (outer_loop %d, inner_loop %d, time %s): %s" % (outer_loop, inner_loop, datetime.datetime.now(), ex))
            logfile.write(traceback.format_exc())
            if failure_count > max_failures:
                print("too many failures, quitting")
                break

child.sendline("close")
child.expect(pexpect.EOF)

print("All tests completed (%d errors)" % failure_count)
