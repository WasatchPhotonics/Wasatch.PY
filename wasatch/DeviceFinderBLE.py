import logging
import asyncio
import re

from time import sleep
from datetime import datetime
from bleak import BleakScanner, BleakClient

from wasatch.BLEDevice import BLEDevice
from wasatch.DeviceID  import DeviceID

log = logging.getLogger(__name__)

class DiscoveredBLEDevice:
    """
    DeviceFinderBLE pushes these through a Queue back to BLEManager. BLEManager
    uses a Queue so it can be read from a tickable QTimer and update GUI elements
    as it reads them.

    The local_name and rssi are display-only (although local_name is used in 
    constructing the DeviceID). The bleak_ble_device will be pass

    """
    def __init__(self, device_id, local_name, rssi, bleak_ble_device):
        self.device_id = device_id
        self.local_name = local_name
        self.rssi = rssi

class DeviceFinderBLE:

    def __init__(self, discovery_queue=None, discovery_callback=None, search_timeout_sec=30):

        self.discovery_queue = discovery_queue
        self.discovery_callback = discovery_callback
        self.search_timeout_sec = search_timeout_sec

        self.client = None          # instantiated BleakClient
        self.keep_scanning = True
        self.stop_scanning_event = asyncio.Event()

    ############################################################################
    # BLE Connection
    ############################################################################

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

    async def connect(self, client):
        log.debug("connect: stopping scanner")
        self.stop_scanning()

        log.debug("==> Device:")
        for attr in ['name', 'address', 'details']:
            if hasattr(device, attr):
                value = getattr(device, attr)
                log.debug(f"  {attr} = {value}")
        log.debug("==> Advertising Data:")
        for attr in ['local_name', 'manufacturer_data', 'platform_data', 'rssi', 'service_data', 'service_uuids', 'tx_power']:
            if hasattr(advertisement_data, attr):
                value = getattr(advertisement_data, attr)
                log.debug(f"  {attr} = {value}")


        log.debug("connect: instantiating BleakClient")
        self.client = BleakClient(address_or_ble_device=device, 
                                  disconnected_callback=self.disconnected_callback,
                                  timeout=self.search_timeout_sec)
        log.debug(f"connect: BleakClient instantiated: {self.client}")

        # connect 
        log.debug(f"connect: calling BleakClient.connect")
        await self.client.connect()

        # grab device information
        log.debug(f"connect: loading device information")
        await self.load_device_information()

        # get Characteristic information
        log.debug(f"connect: loading characteristics")
        await self.load_characteristics()

        elapsed_sec = (datetime.now() - self.start_time).total_seconds()
        log.debug(f"connect: initial connection took {elapsed_sec:.2f} sec")

    def detection_callback(self, bleak_ble_device, advertisement_data):
        """
        discovered device 13874014-5EDA-5E6B-220E-605D00FE86DF: WP-01791, 
        advertisement_data AdvertisementData(local_name='WP-01791', 
                                             service_uuids=['0000ff00-0000-1000-8000-00805f9b34fb', 'd1a7ff00-af78-4449-a34f-4da1afaf51bc'], 
                                             tx_power=0, rssi=-67)
        
        Pushes DiscoveredDevice objects onto discovery_queue.
        """
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

        device_id = DeviceID(label=f"BLE:{advertisement_data.local_name}", bleak_ble_device=bleak_ble_device)
        log.debug(f"detection_callback: instantiated {device_id}")

        discovered_device = DiscoveredDevice(
            device_id = device_id, 
            local_name = advertisement_data.local_name, 
            rssi = advertisement_data.rssi)
        log.debug(f"detection_callback: instantiated {discovered_device}")

        returned = False
        if self.discovery_queue:
            log.debug(f"detection_callback: returning DiscoveredDevice via queue")
            self.discovery_queue.put_nowait(discovered_device)
            returned = True

        if self.discovery_callback:
            log.debug(f"detection_callback: returning DiscoveredDevice via callback")
            self.discovery_callback(discovered_device)
            returned = True

        if not returned:
            log.error("have neither queue nor callback to return DeviceID...?")

        log.debug("detection_callback: done")

    def stop_scanning(self):
        self.keep_scanning = False
        self.stop_scanning_event.set()
        
        # send a "poison-pill" upstream to notify caller that we're no longer scanning
        self.device_id_queue.put_nowait(None)

    ############################################################################
    # Utility
    ############################################################################

    def is_xs(self, device, advertisement_data=None):
        if device is None:
            return
        elif advertisement_data:
            return BLEDevice.WASATCH_SERVICE.lower() in advertisement_data.service_uuids.lower()
        else:
            return "wp-" in device.name.lower()
