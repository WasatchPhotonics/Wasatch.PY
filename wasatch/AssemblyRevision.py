import json

class AssemblyRevision(dict):
    """
    Making this a standalone class because might end up using from both FID (USB)
    and BLEDevice.
    """
    def __init__(self, data=None):
        dict.__init__(self) # https://stackoverflow.com/a/31207881
        self.partnumber = None
        self.variant = None
        self.revision = None

        self.parse_data(data)

    def parse_data(self, data):
        """
        This is based on the data structure defined in ENG-0120 Rev 10.
        """
        if data is None or len(data) != 6:
            log.error(f"parse_data: invalid length {data}")
            return

        self.partnumber = (data[0] << 8) + data[1] + 140_000
        self.variant    = (data[2] << 8) + data[3]
        self.revision   = (data[4] << 8) + data[5]

    def serialize(self):
        """ probably a simpler way to do this with struct """
        buf = [ 0 ] * 6
        if self.partnumber:
            n = self.partnumber - 140_000
            buf[0] = (n >> 8) & 0xff
            buf[1] = n & 0xff
        if self.variant:
            n = self.variant
            buf[2] = (n >> 8) & 0xff
            buf[3] = n & 0xff
        if self.revision:
            n = self.revision
            buf[4] = (n >> 8) & 0xff
            buf[5] = n & 0xff

    def __repr__(self):
        s = ""
        if self.partnumber:
            s += f"{self.partnumber}"
            if self.variant:
                s += f"-{self.variant}"
            if self.revision:
                s += f" Rev{self.revision}"
        return s
