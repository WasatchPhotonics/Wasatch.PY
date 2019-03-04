import usb
import logging

log = logging.getLogger(__name__)

from WasatchDeviceIDUSB import WasatchDeviceIDUSB

##
# Generates a list of WasatchDeviceIDUSB for all connected USB Wasatch Photonics spectrometers.
class DeviceListFID(object):

    ##
    # @see https://github.com/pyusb/pyusb/blob/master/docs/tutorial.rst#user-content-dealing-with-multiple-identical-devices
    def __init__(self):
        device_ids = []
        for bus in usb.busses():
            for device in bus.devices:
                vid = device.idVendor
                pid = device.idProduct

                dev_bus = device.bus
                dev_address = device.address

                log.debug("DeviceListFID: discovered vid 0x%04x, pid 0x%04x, bus %d, address %d", vid, pid, dev_bus, dev_address)

                if vid != 0x24aa:
                    continue

                if pid not in [ 0x1000, 0x2000, 0x4000 ]:
                    continue

                device_id = WasatchDeviceIDUSB(device)
                device_ids.append(device_id)
        return device_ids
