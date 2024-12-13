import logging
import re

log = logging.getLogger(__name__)

##
# Represents a persistent unique identifier for a spectrometer device
# (USB, BLE, TCP, whatever) which should remain valid for connected devices
# in spite of hotplug events around them.
#
# @par Statement of the Problem
#
# Assume we have four WP-785 spectrometers: A, B, C and D.  We connect
# A, B and C, then launch ENLIGHTEN.  A is the first on the USB chain, so
# in a positional listing A would be #1, B is #2, C is #3.
#
# Now we unplug B.  That leaves us with A (1) and C (3).  Except that, 
# positionally C is really now 2.  Now we plug in D.  Is D now 4th on
# the list, or did it slip into the unoccupied 2 slot?  Or was C internally 
# moved back to 2, and now D is 3, but C also thinks it's 3?
#
# One solution would be to UPDATE the positional order in a master list
# on unplug events (detecting B's removal, and changing C from 3 to 2).
# ENLIGHTEN could do that, but note that it would therefore be a client-
# side operation, not within the driver itself.  Or we could move the
# multi-process "Controller" ownership of the several WasatchDeviceWrapper 
# processes into Wasatch.PY, such that Wasatch.PY provided a single-object
# Facade.  We could possibly achieve that by maintaining a list of all
# WasatchDeviceWrapper instances in a static WasatchDeviceWrapper attribute
# (or WasatchDeviceWrappers or WasatchDeviceWrapperFactory as it were).
# But either would require some additional refactoring that I'm not diving
# into right now, although it seems an improved and reasonable architecture.
#
# Or maybe we don't need a DeviceID at all, and just track "the 
# WasatchDeviceWrapper associated with this serial number", and simply
# assume that all spectrometers will have a unique spectrometer (or be
# assigned one at connection).  So maybe my attempt to generate a UNIQUE
# and PERSISTENT DeviceID from the usb.device object is quixotic and
# unnecessary.  That's probably the case.
#
# Part of what drove this is that historically ENLIGHTEN supported a bus_order
# command-line argument which explicitly referenced spectrometers by their
# position on the USB bus.  However, we're now trying to support hotplug
# use-cases, meaning that position is no longer reliable.  But since we had a
# legacy expectation of being able to explicitly identify devices at the bus
# level, I'm trying to retain that capability by habit.  It probably is no
# longer needed, and we can probably replace the old --bus-order option with
# --serial-number instead.
#
# More to the point, the current architecture is such that ENLIGHTEN calls 
# WasatchBus to detect hotplug events, and then ENLIGHTEN instantiates a new 
# WasatchDeviceWrapper to support the new bus device.  That means there really
# ought to be a way to pass the "id" of the device, which was detected by 
# WasatchBus, down into WasatchDeviceWrapper (and hence WasatchDevice and 
# FeatureIdentificationDevice) to be re-instantiated.  I don't trust position,
# I'm not sure how to confirm "claim state" (ASSUMING we wanted the "first 
# eligible unclaimed") , "serial number" seems heavy-handed (and would still 
# require a way to deliberately "skip" already-connected devices, again requiring
# some sort of key).  
#
# ALSO we want to eventually support BLE (which has unique UUID), and perhaps
# TCP/IP (which has IP addresses)...basically I think objects on a "bus" should 
# be uniquely identifiable and addressable from their bus address, WITHOUT making
# guesses based on position or ordering or claim-state or anything like that.
#
# So yeah, I think this is useful.
#
# @par USB
#
# - .vid and .pid are stored as ints
#
# @par BLE
#
# - populates .serial_number from advertised localName.
# - populates .bleak_device from BleakScanner
#
# Adding the non-pickleable .bleak_device kind of defeats some of the original 
# purpose of this class, as ideally serialized (stringified) DeviceIDs were 
# intended to be passable between processes and "re-instantiated" on the 
# receiving end.
#
# However, now that ENLIGHTEN is multi-threaded (instead of multi-process), we
# don't have to worry about that, and it seems convenient to simply stash a 
# handle to the Bleak Device directly in the DeviceID, where it can be passed
# between the following (in typical connection order):
# 
# - wasatch.DeviceFinderBLE (BleakScanner emitting the BleakDevice)
# - enlighten.network.BLEManager (receives via device_id_queue)
# - enlighten.network.BLEManager.BLESelector (for initial add, and subsequent RSSI updates)
# - enlighten.network.BLEManager (after user selection and "Connect")
# - enlighten.Controller.other_device_ids 
# - wasatch.WasatchDeviceWrapper
# - wasatch.WrapperWorker
# - wasatch.BLEDevice (actually create the BleakClient)
#
# @par TCP
#
# - uses .address and .port
#
class DeviceID:

    ##
    # Instantiates a DeviceID object from either a usb.device or an
    # existing device_id string representation.
    #
    # @param device is a usb.device from pyusb
    # @param directory is used with FILE spectrometers
    # @param device_type: this seems to be currently exclusively used to 
    #        distinguish between RealUSBDevice and MockUSBDevice (both extending
    #        AbstractUSBDevice).
    def __init__(self, device=None, label=None, directory=None, device_type=None, overrides=None, spectra_options=None, rssi=None):

        self.type          = None   # "USB", "FILE", "MOCK", "BLE", "TCP"

        # USB
        self.vid           = None   # USB
        self.pid           = None   # USB
        self.bus           = None   # USB
        self.address       = None   # USB, TCP

        # MOCK
        self.name          = None   # MOCK?
        self.device_type   = device_type

        # FILE
        self.directory     = None   # FILE

        # BLE
        self.serial_number = None   # BLE
        self.rssi          = rssi

        self.overrides = overrides                  # MZ: what is this?
        self.spectra_options = spectra_options      # MZ: what is this?
        
        self.bleak_device = None

        if label is not None:
            # instantiate from an existing string id
            if label.startswith("USB:"):
                tok = label.split(":")
                self.type = tok[0]
                self.vid = int(tok[1][2:])
                self.pid = int(tok[2][2:])
                self.bus = int(tok[3])
                self.address = int(tok[4])
            elif label.startswith("FILE:"):
                tok = label.split(":")
                self.type = tok[0]
                self.directory = tok[1]
            elif label.startswith("BLE:"):
                tok = label.split(":")
                self.type = tok[0]
                self.serial_number = tok[1]
            elif label.startswith("TCP:"):
                tok = label.split(":")
                self.type = "TCP"
                self.address = tok[1]
                self.port = int(tok[2])
            elif label.startswith("MOCK:"):
                tok = label.split(":")
                self.type = tok[0]
                self.name = tok[1]
                self.directory = tok[2]
                self.vid = int(str(hash(self.name)))
                self.pid = 0x4000
                self.bus = 111111
                self.address = 111111
            else:
                raise Exception("DeviceID: invalid device_id label %s" % label)

        elif device is not None:
            # MZ: This needs redesigned. Currently it assumes that any "device" 
            #     is implicitly USB.

            # instantiate from a PyUSB Device
            log.debug(f"instantiating from device {device}")
            self.type = "USB"
            self.vid = int(device.idVendor)
            self.pid = int(device.idProduct)
            self.determine_bus_and_address(device)

        elif directory is not None:
            # instantiate from a file spec
            self.type = "FILE"
            self.directory = directory

        else:
            raise Exception("DeviceID: needs usb.device OR device_id label OR directory")

        log.debug("instantiated DeviceID: %s", str(self))

    def determine_bus_and_address(self, device):
        """
        This is for USB devices. It seems to work on tested platforms, but is not
        guaranteed by the protocol or library.
        """
        if hasattr(device, "dev"):
            self.bus     = int(device.dev.bus)
            self.address = int(device.dev.address)
            self.pid     = int(device.dev.idproduct)
            self.vid     = int(device.dev.idvendor)
            if device.dev.product is not None:
                self.product = device.dev.product.rstrip('\x00')
            if device.dev.serial_number is not None:
                self.serial  = device.dev.serial_number.rstrip('\x00')
            #serial number has ascii null chars that must be removed
            return
        else:
            self.bus     = int(device.bus)
            self.address = int(device.address)
            self.pid     = int(device.idProduct)
            self.vid     = int(device.idVendor)
            try:
                if device.product is not None:
                    self.product = device.product.rstrip('\x00')
                if device.serial_number is not None:
                    self.serial  = device.serial_number.rstrip('\x00')
                #serial number has ascii null chars that must be removed
            except Exception as e:
                log.error(f"While creating device id encountered {e}")
            return


        # if the above fails, try to parse from string representation, e.g.:
        # "DEVICE ID 24aa:1000 on Bus 000 Address 001 ================="
        s = str(device)
        m = re.match(r"Bus\s+(\d+)\s+Address\s+(\d+)", s, re.IGNORECASE)
        if m:
            self.bus     = int(m.group(1))
            self.address = int(m.group(2))
            return

        # Give up.  Shouldn't be a problem unless we talking to multiple devices
        # with the same PID at once (Raman Rainbow etc).
        log.error("can't determine bus or address of USB device from:\n%s", s)
        self.bus = -1
        self.address = -1

    def is_file(self):
        return self.type.upper() == "FILE"

    def is_usb(self):
        return self.type.upper() == "USB"

    def is_mock(self):
        return self.type.upper() == "MOCK"

    def is_ble(self):
        return self.type.upper() == "BLE"

    def is_tcp(self):
        return self.type.upper() == "TCP"

    def is_andor(self):
        return self.vid == 0x136e

    # Surely there is a better way to obtain the 'bus' and 'address' attributes 
    # than rendering the usb.device as a string and then parsing it.  I tried 
    # dumping the __dict__ and didn't see the address anywhere...must be in a 
    # sub-object I didn't traverse.
    #
    # @note I'm not sure this is guaranteed to work on all libusb implementations?
    #       But it seems to work on Ubuntu and Win10-64, so...
    #
    # @see https://github.com/pyusb/pyusb/blob/master/docs/tutorial.rst#user-content-dealing-with-multiple-identical-devices
    # def get_bus_and_address_NOT_USED(self, device):
    #     log.debug("parsing bus and address from device")
    # 
    #     # device.dev is what is returned by usb.core.find().  This is what you 
    #     # get if you just print device.dev to stdout
    #     s = str(device.dev)
    # 
    #     # ARE YOU SURE you can't just read device.dev.bus and .address?
    #     log.debug("MZ: s = %s", s)
    #     log.debug("MZ: bus = %s", str(device.dev.bus))
    #     log.debug("MZ: addr = %s", str(device.dev.address))
    # 
    #     device = None
    #     del device
    # 
    #     # extract the "hidden fields" from the first line of the ASCII dump
    #     m = re.match(r"DEVICE ID ([0-9a-f]{4}):([0-9a-f]{4}) on Bus (\d+) Address (\d+)", s, re.IGNORECASE)
    #     if m:
    #         # 1 and 2 are hex VID and PID respectively
    #         bus = int(m.group(3))
    #         address = int(m.group(4))
    #         log.debug("get_bus_and_address: parsed bus = %d, address = %d", bus, address)
    #     else:
    #         bus = -1
    #         address = -1
    #         log.critical("get_bus_and_address: failed to parse bus and address")
    #     return (bus, address)

    def get_pid_hex(self):
        if self.type in ["BLE", "TCP"]:
            return None
        return "%04x" % self.pid

    def get_vid_hex(self):
        if self.type in ["BLE", "TCP"]:
            return None
        return "%04x" % self.vid

    ##
    # Whether a given device is USB, FILE or otherwise, render the DeviceID
    # as a string containing all the relevant bits neccessary to reconstruct
    # the object into a parsed structure while providing a concise, readable
    # and hashable unique key.
    def __repr__(self):
        if   self.type == "USB":  return f"<DeviceID {self.type}:0x{self.vid:04x}:0x{self.pid:04x}:{self.bus}:{self.address}>"
        elif self.type == "FILE": return f"<DeviceID {self.type}:{self.directory}>"
        elif self.type == "MOCK": return f"<DeviceID {self.type}:{self.name}:{self.directory}>"
        elif self.type == "BLE":  return f"<DeviceID {self.type}:{self.serial_number}> (RSSI {self.rssi})"
        elif self.type == "TCP":  return f"<DeviceID {self.type}:{self.address}:{self.port}>"
        else: raise Exception("unsupported DeviceID type %s" % self.type)

    def __eq__(self, other):
        return str(self) == str(other)

    def __ne__(self, other):
        return str(self) != str(other)

    def __lt__(self, other):
        return str(self) < str(other)

    def __hash__(self):
        return hash(str(self))

    ## So that dict() can return a clean __dict__ without any "private" attributes
    #  (which we should probably __prefix or something)
    def to_dict(self):
        d = {}
        for k, v in self.__dict__.items():
            if k not in ["device"]:
                d[k] = v
            d["device_type"] = str(self.device_type)
        return d
