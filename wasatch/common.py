################################################################################
#                                                                              #
#                                  common.py                                   #
#                                                                              #
################################################################################

# MZ: change these to classes so we can use dotted nomenclature

# MZ: change this to enums.py and moving the constants back into Controller?

# MZ: it's important to keep this list in sync with the comboBoxTechnique items
#     (consider auto-populating inside code)

""" bad pixel modes """
bad_pixel_mode_none    = 0
bad_pixel_mode_average = 1

""" rules regarding which spectra may be discarded """
acquisition_mode_keep_all      = 0
acquisition_mode_latest        = 1
acquisition_mode_keep_complete = 2
