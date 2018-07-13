import usb
import logging

log = logging.getLogger(__name__)

##
# Encapsulates (sort of) the set of PIDs associated with StrokerProtocol
# spectrometers.
class DeviceListSP(object):
    def __init__(self):
        pass

    ## Return the list of connected StrokerProtocol devices
    def get_all_vid_pids(self):
        VID = 0x24aa
        vid_pids = []
        for bus in usb.busses():
            for device in bus.devices:
                vid_pid = self.device_match(device, VID)
                if vid_pid:
                    vid_pids.append(vid_pid)

        return vid_pids

    ## if the given device is SP, return a (0xAAAA, 0xBBBB) tuple
    def device_match(self, device, vid=0x24aa):
        if device.idVendor != vid:
            return None

        # reject FID devices
        if device.idProduct in [ 0x1000, 0x2000, 0x3000, 0x4000 ]:
            return None

        vid_pid = (hex(device.idVendor), hex(device.idProduct))
        return vid_pid
