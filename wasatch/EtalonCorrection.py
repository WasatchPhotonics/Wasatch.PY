import math
import numpy as np
import struct
import logging
import json
import os

from datetime import date

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

    def cache_json_data(self, data, serial_number, pathname=None):
        """
        ToDo:
        - if pathname is None, create as [path to]/EnlightenSpectra/config/[SN].json
        - if pathname does not exist, create it as {}
        - load the JSON file to a dict as 'data'
        - add new data["pixel_corrections"]["etalon_correction"]["mode"] and ["factors"] as above
        - re-save updated dict back to pathname
        """
        # Check for the pathname and update if needed
        if pathname is None:
            pathname = ('~/EnlightenSpectra/config/') # I feel like this is wrong, double check
            
        pathname = os.path.join(pathname, "%s.json" % serial_number)
        
        # Takes the etalon data and dictonaries it for JSON creation
        m = {
            "pixel_corrections":{}#,
            #"etalon_corrections": {}
        }
        m["pixel_corrections"]["etalon_correction"] = data
        
# This is stuff to add back in once it works proper        
        #if self.mode is not None:
            #m["pixel_corrections"]["etalon_correction"]["mode"] = self.mode
        #else:
            #log.debug("etalon_correction mode not found")
        
        #if self.factors is not None:
            #m["pixel_corrections"]["etalon_correction"]["factors"] = self.factors
        #else:
            #log.debug("etalon_correction factors not found")
            
        #m["pixel_corrections"]["etalon_correction"]["creation_date"] = date.today()
        
        # Create the JSON
        s = self.to_json(data = m)
        
        # Write the JSON to file, should save to the config folder of EnlightenSpectra/config/
        with open(pathname, "w", encoding = 'utf-8') as f:
            f.write(s)

        log.error("cache_json_data({pathname}): NOT IMPLEMENTED")

    def to_json(self, data):
        
        s = json.dumps(data, sort_keys = True, indent = 4, default=lambda o: o.to_json())
        
        return util.clean_json(s)   

        

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
