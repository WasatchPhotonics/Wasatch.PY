import os
import asyncio
import logging

from bleak import BleakClient
from datetime import datetime
from functools import partial
from threading import Thread

from wasatch.EEPROM                   import EEPROM
from wasatch.Reading                  import Reading
from wasatch.ControlObject            import ControlObject
from wasatch.StatusMessage            import StatusMessage
from wasatch.InterfaceDevice          import InterfaceDevice
from wasatch.SpectrometerRequest      import SpectrometerRequest
from wasatch.SpectrometerSettings     import SpectrometerSettings
from wasatch.SpectrometerResponse     import SpectrometerResponse, ErrorLevel
from wasatch.USBCPowerConnectionState import USBCPowerConnectionState

from . import utils

log = logging.getLogger(__name__)

"""
This file contains all classes used in communicating with the Wasatch Photonics 
XS spectrometer over Bluetooth® LE (BLE).

These three classes are defined:

- BLEDevice
- Generics
- Generic

The BLEDevice class provides similar functionality for BLE spectrometers as the 
WasatchDevice + FeatureIdentificationDevice classes do for USB spectrometers.

This is a rough Entity Relationship diagram showing how these classes are used 
from ENLIGHTEN™:
                         ____________
                        |_Controller_|
     ____________           |   |
    |_BLEManager_|----------+   |
          |                     |                     ENLIGHTEN repo
   - - - -|- - - - - - - - - - -|- - - - - - - - - - - - - - - - - -
   _______|_________    ________|_____________       Wasatch.PY repo
  |_DeviceFinderBLE_|  |_WasatchDeviceWrapper_|
                                |                    Main Thread
       = = = = = = = = = = = = =|= = = = = = = = = = = = = = = =
    _________________     ______|________    Spectrometer Thread
   |_InterfaceDevice_|   |_WrapperWorker_|
                /_\             |     
                 |         _____|_____         
 WasatchDevice <-+------->|_BLEDevice_|
   AndorDevice <-+              |
     SPIDevice <-+         _____|____       _________  
     TCPDevice <-+        |_Generics_|<>---|_Generic_|
     IDSDevice <-+        
           etc           

Naratively, ENLIGHTEN's Controller has a BLEManager class to manage BLE affairs 
at the GUI level, including connection and search buttons, visual tables of 
discovered devices etc.

ENLIGHTEN's BLEManager in turn uses Wasatch.PY's DeviceFinderBLE to scan for and
discover BLE ranges in range, returning their labels (serial numbers) and RSSI
signal strengths. If the user selects a serial number, the BLEManager passes it
back to the Controller which hands it to a new WasatchDeviceWrapper (one per
Spectrometer) to instantiate and control.

WasatchDeviceWrapper does this by spawning a WrapperWorker in a child thread.
The BLEDevice (like any InterfaceDevice) will run in the child thread, one per
Spectrometer. Within the new thread, WrapperWorker instantiates the requested
InterfaceDevice type, WasatchDevice for USB, BLEDevice for Bluetooth, etc.

BLEDevice is responsible for pairing and controlling one Bluetooth spectrometer.
Like any BLE product, the device is accessed through a Service comprising several
Characteristics. 

Naively, each Characteristic might be expected to map to a particular 
spectrometer feature, such as IntegrationTime, GainDB, LaserEnable etc. However,
each Characteristic has a memory footprint, and the Ezurio BL652 module only has
room for a sparse handful.

Therefore, we compromised on a minimal set of Characteristics where only the most
performance-critical or problematic spectrometer features received their own
Characteristic, and every other feature gets rolled into a slower "Generic" 
Characteristic which can handle arbitrary low-rate data exchanges.

These are the current Characteristics:

- LASER_STATE: gets its own characteristic so nothing can slow or confuse the 
  setting or reading of laser configuration. Exchanges a marshalled record 
  including enable state, interlock state, power level etc.

- BATTERY_STATE: again gets its own characteristic because battery management
  is a surprisingly complicated yet critical aspect of handheld system design.

- ACQUIRE and SPECTRA: currently used to "request" measurements with the ACQUIRE
  characteristic, and to receive the responses via SPECTRA. This was done to 
  separate the performance-critical spectral bulk data response (which requires
  multiple exchanges to complete) from higher-level measurement "status" 
  responses (returned over ACQUIRE).

- GENERIC: everything else (EEPROM, integration time, gain, ROI, TEC,  etc)

# Concurrency

Legacy ENLIGHTEN WrapperWorker.run <--> wasatch.InterfaceDevice communication
normally occurs within a threading.Thread, using what we would now call 
"traditional" (synchronous) Python function calls. That works fine for USB 
and even TCP/IP devices, as both pyusb.ctrl_transfer and socket.recv/send 
expose typical "blocking" procedural APIs. (Python didn't even get an async
keyword until 2015.)

However, BLE is all about asynchronous communication, and the Bleak package
only offers an async API. Therefore we need to wrap calls to the Bleak 
objects using asyncio, so we can call async functions from synchronous code.

With regard to BLEManager <--> DeviceFinderBLE, BLEManager creates the 
asyncio scan_loop, allowing DeviceFinderBLE to expose an asynchronous API 
(public async methods).

That begs the question of how we should handle WrapperWorker <--> 
wasatch.BLEDevice communication. Should wasatch.BLEDevice likewise expose
an asynchronous API, similar to what DeviceFinderBLE does, and let 
WrapperWorker manage the asyncio run-loop?

After reflection, I think not. Right now we have a reasonably robust 
WrapperWorker which is successfully talking to WP USB (WasatchDevice),
Andor USB (AndorDevice), TCP/IP (TCPDevice), and historically even SPI 
(SPIDevice) and 3rd-party (OceanDevice) products. I don't want to "screw" 
with WrapperWorker to make it friendly for BLE, and maybe break something
else.

So WrapperWorker is going to stay "synchronous", and we will let 
wasatch.BLEDevice manage its own asyncio.run_loop for turning WrapperWorker 
requests into await-able async calls.

I am unclear whether it is safe / recommended to have multiple asyncio 
run_loops in a single application (especially when that application is built
atop PySide6, which has run-loops of its own). I don't know if this helps or
hurts, but currently I'm making the BLEDevice run-loop a public singleton so
it can be shared across BLEDevice instances, as well as with BLEManager.

# nomenclature

For consistency with other InterfaceDevice classes, getters and setters
are assumed to be synchronous unless the method name explicitly ends in 
"_async".

Going a step further, all async methods in this file explicitly end in
"_async" for consistency. Note that Bleak library methods do not follow
this convention.

# wasatch.DeviceID

The bleak.BLEDevice is attached to the wasatch.DeviceID because we have 
existing infrastructure in enlighten.Controller to maintain a set of 
.other_device_ids (DeviceID objects) it can check to connect to "non-self-
discovery" spectrometers. Currently those include WISP TCP/IP spectrometers
and BLE spectrometers, both of which require some level of user-interaction
to detect/find. 

Controller then passes the wasatch.DeviceID into wasatch.WasatchDeviceWrapper
(and wasatch.WrapperWorker) to properly conenct to the requested device. 

By way of comparison, WISP needs IP addr:port, so those go into DeviceID;
in like manner BLE needs bleak.BLEDevice, hence the extra attributes in 
DeviceID.

# BLE Discovery

In wasatch.DeviceFinderBLE, bleak.BleakScanner issues a series of 
notifications including (bleak.backends.device.BLEDevice, 
bleak.backends.scanner.AdvertisementData).

AdvertisementData contains these useful attributes:
    - local_name            <-- WP-12345
    - rssi                  <-- signal strength
    - manufacturer_data
    - platform_data
    - service_data
    - service_uuids
    - tx_power

DeviceFinderBLE generates a wasatch.DeviceID with both displayable
(serial_number and rssi) and hidden (bleak_ble_device) attributes.

To generate a bleak.BleakClient, we need the bleak.backends.device.BLEDevice
from BleakScanner. Therefore, that handle needs to be be available in 
wasatch.BLEDevice. The easiest way to get it there is for 
wasatch.DeviceFinderBLE to embed it in the wasatch.DeviceID it writes to the
wasatch.DiscoveredBLEDevice that it sends back to enlighten.BLEManager, so
BLEManager can write the selected DeviceID to Controller.other_device_ids.

# Issues

Bleak Notifications don't seem to work on Windows under Parallels (using a 
MacOS host). They work fine from a "real" Dell laptop, so this is probably
a Parallels bug.

# TODO

- consider registering callback with StatusIndicators, BatteryFeature, 
  LaserControlFeature etc for BATTERY_STATE and LASER_STATE notifications
- add HardwareStripCharts for all the XS bits, like AmbientTemperature, 
  LaserChargerTemperature, MicrocontrollerTemperature etc

# Misc

@todo consider https://github.com/django/asgiref#function-wrappers
"""

