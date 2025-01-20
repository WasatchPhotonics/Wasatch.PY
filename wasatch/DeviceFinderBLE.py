import logging
import asyncio

from datetime import datetime
from bleak import BleakScanner

from wasatch.BLEDevice import BLEDevice
from wasatch.DeviceID  import DeviceID

log = logging.getLogger(__name__)

class DiscoveredBLEDevice:
    def __init__(self, device_id, rssi):
        self.device_id = device_id
        self.rssi = rssi

class DeviceFinderBLE:
    """ @see consolidated BLE docs in wasatch.BLEDevice """

    def __init__(self, discovery_queue=None, discovery_callback=None, search_timeout_sec=30):

        self.discovery_queue = discovery_queue
        self.discovery_callback = discovery_callback
        self.search_timeout_sec = search_timeout_sec

        self.keep_scanning = True
        self.stop_scanning_event = asyncio.Event()

    async def search_for_devices(self):
        log.debug("search_for_devices: start")
        self.start_time = datetime.now()

        # for some reason asyncio.timeout() isn't in my Python 3.10.15, so kludging
        async def cancel_task(sec):
            await asyncio.sleep(sec)
            self.stop_scanning()

        try:
            log.debug(f"search_for_devices: scheduling cancel_task in {self.search_timeout_sec}sec")
            task = asyncio.create_task(cancel_task(self.search_timeout_sec))

            cb = self.detection_callback
            uuid = BLEDevice.WASATCH_SERVICE
            log.debug(f"search_for_devices: instantiating BleakScanner with callback {cb}, uuid {uuid}")
            async with BleakScanner(detection_callback=self.detection_callback, service_uuids=[BLEDevice.WASATCH_SERVICE]) as scanner:
                log.debug(f"search_for_devices: awaiting stop_scanning_event")
                await self.stop_scanning_event.wait()
        except:
            log.error("exception while scanning for devices", exc_info=1)

        # scanner stops when block exits
        log.debug("search_for_devices: done")

    def detection_callback(self, bleak_ble_device, advertisement_data):
        log.debug("detection_callback: start")
        if not self.keep_scanning:
            log.debug("detection_callback: no longer scanning...done")
            return

        elapsed_sec = (datetime.now() - self.start_time).total_seconds()
        if elapsed_sec >= self.search_timeout_sec:
            log.debug(f"detection_callback: timeout expired ({elapsed_sec}sec > {self.search_timeout_sec})...done")
            self.stop_scanning()
            return
        
        log.debug(f"detection_callback: discovered device {bleak_ble_device}, advertisement_data {advertisement_data}")
        if not self.is_xs(bleak_ble_device):
            log.debug(f"detection_callback: not XS...ignoring")
            return

        serial_number = advertisement_data.local_name
        rssi = advertisement_data.rssi
        device_id = DeviceID(label=f"BLE:{serial_number}", bleak_ble_device=bleak_ble_device)

        discovered_device = DiscoveredBLEDevice(
            device_id = device_id, 
            rssi = rssi)
        log.debug(f"detection_callback: instantiated {discovered_device}")

        if self.discovery_queue:
            log.debug(f"detection_callback: returning DiscoveredBLEDevice via queue")
            self.discovery_queue.put_nowait(discovered_device)

        if self.discovery_callback:
            log.debug(f"detection_callback: returning DiscoveredBLEDevice via callback")
            self.discovery_callback(discovered_device)

        if not (self.discovery_queue or self.discovery_callback):
            log.error("have neither queue nor callback to return DeviceID...?")

        log.debug("detection_callback: done")

    def stop_scanning(self):
        log.debug("stop_scanning")
        self.keep_scanning = False
        self.stop_scanning_event.set()
        
        # send a "poison-pill" upstream to notify caller that we're no longer scanning
        self.discovery_queue.put_nowait(None)

    def is_xs(self, device, advertisement_data=None):
        if device is None:
            return
        elif advertisement_data:
            return BLEDevice.WASATCH_SERVICE.lower() in advertisement_data.service_uuids.lower()
        else:
            return "WP-" in device.name.upper()
