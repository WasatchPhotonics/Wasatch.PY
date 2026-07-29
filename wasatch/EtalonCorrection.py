import math
import numpy as np
import struct
import logging
import json
import os

from . import utils

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

    def cache_json_data(self, serial_number, pathname=None):
        """
        @param (input) serial_number e.g. "WP-12345"
        @pathname (input) /path/to/WP-12345.json

        This function saves the EtalonCorrection data in JSON format to disk so 
        we don't have to load it over BLE / USB / whatever again.

        If the JSON file already exists, we ADD this to the file. If the JSON
        file doesn't already exist, we CREATE it with this data.

        In short:

        - if pathname is None, create as [path to]/EnlightenSpectra/config/[SN].json
        - if pathname does not exist, create it as {}
        - load the JSON file to a dict as 'data'
        - add new data["pixel_corrections"]["etalon_correction"]["mode"] and ["factors"] as above
        - re-save updated dict back to pathname
        """
        if pathname is None:
            dirname = os.path.join(utils.get_default_data_dir(), "config")
            if not os.path.isdir(dirname):
                log.debug(f"cache_json_data: creating {dirname}")
                os.makedirs(dirname)
            pathname = os.path.join(dirname, f"{serial_number}.json")

        # load file if it exists
        if os.path.exists(pathname):
            log.debug(f"cache_json_data: importing existing {pathname}")
            with open(pathname, encoding='utf-8') as infile:
                existing_data = json.load(infile)
        else:
            log.debug(f"cache_json_data: {pathname} not found, creating")
            existing_data = {}
        
        # create pixel corrections if doesn't exist
        if "pixel_corrections" not in existing_data:
            existing_data["pixel_corrections"] = {}

        # add/overwrite etalon correction to pixel corrections
        existing_data["pixel_corrections"]["etalon_correction"] = {
            "mode": self.mode,
            "factors": self.factors
        }
        
        # create or overwrite the JSON file
        with open(pathname, "w", encoding = 'utf-8') as outfile:
            json.dump(existing_data, outfile, sort_keys=True)

        log.debug(f"cache_json_data: wrote {pathname}")

    def eeprom_page_range(self):
        """ called by FeatureIdentificationDevice._read_pixel_correction_from_eeprom """
        first = 10
        count = self.pixels * self.BYTES_PER_FLOAT // self.BYTES_PER_PAGE
        return (first, count)

    def parse_eeprom_buffers(self, buffers):
        log.debug("parsing EEPROM factors")
        self.mode = "default" # save your funky stuff for JSON
        self.factors = []
        for buf_index, buf in enumerate(buffers):
            for index in range(self.BYTES_PER_PAGE // self.BYTES_PER_FLOAT):
                offset = index * 4
                float_buf = buf[offset:offset+4]
                value = struct.unpack("f", float_buf)[0]
                # log.debug(f"parse_eeprom_buffers: buf_index {buf_index}, pixel index {index}, offset {offset}, float_buf {float_buf}, value {value}")
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
