import usb
import logging
import usb.backend.libusb0 as libusb0
from .AbstractUSBDevice import AbstractUSBDevice

log = logging.getLogger(__name__)

class RealUSBDevice(AbstractUSBDevice):

    def __init__(self,device_id):
        self.device_id = device_id
        self.vid = self.device_id.vid
        self.pid = self.device_id.pid
        self.bus = self.device_id.bus
        self.address = self.device_id.address

    def find(self, *args, **kwargs):
        return usb.core.find(*args, **kwargs, backend=libusb0.get_backend())

    def set_configuration(self, device):
        device.set_configuration()

    def reset(self, dev):
        dev.reset()

    def claim_interface(self):
        return usb.util.claim_interface(*args, **kwargs)

    def release_interface(self, *args, **kwargs):
        return usb.util.release_interface(*args, **kwargs)

    def ctrl_transfer(self, *args):
        device = args[0]
        ctrl_args = args[1:]
        return device.ctrl_transfer(*ctrl_args)

    def read(self, *args, **kwargs):
        device =  args[0]
        read_args = args[1:]
        return device.read(*read_args, **kwargs)

    def send_code(self):
        pass

    def to_dict():
        return str(self)

    def __str__(self):
        return "<RealUSBDevice 0x%04x:0x%04x:%d:%d>" % (self.vid, self.pid, self.bus, self.address)

    def __hash__(self):
        return hash(str(self))

    def __repr__(self):
        return str(self)

    def __eq__(self, other):
        return str(self) == str(other)

    def __ne__(self, other):
        return str(self) != str(other)

    def __lt__(self, other):
        return str(self) < str(other)
