import logging
import os

from DeviceFinderUSB import DeviceFinderUSB

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
# 'device_ids' array.
#
# @param use_sim not used, left to avoid breaking old code
class WasatchBus(object):
    def __init__(self, use_sim=False, monitor_dir=None):

        self.monitor_dir = monitor_dir

        self.device_ids = []

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
        self.device_ids = []

        if self.file_bus:
            self.device_ids.extend(self.file_bus.update())

        if self.usb_bus:
            self.device_ids.extend(self.usb_bus.update())

    ## called by Controller.update_connections
    def dump(self):
        log.debug("WasatchBus.dump: %s", self.device_ids)

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

    ## Return a list of DeviceIDs on the USB bus
    def update(self):
        device_ids = []

        try:
            log.debug("USBBus.update: instantiating DeviceFinderUSB")
            finder = DeviceFinderUSB()
            device_ids.extend(finder.find_usb_devices())
        except USBError:
            # MZ: this seems to happen when I run from Git Bash shell
            #     (resolved on MacOS with 'brew install libusb')
            if not self.backend_error_raised:
                log.warn("No libusb backend", exc_info=1)
                self.backend_error_raised = True

        except Exception:
            log.critical("LIBUSB error", exc_info=1)

        return device_ids

# ##############################################################################
#                                                                              #
#                                   FileBus                                    #
#                               (file private)                                 #
#                                                                              #
# ##############################################################################

##
# Support a "virtual spectrometer" implemented via a "watch folder" containing
# a file named "spectrometer.json" (see FileSpectrometer for format), allowing
# ENLIGHTEN to send commands "to" the remote spectrometer (by writing command
# files to the directory), and reading spectra "from" the remote spectrometer
# (by reading spectrum files from the directory).  
#
# It was initially created to allow ENLIGHTEN to control an instance of ASISpec
# and make a ZWO ASI290MM camera (positioned behind an optical bench and grating)
# appear as a spectrometer in ENLIGHTEN.
#
# Currently this only supports a single directory, passed to the constructor.
# As a proper "Bus" we should conceptually support the concept of multiple 
# "watch folders", but there hasn't been a need to date.  
#
# One possible application would be to use this as a "test-driver" for ENLIGHTEN,
# "faking" the inputs and outputs of a variety of different simulated spectrometers
# from an external program.  It wouldn't be terribly efficient (there are faster
# ways to pipe data than via files in a watch-folder) but would work.
# 
# @see FileSpectrometer
class FileBus(object):
    def __init__(self, directory):
        super(B, self).__init__()
        self.directory = directory
        self.configfile = os.path.join(self.directory, "spectrometer.json")

    def update(self):
        device_ids = []
        if os.access(self.directory, os.W_OK) and os.path.isfile(self.configfile):
            device_id = DeviceID(directory = self.directory)
            device_ids.append(device_id)
        return device_ids
