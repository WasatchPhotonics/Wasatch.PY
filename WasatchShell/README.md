# Overview

wasatch-shell.py is a simple interactive wrapper over Wasatch.PY, providing
command-line access to most functions including simple acquisitions.

    $ set PYTHONPATH=..     # note absence of quotation marks!
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
    10.1309717125
    wp> get_spectrum
     ...
    wp> get_spectrum_pretty
     ...
    wp> get_spectrum_save foo.csv
    wp> quit

load-test.py is a wrapper over wasatch-shell.py which bulk-streams a long series
of commands at the driver, forcing the spectrometer through a heavy sequence of
operations in order to find any USB or communication weaknesses in the driver and
firmware implementation under stress.

    $ ./load-test.py 100 50
    Pass 1 of 100
      Iteration 1 of 50
      Iteration 2 of 50
      ...

# Expect Scripts

A couple additional scripts are provided to show how expect (or pexpect) can be used
to interact with a spectrometer using WasatchShell.

## load-test.py

A sample framework to perform a heavy "load test" of a spectrometer, walking through a series
of functions with Monte Carlo randomization to help wring-out the deepest of bugs.

## one-shot.py

A simple command-line wrapper to take a single measurement and output data to console,
optionally filtering by wavelength or wavenumber:

    $ python one-shot.py --laser --integration-time-ms 1000 --scans-to-average 5 --wavenumber 1046
    1046.00,18796.25

# Dependencies

## GNU Readline

WasatchShell uses GNU Readline, so you can create a 
[~/.inputrc file](https://www.gnu.org/software/bash/manual/html_node/Readline-Init-File.html)
to enable 'vi' editing with history searching, etc.

# Version History

- 09-18-2019 2.2.6
    - added set_interpolated_x_axis_nm
    - added clear
    - added one-shot.py
- 08-20-2019 2.2.5
    - default to laser_power_high_resolution
    - if laser power calibration is found, require modulation and default to max configured milliwatts
- 07-19-2019 2.2.4
    - export NaN as null in JSON
- 07-16-2019 2.2.3
    - added --eod
- 04-24-2019 2.2.2
    - added laser-test.py
    - added max\_tries and max\_integration\_time\_ms to balance\_acquisition
- 04-18-2019 2.2.1
    - wasatch-shell.py
        - added has\_laser\_power\_calibration 
    - load-test.py 
        - added --script-file
        - added script\_long.py and script\_short.py 
        - increased buffer size, timeout on load-test.py
- 04-18-2019 2.2.0
    - ported to Python 3.4 to support latest Wasatch.PY
- 03-19-2019 2.1.1
    - added set\_laser\_power\_perc
- 03-15-2019 2.1.0
    - updated to DeviceID
    - refactored input token processing
    - changed from direct FID calls to WasatchDevice.change\_setting
    - changed to non-free-running mode
    - changed to bare readings to reduce duplicate photodiode reads
    - added set\_scans\_to\_average
    - added set\_acquisition\_laser\_trigger\_enable
    - added set\_acquisition\_laser\_trigger\_delay\_ms
- 10-24-2018 2.0.4
    - added has\_linearity\_coeffs
- 08-22-2018 2.0.3
    - improved exception handling
- 08-02-2018 2.0.2
    - added GNU readline support
    - allowed saving of interpolated spectra
    - fixed on Windows (Git Cmd shell)
- 07-31-2018 2.0.1
    - migrated load-test.tcl to load-test.py for Windows
- 07-31-2018 2.0.2
    - added set\_interpolated\_x\_axis\_cm
- 07-27-2018 2.0.1
    - added load-test.tcl
- 07-27-2018 2.0.0
    - changed to a wrapper over Wasatch.PY
- 07-24-2018 1.0.7
    - added auto\_balance
- 05-31-2018 1.0.6
    - changed laser power granularity to 0.1%
- 05-30-2018 1.0.5
    - added HAS\_PHOTODIODE\_CALIBRATION
