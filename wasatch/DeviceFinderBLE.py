import logging
import asyncio
import re

from time import sleep
from datetime import datetime
from bleak import BleakScanner, BleakClient

from wasatch import BLEDevice       # for WASATCH_SERVICE

log = logging.getLogger(__name__)

class DeviceFinderBLE:

    def __init__(self, spectrometer_detected_callback=None, search_timeout_sec=30):
        self.search_timeout_sec = search_timeout_sec
        self.spectrometer_detected_callback = spectrometer_detected_callback

        # scanning
        self.client = None                                  # instantiated BleakClient
        self.keep_scanning = True
        self.stop_scanning_event = asyncio.Event()

        self.notifications = set()                          # all Characteristics to which we're subscribed for notifications

    ############################################################################
    # BLE Connection
    ############################################################################

    async def search_for_devices(self):
        self.start_time = datetime.now()

        # for some reason asyncio.timeout() isn't in my Python 3.10.15, so kludging
        async def cancel_task(sec):
            await asyncio.sleep(sec)
            self.stop_scanning()
        task = asyncio.create_task(cancel_task(self.args.search_timeout_sec))

        log.debug(f"rssi local_name")
        async with BleakScanner(detection_callback=self.detection_callback, service_uuids=[BLEDevice.WASATCH_SERVICE]) as scanner:
            await self.stop_scanning_event.wait()

        # scanner stops when block exits
        log.debug("scanner stopped")

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
        discovered device 13874014-5EDA-5E6B-220E-605D00FE86DF: WP-SiG:WP-01791, 
        advertisement_data AdvertisementData(local_name='WP-SiG:WP-01791', 
                                             service_uuids=['0000ff00-0000-1000-8000-00805f9b34fb', 'd1a7ff00-af78-4449-a34f-4da1afaf51bc'], 
                                             tx_power=0, rssi=-67)
        """
        if not self.keep_scanning:
            return

        if (datetime.now() - self.start_time).total_seconds() >= self.search_timeout_sec:
            log.debug("timeout expired")
            self.stop_scanning()
            return

        log.debug(f"discovered device {device}, advertisement_data {advertisement_data}")
        if not self.is_xs(device):
            return

        log.debug(f"{advertisement_data.rssi:4d} {advertisement_data.local_name}")
        if self.spectrometer_detected_callback:
            log.debug(f"passing device, advertisement_data back to caller {self.spectrometer_detected_callback}")
            self.spectrometer_detected_callback(device, advertisement_data)

    def stop_scanning(self):
        self.keep_scanning = False
        self.stop_scanning_event.set()

    ############################################################################
    # Utility
    ############################################################################

    def is_xs(self, device, advertisement_data=None):
        if device is None:
            return
        elif advertisement_data is not None:
            return BLEDevice.WASATCH_SERVICE.lower() in advertisement_data.service_uuids
        else:
            return "wp-" in device.name.lower()
