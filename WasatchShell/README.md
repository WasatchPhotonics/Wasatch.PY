# Overview

Before there was Wasatch.PY (the open-source back-end of ENLIGHTEN), there was
"wasatch-shell.py", a simple Python script allowing an ASCII request-response 
control of spectrometers through STDIN / STDOUT pipes.

This script is not recommended for production use in new development, but is a 
simple and useful demonstration of how to enable and query various functions,
as well as perform quick tests from the command-line.

Note that the local copy of EEPROM.py is snapshot of ../wasatch/EEPROM.py, and 
can be freely updated from that source.

# Sample Execution

    $ ./wasatch-shell.py
    help

    open

    setinttime
    100

    # set laser to 70% power
    setlsi
    100
    70

    # enable laser
    setlse
    1

    # confirm laser firing
    get_laser

    # set laser to 50mW
    set_lsi_mw
    50

    # confirm laser output power
    get_photodiode_mw
    49.1342855427

    # read laser temperature
    get_laser_temp

    # read secondary adc
    get_photodiode
    1152

    startacquisition

    getspectrum

    # disable laser
    setlse
    0

    # confirm laser disengaged
    get_laser

    # shutdown
    close

# Version History

- 05-31-2018 1.0.6
    - changed laser power granularity to 0.1%
- 05-30-2018 1.0.5
    - added HAS\_PHOTODIODE\_CALIBRATION
