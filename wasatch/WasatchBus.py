import logging

from .DeviceFinderUSB import DeviceFinderUSB

from usb import USBError

log = logging.getLogger(__name__)

##
# The different bus classes don't use inheritance and don't follow a common ABC
# or interface, but each should have an update() method, and each should have a 
# 'device_ids' array.
#
# @param use_sim not used, left to avoid breaking old code
# @param monitor_dir not used, left to avoid breaking old code
class WasatchBus:
    def __init__(self, use_sim=False, monitor_dir=None):
        self.device_ids = []

        # update buses on creation
        self.usb_bus = USBBus()
        self.update()

    ## called by enlighten.Controller.tick_bus_listener()
    def update(self, poll = False):
        if self.usb_bus:
            # MZ: if we call .extend here...when are devices ever purged from the stateful list?
            # self.device_ids.extend(self.usb_bus.update(poll)) 
            self.device_ids = self.usb_bus.update(poll)
            self.device_ids = list(set(self.device_ids)) # used in case of poll if same device is present before connection finishes

    def is_empty(self): # -> bool 
        return 0 == len(self.device_ids)

    ## called by Controller.update_connections
    def dump(self):
        log.debug("WasatchBus.dump: %s", self.device_ids)

class USBBus:
    finder = DeviceFinderUSB() # note: static attribute

    def __init__(self):
        self.backend_error_raised = False
        self.update()

    ## Return a list of DeviceIDs on the USB bus
    def update(self, poll = False):
        device_ids = []
        try:
            log.debug("USBBus.update: instantiating DeviceFinderUSB")
            device_ids = self.finder.find_usb_devices(poll=True)
        except USBError:
            # MZ: this seems to happen when I run from Git Bash shell
            #     (resolved on MacOS with 'brew install libusb')
            if not self.backend_error_raised:
                log.warn("No libusb backend", exc_info=1)
                self.backend_error_raised = True
        except Exception:
            log.critical("LIBUSB error", exc_info=1)

        log.debug(f"USBBus.update: found {len(device_ids)}")
        return device_ids
