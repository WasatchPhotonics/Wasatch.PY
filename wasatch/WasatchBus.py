import logging

from .DeviceFinderUSB import DeviceFinderUSB
from .DeviceFinderTCP import DeviceFinderTCP

from usb import USBError

log = logging.getLogger(__name__)

class WasatchBus:
    """
    The different Bus classes don't use inheritance and don't follow a common ABC
    or interface, but each should have an update() method, and each should have a 
    'device_ids' array.
    
    @param use_sim not used, left to avoid breaking old code
    @param monitor_dir not used, left to avoid breaking old code
    """
    def __init__(self, use_sim=False, monitor_dir=None):
        self.device_ids = []

        self.usb_bus = USBBus()
        self.tcp_bus = TCPBus()

        self.update()

    def update(self, poll=False):
        """ CONNECT-3: called by enlighten.Controller.tick_bus_listener() """
        device_ids = []
        if self.usb_bus:
            usb_device_ids = self.usb_bus.update(poll)
            device_ids.extend(usb_device_ids)

        if self.tcp_bus:
            tcp_device_ids = self.tcp_bus.update(poll)
            device_ids.extend(tcp_device_ids)

        # purge any duplicates (can happen while USB device is enumerating)
        self.device_ids = list(set(device_ids)) 

    def is_empty(self): 
        return 0 == len(self.device_ids)

    def dump(self):
        """ called by Controller.update_connections """
        log.debug("WasatchBus:")
        for device_id in self.device_ids:
            log.debug(f"  {device_id}")

class USBBus:
    finder = DeviceFinderUSB() # static attribute

    def __init__(self):
        self.backend_error_raised = False

    def update(self, poll=False):
        device_ids = []
        try:
            """ CONNECT-4: generate a list of DeviceIDs on the USB bus """
            device_ids = self.finder.find_usb_devices(poll=True)
        except USBError:
            # MZ: this seems to happen when I run from Git Bash shell
            #     (resolved on MacOS with 'brew install libusb')
            if not self.backend_error_raised:
                log.warn("USBBus: No libusb backend", exc_info=1)
                self.backend_error_raised = True
        except Exception:
            log.critical("USBBus: LIBUSB error", exc_info=1)

        return device_ids

class TCPBus:
    
    # these are static so callers can casually instantiate and release WasatchBus
    # objects whenever they want, but these singletons will remain quietly 
    # persistent
    finder = DeviceFinderTCP() # static
    addresses = []

    def __init__(self):
        pass

    def update(self, poll=False):
        device_ids = self.finder.find_tcp_devices()
        return device_ids
