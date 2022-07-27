import re
import logging
import platform

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

    def __init__(self):
        self.system = platform.system()
        self.startup_scan = 0
        if self.system == "Windows":
            import win32com.client
            # see https://docs.microsoft.com/en-us/windows/win32/wmisdk/creating-a-wmi-script
            obj_WMI_service = win32com.client.GetObject("winmgmts:")
            # see https://docs.microsoft.com/en-us/windows/win32/wmisdk/querying-with-wql
            raw_wql = "SELECT * FROM __InstanceCreationEvent WITHIN 0.1 WHERE TargetInstance ISA \'Win32_PnPEntity\'"
            # see https://docs.microsoft.com/en-us/windows/win32/wmisdk/monitoring-events
            # while it removes polling the usb bus
            # the polling now shifts to WMI events as the query is a polling operation
            self.obj_events = obj_WMI_service.ExecNotificationQuery(raw_wql)
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

    def windows_monitoring(self) -> bool:
        log.debug(f"scaning for WMI events")
        device_ids = []
        try:
            # the next event raises an error on timeout
            # so if a connection has occured then add it to device ids
            # when we stop seeing devices then pass on to the processing
            while True:
                obj_received = self.obj_events.NextEvent(1)
                device_id = obj_received.Properties_("TargetInstance").Value.DeviceID
                device_ids.append(device_id)
        except:
            pass
        device_ids = list(filter(lambda dev_id: "24aa" in dev_id.lower(), device_ids))
        pids = [re.findall(r'PID_(....)', dev) for dev in device_ids]
        pids = [id_num[0] for id_num in pids if id_num is not []]
        # end by querying just the desired Wasatch Devices via pyusb
        # this provides an easy meshing with our current setup using pyusb devices
        device_ids = [usb.core.find(idVendor=0x24aa, idProduct=int(pid, 16)) for pid in pids]
        return [DeviceID(device) for device in device_ids]

    def find_usb_devices(self):
        device_ids = []
        if self.startup_scan < 2:
            # our first few scans should always be a bus poll
            # this is because no events will be registered
            device_ids = self.bus_polling()
            self.startup_scan += 1
        elif self.system == "Windows":
            device_ids = self.windows_monitoring()
        else:
            device_ids = self.bus_polling()
        return device_ids
