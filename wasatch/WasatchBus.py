import logging
import os

from DeviceListFID import DeviceListFID

from usb import USBError

log = logging.getLogger(__name__)

# ##############################################################################
#                                                                              #
#                                WasatchBus                                    #
#                                                                              #
# ##############################################################################

##
# The different bus classes don't use inheritance and don't follow a common ABC
# or interface, but each should have an update() method, and each should have a 
# 'uuids' array (TODO: USE INHERITANCE!) 
#
# 'uuids' should be an array of strings, where the values are meaningful to 
# that particular bus ("VID:PID:n" (e.g. "0x24aa:0x1000:0") for USBBus, or
# "/foo/bar" for MonitorBus).  The uuids array can be empty (or probably None), 
# or the first element can be "disconnected".
#
# Earlier versions of Wasatch.PY / ENLIGHTEN only used VID:PID for USBBus,
# which prevented the keys from being unique when multiple spectrometers were 
# plugged in.  ENLIGHTEN 3.4+ requires that all device keys be unique to support
# parallel device operation.
#
# I am borrowing the term "UUID" from the BLE world to indiciate a unique device
# indicator, but these are certainly not necessarily formatted as BLE UUIDs.
# Also to be clear, the UUID simply refers to the connection, and will change
# if the devices are plugged into different USB ports, or in a different order,
# or the user reboots or whatever.  
class WasatchBus(object):
    def __init__(self, 
            use_sim     = False,
            monitor_dir = None):

        self.monitor_dir = monitor_dir

        self.uuids = []

        self.file_bus = None
        self.usb_bus = None

        if self.monitor_dir:
            self.file_bus = FileBus(self.monitor_dir) 
        else:
            self.usb_bus = USBBus()

        # iterate buses on creation
        self.update()

    ## called by Controller.update_connections
    def update(self):
        self.uuids = []

        if self.file_bus:
            self.uuids.extend(self.file_bus.update())

        if self.usb_bus:
            self.uuids.extend(self.usb_bus.update())

    ## called by Controller.update_connections
    def dump(self):
        log.debug("WasatchBus.dump: %s", self.uuids)

# ##############################################################################
#                                                                              #
#                                    USBBus                                    #
#                               (file private)                                 #
#                                                                              #
# ##############################################################################

class USBBus(object):

    def __init__(self):
        self.backend_error_raised = False
        self.update()

    ## Return a list of connected USB device keys, in the format
    # "VID:PID:order:pidOrder", e.g. [ "0x24aa:0x1000:0:0", "0x24aa:0x4000:1:0", "0x24aa:0x1000:2:1" ]
    def update(self):
        log.debug("USBBus.update: instantiating DeviceListFID")
        lister = DeviceListFID()

        uuids = []
        try:
            log.debug("USBBus.update: calling DeviceListFID.get_usb_discovery_recs")
            recs = lister.get_usb_discovery_recs()
            for rec in recs:
                uuids.append(rec.get_uuid())
        except USBError:
            # MZ: this seems to happen when I run from Git Bash shell
            #     (resolved on MacOS with 'brew install libusb')
            if not self.backend_error_raised:
                log.warn("No libusb backend", exc_info=1)
                self.backend_error_raised = True

        except Exception:
            log.critical("LIBUSB error", exc_info=1)

        return uuids 

# ##############################################################################
#                                                                              #
#                                   FileBus                                    #
#                               (file private)                                 #
#                                                                              #
# ##############################################################################

class FileBus(object):
    def __init__(self, directory):
        super(B, self).__init__()
        self.directory = directory
        self.configfile = os.path.join(self.directory, "spectrometer.json")

    def update(self):
        uuids = []
        if os.access(self.directory, os.W_OK) and os.path.isfile(self.configfile):
            uuids.append(self.directory)
        return uuids 

