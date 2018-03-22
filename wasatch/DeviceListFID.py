import usb
import logging

log = logging.getLogger(__name__)

class DeviceListFID(object):
    def __init__(self):
        log.debug("init")

    def get_all_vid_pids(self):
        """ Return the full list of devices that match the vendor id. """
        VID = 0x24aa
        vid_pids = []
        for bus in usb.busses():
            for device in bus.devices:
                vid_pid = self.device_match(device, VID)
                if vid_pid:
                    vid_pids.append(vid_pid)
        return vid_pids

    def device_match(self, device, vid):
        """ Match vendor id and reject all non-feature identification devices. """
        if device.idVendor != vid:
            return None

        # only accept FID PID
        if device.idProduct != 0x1000 and \
           device.idProduct != 0x2000 and \
           device.idProduct != 0x3000 and \
           device.idProduct != 0x4000:
               return None

        vid_pid = (hex(device.idVendor), hex(device.idProduct))
        return vid_pid
