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

# Changes from 1.0

WasatchShell 1.x was a standalone script which did not use the rest of 
Wasatch.PY.  It basically re-implemented various commands, in some cases 
implementing them differently than the main Wasatch.PY and so potentially 
generated different results software from other Wasatch.PY applications
like ENLIGHTEN.  

WasatchShell 2.x is a lightweight wrapper over Wasatch.PY.  Sspecifically,
it uses WasatchBus, WasatchDevice and FeatureIdentificationDevice, but NOT
WasatchDeviceWrapper.  That means that it uses Wasatch.PY in a "blocking"
(single-threaded, single-process) architecture, rather than the non-blocking 
multi-process pipeline used by ENLIGHTEN.

- Command parameters can be on the same line, or on following lines.  The 
  following are all equivalent:

  balance\_acquisition integ 45000 2500 850 nm

  balance\_acquisition integ
  45000 2500
  850 nm

  balance\_acquisition 
  integ
  45000 
  2500
  850 
  nm

- Boolean arguments may be passed as "on/off", "true/false", "yes/no" or the 
  original "1/0".  Outputs are still generally 1/0.

## Renamed commands

- get\_actual\_integration\_time -> get\_actual\_integration\_time\_us
- get\_integration\_time -> get\_integration\_time\_ms
- get\_laser\_mod -> get\_laser\_mod\_enabled
- get\_laser\_ramping\_mode -> get\_laser\_power\_ramping\_enabled
- get\_laser\_temp -> get\_laser\_temperature\_degc
- get\_laser\_temp\_setpoint -> [removed]
- get\_mod\_duration -> get\_laser\_mod\_duration
- get\_mod\_period -> get\_laser\_mod\_period
- get\_mod\_pulse\_delay -> get\_laser\_mod\_pulse\_delay
- get\_photodiode\_mw -> get\_secondary\_adc\_calibrated
- get\_selected\_laser -> get\_selected\_adc
- gettecenable -> get\_tec\_enable
- gettemp -> get\_detector\_temperature\_degc
- gettempset -> get\_detector\_temperature\_setpoint\_degc
- set\_lsi\_mw -> set\_laser\_power\_mw
- setinttime -> set\_integration\_time\_ms
- setlse -> set\_laser\_enable
- settece -> set\_tec\_enable
- startacquisition / getdata -> get\_spectrum
- vr\_get\_num\_frames -> get\_vr\_num\_frames

# Version History

- 07-31-2018 2.0.x
    - migrating load-test.tcl to load-test.py for Windows
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
