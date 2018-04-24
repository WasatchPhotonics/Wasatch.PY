# Overview

Before there was Wasatch.PY (the open-source back-end of ENLIGHTEN), there was
"wasatch.py", a simple Python script allowing an ASCII request-response control 
of spectrometers through STDIN / STDOUT pipes.

This script is not recommended for production use in new development, but is a 
simple and useful demonstration of how to 

# Sample Execution

    $ ./wasatch.py
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

    # read laser temperature
    select_laser
    0
    get_laser_temp

    # read secondary adc
    select_laser
    1
    get_laser_temp

    startacquisition

    getspectrum

    # disable laser
    setlse
    0

    # confirm laser disengaged
    get_laser

    # shutdown
    close
