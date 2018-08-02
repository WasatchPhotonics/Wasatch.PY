#!/usr/bin/expect -f
################################################################################
#                               load-test.tcl
################################################################################
# 
#  DESCRIPTION:  Allows user to "hammer" the spectrometer with a repeatable 
#                pattern of operations in an arbitrarily complex or heavy
#                load in order to ferret-out any underlying communication 
#                issues which only emit under conditions of duress.
#
#  INVOCATION:   $ ./load-test.tcl [outer_loop_count] [inner_loop_count]
#                  (value <= 0 means run indefinitely)
#
#  NOTES:        The script is written using TCL's "expect" engine; see
#                https://en.wikipedia.org/wiki/Expect
#
#  STATUS:       THIS SCRIPT IS LARGELY DEPRECATED, AND PROVIDED FOR HISTORICAL 
#                INTEREST.  (Hard to find cmd-line 'expect' shells for Win)
#
################################################################################

# constants
set script_name "./wasatch-shell.py"
set prompt "wp>"
set success "1\r"
set timeout 1

# process cmd-line arguments
set max_outer_loop 5
set max_inner_loop 10
if { [llength $argv] > 0 } {
    set max_outer_loop [lindex $argv 0]
    if { [llength $argv] > 1 } {
        set max_inner_loop [lindex $argv 1]
    }
}

# run the wasatch-shell script
spawn $script_name

# confirm the script launches correctly
expect "wasatch-shell version"
expect $prompt

# open the spectrometer
send -- "open\r"
expect $success
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
    expect $success
    expect $prompt

    send -- "set_detector_tec_setpoint_degc 10\r"
    expect $success
    expect $prompt

    send -- "set_tec_enable on\r"
    expect $success
    expect $prompt

    send -- "set_laser_power_mw 70\r"
    expect $success
    expect $prompt

    send -- "set_laser_enable on\r"
    expect $success
    expect $prompt

    for {set inner_loop 0} {$max_inner_loop <= 0 || $inner_loop < $max_inner_loop} {incr inner_loop 1} {
        send -- "get_detector_temperature_degc\r"
        expect $prompt

        send -- "get_tec_enabled\r"
        expect $success
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
        expect $success
        expect $prompt

        send -- "get_laser_mod_enabled\r"
        expect $success
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
    expect $success
    expect $prompt

    send -- "set_laser_enable off\r"
    expect $success
    expect $prompt
}

send -- "close\r"
expect eof

puts "All tests completed."
