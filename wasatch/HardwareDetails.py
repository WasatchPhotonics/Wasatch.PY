class HardwareDetails(object):
    """ Simple data structure to host the details read from the EEPROM
        (or defaults). Must be defined at the top level in a module in
        order to be pickleable (support serialization). """

    # not sure why this isn't just a dict
    name = "hwdetails"

