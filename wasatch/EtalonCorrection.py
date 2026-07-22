import math
import numpy as np
import struct
import logging

log = logging.getLogger(__name__)

class EtalonCorrection:

    BYTES_PER_PAGE      = 64
    BYTES_PER_FLOAT     = 4

    def __init__(self, pixels):
        self.pixels = pixels

        self.mode = None
        self.factors = None 

    def to_json(self): 
        return vars(self)

    def parse_json_data(self, data):
        """
        {
            "pixel_corrections": {
                "etalon_correction": {  <-- YOU ARE HERE
                    "mode": "default",
                    "factors": [ ... ]
                },
                "ingaas_correction": {
                    "mode": "default",
                    "offsets": [ ... ],
                    "slopes": [ ... ]
                }
            }
        }
        """
        self.mode = data["mode"]
        self.factors = data["factors"]

    def cache_json_data(self, pathname=None):
        """
        ToDo:
        - if pathname is None, create as [path to]/EnlightenSpectra/config/[SN].json
        - if pathname does not exist, create it as {}
        - load the JSON file to a dict as 'data'
        - add new data["pixel_corrections"]["etalon_correction"]["mode"] and ["factors"] as above
        - re-save updated dict back to pathname
        """
        log.error("cache_json_data({pathname}): NOT IMPLEMENTED")

    def eeprom_page_range(self):
        """ called by FeatureIdentificationDevice._read_pixel_correction_from_eeprom """
        first = 10
        count = self.pixels * self.BYTES_PER_FLOAT // self.BYTES_PER_PAGE
        return (first, count)

    def parse_eeprom_buffers(self, buffers):
        log.debug("parsing EEPROM factors")
        self.mode = "default" # save your funky stuff for JSON
        self.factors = []
        for buf in buffers:
            for index in range(self.BYTES_PER_PAGE // self.BYTES_PER_FLOAT):
                offset = index * 4
                value = struct.unpack("f", buf[offset:offset+4])[0]
                self.factors.append(value)

        hi = max(self.factors)
        lo = min(self.factors)
        mean = np.mean(self.factors)

        log.debug(f"parsed {len(self.factors)} factors from {len(buffers)} pages (lo {lo}, hi {hi}, mean {mean})")

        return True

    def apply(self, spectrum):
        if self.mode != "default":
            log.error("unimplemented mode {self.mode}")
            return spectrum

        corrected = [] 
        for i, intensity in enumerate(spectrum):
            corrected.append(intensity * self.factors[i])
        return corrected
