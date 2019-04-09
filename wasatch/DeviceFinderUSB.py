import logging
import usb

log = logging.getLogger(__name__)

from DeviceID import DeviceID

##
# Generates a list of DeviceID objects for all connected USB Wasatch Photonics 
# spectrometers.
class DeviceFinderUSB(object):

    def __init__(self):
        pass

    ##
    # Iterates over each USB bus, and each device on the bus, retaining any
    # with a Wasatch Photonics VID and supported PID and instantiating a
    # DeviceID for each.  
    #
    # Note that DeviceID internally pulls more attributes from the Device object.
    def find_usb_devices(self):
        device_ids = []
        for bus in usb.busses():
            for device in bus.devices:
                vid = int(device.idVendor)
                pid = int(device.idProduct)
                log.debug("DeviceListFID: discovered vid 0x%04x, pid 0x%04x", vid, pid)

                if vid != 0x24aa:
                    continue

                if pid not in [ 0x1000, 0x2000, 0x4000 ]:
                    continue

                device_id = DeviceID(device=device)
                device_ids.append(device_id)
        return device_ids
