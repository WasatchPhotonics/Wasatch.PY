class DeviceID(object):

    def __init__(self, device=None, device_id=None, directory=None):
        if device_id is not None:
            # instantiate from an existing string id
            if device_id.startswith("USB:"):
                tok = device_id.split(":")
                self.type = tok[0]
                self.vid = int(tok[1][2:])
                self.pid = int(tok[2][2:])
                self.bus = int(tok[3])
                self.address = int(tok[4])
            elif device_id.startswith("FILE:")
                self.type = tok[0]
                self.directory = tok[1]
            else:
                raise Exception("DeviceID: invalid device_id %s" % device_id)

        elif device is not None:
            # instantiate from a PyUSB Device
            self.type = "USB"
            self.vid = device.idVendor
            self.pid = device.idProduct
            self.bus = device.bus
            self.address = device.address

        elif directory is not None:
            # instantiate from a file spec
            self.type = "FILE"
            self.directory = directory

        else:
            raise Exception("DeviceID: needs device or device_id or directory")

    def __str__(self):
        if self.type == "USB":
            "%s:0x%04x:0x%04x:%d:%d" % (self.type, self.vid, self.pid, self.bus, self.address)
        elif self.type == "FILE":
            "%s:%s" % (self.type, self.directory)
        else:
            raise Exception("unsupported DeviceID type %s" % self.type)

