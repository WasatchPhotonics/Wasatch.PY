import logging

log = logging.getLogger(__name__)

## Not used - incomplete!
SETTINGS = {
    # EEPROM
    "adc_to_degC_coeffs":              { "datatype": "float[]" },
    "degC_to_dac_coeffs":              { "datatype": "float[]" },
    "wavelength_coeffs":               { "datatype": "float[]" },
    "update_eeprom":                   { "datatype": "object" },
    "replace_eeprom":                  { "datatype": "object" },
    "write_eeprom":                    { "datatype": "void" },

    # Detector
    "integration_time_ms":             { "datatype": "int" },
    "detector_tec_enable":             { "datatype": "bool" },
    "detector_tec_setpoint_degC":      { "datatype": "int" },
    "high_gain_mode_enable":           { "datatype": "bool" },

    # Laser
    "laser_enable":                    { "datatype": "bool" },
    "laser_power_perc":                { "datatype": "float" },
    "laser_power_mW":                  { "datatype": "float" },
    "laser_temperature_setpoint_raw":  { "datatype": "int" },

    # Processing Modes
    "area_scan_enable":                { "datatype": "bool" },
    "bad_pixel_mode":                  { "datatype": "int" },
    "invert_x_axis":                   { "datatype": "bool" },
    "scans_to_average":                { "datatype": "int" },

    # Secondary ADC
    "enable_secondary_adc":            { "datatype": "bool" },

    # FPGA registers
    "ccd_gain":                        { "datatype": "float" },
    "ccd_offset":                      { "datatype": "int" },

    # Triggering
    "trigger_source":                  { "datatype": "int" },

    # other
    "log_level":                       { "datatype": "string" },
    "max_usb_interval_ms":             { "datatype": "int" },
    "min_usb_interval_ms":             { "datatype": "int" },
    "reset_fpga":                      { "datatype": "void" },
}

##
# This class encapsulates information about the "ControlObject" settings
# supported by WasatchDevice hardware classes (FID and SP).  These are
# traditionally called "settings" when passing ControlObjects from ENLIGHTEN
# down into Wasatch.PY via "settings queues", but are very different from
# SpectrometerSettings (actual settings of the spectrometer, vs commands
# being passed between processes). 
#
# We're not actually using this at the moment, but it would be a way to provide 
# some automated data validation and type-checking on callers like wasatch-shell.py
class CommandSettings(object):

    def get_settings(self):
        return sorted(SETTINGS.keys())

    def get_datatype(self, setting):
        if not setting in SETTINGS:
            return None

        return SETTINGS[setting]["datatype"]

    def valid(self, setting):
        return setting in SETTINGS

    def convert_type(self, setting, value):
        if not setting in SETTINGS:
            log.error("invalid setting: %s", setting)
            return None

        dt = SETTINGS[setting]["datatype"]
        if dt == "bool":
            return "true" in value.lower()
        elif dt == "int":
            return int(value)
        elif dt == "float":
            return float(value)
        elif dt == "string":
            return str(value)
        elif dt == "float[]":
            values = []
            for tok in value.split(','):
                values.append(float(tok))
            return values
        else:
            log.debug("don't know how to convert %s %s settings", setting, dt)
            return value
