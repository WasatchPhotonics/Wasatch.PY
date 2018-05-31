import usb
import logging

log = logging.getLogger(__name__)

class DeviceListSP(object):
    """ Create a list of vendor id, product id pairs of any device on the bus 
        with the 0x24AA VID. Explicitly reject the newer feature identification 
        devices. """
    def __init__(self):
        pass

    def get_all_vid_pids(self):
        """ Return the full list of devices that match the vendor id. Explicitly 
            reject the feature identification codes. """

        VID = 0x24aa
        vid_pids = []
        for bus in usb.busses():
            for device in bus.devices:
                vid_pid = self.device_match(device, VID)
                if vid_pid:
                    vid_pids.append(vid_pid)

        return vid_pids

    def device_match(self, device, vid):
        """ Match vendor id and rejectable feature identification devices. """
        if device.idVendor != vid:
            return None

        # reject any FID PID
        if device.idProduct == 0x1000 or \
           device.idProduct == 0x2000 or \
           device.idProduct == 0x3000 or \
           device.idProduct == 0x4000:
               return None

        hex_pid = "0x%04x" % device.idProduct
        vid_pid = (hex(device.idVendor), hex_pid)
        return vid_pid
