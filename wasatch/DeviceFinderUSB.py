import re
import logging
import platform
from functools import partial
if platform.system() == 'Darwin':
    from ctypes import *
    from CoreFoundation import *
import usb
import usb.backend.libusb0 as libusb0
log = logging.getLogger(__name__)
from .DeviceID import DeviceID


class DeviceFinderUSB(object):
    WASATCH_VID = 9386
    OCEAN_VID = 9303
    ANDOR_VID = 4974
    FT232_SPI_VID = 1027
    WP_HAMA_SILICON_PID = 4096
    WP_HAMA_INGAAS_PID = 8192
    WP_ARM_PID = 16384
    VALID_ID_LIST = [WASATCH_VID, OCEAN_VID, ANDOR_VID, FT232_SPI_VID]
    STR_VALID_IDS = [hex(id)[2:] for id in VALID_ID_LIST]
    USE_MONITORING = True
    MIN_POLLING_SCANS = 10

    def __init__(self):
        self.system = platform.system()
        self.startup_scan = 0
        if self.system == 'Windows':
            import win32com.client
            obj_WMI_service = win32com.client.GetObject('winmgmts:')
            raw_wql = (
                "SELECT * FROM __InstanceCreationEvent WITHIN 1 WHERE TargetInstance ISA 'Win32_PnPEntity'"
                )
            self.obj_events = obj_WMI_service.ExecNotificationQuery(raw_wql)
        elif self.system == 'Linux':
            import pyudev
            self.context = pyudev.Context()
            self.monitor = pyudev.Monitor.from_netlink(self.context)
        else:
            pass

    def bus_polling(self):
        device_ids = []
        count = 0
        for device in usb.core.find(find_all=True, backend=libusb0.
            get_backend()):
            count += 1
            vid = int(device.idVendor)
            pid = int(device.idProduct)
            log.debug(
                'DeviceListFID: discovered vid 0x%04x, pid 0x%04x (count %d), address %s'
                , vid, pid, count, device.address)
            if vid not in [self.WASATCH_VID, self.OCEAN_VID, self.ANDOR_VID,
                self.FT232_SPI_VID]:
                continue
            if vid == self.WASATCH_VID and pid not in [self.
                WP_HAMA_SILICON_PID, self.WP_HAMA_INGAAS_PID, self.WP_ARM_PID]:
                continue
            device_id = DeviceID(device=device)
            device_ids.append(device_id)
        return device_ids

    def linux_monitoring(self):
        device_ids = []
        for device in iter(partial(self.monitor.poll, 0.001), None):
            if device is not None and device.action == 'add' and device.get(
                'ID_VENDOR_ID') is not None:
                device_ids.append(device)
                log.debug(f'got a udev device add event')
        valid_devices = [dev for dev in device_ids if dev.get(
            'ID_VENDOR_ID').lower() in self.STR_VALID_IDS]
        pyusb_devices = [usb.core.find(idVendor=int(dev.get('ID_VENDOR_ID'),
            16), idProduct=int(dev.get('ID_MODEL_ID'), 16)) for dev in
            valid_devices]
        if None in pyusb_devices:
            log.error(
                f'pyudev notified of a matching device, but error when doing pyusb query'
                )
            pyusb_devices = [dev for dev in pyusb_devices if dev is not None]
        if pyusb_devices != []:
            log.debug(f'pyudev returned devices of {pyusb_devices}')
        return [DeviceID(device) for device in pyusb_devices]

    def id_in_valid_ids(self, dev_id):
        valid_id_present = map(lambda valid: valid in dev_id, self.
            STR_VALID_IDS)
        return any(valid_id_present)

    def windows_monitoring(self):
        device_ids = []
        try:
            while True:
                obj_received = self.obj_events.NextEvent(0.001)
                device_id = obj_received.Properties_('TargetInstance'
                    ).Value.DeviceID
                device_ids.append(device_id.lower())
        except:
            pass
        if device_ids != []:
            log.debug(f'found a WMI event of {device_ids}')
        valid_ids = [dev_id for dev_id in device_ids if self.
            id_in_valid_ids(dev_id)]
        pids = [re.findall('pid_(....)', dev) for dev in valid_ids]
        pids = [id_num[0] for id_num in pids if id_num is not []]
        vids = [re.findall('vid_(....)', dev) for dev in valid_ids]
        vids = [id_num[0] for id_num in vids if id_num is not []]
        pyusb_devices = [usb.core.find(idVendor=int(vid, 16), idProduct=int
            (pid, 16), backend=libusb0.get_backend()) for vid, pid in zip(
            vids, pids)]
        if None in pyusb_devices:
            log.error(
                f'WMI notified of a matching device, but error when doing pyusb query'
                )
            pyusb_devices = [dev for dev in pyusb_devices if dev is not None]
        if pyusb_devices != []:
            log.debug(f'WMI returned devices of {pyusb_devices}')
        return [DeviceID(device) for device in pyusb_devices]

    def find_usb_devices(self, poll=False):
        log.debug('DeviceFinderUSB.find_usb_devices: starting')
        device_ids = []
        if (self.startup_scan < self.MIN_POLLING_SCANS or not self.
            USE_MONITORING or poll):
            log.debug(
                f'DeviceFinderUSB.find_usb_devices: just doing a bus poll for startup_scan {self.startup_scan}'
                )
            device_ids = self.bus_polling()
            self.startup_scan += 1
        elif self.system == 'Windows':
            device_ids = self.windows_monitoring()
        elif self.system == 'Linux':
            device_ids = self.linux_monitoring()
        else:
            device_ids = self.bus_polling()
        log.debug(
            f'DeviceFinderUSB.find_usb_devices: returning {len(device_ids)} devices'
            )
        return device_ids

    def mac_monitoring(self):
        """

        The challenge of implementing Mac usb events was decided not to be worth it

        since it is such a small porition of users. I've included my code below the not implemented error

        for reference in case we decide to implement later. This is incomplete and shouldn't be called.

        """
        raise NotImplementedError
        from ctypes import cdll, util
        kIOMasterPortDefault = c_void_p.in_dll(iokit, 'kIOMasterPortDefault')
        res = iokit.IONotificationPortCreate(kIOMasterPortDefault)
        kIOPublishNotification = c_int.in_dll(iokit, 'kIOPublishNotification')
        matching_dict = CFDictionaryCreateMutable(None, 0,
            kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks)
        matching_dict['IOProviderClass'] = 'IOUSBDevice'
        matching_dict['idVendor'] = 9386