class BLEDevice(InterfaceDevice):

    ############################################################################
    # static attributes
    ############################################################################

    static_run_loop = None
    static_thread = None

    ############################################################################
    # static methods
    ############################################################################

    @staticmethod
    def get_run_loop():

        def make_run_loop():
            asyncio.set_event_loop(BLEDevice.static_run_loop)
            BLEDevice.static_run_loop.run_forever()

        if BLEDevice.static_run_loop is None:
            BLEDevice.static_run_loop = asyncio.new_event_loop()
            static_thread = Thread(target=make_run_loop, daemon=True)
            static_thread.start()

        return BLEDevice.static_run_loop

    ############################################################################
    # constants
    ############################################################################

    WASATCH_SERVICE   = "D1A7FF00-AF78-4449-A34F-4DA1AFAF51BC"
   #DISCOVERY_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"

    CONNECT_TIMEOUT_SEC = 10

    MAX_EEPROM_PAGES = 8 # separate from EEPROMFields, as XS BLE FW may not be in sync

    STATUS_UPDATE_PERIOD_SEC = 60 # update Ambient temperature etc via acquire_data

    LASER_TEC_MODES = {
        0: 'OFF', 
        1: 'ON', 
        2: 'AUTO', 
        3: 'AUTO_ON'
    }

    ACQUIRE_STATUS_CODES = {
         0: ("NAK",                         "No error, the spectrum just isn't ready yet"),
         1: ("ERR_BATT_SOC_INFO_NOT_RCVD",  "Can't read battery, and therefore can't take Auto-Dark or Auto-Raman spectra"),
         2: ("ERR_BATT_SOC_TOO_LOW",        "Battery is too low to take Auto-Dark or Auto-Raman spectra"),
         3: ("ERR_LASER_DIS_FLR",           "Failure disabling the laser"),
         4: ("ERR_LASER_ENA_FLR",           "Failure enabling the laser"),
         5: ("ERR_IMG_SNSR_IN_BAD_STATE",   "The sensor is not able to take spectra"),
         6: ("ERR_IMG_SNSR_STATE_TRANS_FLR","The sensor failed to apply acquisition parameters"),
         7: ("ERR_SPEC_ACQ_SIG_WAIT_TMO",   "The sensor failed to take a spectrum (timeout exceeded)"),
         8: ("ERR_INTERLOCK_OPEN",          "Can't perform Raman measurement because interlock open"),
         9: ("ERR_RESERVED",                "Reserved acquisition error"),
        10: ("ERR_BAD_MSG_FROM_STM32",      "Internal error (bad message from STM32)"),
        11: ("ERR_AUTO_RAMAN_IN_PROG",      "Auto-Raman already in progress"),
        12: ("ERR_SEG_TX_FLR",              "Error transmitting image segment over BLE"),
        13: ("ERR_FPGA_READ_FLR",           "Error reading from FPGA"),
        14: ("ERR_FPGA_UPDATE_FLR",         "Error writing to FPGA"),
            # reserved
        32: ("AUTO_OPT_TARGET_RATIO",       "Auto-Raman is in the process of optimizing acquisition parameters"),
        33: ("TAKING_DARK",                 "Auto-Dark/Raman taking spectra (no laser)"),
        34: ("LASER_WARNING_DELAY",         "Auto-Dark/Raman paused during laser warning delay period"),
        35: ("LASER_WARMUP",                "Auto-Dark/Raman paused during laser warmup period"),
        36: ("TAKING_RAMAN",                "Auto-Dark/Raman taking spectra (laser enabled)")
    }

    IMAGE_SENSOR_STATES = {
        0: "UNKNOWN",
        1: "STANDBY",
        2: "TRANS_IN_OUT_STANDBY",
        3: "REG_HOLD",
        4: "ACTIVE",
        5: "ERROR"
    }

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        super().__init__()

        self.device_id      = device_id
        self.message_queue  = message_queue
        self.alert_queue    = alert_queue

        # all Characteristics to which we're subscribed for notifications
        self.notifications = set() 

        # Characteristics
        self.code_by_name = { "LASER_STATE":             0xff03,
                              "ACQUIRE":                 0xff04,
                              "BATTERY_STATE":           0xff09,
                              "GENERIC":                 0xff0a,
                              "SPECTRA":                 0xff0b }

        self.name_by_uuid = { self.wrap_uuid(code): name for name, code in self.code_by_name.items() }

        # Generics         Name                       Lvl  Set   Get Size
        self.generics = Generics()
        self.generics.add("LASER_TEC_MODE",            0, 0x84, 0x85,  1) # TEST [passed]
        self.generics.add("POWER_OFF",                 0, 0x87, None,  1) # TEST [passed]
        self.generics.add("LASER_WARNING_DELAY_SEC",   0, 0x8a, 0x8b,  1) # TEST [w]
        self.generics.add("RESET_UNIT",                0, 0x93, None,  1) # TEST [w]
        self.generics.add("AUTO_RAMAN_PARAMS",         0, 0x95, 0x98, 23, data_type="raw_data") # TEST [w]
        self.generics.add("IMAGE_SENSOR_STATE",        0, None, 0x97,  1) # TEST [passed]
        self.generics.add("INTEGRATION_TIME_MS",       0, 0xb2, 0xbf,  3) # TEST [passed]
        self.generics.add("GAIN_DB",                   0, 0xb7, 0xc5,  2, data_type="funky_float", epsilon=0.01) 
                                                       
        self.generics.add("EEPROM_DATA",               1, None, 0x01, 64, data_type="raw_data")
        self.generics.add("START_LINE",                1, 0x21, 0x22,  2) # TEST [w]
        self.generics.add("STOP_LINE",                 1, 0x23, 0x24,  2) # TEST [w]
        self.generics.add("AMBIENT_TEMPERATURE_DEG_C", 1, None, 0x2a,  1) # TEST [passed]
        self.generics.add("CPU_UNIQUE_ID",             1, None, 0x2c, 12, data_type="raw_data")
        self.generics.add("POWER_WATCHDOG_SEC",        1, 0x30, 0x31,  2) # TEST [w]
        self.generics.add("SCANS_TO_AVERAGE",          1, 0x62, 0x63,  2) # TEST
        self.generics.add("USB_ADAPTER_INFO",          1, None, 0x78,  5, data_type="raw_data") # TEST
        self.generics.add("LASER_OFF_DELAY_MS",        1, 0x90, 0x91,  2)

        # InterfaceDevice niceties
        self.settings = SpectrometerSettings(self.device_id)
        self.process_f = self.init_process_funcs()

        self.session_reading_count = 0
        self.take_one_request = None

        self.testing = False
        self.last_status_update_time = None
        
        # ability to bridge sync-async
        self.run_loop = self.get_run_loop()

    def  __str__(self): return f"<BLEDevice device_id {self.device_id}>"
    def __hash__(self): return hash(str(self))
    def __repr__(self): return str(self)
    def  __eq__ (self, rhs): return hash(self) == hash(rhs)
    def  __ne__ (self, rhs): return str (self) != str(rhs)
    def  __lt__ (self, rhs): return str (self) <  str(rhs)

    ###############################################################
    # Connection
    ###############################################################

    def connect(self):
        """ Synchronous, because called by WrapperWorker """
        future = asyncio.run_coroutine_threadsafe(self.connect_async(), self.run_loop)
        return future.result()

    async def connect_async(self):
        log.debug("connect_async: start")
        start_time = datetime.now()

        bleak_ble_device = self.device_id.bleak_ble_device
        if bleak_ble_device is None:
            msg = f"can't connect without bleak_ble_device"
            log.critical(f"connect: {msg}")
            return SpectrometerResponse(False, error_msg=msg)

        log.debug(f"connect_async: instantiating BleakClient")
        self.client = BleakClient(address_or_ble_device = bleak_ble_device, 
                                  disconnected_callback = self.disconnected_callback,
                                  timeout               = self.CONNECT_TIMEOUT_SEC)
        log.debug(f"connect_async: BleakClient instantiated: {self.client}")

        # no longer need handle in DeviceID, so clear it to simplify deepcopy etc
        self.device_id.bleak_ble_device = None

        log.debug(f"connect_async: calling client.connect")
        await self.client.connect()

        log.debug(f"connect_async: grabbing device information")
        await self.load_device_information_async()

        log.debug(f"connect_async: initializing Characteristics")
        await self.init_characteristics_async()

        ########################################################################
        # load EEPROM
        ########################################################################

        log.debug(f"connect_async: reading EEPROM")
        await self.read_eeprom_async()

        ########################################################################
        # post-EEPROM connection tasks
        ########################################################################

        log.debug(f"connect_async: processing retrieved EEPROM")
        self.settings.update_wavecal()

        log.debug(f"connect_async: initializing LASER_STATE")
        await self.update_laser_state_async()

        log.debug(f"connect_async: initializing BATTERY_STATE")
        await self.update_battery_state_async()

        log.debug(f"connect_async: initializing integration time")
        self.integration_time_ms = self.settings.eeprom.startup_integration_time_ms
        await self.set_integration_time_ms_async(self.integration_time_ms)

        log.debug(f"connect_async: initializing scan averaging")
        await self.set_scans_to_average_async(1)

        # learn more about the device
        self.settings.microcontroller_serial_number = await self.get_cpu_unique_id_async()
        log.debug(f"connect_async: cpu_unique_id = {self.settings.microcontroller_serial_number}")

        self.settings.state.power_connection_state = await self.get_power_connection_state_async()
        log.debug(f"connect_async: power_connection_state = {self.settings.state.power_connection_state}")

        ########################################################################
        # done
        ########################################################################

        msg  = f"Connected to {self.settings.eeprom.model} {self.settings.eeprom.serial_number} with {self.settings.pixels()} pixels "
        msg += f"from ({self.settings.wavelengths[0]:.2f}, {self.settings.wavelengths[-1]:.2f}nm)"
        if self.settings.wavenumbers:
            msg += f" ({self.settings.wavenumbers[0]:.2f}, {self.settings.wavenumbers[-1]:.2f}cm⁻¹)"
        log.debug(msg)

        elapsed_sec = (datetime.now() - start_time).total_seconds()
        log.debug(f"connect_async: connection took {elapsed_sec:.2f} sec")

        log.debug(f"connect_async: done")
        return SpectrometerResponse(True)

    async def load_device_information_async(self):
        log.debug("Device Information:")
        log.debug(f"  address {self.client.address}")
        log.debug(f"  mtu_size {self.client.mtu_size} bytes")

        self.device_info = {}
        for service in self.client.services:
            if "Device Information" in str(service):
                for char in service.characteristics:
                    name = char.description
                    value = self.decode(await self.client.read_gatt_char(char.uuid))
                    self.device_info[name] = value
                    log.debug(f"  {name} {value}")

        # warn on old firmware
        if utils.vercmp(self.device_info["Software Revision String"], "4.10.7") < 0:
            log.error("intended for use with BLE FW 4.10.7 or higher (found {self.device_info['Software Revision String']})")

        # copy firmware revisions to SpectrometerSettings
        self.settings.microcontroller_firmware_version = self.device_info["Firmware Revision String"]
        self.settings.fpga_firmware_version = self.device_info["Hardware Revision String"]
        self.settings.ble_firmware_version = self.device_info["Software Revision String"]

    async def init_characteristics_async(self):
        # find the primary service
        primary_service = None
        for service in self.client.services:
            if service.uuid.lower() == self.WASATCH_SERVICE.lower():
                primary_service = service
                
        if primary_service is None:
            return

        # iterate over standard Characteristics
        # @see https://bleak.readthedocs.io/en/latest/api/client.html#gatt-characteristics
        log.debug("Characteristics:")
        for char in primary_service.characteristics:
            name = self.get_name_by_uuid(char.uuid)
            extra = ""

            if "write-without-response" in char.properties:
                extra += f", Max write w/o rsp size: {char.max_write_without_response_size}"

            props = ",".join(char.properties)
            log.debug(f"  {name:30s} {char.uuid} ({props}){extra}")

            # reminder: INDICATE is acknowledged (i.e. TCP),
            #           NOTIFY is unacknowledged (i.e. UDP).
            if "notify" in char.properties or "indicate" in char.properties:
                if name == "BATTERY_STATE":  
                    log.debug(f"starting {name} notifications")
                    await self.client.start_notify(char.uuid, self.battery_notification_async)
                    self.notifications.add(char.uuid)
                elif name == "LASER_STATE":
                    log.debug(f"starting {name} indications")
                    await self.client.start_notify(char.uuid, self.laser_state_notification_async)
                    self.notifications.add(char.uuid)
                elif name == "GENERIC":
                    log.debug(f"starting {name} indications")
                    await self.client.start_notify(char.uuid, self.generics.notification_callback_async)
                    self.notifications.add(char.uuid)
                elif name == "ACQUIRE":
                    log.debug(f"starting {name} notifications")
                    await self.client.start_notify(char.uuid, self.acquire_notification) # note callback methods are not necessarily async
                    self.notifications.add(char.uuid)
                elif name == "SPECTRA":
                    log.debug(f"starting {name} indications")
                    await self.client.start_notify(char.uuid, self.spectra_notification)
                    self.notifications.add(char.uuid)

    def disconnected_callback(self, arg):
        """
        Should we send a poison-pill upstream?
        """
        log.critical(f"disconnected_callback: received arg {arg}")

    def disconnect(self):
        log.debug("disconnect: start")

        log.debug("disconnect: stopping notifications")
        future = asyncio.run_coroutine_threadsafe(self.stop_notifications_async(), self.run_loop)
        _ = future.result()

        try:
            log.debug("disconnect: calling BleakClient.disconnect")
            future = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.run_loop)
            response = future.result()
        except:
            log.error("exception calling BleakClient.disconnect", exc_info=1)
        log.debug("disconnect: done")
        return SpectrometerResponse(True)

    async def stop_notifications_async(self):
        log.debug("stopping notifications")
        for uuid in self.notifications:
            await self.client.stop_notify(uuid)

    ############################################################################
    # Characteristic utilities
    ############################################################################

    async def read_char_async(self, name, min_len=None, quiet=False):
        uuid = self.get_uuid_by_name(name)
        if uuid is None:
            raise RuntimeError(f"invalid characteristic {name}")

        response = await self.client.read_gatt_char(uuid)
        if response is None:
            # responses may be optional on a write, but they're required on read
            raise RuntimeError("attempt to read {name} returned no data")

        if not quiet:
            log.debug(f"<< read_char_async({name}, min_len {min_len}): {utils.to_hex(response)}")

        if min_len is not None and len(response) < min_len:
            raise RuntimeError(f"characteristic {name} returned insufficient data ({len(response)} < {min_len})")

        buf = bytearray()
        for byte in response:
            buf.append(byte)
        return buf

    async def write_char_async(self, name, data, quiet=False, callback=None, ack_name=None):
        name = name.upper()
        uuid = self.get_uuid_by_name(name)
        if uuid is None:
            raise RuntimeError(f"invalid characteristic {name}")
        extra = []

        if name == "GENERIC":
            # STEP FIVE: allocate a new sequence number, and associate it with the passed callback
            if callback is None and ack_name is not None:
                # we weren't given an explicit callback, but this GENERIC opcode 
                # generates an acknowledgement, so setup a lambda to catch it (so
                # we can block on it before returning)
                callback = partial(self.generics.process_acknowledgement_async, name=ack_name)
            seq = self.generics.next_seq(callback)
            prefixed = [ seq ]
            for v in data:
                prefixed.append(v)
            data = prefixed
            extra.append(self.expand_path(name, data))

        if not quiet:
            code = self.code_by_name.get(name)
            log.debug(f">> write_char_async({name} 0x{code:02x}, {utils.to_hex(data)}){', '.join(extra)}")

        if isinstance(data, list):
            data = bytearray(data)

        # MZ: I'm not sure why all writes require a response, but empirical 
        # testing indicates we reliably get randomly scrambled EEPROM contents 
        # without this.

        # STEP SEVEN: actually write the cmd (or for Generic reads, "read request") to the Peripheral
        await self.client.write_gatt_char(uuid, data, response=True)

        if ack_name is not None:
            # block on the acknowledgement we created above
            # MZ: when would we NOT want to wait on an acknowledgement?
            log.debug(f"write_char_async: waiting for {ack_name} ack")
            await self.generics.wait_async(ack_name)

    async def write_generic_async(self, name, data):
        await self.write_char_async("GENERIC", self.generics.generate_write_request(name, data), ack_name=name)

    def expand_path(self, name, data):
        if name != "GENERIC":
            return [ f"0x{v:02x}" for v in data ]
        path = [ f"SEQ 0x{data[0]:02x}" ]
        header = True
        for i in range(1, len(data)):
            if header:
                code = data[i]
                name = self.generics.get_name(code)
                path.append(name)
                if code != 0xff:
                    header = False
            else:
                path.append(f"0x{data[i]:02x}")
        return "[" + ", ".join(path) + "]"

    ############################################################################
    # GENERIC Characteristic
    ############################################################################

    # Acquisition Parameters ###################################################

    def set_integration_time_ms(self, ms):
        future = asyncio.run_coroutine_threadsafe(self.set_integration_time_ms_async(ms), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_integration_time_ms_async(self, ms):
        ms = int(round(ms))
        await self.write_generic_async("INTEGRATION_TIME_MS", ms)
        self.settings.state.prev_integration_time_ms = self.settings.state.integration_time_ms
        self.settings.state.integration_time_ms = ms

    def set_gain_db(self, db):
        future = asyncio.run_coroutine_threadsafe(self.set_gain_db_async(db), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_gain_db_async(self, db):
        await self.write_generic_async("GAIN_DB", db)
        self.settings.state.gain_db = db

    def set_scans_to_average(self, n):
        future = asyncio.run_coroutine_threadsafe(self.set_scans_to_average_async(n), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_scans_to_average_async(self, n):
        name = "SCANS_TO_AVERAGE"
        await self.write_generic_async("SCANS_TO_AVERAGE", n)
        self.settings.state.scans_to_average = n

    def set_auto_raman_params(self, data):
        future = asyncio.run_coroutine_threadsafe(self.set_auto_raman_params_async(data), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_auto_raman_params_async(self, data):
        await self.write_generic_async("AUTO_RAMAN_PARAMS", data)

    def set_start_line(self, n):
        future = asyncio.run_coroutine_threadsafe(self.set_start_line_async(n), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_start_line_async(self, n):
        await self.write_generic_async("START_LINE", n)

    def set_stop_line(self, n):
        future = asyncio.run_coroutine_threadsafe(self.set_stop_line_async(n), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_stop_line_async(self, n):
        await self.write_generic_async("STOP_LINE", n)

    def set_vertical_roi(self, pair):
        start_line = pair[0]
        stop_line = pair[1]
        if stop_line <= start_line:
            log.debug("declining to set 1-line vertical ROI")
            return SpectrometerResponse(False)

        self.set_start_line(start_line)
        self.set_stop_line (stop_line)
        return SpectrometerResponse(True)

    # Laser Control ############################################################

    def set_laser_tec_mode(self, n):
        future = asyncio.run_coroutine_threadsafe(self.set_laser_tec_mode_async(n), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_laser_tec_mode_async(self, n):
        await self.write_generic_async("LASER_TEC_MODE", n)

    # Miscellaneous ############################################################

    def set_power_watchdog_sec(self, sec):
        future = asyncio.run_coroutine_threadsafe(self.set_power_watchdog_sec_async(n), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_power_watchdog_sec_async(self, sec):
        await self.write_generic_async("POWER_WATCHDOG_SEC", sec)

    def set_laser_warning_delay_sec(self, sec):
        future = asyncio.run_coroutine_threadsafe(self.set_laser_warning_delay_sec_async(n), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_laser_warning_delay_sec_async(self, sec):
        await self.write_generic_async("LASER_WARNING_DELAY_SEC", sec)

    def get_cpu_unique_id(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.get_cpu_unique_id_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def get_cpu_unique_id_async(self):
        """
        2 bytes of X coordinate (4 BCD nibbles)
        2 bytes of Y coordinate (4 BCD nibbles)
        1 byte of wafer number (8 bit unsigned) Bits 32:39
        3 bytes of LOT number (ASCII encoded) Bits 40:63
        4 bytes of LOT number (ASCII encoded) Bits 64:95

        example: [ x45, 0x00, 0x48, 0x00, 0x14, 0x51, 0x33, 0x33, 0x32, 0x36, 0x31, 0x30 ]
                   ----x----  -----y----  wafer ------LOT 1-----  --------LOT 2---------

        yields:  pos (45, 48), wafer 20, lot Q33-2610
        """
        data = await self.get_generic_value_async("CPU_UNIQUE_ID")

        value_hex_str = "".join([ f"{c:02x}" for c in data ])
        self.settings.microcontroller_serial_number = value_hex_str

        if len(data) == 12:
            # MZ: I'm not sure if I'm decoding these two fields of 4 BCD digits
            # in the right order, but it doesn't really matter. In this method,
            # 0x1234 would yields decimal 1234, which I think is the intention.
            x = ( ((data[0] >> 0) & 0xf) *    1 +
                  ((data[0] >> 4) & 0xf) *   10 + 
                  ((data[1] >> 0) & 0xf) *  100 +
                  ((data[1] >> 4) & 0xf) * 1000 )
            y = ( ((data[2] >> 0) & 0xf) *    1 +
                  ((data[2] >> 4) & 0xf) *   10 + 
                  ((data[3] >> 0) & 0xf) *  100 +
                  ((data[3] >> 4) & 0xf) * 1000 )
            wafer = data[4]
            lot = "".join([chr(data[i]) for i in range(5, 8)]) + "-" + "".join([chr(data[i]) for i in range(8, 12)])
            log.debug(f"decoded CPU_UNIQUE_ID: pos ({x}, {y}), wafer {wafer}, lot {lot}")

        return value_hex_str

    def get_power_connection_state(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.get_power_connection_state_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def get_power_connection_state_async(self):
        data = await self.get_generic_value_async("USB_ADAPTER_INFO")
        state = USBCPowerConnectionState(data)
        self.settings.state.power_connection_state = state
        return state

    def get_image_sensor_state(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.get_image_sensor_state_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def get_image_sensor_state_async(self):
        state = await self.get_generic_value_async("IMAGE_SENSOR_STATE")
        if self.testing:
            msg = f"IMAGE_SENSOR_STATE value {state}, label {self.IMAGE_SENSOR_STATES.get(state, None)}"
            self.queue_message("marquee_info", msg)
        return state

    def get_ambient_temperature_deg_c(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.get_ambient_temperature_deg_c_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def get_ambient_temperature_deg_c_async(self):
        temp = await self.get_generic_value_async("AMBIENT_TEMPERATURE_DEG_C")
        self.settings.state.ambient_temperature_deg_c = temp
        return temp

    def set_reset_unit(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.set_reset_unit_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_reset_unit_async(self):
        await self.write_char_async("GENERIC", self.generics.generate_write_request("RESET_UNIT"))

    def set_power_off(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.set_power_off_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_power_off_async(self, arg=None):
        await self.write_char_async("GENERIC", self.generics.generate_write_request("POWER_OFF"))

    # EEPROM ###################################################################

    async def read_eeprom_async(self):
        await self.read_eeprom_pages_async()
        self.settings.eeprom.parse(self.pages)

    async def read_eeprom_pages_async(self):
        start_time = datetime.now()

        self.eeprom = {}
        self.pages = []

        name = "EEPROM_DATA"
        for page in range(self.MAX_EEPROM_PAGES):
            buf = bytearray()
            while len(buf) < 64:
                
                offset = len(buf)
                request = self.generics.generate_read_request(name)
                request.append(0) # page is big-endian uint16, update this for pages > 255
                request.append(page)
                request.append(offset)

                log.debug(f"read_eeprom_pages_async: querying {name} ({utils.to_hex(request)})")
                await self.write_char_async("GENERIC", request, callback=lambda data: self.generics.process_response_async(name, data))

                log.debug(f"read_eeprom_pages_async: waiting on {name}")
                await self.generics.wait_async(name)

                data = self.generics.get_value(name)
                log.debug(f"read_eeprom_pages_async: received page {page}, offset {offset}: {data}")

                for byte in data:
                    buf.append(byte)
            self.pages.append(buf)

        elapsed_sec = (datetime.now() - start_time).total_seconds()
        log.debug(f"reading eeprom took {elapsed_sec:.2f} sec")

    # getter helper ############################################################

    async def get_generic_value_async(self, name):
        """
        This method needs to be in BLEDevice, rather than Generics, because it
        uses write_char.
        """
        # STEP ONE: generate the payload for writing the "read request" for this particular attribute to the Generic characteristic
        request = self.generics.generate_read_request(name)
        log.debug(f"get_generic_value_async: querying {name} ({utils.to_hex(request)})")

        # STEP FOUR: write the "read request" to the attribute. Store a callback
        # which should be triggered when the response notification (carrying the
        # same sequence number) is returned.
        await self.write_char_async("GENERIC", request, callback=lambda data: self.generics.process_response_async(name, data))

        # STEP EIGHTEEN: this blocking wait will be satisfied after steps 4-17 are complete
        log.debug(f"get_generic_value_async: waiting on {name}")
        await self.generics.wait_async(name)

        # STEP NINETEEN: read the deserialized response value we stored in the notification callback
        log.debug(f"get_generic_value_async: taking response from {name}")
        value = self.generics.get_value(name)
        log.debug(f"get_generic_value_async: done (value {value})")

        # STEP TWENTY: return the value to whoever called this function.
        return value

    ############################################################################
    # BATTERY_STATE Characteristic
    ############################################################################

    async def battery_notification_async(self, sender, data):
        await self.update_battery_state_async(data)

    def update_battery_state(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.update_battery_state_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def update_battery_state_async(self, buf=None):
        """
        These will be pushed automatically 2/min from Central, if-and-only-if
        no acquisition is occuring at the time of the scheduled event. However,
        at least some(?) clients (Bleak on MacOS) don't seem to receive the
        notifications until the next explicit read/write of a Characteristic 
        (any Chararacteristic? seemingly observed with ACQUIRE_CMD).
        """
        if buf is None:
            log.debug("updating battery state")
            buf = await self.read_char_async("BATTERY_STATE", 5)
        if buf is None:
            return

        state = self.settings.state
        state.battery_charging = buf[0] != 0
        state.battery_percentage = buf[1] + buf[2] / 256.0
        state.battery_temperature_deg_c = buf[3]
        state.battery_charger_temperature_deg_c = buf[4]

        log.debug(f"updated battery state: chg {state.battery_charging}, " +
                  f"perc {state.battery_percentage:.2f}, " + 
                  f"temp {state.battery_temperature_deg_c}C, " +
                  f"chgTemp {state.battery_temperature_deg_c}C")

    ############################################################################
    # LASER_STATE Characteristic
    ############################################################################

    async def laser_state_notification_async(self, sender, data):
        await self.update_laser_state_async(data)

    def update_laser_state(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.update_laser_state_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def update_laser_state_async(self, buf=None):
        if buf is None:
            log.debug("updating laser state")
            buf = await self.read_char_async("LASER_STATE", 7)
        if buf is None:
            return

        state = self.settings.state

        if len(buf) >= 4:
            # ignore bytes 0 and 1 (mode and type)
            state.laser_enabled = buf[2]
            state.laser_watchdog_sec = (buf[3] << 8) | buf[4]

        # skip bytes 5 and 6 reserved (used to be laser_warning_delay_ms)

        if len(buf) >= 8: 
            state.laser_can_fire  = buf[7] & 0x01
            state.laser_is_firing = buf[7] & 0x02

        if len(buf) >= 9: 
            state.laser_pwm_perc = buf[8]

        log.debug(f"updated laser state: enabled {state.laser_enabled}, " +
                  f"watchdog {state.laser_watchdog_sec}sec, " +
                  f"can_fire {state.laser_can_fire}, " +
                  f"is_firing {state.laser_is_firing}, " + 
                  f"PWM {state.laser_pwm_perc}")

    def set_laser_enable(self, flag):
        future = asyncio.run_coroutine_threadsafe(self.set_laser_enable_async(flag), self.run_loop)
        return SpectrometerResponse(future.result())
    async def set_laser_enable_async(self, flag):
        log.debug(f"setting laser enable {flag}")
        self.laser_enable = flag
        await self.sync_laser_state_async()

    async def sync_laser_state_async(self):
        log.debug(f"sync_laser_state_async: start")

        data = [ 0xff,                   # mode (NO CHANGE)
                 0xff,                   # type (NO CHANGE)
                 0x01 if self.laser_enable else 0x00, 
                 0xff,                   # laser watchdog (NO CHANGE)
                 0x00,                   # reserved (legacy laser_warning_delay_ms)
                 0x00 ]                  # reserved (legacy laser_warning_delay_ms)

        log.debug(f"sync_laser_state_asyhc: calling write_char_async('LASER_STATE') with data {data}")
        await self.write_char_async("LASER_STATE", data)
        log.debug(f"sync_laser_state_async: done")

    ############################################################################
    # ACQUIRE and SPECTRA Characteristics
    ############################################################################

    def acquire_data(self):
        """ Synchronous, because called by WrapperWorker """

        auto_raman = self.take_one_request and self.take_one_request.auto_raman_request

        future = asyncio.run_coroutine_threadsafe(self.get_spectrum_async(), self.run_loop)
        spectrum = future.result()

        log.debug(f"acquire_data: received spectrum of length {len(spectrum)}: {spectrum[:10]}")
        reading = Reading(device_id=self.device_id)
        state = self.settings.state
        now = datetime.now()

        if (self.last_status_update_time is None or (now - self.last_status_update_time).total_seconds() >= self.STATUS_UPDATE_PERIOD_SEC):
            self.update_status()

        reading.spectrum = spectrum
        reading.averaged = True
        reading.device_id = self.device_id
        reading.session_count = self.session_reading_count
        reading.laser_enabled = state.laser_enabled or auto_raman
        reading.laser_can_fire = state.laser_can_fire
        reading.laser_is_firing = state.laser_is_firing
        reading.battery_charging = state.battery_charging
        reading.take_one_request = self.take_one_request
        reading.timestamp_complete = datetime.now()
        reading.battery_percentage = state.battery_percentage
        reading.power_connection_state = state.power_connection_state
        reading.ambient_temperature_degC = state.ambient_temperature_deg_c # deg_c -> degC :-(

        self.take_one_request = None

        return SpectrometerResponse(data=reading)

    def acquire_notification(self, sender, data):
        if (len(data) < 3):
            raise RuntimeError(f"received invalid ACQUIRE notification of {len(data)} bytes: {data}")

        # first two bytes declare whether it's a status message (all we should be 
        # getting now under API9) or spectral data (deprecated after API6)
        #
        # MZ: these are now wasted bytes in API9, which could be deprecated in API10
        first_pixel = int((data[0] << 8) | data[1]) # big-endian int16
        if first_pixel != 0xffff:
            raise RuntimeError(f"received invalid SPECTRA data (API6) on API9 ACQUIRE characteristic!")
            
        status = data[2]
        payload = data[3:]
        msg = self.parse_acquire_status(status, payload)
        log.debug(f"acquire_notification: {msg}")
        
    def parse_acquire_status(self, status, payload):
        if status not in self.ACQUIRE_STATUS_CODES:
            raise RuntimeError("ACQUIRE notification included unsupported status code 0x{status:02x}, payload {payload}")

        short, long = self.ACQUIRE_STATUS_CODES[status]
        msg = f"{short}: {long}"

        # special handling for status codes including payload
        if status == 32: 
            target_ratio = int(payload[0])
            msg += f" (target ratio {target_ratio}%)"
        elif status in [33, 34, 35, 36]:
            current_step = int(payload[0] << 8 | payload[1])
            total_steps  = int(payload[2] << 8 | payload[3])

            step_msg = ""
            if total_steps > 1:
                step_msg = f" ({current_step + 1}/{total_steps})" 
                msg += step_msg

        # Auto-Raman takes awhile, so flow up some status indications
        if self.take_one_request and self.take_one_request.auto_raman_request:
            if short == "LASER_WARMUP":
                self.queue_message("marquee_info", "waiting for laser to stabilize")
                self.queue_message("progress_bar", -1)
            elif short == "AUTO_OPT_TARGET_RATIO":
                self.queue_message("marquee_info", "optimizing acquisition parameters")
                self.queue_message("progress_bar", -1)
            elif short == "TAKING_RAMAN":
                self.queue_message("marquee_info", "averaging Raman spectra")
                self.queue_message("progress_bar", 100.0 * current_step / total_steps)
            elif short == "TAKING_DARK":
                self.queue_message("marquee_info", "averaging dark spectra")
                self.queue_message("progress_bar", 100.0 * current_step / total_steps)

        return msg
    
    def spectra_notification(self, sender, data):
        if len(data) < 4:
            raise RuntimeError(f"received invalid SPECTRA notification of {len(data)} bytes: {data}")

        # first two bytes declare whether it's spectral data (all we should be 
        # getting now under API9) or a status message (deprecated after API6)
        first_pixel = int((data[0] << 8) | data[1]) # big-endian int16

        if first_pixel == 0xffff:
            raise RuntimeError("received invalid API6 ACQUIRE status notification on API9 SPECTRA characteristic!")

        # apparently it's spectral data

        # validate first_pixel
        if first_pixel != self.pixels_read:
            raise RuntimeError(f"received first_pixel {first_pixel} when pixels_read {self.pixels_read}")

        spectral_data = data[2:]
        pixels_in_packet = int(len(spectral_data) / 2)

        for i in range(pixels_in_packet):
            # pixel intensities are little-endian uint16
            offset = i * 2
            intensity = int((spectral_data[offset+1] << 8) | spectral_data[offset]) 
            self.spectrum[self.pixels_read] = intensity
            self.pixels_read += 1

    def get_spectrum(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.get_spectrum_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def get_spectrum_async(self):
        """
        This is an asynchronous high-level function which wraps the mechanical 
        steps of sending an ACQUIRE and receiving the full SPECTRA in response.
        """

        auto_raman_request = self.take_one_request.auto_raman_request if self.take_one_request else None
        if auto_raman_request:
            await self.set_auto_raman_params_async(auto_raman_request.serialize())

        self.pixels_read = 0
        self.spectrum = [0] * self.settings.pixels()

        # send the ACQUIRE
        spectrum_type = 2 if auto_raman_request else 0
        log.debug(f"sending ACQUIRE with type {spectrum_type}")
        await self.write_char_async("ACQUIRE", [spectrum_type])

        # compute timeout
        if auto_raman_request:
            timeout_ms = auto_raman_request.max_ms + 6000
        else:
            timeout_ms = ( 4 
                         * max(self.settings.state.prev_integration_time_ms, self.settings.state.integration_time_ms) 
                         * self.settings.state.scans_to_average 
                         + 6000) # 4sec latency + 2sec buffer

        # wait for spectral data to arrive
        start_time = datetime.now()
        while self.pixels_read < self.settings.pixels():
            if (datetime.now() - start_time).total_seconds() * 1000 > timeout_ms:
                raise RuntimeError(f"failed to read spectrum within timeout {timeout_ms}ms")

            # log.debug(f"still waiting for spectra ({self.pixels_read}/{self.settings.pixels()} read)")
            await asyncio.sleep(0.2)

        ########################################################################
        # post-processing
        ########################################################################

        # note, this needs updated for 633XS
        log.debug("applying 2x2 binning")
        binned = []
        for i in range(len(self.spectrum)-1):
            binned.append((self.spectrum[i] + self.spectrum[i+1]) / 2.0)
        binned.append(self.spectrum[-1])
        self.spectrum = binned

        # @todo add bad-pixel correction
        # @todo add invert_detector
        # @todo add many things...
            
        if auto_raman_request:
            self.queue_message("progress_bar", 100)

        return self.spectrum

    def update_status(self, arg=None):
        future = asyncio.run_coroutine_threadsafe(self.update_status_async(), self.run_loop)
        return SpectrometerResponse(future.result())
    async def update_status_async(self):
        """
        Low-rate function to update miscellaneous status attributes to flow up 
        to ENLIGHTEN in Readings.

        BATTERY_STATE should automatically update on its own firmware schedule,
        and LASER_STATE should update on every change in the device. However,
        this lets software drive (and initialize) that loop directly. Also, we 
        can add non-"urgent" attributes like ambient temperature, etc.
        """
        await self.update_battery_state_async()
        await self.update_laser_state_async()
        await self.get_ambient_temperature_deg_c_async()
        await self.get_power_connection_state_async()

        self.last_status_update_time = datetime.now()

    ############################################################################
    # Auto-Raman
    ############################################################################

    def set_take_one_request(self, value):
        self.take_one_request = value

    ############################################################################
    # InterfaceDevice
    ############################################################################

    def heartbeat(self, value):
        pass 

    def set_testing(self, flag):
        self.testing = True if flag else False

    def queue_message(self, setting, value):
        """
        currently supported settings:
            progress_bar (-1, 100)
            marquee_info
            marquee_error
            laser_firing_indicators (firing, not firing)
        """
        if self.message_queue:
            msg = StatusMessage(setting, value)
            log.debug(f"queue_message: {msg}")
            try:
                self.message_queue.put(msg) 
            except:
                log.error("failed to enqueue StatusMessage (%s, %s)", setting, value, exc_info=1)

    def init_process_funcs(self): # -> dict[str, Callable[..., Any]] 
        f = {}
        f["acquire_data"]            = self.acquire_data
        f["close"]                   = self.disconnect
        f["connect"]                 = self.connect
        f["detector_gain"]           = self.set_gain_db
        f["disconnect"]              = self.disconnect
        f["heartbeat"]               = self.heartbeat
        f["integration_time_ms"]     = self.set_integration_time_ms
        f["laser_enable"]            = self.set_laser_enable
        f["laser_tec_mode"]          = self.set_laser_tec_mode
        f["laser_warning_delay_sec"] = self.set_laser_warning_delay_sec
        f["power_off"]               = self.set_power_off
        f["reset_unit"]              = self.set_reset_unit
        f["scans_to_average"]        = self.set_scans_to_average
        f["start_line"]              = self.set_start_line
        f["stop_line"]               = self.set_stop_line
        f["take_one_request"]        = self.set_take_one_request
        f["testing"]                 = self.set_testing
        f["get_image_sensor_state"]  = self.get_image_sensor_state
        f["update_status"]           = self.update_status
        f["vertical_binning"]        = self.set_vertical_roi
        return f

    ############################################################################
    # Utility
    ############################################################################

    def decode(self, data):
        try:
            if isinstance(data, bytearray):
                return data.decode('utf-8')
        except:
            pass
        return data

    def wrap_uuid(self, code):
        return f"d1a7{code:04x}-af78-4449-a34f-4da1afaf51bc".lower()

    def get_name_by_uuid(self, uuid):
        return self.name_by_uuid.get(uuid.lower(), None)
        
    def get_uuid_by_name(self, name):
        code = self.code_by_name.get(name.upper(), None)
        if code is None:
            return
        return self.wrap_uuid(code)

################################################################################
#                                                                              #
#                               Support Classes                                #
#                                                                              #
################################################################################

class Generic:
    """ 
    Encapsulates paired setter and getter accessors for a single attribute.
    """

    def __init__(self, name, tier, setter, getter, size, epsilon=0, data_type=None):
        self.name      = name
        self.tier      = tier # 0, 1 or 2
        self.setter    = setter
        self.getter    = getter
        self.size      = size
        self.epsilon   = epsilon
        self.data_type = data_type

        self.value = None
        self.event = asyncio.Event()
    
    def serialize(self, value):
        data = []
        if value is None:
            pass
        elif self.data_type == "funky_float":
            data.append(int(value) & 0xff)
            data.append(int((value - int(value)) * 256) & 0xff)
        elif self.data_type == "raw_data":
            data = value # for writing EEPROM
        else: 
            # assume big-endian uint[size]            
            for i in range(self.size):
                data.append((value >> (8 * (self.size - (i+1)))) & 0xff)
        return data

    def deserialize(self, data):
        # STEP SIXTEEN: deserialize the returned Generic response payload 
        #               according to the attribute type
        if self.data_type == "funky_float":
            return data[0] + data[1] / 256.0
        elif self.data_type == "raw_data":
            return data
        else:
            # by default, treat as big-endian uint
            value = 0
            for byte in data:
                value <<= 8
                value |= byte
            return value

    def generate_write_request(self, value):
        if self.setter is None:
            raise RuntimeError(f"Generic {self.name} is read-only")
        request = [ 0xff ] * self.tier
        request.append(self.setter)
        request.extend(self.serialize(value))
        return request

    def generate_read_request(self):
        # STEP THREE: generate the "read request" payload for this attribute
        if self.getter is None:
            raise RuntimeError(f"Generic {self.name} is write-only")
        request = [ 0xff ] * self.tier
        request.append(self.getter)
        return request

class Generics:
    """ Façade to access all Generic attributes in the BLE interface """

    RESPONSE_ERRORS = [ 'OK', 
                        'NO_RESPONSE_FROM_HOST', 
                        'FPGA_READ_FAILURE', 
                        'INVALID_PARAMETER', 
                        'UNSUPPORTED_COMMAND' ]
    def __init__(self):
        self.seq = 0
        self.generics = {}
        self.callbacks = {}

    def next_seq(self, callback=None):
        self.seq = (self.seq + 1) % 256
        if self.seq in self.callbacks:
            raise RuntimeError("seq {self.seq} has unprocessed callback {self.callbacks[self.seq]}")
        elif callback:
            # STEP SIX: store the callback function in a table, keyed on the new sequence number
            self.callbacks[self.seq] = callback
        return self.seq

    def get_callback(self, seq):
        # STEP ELEVEN: remove the stored callback from the table, so it won't accidentally be re-used
        if seq in self.callbacks:
            return self.callbacks.pop(seq)

        # probably an uncaught acknowledgement from a generic setter like SET_INTEGRATION_TIME_MS
        log.debug(f"get_callback: seq {seq} not found in callbacks")

        # @todo: we're getting these from SET_START_LINE / SET_STOP_LINE, where 
        # the response notification doesn't seem to include the request's SEQ 
        # number

    def add(self, name, tier, setter, getter, size, epsilon=0, data_type=None):
        self.generics[name] = Generic(name, tier, setter, getter, size, epsilon=epsilon, data_type=data_type)

    def generate_write_request(self, name, value=None):
        return self.generics[name].generate_write_request(value)

    def generate_read_request(self, name):
        # STEP TWO: generate the "read request" payload for the named attribute
        return self.generics[name].generate_read_request()

    def get_value(self, name):
        return self.generics[name].value

    async def wait_async(self, name):
        await self.generics[name].event.wait()
        self.generics[name].event.clear()

    async def process_acknowledgement_async(self, data, name):
        log.debug(f"received acknowledgement for {name}")
        generic = self.generics[name]
        generic.event.set()

    async def process_response_async(self, name, data):
        # STEP THIRTEEN: this is the standard callback triggered after receiving
        # a notification from the Generic Characteristic

        # STEP FOURTEEN: lookup the specific Generic attribute (AMBIENT_TEMPERATURE_DEG_C, etc) associated with this transaction
        generic = self.generics[name]

        # STEP FIFTEEN: parse the response payload according to the attribute
        generic.value = generic.deserialize(data)

        # STEP SEVENTEEN: raise the asynchronous "event" flag to tell the 
        # await'ing requester that the response value is now available and stored
        # in the Generic object
        generic.event.set()

    async def notification_callback_async(self, sender, data):
        # STEP EIGHT: we have received a response notification from the Generic Characteristic

        log.debug(f"received GENERIC notification, data {utils.to_hex(data)}")

        # STEP NINE: extract the sequence number from the notification response
        result = None
        if len(data) < 3:
            seq, err = data[0], data[1]
        else:
            seq, err, result = data[0], data[1], data[2:]

        if err < len(self.RESPONSE_ERRORS):
            response_error = self.RESPONSE_ERRORS[err]
        else:
            response_error = f"UNSUPPORTED RESPONSE_ERROR: 0x{err}"

        if response_error != "OK":
            raise RuntimeError(f"GENERIC notification included error code {err} ({response_error}); data {utils.to_hex(data)}")

        # STEP TEN: lookup the stored callback for this sequence number
        #
        # pass the response data, minus the sequence and error-code header, to 
        # the registered callback function for that sequence ID
        callback = self.get_callback(seq)

        # STEP TWELVE: actually call the callback
        if callback:
            await callback(result)
        
    def get_name(self, code):
        if code == 0xff:
            return "NEXT_TIER"

        for name, generic in self.generics.items():
            if code == generic.setter:
                return f"SET_{name}"
            elif code == generic.getter:
                return f"GET_{name}"
        return "UNKNOWN"

    def equals(self, name, expected):
        """ consider overriding for different data_types """
        actual  = self.generics[name].value
        epsilon = self.generics[name].epsilon
        delta   = abs(actual - expected)
        return delta <= epsilon
