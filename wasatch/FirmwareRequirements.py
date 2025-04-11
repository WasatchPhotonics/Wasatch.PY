import logging

from .utils import vercmp

log = logging.getLogger(__name__)

class FirmwareRequirements:
    """
    This is a place to capture developmental, R&D features which are only 
    available in specific firmware versions. The currrent implementation assumes
    that features, once added, aren't removed (minimum check is sufficient). We 
    can always add complexity down the road, encapsulated within this class.
    """

    def __init__(self, settings):
        self.settings = settings

        self.feature_versions = {
            "imx_stabilization":                { "microcontroller": { "min": "1.0.7.0" } },
            "microcontroller_serial_number":    { "microcontroller": { "min": "1.0.4.5", "unsupported": [ "11.3.0.37" ] } },
            "get_ble_firmware_version":         { "microcontroller": { "min": "1.0.4.5", "unsupported": [ "11.3.0.37", "1.0.33.7" ] } },
            "get_laser_warning_delay_sec":      { "microcontroller": { "min": "1.0.4.5", "unsupported": [ "11.3.0.37" ] } },
            "hamamatsu_vertical_roi":           { "microcontroller": { "min": "10.0.0.47" } }, # , "fpga": { "min": "35_12_0", "includes": "_" } },
        }

    def supports(self, feature):
        """
        @todo generalize the logic within microcontroller and fpga portions; add ble
        """
        if feature not in self.feature_versions:
            log.error(f"supports: unknown feature {feature}")
            return False

        micro_ver = self.settings.microcontroller_firmware_version
        fpga_ver  = self.settings.fpga_firmware_version

        reqts = self.feature_versions[feature]
        if "microcontroller" in reqts:
            reqt = reqts["microcontroller"]
            if "min" in reqt:
                min_ = reqt["min"]
                if vercmp(micro_ver, min_) < 0:
                    # log.debug(f"supports: {feature} NOT supported (micro {micro_ver} < required {min_}")
                    return False
            if "unsupported" in reqt:
                if micro_ver in reqt["unsupported"]:
                    return False
            # could support "max", list etc

        if "fpga" in reqts:
            reqt = reqts["fpga"]
            if "includes" in reqt:
                if reqs["includes"] not in fpga_ver:
                    return False
            if "min" in reqt:
                min_ = reqt["min"]
                if vercmp(fpga_ver, min_) < 0:
                    # log.debug(f"supports: {feature} NOT supported (fpga {fpga_ver} < required {min_}")
                    return False
            # could support "max", list etc

        # log.debug(f"supports: {feature} supported")
        return True

    def __repr__(self):
        return "Firmware Requirements"
