import struct
import logging

log = logging.getLogger(__name__)

class EtalonCorrection:

    BYTES_PER_PAGE      = 64
    BYTES_PER_FLOAT     = 4

    def __init__(self, pixels):
        self.pixels = pixels

        self.enabled = False
        self.mode = None
        self.factors = None 

    def parse_json_data(self, data):
        """
        {
            "pixel_calibrations": {
                "etalon_correction": {  <-- data
                    "mode": "default",
                    "factors": [ ... ]
                },
                "ingaas_even_odd": {
                    "mode": "default",
                    "offsets": [ ... ],
                    "slopes": [ ... ]
                }
            }
        }
        """
        self.mode = data["mode"]
        self.factors = data["factors"]

    def eeprom_page_range(self):
        first = 10
        count = self.pixels * self.BYTES_PER_FLOAT // self.BYTES_PER_PAGE
        return (first, count)

    def parse_eeprom_buffers(self, buffers):
        self.mode = "default" # save your funky stuff for JSON
        self.factors = []
        for buf in buffers:
            for index in range(self.BYTES_PER_PAGE // self.BYTES_PER_FLOAT):
                value = struct.unpack("f", buf[index:index+4])[0]
                self.factors.append(value)
        log.debug("parsed {len(self.factors)} factors from {len(buffers)} pages")

    def apply(self, spectrum):
        if not self.enabled:
            return spectrum

        if self.mode != "default":
            log.error("unimplemented mode {self.mode}")
            return spectrum

        smoothed = [] 
        for i, intensity in enumerate(spectrum):
            smoothed.append(intensity * self.factors[i])
        return smoothed
