#!/usr/bin/expect -f
################################################################################
#                               load-test.tcl
################################################################################
# 
#  DESCRIPTION:  Allows user to "hammer" the spectrometer with a repeatable 
#                pattern of operations in an arbitrarily complex or heavity
#                load in order to ferret-out any underlying communication 
#                issues which only emit under conditions of heavy duress.
#
#  INVOCATION:   $ ./load-test.tcl [outer_loops] [inner_loops}
#                  (value <= 0 means run indefinitely)
#
#  NOTES:        The script is written using TCL's "expect" engine; see
#                https://www.tcl.tk/man/expect5.31/expect.1.html
#
################################################################################

set script_name "./wasatch-shell.py"
set max_outer_loop 5
set max_inner_loop 10
set prompt "wp>"
set timeout 1

# process cmd-line arguments
if { [llength $argv] > 0 } {
    set max_outer_loop [lindex $argv 0]
    if { [llength $argv] > 1 } {
        set max_inner_loop [lindex $argv 1]
    }
}

puts [format "outer loops: %d" $max_outer_loop]
puts [format "inner loops: %d" $max_inner_loop]

# this is the script we're going to be testing
spawn $script_name

expect "wasatch-shell version"
expect $prompt

send -- "open\r"
expect "1\r"
expect $prompt

puts "\rSuccessfully enumerated spectrometer"
puts [format "Beginning load-test of %d passes, each of %d iterations" $max_outer_loop $max_inner_loop]

for {set outer_loop 0} {$max_outer_loop <= 0 || $outer_loop < $max_outer_loop} {incr outer_loop 1} {

    puts "\r------------------------------------------------------------"
    puts [format "Beginning pass %d of %d" [expr $outer_loop + 1] $max_outer_loop]
    puts ""
    sleep 2

    send -- "get_config_json\r"
    expect "wavelength_coeffs"
    expect $prompt

    send -- "set_integration_time_ms 100\r"
    expect "1\r"
    expect $prompt

    send -- "set_detector_tec_setpoint_degc 10\r"
    expect "1\r"
    expect $prompt

    send -- "set_tec_enable on\r"
    expect "1\r"
    expect $prompt

    send -- "set_laser_power_mw 70\r"
    expect "1\r"
    expect $prompt

    send -- "set_laser_enable on\r"
    expect "1\r"
    expect $prompt

    for {set inner_loop 0} {$max_inner_loop <= 0 || $inner_loop < $max_inner_loop} {incr inner_loop 1} {
        send -- "get_detector_temperature_degc\r"
        expect $prompt

        send -- "get_tec_enabled\r"
        expect "1\r"
        expect $prompt

        send -- "get_integration_time_ms\r"
        expect "100\r"
        expect $prompt

        send -- "get_laser_mod_duration\r"
        expect $prompt

        send -- "get_laser_mod_pulse_delay\r"
        expect $prompt

        send -- "get_laser_mod_period\r"
        expect "100\r"
        expect $prompt

        send -- "get_laser_temperature_degc\r"
        expect $prompt

        send -- "get_actual_frames\r"
        expect $prompt

        send -- "get_laser_mod_pulse_width\r"
        expect $prompt

        send -- "get_actual_integration_time_us\r"
        expect $prompt

        send -- "get_external_trigger_output\r"
        expect $prompt

        send -- "get_laser_enabled\r"
        expect "1\r"
        expect $prompt

        send -- "get_laser_mod_enabled\r"
        expect "1\r"
        expect $prompt

        send -- "get_laser_power_ramping_enabled\r"
        expect $prompt

        send -- "get_vr_num_frames\r"
        expect $prompt

        send -- "get_spectrum\r"
        expect $prompt

        send -- "get_laser_temperature_degc\r"
        expect $prompt

        send -- "get_selected_adc\r"
        expect "0\r"
        expect $prompt

        send -- "get_secondary_adc_calibrated\r"
        expect $prompt

        send -- "get_selected_adc\r"
        expect "1\r"
        expect $prompt
    }

    send -- "set_tec_enable off\r"
    expect "1\r"
    expect $prompt

    send -- "set_laser_enable off\r"
    expect "1\r"
    expect $prompt
}

send -- "close\r"
expect eof

puts "All tests completed."
