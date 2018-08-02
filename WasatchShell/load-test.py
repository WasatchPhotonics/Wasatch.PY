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

import argparse
import platform
import pexpect
import time
import sys

from pexpect.popen_spawn import PopenSpawn

################################################################################
# constants
################################################################################

prompt = "wp>"
success = "1"

################################################################################
# command-line arguments
################################################################################

parser = argparse.ArgumentParser(description="Load test of Wasatch.PY function calls")
parser.add_argument("--outer-loop", type=int, default=1,  help="outer loop count (0 for inf)") # MZ was 5
parser.add_argument("--inner-loop", type=int, default=10, help="inner loop count")
args = parser.parse_args()

################################################################################
# initialize test
################################################################################

# spawn the shell process
logfile = open("load-test.log", "w")
child = PopenSpawn("python -u ./wasatch-shell.py --log-level debug", logfile=logfile, timeout=5)

# confirm the script launches correctly
child.expect("wasatch-shell version")
child.expect(prompt)

# open the spectrometer
child.sendline("open")
try:
    child.expect(success)
    child.expect(prompt)
except pexpect.exceptions.TIMEOUT:
    print "ERROR: No spectrometers found"
    sys.exit(1)

print "Successfully enumerated spectrometer"

################################################################################
# run the test iterations
################################################################################

outer_loop = 0
while True:
    if args.outer_loop > 0:
        if outer_loop >= args.outer_loop:
            break
        outer_loop += 1

    print "Pass %d of %d" % (outer_loop, args.outer_loop)
    time.sleep(2)

    child.sendline("get_config_json")
    child.expect("wavelength_coeffs")
    child.expect(prompt)

    child.sendline("set_integration_time_ms 100")
    child.expect(success)
    child.expect(prompt)

    child.sendline("set_detector_tec_setpoint_degc 10")
    child.expect(success)
    child.expect(prompt)

    child.sendline("set_tec_enable on")
    child.expect(success)
    child.expect(prompt)

    child.sendline("set_laser_power_mw 70")
    child.expect(success)
    child.expect(prompt)

    child.sendline("set_laser_enable on")
    child.expect(success)
    child.expect(prompt)

    for inner_loop in range(args.inner_loop):
        print "  Iteration %d of %d" % (inner_loop, args.inner_loop)
        
        child.sendline("get_detector_temperature_degc")
        child.expect(prompt)

        child.sendline("get_tec_enabled")
        child.expect(success)
        child.expect(prompt)

        child.sendline("get_integration_time_ms")
        child.expect("100")
        child.expect(prompt)

        child.sendline("get_laser_mod_duration")
        child.expect(prompt)

        child.sendline("get_laser_mod_pulse_delay")
        child.expect(prompt)

        child.sendline("get_laser_mod_period")
        child.expect("100")
        child.expect(prompt)

        child.sendline("get_laser_temperature_degc")
        child.expect(prompt)

        child.sendline("get_actual_frames")
        child.expect(prompt)

        child.sendline("get_laser_mod_pulse_width")
        child.expect(prompt)

        child.sendline("get_actual_integration_time_us")
        child.expect(prompt)

        child.sendline("get_external_trigger_output")
        child.expect(prompt)

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

        child.sendline("get_laser_temperature_degc")
        child.expect(prompt)

        child.sendline("get_selected_adc")
        child.expect("0")
        child.expect(prompt)

        child.sendline("get_secondary_adc_calibrated")
        child.expect(prompt)

        child.sendline("get_selected_adc")
        child.expect("1")
        child.expect(prompt)

    child.sendline("set_tec_enable off")
    child.expect(success)
    child.expect(prompt)

    child.sendline("set_laser_enable off")
    child.expect(success)
    child.expect(prompt)

child.sendline("close")
child.expect(pexpect.EOF)

print "All tests completed."
