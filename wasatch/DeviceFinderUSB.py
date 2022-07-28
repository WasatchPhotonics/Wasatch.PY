import re
import logging
import platform
from functools import partial
if platform.system() == "Darwin":
    # would like to include in Mac stub
    # * imports must be at the module level though
    from ctypes import *
    from CoreFoundation import *

import usb
import usb.backend.libusb0 as libusb0

log = logging.getLogger(__name__)

from .DeviceID import DeviceID

##
# Generates a list of DeviceID objects for all connected USB Wasatch Photonics 
# spectrometers.
class DeviceFinderUSB(object):

    WASATCH_VID = 0x24aa
    OCEAN_VID = 0x2457
    ANDOR_VID = 0x136e
    FT232_SPI_VID = 0x0403
    WP_HAMA_SILICON_PID = 0x1000
    WP_HAMA_INGAAS_PID = 0x2000
    WP_ARM_PID = 0x4000
    VALID_ID_LIST = [WASATCH_VID, OCEAN_VID, ANDOR_VID, FT232_SPI_VID]
    STR_VALID_IDS = [hex(id)[2:] for id in VALID_ID_LIST]

    def __init__(self):
        self.system = platform.system()
        self.startup_scan = 0
        if self.system == "Windows":
            import win32com.client
            # see https://docs.microsoft.com/en-us/windows/win32/wmisdk/creating-a-wmi-script
            obj_WMI_service = win32com.client.GetObject("winmgmts:")
            # see https://docs.microsoft.com/en-us/windows/win32/wmisdk/querying-with-wql
            raw_wql = "SELECT * FROM __InstanceCreationEvent WITHIN 1 WHERE TargetInstance ISA \'Win32_PnPEntity\'"
            # see https://docs.microsoft.com/en-us/windows/win32/wmisdk/monitoring-events
            # while it removes polling the usb bus
            # the polling now shifts to WMI events as the query is a polling operation
            self.obj_events = obj_WMI_service.ExecNotificationQuery(raw_wql)
        elif self.system == "Linux":
            import pyudev
            self.context = pyudev.Context()
            self.monitor = pyudev.Monitor.from_netlink(self.context)
        else:
            pass

    ##
    # Iterates over each supported PID, searching for any Wasatch devices
    # with a known VID/PID and generating a list of DeviceIDs.
    #
    # Note that DeviceID internally pulls more attributes from the Device object.
    # def find_usb_devices_alternate_unused(self):
    #     vid = 0x24aa
    #     device_ids = []
    #     count = 0
    #     for pid in [0x1000, 0x2000, 0x4000]:
    #         # we could also remove iProduct and do one find on vid
    #         devices = usb.core.find(find_all=True, idVendor=vid, idProduct=pid)
    #         for device in devices:
    #             count += 1
    #             log.debug("DeviceListFID: discovered vid 0x%04x, pid 0x%04x (count %d)", vid, pid, count)
    #
    #             device_id = DeviceID(device=device)
    #             device_ids.append(device_id)
    #     return device_ids
    
    ##
    # Iterates over each USB bus, and each device on the bus, retaining any
    # with a Wasatch Photonics VID and supported PID and instantiating a
    # DeviceID for each.  
    #
    # Note that DeviceID internally pulls more attributes from the Device object.
    def bus_polling(self) -> list[DeviceID]:
        device_ids = []
        count = 0
        for device in usb.core.find(find_all=True, backend=libusb0.get_backend()):
            count += 1
            vid = int(device.idVendor)
            pid = int(device.idProduct)
            log.debug("DeviceListFID: discovered vid 0x%04x, pid 0x%04x (count %d)", vid, pid, count)

            if vid not in [self.WASATCH_VID, self.OCEAN_VID, self.ANDOR_VID, self.FT232_SPI_VID]:
                continue

            if vid == self.WASATCH_VID and pid not in [ self.WP_HAMA_SILICON_PID, self.WP_HAMA_INGAAS_PID, self.WP_ARM_PID ]:
                continue

            device_id = DeviceID(device=device)
            device_ids.append(device_id)
        return device_ids

    def linux_monitoring(self) -> list[DeviceID]:
        device_ids = []
        for device in iter(partial(self.monitor.poll, 0.001), None):
            # sometimes I see events with None for the vendor id, those should be skipped
            if device is not None and device.action == "add" and device.get('ID_VENDOR_ID') is not None:
                device_ids.append(device)
                log.debug(f"got a udev device add event")
        valid_devices = [dev for dev in device_ids if dev.get('ID_VENDOR_ID').lower() in self.STR_VALID_IDS]
        pyusb_devices = [usb.core.find(idVendor=int(dev.get('ID_VENDOR_ID'), 16), idProduct=int(dev.get('ID_MODEL_ID'), 16)) for dev in valid_devices]
        # if there is an error/can't find pyusb returns none
        # filter those out
        if None in pyusb_devices:
            log.error(f"pyudev notified of a matching device, but error when doing pyusb query")
            pyusb_devices = [dev for dev in pyusb_devices if dev is not None] 
        if pyusb_devices != []:
            log.debug(f"pyudev returned devices of {pyusb_devices}")
        return [DeviceID(device) for device in pyusb_devices]

    # I like this line but think it deserves a comment
    # map across the STR_VALID_IDS checking if it is in current id string, resulting in a bool array
    # if any true in that bool array then any() is true and thus it will be in the list
    def id_in_valid_ids(self, dev_id):
        valid_id_present = map(lambda valid: valid in dev_id, self.STR_VALID_IDS)
        return any(valid_id_present)

    def windows_monitoring(self) -> list[DeviceID]:
        device_ids = []
        try:
            # the next event raises an error on timeout
            # so if a connection has occured then add it to device ids
            # when we stop seeing devices then pass on to the processing
            while True:
                obj_received = self.obj_events.NextEvent(0.001)
                device_id = obj_received.Properties_("TargetInstance").Value.DeviceID
                device_ids.append(device_id.lower())
        except:
            pass
        if device_ids != []:
            log.debug(f"found a WMI event of {device_ids}")
        valid_ids = [dev_id for dev_id in device_ids if self.id_in_valid_ids(dev_id)] 
        pids = [re.findall(r'pid_(....)', dev) for dev in valid_ids]
        pids = [id_num[0] for id_num in pids if id_num is not []]
        vids = [re.findall(r'vid_(....)', dev) for dev in valid_ids]
        vids = [id_num[0] for id_num in vids if id_num is not []]
        # end by querying just the desired Wasatch Devices via pyusb
        # this provides an easy meshing with our current setup using pyusb devices
        pyusb_devices = [usb.core.find(idVendor=int(vid, 16), idProduct=int(pid, 16)) for vid, pid in zip(vids, pids)]
        # if there is an error/can't find pyusb returns none
        # filter those out
        if None in pyusb_devices:
            log.error(f"WMI notified of a matching device, but error when doing pyusb query")
            pyusb_devices = [dev for dev in pyusb_devices if dev is not None] 
        if pyusb_devices != []:
            log.debug(f"WMI returned devices of {pyusb_devices}")
        return [DeviceID(device) for device in pyusb_devices]

    def find_usb_devices(self):
        device_ids = []
        if self.startup_scan < 2:
            # our first few scans should always be a bus poll
            # this is because no events will be registered
            device_ids = self.bus_polling()
            self.startup_scan += 1
        elif self.system == "Windows":
            device_ids = self.windows_monitoring()
        elif self.system == "Linux":
            device_ids = self.linux_monitoring()
        else:
            device_ids = self.bus_polling()
        return device_ids

    def mac_monitoring(self):
        """
        The challenge of implementing Mac usb events was decided not to be worth it
        since it is such a small porition of users. I've included my code below the not implemented error
        for reference in case we decide to implement later. This is incomplete and shouldn't be called.
        """
        raise NotImplementedError

        from ctypes import cdll, util

        #iokit = cdll.LoadLibrary(util.find_library('IOKit'))

        # See ctypes for what these calls are doing
        # For where iokit info is coming from
        # Apple docs, which imo are bad with clear examples, if any, 
        # so I'm structuring this 
        # in a how to actually create and do way
        # IO notifications in python using ctypes
        # https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/Introduction/Introduction.html
        # AddMatchingNotification is our goal since that provides the iterator of devices
        # https://developer.apple.com/documentation/iokit/1514362-ioserviceaddmatchingnotification?language=objc
        # So our requirements are to create the following args
        # IONotificationPortRef, io_name_t, CFDictionaryRef, IOServiceMatchingCallback, io_iterator_t

        # For the IONotificationPortRef we call IONotificationPortCreate, which requires a mach_port_t
        # the primary port comes from a constant used in the dylib
        # https://developer.apple.com/documentation/iokit/1514480-ionotificationportcreate?language=objc
        kIOMasterPortDefault = c_void_p.in_dll(iokit, 'kIOMasterPortDefault')
        res = iokit.IONotificationPortCreate(kIOMasterPortDefault)

        # For the io_name_t that comes from a constant in the dll
        # See the parameter description in the AddMatchinServiceNotification link at the top
        # We want publish because it means whenever a connection occurs
        kIOPublishNotification = c_int.in_dll(iokit, 'kIOPublishNotification') # this throws an error that it doesn't have this symbol, idk why. It should export that

        # CFDictionary creation call info, along with args to pass, can be found here
        # https://developer.apple.com/documentation/corefoundation/1516791-cfdictionarycreatemutable?language=objc
        # the pyobjc docs weren't helpful and just say this call exists but not how to call it
        # we choose mutable when it could just be CFDictionaryCreate because it's easier to work with
        # after creation this can be interacted with like a python dict
        # the nonmutable requires passing c arrays for keys, values, which why bother when we can use python syntax
        # expected keys example can be seen in xml doc in 
        # Figure 4-2 I found more helpful since it is for USB
        # https://developer.apple.com/library/archive/documentation/DeviceDrivers/Conceptual/IOKitFundamentals/Matching/Matching.html
        # XML docs say every dict requires IOProviderClass
        # The value for the key can be found in the appendix of the driver docs
        matching_dict = CFDictionaryCreateMutable(None, 0,  kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks)
        matching_dict["IOProviderClass"] = "IOUSBDevice"
        matching_dict["idVendor"] = 0x24aa # we can have the driver pre filter for wasatch, so I'm utilizing that

