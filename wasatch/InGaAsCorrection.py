import struct
import logging

log = logging.getLogger(__name__)

class InGaAsCorrection:

    BYTES_PER_PAGE      = 64
    BYTES_PER_FLOAT     = 4

    def __init__(self, pixels=512):
        self.pixels = pixels

        self.enabled = False
        self.mode = None
        self.offsets = None 
        self.slopes = None 

    def parse_json_data(self, data):
        """
        {
            "pixel_corrections": {
                "etalon_correction": {
                    "mode": "default",
                    "factors": [ ... ]
                },
                "ingaas_correction": {  <-- YOU ARE HERE
                    "mode": "default",
                    "offsets": [ ... ],
                    "slopes": [ ... ]
                }
            }
        }
        """
        self.mode = data["mode"]
        self.offsets = data["offsets"]
        self.slopes = data["slopes"]

    def eeprom_page_range(self):
        first = 10
        count = self.pixels * self.BYTES_PER_FLOAT // self.BYTES_PER_PAGE
        count *= 2 # offsets + slopes
        return (first, count)

    def parse_eeprom_buffers(self, buffers):
        self.mode = "default" # save your funky stuff for JSON
        self.offsets = []
        self.slopes = []

        half = len(buffers) // 2

        # offsets
        for buf in buffers[:half]:
            for index in range(self.BYTES_PER_PAGE // self.BYTES_PER_FLOAT):
                offset = index * 4
                value = struct.unpack("f", buf[offset:offset+4])[0]
                self.offsets.append(value)

        # slopes
        for buf in buffers[half:]:
            for index in range(self.BYTES_PER_PAGE // self.BYTES_PER_FLOAT):
                offset = index * 4
                value = struct.unpack("f", buf[offset:offset+4])[0]
                self.slopes.append(value)

        log.debug("parsed {len(self.offsets)} offsets and {len(self.slopes)} slopes from {len(buffers)} pages")

    def apply(self, spectrum):
        if not self.enabled:
            return spectrum

        if self.mode != "default":
            log.error("unimplemented mode {self.mode}")
            return spectrum

        smoothed = [] 
        for i, intensity in enumerate(spectrum):
            smoothed.append(intensity * self.slopes[i] + self.offsets[i])
        return smoothed
