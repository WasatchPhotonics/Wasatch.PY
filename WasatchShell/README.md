# Overview

This script is a simple interactive wrapper over Wasatch.PY, providing
command-line access to most functions including simple acquisitions.

# Sample Execution

    $ ./wasatch-shell.py
    wp> help
    ...
    wp> open
    1
    wp> set_integration_time_ms 100
    wp> set_laser_power_perc 70
    wp> set_laser_enable on
    wp> get_laser_enabled
    1
    wp> get_secondary_adc_calibrated
    49.1342855427
    wp> get_laser_temperature_degc
    wp> get_spectrum
    wp> get_spectrum_pretty
    wp> get_spectrum_save foo.csv
    wp> quit

# Version History

- 07-27-2018 2.0.0
    - changed to a wrapper over Wasatch.PY
- 07-24-2018 1.0.7
    - added auto\_balance
- 05-31-2018 1.0.6
    - changed laser power granularity to 0.1%
- 05-30-2018 1.0.5
    - added HAS\_PHOTODIODE\_CALIBRATION
