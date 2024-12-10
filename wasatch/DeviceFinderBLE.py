import logging
import asyncio
import re

from time import sleep
from datetime import datetime
from bleak import BleakScanner, BleakClient

from wasatch.BLEDevice import BLEDevice
from wasatch.DeviceID  import DeviceID

log = logging.getLogger(__name__)

class DeviceFinderBLE:

    def __init__(self, device_id_queue=None, callback=None, search_timeout_sec=30):

        self.device_id_queue = device_id_queue
        self.search_timeout_sec = search_timeout_sec
        self.callback = callback

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

    def detection_callback(self, device, advertisement_data):
        """
        discovered device 13874014-5EDA-5E6B-220E-605D00FE86DF: WP-01791, 
        advertisement_data AdvertisementData(local_name='WP-01791', 
                                             service_uuids=['0000ff00-0000-1000-8000-00805f9b34fb', 'd1a7ff00-af78-4449-a34f-4da1afaf51bc'], 
                                             tx_power=0, rssi=-67)
        
        Pushes DeviceID objects onto device_id_queue.
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
        
        log.debug(f"detection_callback: discovered device {device}, advertisement_data {advertisement_data}")
        if not self.is_xs(device):
            log.debug(f"detection_callback: not XS...ignoring")
            return

        device_id = DeviceID(label=f"BLE:{advertisement_data.local_name}", rssi=advertisement_data.rssi)
        log.debug(f"detection_callback: instantiated {device_id}")

        returned = False
        if self.device_id_queue:
            log.debug(f"detection_callback: returning DeviceID via queue {self.device_id_queue}")
            self.device_id_queue.put_nowait(device_id)
            returned = True

        if self.callback:
            log.debug(f"detection_callback: returning DeviceID via callback {self.callback}")
            self.callback(device_id)
            returned = True

        if not returned:
            log.error("have neither queue nor callback to return DeviceID...?")

        log.debug("detection_callback: done")

    def stop_scanning(self):
        self.keep_scanning = False
        self.stop_scanning_event.set()

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
