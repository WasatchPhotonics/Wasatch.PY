import os
import asyncio
import logging

from bleak import BleakClient
from datetime import datetime
from threading import Thread

from wasatch.EEPROM               import EEPROM
from wasatch.Reading              import Reading
from wasatch.ControlObject        import ControlObject
from wasatch.InterfaceDevice      import InterfaceDevice
from wasatch.SpectrometerRequest  import SpectrometerRequest
from wasatch.SpectrometerSettings import SpectrometerSettings
from wasatch.SpectrometerResponse import SpectrometerResponse, ErrorLevel

log = logging.getLogger(__name__)

# static, so available class-wide
def to_hex(a):
    if a is None:
        return "[ ]"
    return "[ " + ", ".join([f"0x{v:02x}" for v in a]) + " ]"

class BLEDevice(InterfaceDevice):
    """
    This class is the BLE counterpart to our USB WasatchDevice, likewise
    servicing requests from WrapperWorker in a dedicated spectrometer Thread.

    WrapperWorker <--> InterfaceDevice 
                       <>+-- WasatchDevice <-> pyusb (X, XM, XS USB spectrometers)
                         `-- BLEDevice <-----> bleak (XS BLE spectrometers)
                         `-- TCPDevice <-----> socket (network spectrometers)
                         `-- SPIDevice <-----> pyftdi (industrial spectrometers)
                         `-- OceanDevice <---> pyseabreeze (3rd-party spectrometers)

    # Architecture

    BLEManager instantiates a DeviceFinderBLE to generate a list of available
    spectrometers.

    DeviceFinderBLE instantiates a BleakScanner, and passes DiscoveredBLEDevices
    (rssi and DeviceID (containing serial_number and bleak.BLEDevice)) back to 
    BLEManager so the user can pick one.

    BLEManager adds the selected wasatch.DeviceID to Controller.other_device_ids,
    which will presently pass it to a new WasatchDeviceWrapper and associated
    WrapperWorker thread.

    The WrapperWorker will then instantiate a wasatch.BLEDevice with the 
    wasatch.DeviceID. The wasatch.BLEDevice will use the DeviceID's embedded
    bleak.BLEDevice to instantiate a BleakClient, which will be used for 
    subsequent communications.

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

    # wasatch.DeviceID

    The bleak.BLEDevice is attached to the wasatch.DeviceID because we have 
    existing infrastructure in enlighten.Controller to maintain a set of 
    .other_device_ids (DeviceID objects) it can check to connect to "non-self-
    discovery" spectrometers. Currently those include WISP TCP/IP spectrometers
    and BLE spectrometers, both of which require some level of user-interaction
    to detect/find. 

    Controller then passes the wasatch.DeviceID into wasatch.WasatchDeviceWrapper
    (and wasatch.WrapperWorker) to properly conenct to the requested device. WISP
    needs IP addr:port, so those go into DeviceID, and BLE needs bleak.BLEDevice, 
    hence the extra attributes in DeviceID.
    
    # BLE Discovery

    In wasatch.DeviceFinderBLE, bleak.BleakScanner issues a series of 
    notifications including (bleak.backends.device.BLEDevice, 
    bleak.backends.scanner.AdvertisementData).

    AdvertisementData contains these useful attributes:
        - local_name            <-- WP-12345
        - rssi                  <-- 
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

    @todo consider https://github.com/django/asgiref#function-wrappers
    """

    WASATCH_SERVICE   = "D1A7FF00-AF78-4449-A34F-4DA1AFAF51BC"
   #DISCOVERY_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"

    CONNECT_TIMEOUT_SEC = 10

    LASER_TEC_MODES = ['OFF', 'ON', 'AUTO', 'AUTO_ON']

    ACQUIRE_STATUS_CODES = {
         0: ("NAK",                         "No error, the spectrum just isn't ready yet"),
         1: ("ERR_BATT_SOC_INFO_NOT_RCVD",  "Can't read battery, and therefore can't take Auto-Dark or Auto-Raman spectra"),
         2: ("ERR_BATT_SOC_TOO_LOW",        "Battery is too low to take Auto-Dark or Auto-Raman spectra"),
         3: ("ERR_LASER_DIS_FLR",           "Failure disabling the laser"),
         4: ("ERR_LASER_ENA_FLR",           "Failure enabling the laser"),
         5: ("ERR_IMG_SNSR_IN_BAD_STATE",   "The sensor is not able to take spectra"),
         6: ("ERR_IMG_SNSR_STATE_TRANS_FLR","The sensor failed to apply acquisition parameters"),
         7: ("ERR_SPEC_ACQ_SIG_WAIT_TMO",   "The sensor failed to take a spectrum (timeout exceeded)"),
        32: ("AUTO_OPT_TARGET_RATIO",       "Auto-Raman is in the process of optimizing acquisition parameters"),
        33: ("AUTO_TAKING_DARK",            "Auto-Dark/Raman is taking dark spectra"),
        34: ("AUTO_LASER_WARNING_DELAY",    "Auto-Dark/Raman is paused during laser warning delay period"),
        35: ("AUTO_LASER_WARMUP",           "Auto-Dark/Raman is paused during laser warmup period"),
        36: ("AUTO_TAKING_RAMAN",           "Auto-Dark/Raman is taking Raman measurements"),
    }

    # public singleton
    static_run_loop = None
    static_thread = None

    @staticmethod
    def get_run_loop():

        def make_run_loop():
            log.debug("make_run_loop: setting run_loop")
            asyncio.set_event_loop(BLEDevice.static_run_loop)
            log.debug("make_run_loop: running run_loop forever")
            BLEDevice.static_run_loop.run_forever()
            log.debug("make_run_loop: done")

        if BLEDevice.static_run_loop is None:
            log.debug("get_run_loop: instantiating event loop")
            BLEDevice.static_run_loop = asyncio.new_event_loop()

            log.debug("get_run_loop: instantiating Thread")
            static_thread = Thread(target=make_run_loop, daemon=True)

            log.debug("get_run_loop: starting thread")
            static_thread.start()

        return BLEDevice.static_run_loop

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        log.debug("init: start")

        super().__init__()
        self.device_id = device_id

        self.notifications = set() # all Characteristics to which we're subscribed for notifications

        # Characteristics
        self.code_by_name = { "LASER_STATE":             0xff03,
                              "ACQUIRE":                 0xff04,
                              "BATTERY_STATE":           0xff09,
                              "GENERIC":                 0xff0a }
        self.name_by_uuid = { self.wrap_uuid(code): name for name, code in self.code_by_name.items() }

        # Generics         Name                        Lvl  Set   Get Size
        self.generics = Generics()
        self.generics.add("LASER_TEC_MODE",             0, 0x84, 0x85, 1)
        self.generics.add("GAIN_DB",                    0, 0xb7, 0xc5, 2, epsilon=0.01)
        self.generics.add("INTEGRATION_TIME_MS",        0, 0xb2, 0xbf, 3)
        self.generics.add("LASER_WARNING_DELAY_SEC",    0, 0x8a, 0x8b, 1)
        self.generics.add("EEPROM_DATA",                1, None, 0x01, 1)
        self.generics.add("START_LINE",                 1, 0x21, 0x22, 2)
        self.generics.add("STOP_LINE",                  1, 0x23, 0x24, 2)
        self.generics.add("AMBIENT_TEMPERATURE_DEG_C",  1, None, 0x2a, 1)
        self.generics.add("POWER_WATCHDOG_SEC",         1, 0x30, 0x31, 2)
        self.generics.add("SCANS_TO_AVERAGE",           1, 0x62, 0x63, 2)

        ########################################################################
        # things not in ble-util.py
        ########################################################################

        self.is_ble = True
        self.is_andor = lambda : False 

        self.settings = SpectrometerSettings(self.device_id)

        log.debug("init: creating scan_loop")
        self.run_loop = self.get_run_loop()

        log.debug("init: initializing process funcs")
        self.process_f = self.init_process_funcs()

        log.debug("init: done")

    def  __str__(self): return f"<BLEDevice device_id {self.device_id}>"
    def __hash__(self): return hash(str(self))
    def __repr__(self): return str(self)
    def  __eq__ (self, rhs): return hash(self) == hash(rhs)
    def  __ne__ (self, rhs): return str (self) != str(rhs)
    def  __lt__ (self, rhs): return str (self) <  str(rhs)

    def disconnect(self):
        log.debug("disconnect: start")
        try:
            future = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.run_loop)
            response = future.result()
        except:
            log.error("exception calling BleakClient.disconnect", exc_info=1)
        log.debug("disconnect: done")
        return SpectrometerResponse(True)

    ###############################################################
    # Public Methods
    ###############################################################

    def connect(self):
        """
        This is a synchronous method that WrapperWorker can call.
        """
        log.debug("connect: calling connect_async")
        future = asyncio.run_coroutine_threadsafe(self.connect_async(), self.run_loop)
        log.debug(f"connect: connect_async future pending ({future})")
        response = future.result()
        log.debug(f"connect: back from connect_async (response {response})")

        return response

    async def connect_async(self):
        log.debug("connect_async: start")
        start_time = datetime.now()

        bleak_ble_device = self.device_id.bleak_ble_device
        if bleak_ble_device is None:
            msg = f"can't connect without bleak_ble_device"
            log.critical(f"connect: {msg}")
            return SpectrometerResponse(False, error_msg=msg)

        log.debug("connect_async: instantiating BleakClient")
        self.client = BleakClient(address_or_ble_device=bleak_ble_device, 
                                  disconnected_callback=self.disconnected_callback,
                                  timeout=self.CONNECT_TIMEOUT_SEC)
        log.debug(f"connect_async: BleakClient instantiated: {self.client}")

        log.debug(f"connect_async: calling client.connect")
        await self.client.connect()

        log.debug(f"connect_async: grabbing device information")
        await self.load_device_information()

        log.debug(f"connect_async: loading Characteristics")
        await self.load_characteristics()

        log.debug(f"connect_async: reading EEPROM")
        await self.read_eeprom()

        log.debug("connect_async: processing retrieved EEPROM")
        self.settings.update_wavecal()

        # grab initial integration time (used for acquisition timeout)
        self.integration_time_ms = self.settings.eeprom.startup_integration_time_ms

        msg  = f"Connected to {self.settings.eeprom.model} {self.settings.eeprom.serial_number} with {self.settings.pixels()} pixels "
        msg += f"from ({self.settings.wavelengths[0]:.2f}, {self.settings.wavelengths[-1]:.2f}nm)"
        if self.settings.wavenumbers:
            msg += f" ({self.settings.wavenumbers[0]:.2f}, {self.settings.wavenumbers[-1]:.2f}cm⁻¹)"
        log.debug(msg)

        elapsed_sec = (datetime.now() - start_time).total_seconds()
        log.debug(f"connect_async: connection took {elapsed_sec:.2f} sec")

        log.debug("connect_async: done")
        return SpectrometerResponse(True)

    ############################################################################
    # 
    #                           from ble-util.py
    # 
    ############################################################################

    def disconnected_callback(self):
        log.critical("disconnected")
        # send poison-pill upstream?

    async def load_device_information(self):
        log.debug("Device Information:")
        log.debug(f"  address {self.client.address}")
        log.debug(f"  mtu_size {self.client.mtu_size} bytes")

        device_info = {}
        for service in self.client.services:
            if "Device Information" in str(service):
                for char in service.characteristics:
                    name = char.description
                    value = self.decode(await self.client.read_gatt_char(char.uuid))
                    device_info[name] = value
                    log.debug(f"  {name} {value}")

        return device_info

    async def load_characteristics(self):
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
    
            if "notify" in char.properties or "indicate" in char.properties:
                if name == "BATTERY_STATE":  
                    log.debug(f"starting {name} notifications")
                    await self.client.start_notify(char.uuid, self.battery_notification)
                    self.notifications.add(char.uuid)
                elif name == "LASER_STATE":
                    log.debug(f"starting {name} notifications")
                    await self.client.start_notify(char.uuid, self.laser_state_notification)
                    self.notifications.add(char.uuid)
                elif name == "GENERIC":
                    log.debug(f"starting {name} notifications")
                    await self.client.start_notify(char.uuid, self.generics.notification_callback)
                    self.notifications.add(char.uuid)
                elif name == "ACQUIRE":
                    log.debug(f"starting {name} notifications")
                    await self.client.start_notify(char.uuid, self.acquire_notification)
                    self.notifications.add(char.uuid)

    async def stop_notifications(self):
        for uuid in self.notifications:
            await self.client.stop_notify(uuid)

    def battery_notification(self, sender, data):
        charging = data[0] != 0
        perc = int(data[1])
        log.debug(f"received BATTERY_STATE notification: level {perc}%, charging {charging} (data {data})")

    def laser_state_notification(self, sender, data):
        status = self.parse_laser_state(data)
        log.debug(f"received LASER_STATE notification: sender {sender}, data {data}: {status}")

    async def read_char(self, name, min_len=None, quiet=False):
        uuid = self.get_uuid_by_name(name)
        if uuid is None:
            raise RuntimeError(f"invalid characteristic {name}")

        response = await self.client.read_gatt_char(uuid)
        if response is None:
            # responses may be optional on a write, but they're required on read
            raise RuntimeError("attempt to read {name} returned no data")

        if not quiet:
            log.debug(f"<< read_char({name}, min_len {min_len}): {response}")

        if min_len is not None and len(response) < min_len:
            raise RuntimeError(f"characteristic {name} returned insufficient data ({len(response)} < {min_len})")

        buf = bytearray()
        for byte in response:
            buf.append(byte)
        return buf

    async def write_char(self, name, data, quiet=False, callback=None):
        name = name.upper()
        uuid = self.get_uuid_by_name(name)
        if uuid is None:
            raise RuntimeError(f"invalid characteristic {name}")

        if name == "GENERIC":
            seq = self.generics.next_seq(callback)
            prefixed = [ seq ]
            for v in data:
                prefixed.append(v)
            data = prefixed

        if not quiet:
            code = self.code_by_name.get(name)
            log.debug(f">> write_char({name} 0x{code:02x}, {to_hex(data)})")

        if isinstance(data, list):
            data = bytearray(data)

        # MZ: I'm not sure why all writes require a response, but empirical 
        # testing indicates we reliably get randomly scrambled EEPROM contents 
        # without this.
        await self.client.write_gatt_char(uuid, data, response=True)

    ############################################################################
    # Timeouts
    ############################################################################

    async def set_power_watchdog_sec(self, sec):
        await self.write_char("GENERIC", self.generics.generate_write_request("POWER_WATCHDOG_SEC", sec))

    async def set_laser_warning_delay_sec(self, sec):
        await self.write_char("GENERIC", self.generics.generate_write_request("LASER_WARNING_DELAY_SEC", sec))

    ############################################################################
    # Laser Control
    ############################################################################

    async def set_laser_enable(self, flag):
        """
        @bug mode and type should be settable to 0xff (same with watchdog)
        """
        log.debug(f"setting laser enable {flag}")
        self.laser_enable = flag
        await self.sync_laser_state()

    async def sync_laser_state(self):
        # kludge for BLE FW <4.8.9
        laser_warning_delay_ms = self.laser_warning_delay_sec * 1000

        data = [ 0x00,                   # mode
                 0x00,                   # type
                 0x01 if self.laser_enable else 0x00, 
                 0x00,                   # laser watchdog (DISABLE)
                 (laser_warning_delay_ms >> 8) & 0xff,
                 (laser_warning_delay_ms     ) & 0xff ]
               # 0xff                    # status mask
        await self.write_char("LASER_STATE", data)

    def parse_laser_state(self, data):
        result = { 'laser_enable': False, 'laser_watchdog_sec': 0, 'status_mask': 0, 'laser_firing': False, 'interlock_closed': False }
        size = len(data)
        #if size > 0: result['mode']              = data[0]
        #if size > 1: result['type']              = data[1]
        if size > 2: result['laser_enable']       = data[2] != 0
        if size > 3: result['laser_watchdog_sec'] = data[3]
        # ignore bytes 4-5 (reserved)
        if size > 6: 
            result['status_mask']                 = data[6]
            result['interlock_closed']            = data[6] & 0x01 != 0
            result['laser_firing']                = data[6] & 0x02 != 0
        return result

    async def set_laser_tec_mode(self, mode: str):
        index = self.LASER_TEC_MODES.index(mode)
        await self.write_char("GENERIC", self.generics.generate_write_request("LASER_TEC_MODE", index))

    ############################################################################
    # Acquisition Parameters
    ############################################################################

    async def set_integration_time_ms(self, ms):
        # using dedicated Characteristic, although 2nd-tier version now exists
        log.debug(f"setting integration time to {ms}ms")
        data = [ 0x00,               # fixed
                 (ms >> 16) & 0xff,  # MSB
                 (ms >>  8) & 0xff,
                 (ms      ) & 0xff ] # LSB
        await self.write_char("INTEGRATION_TIME_MS", data)
        self.settings.state.prev_integration_time_ms = self.settings.state.integration_time_ms
        self.settings.state.integration_time_ms = ms
        return SpectrometerResponse(True)

    async def set_gain_db(self, db):
        # using dedicated Characteristic, although 2nd-tier version now exists
        log.debug(f"setting gain to {db}dB")
        msb = int(db) & 0xff
        lsb = int((db - int(db)) * 256) & 0xff
        await self.write_char("GAIN_DB", [msb, lsb])
        self.settings.state.gain_db = db
        return SpectrometerResponse(True)

    async def set_scans_to_average(self, n):
        log.debug(f"setting scan averaging to {n}")
        await self.write_char("GENERIC", self.generics.generate_write_request("SCANS_TO_AVERAGE", n))
        self.settings.state.scans_to_average = n
        return SpectrometerResponse(True)

    async def set_start_line(self, n):
        log.debug(f"setting start line to {n}")
        await self.write_char("GENERIC", self.generics.generate_write_request("START_LINE", n))
        return SpectrometerResponse(True)

    async def set_stop_line(self, n):
        log.debug(f"setting stop line to {n}")
        await self.write_char("GENERIC", self.generics.generate_write_request("STOP_LINE", n))
        return SpectrometerResponse(True)

    async def set_vertical_roi(self, pair):
        await self.set_start_line(pair[0])
        await self.set_stop_line (pair[1])
        return SpectrometerResponse(True)

    ############################################################################
    # Monitor
    ############################################################################

    async def get_battery_state(self):
        """
        These will be pushed automatically 1/min from Central, if-and-only-if
        no acquisition is occuring at the time of the scheduled event. However,
        at least some(?) clients (Bleak on MacOS) don't seem to receive the
        notifications until the next explicit read/write of a Characteristic 
        (any Chararacteristic? seemingly observed with ACQUIRE_CMD).
        """
        buf = await self.read_char("BATTERY_STATE", 2)
        log.debug(f"battery response: {buf}")
        return { 'charging': buf[0] != 0,
                 'perc': int(buf[1]) }

    async def get_laser_state(self):
        retval = {}
        for k in [ 'mode', 'type', 'enable', 'watchdog_sec', 'mask', 'interlock_closed', 'laser_firing' ]:
            retval[k] = 'UNKNOWN'
            
        buf = await self.read_char("LASER_STATE", 7)
        if len(buf) >= 4:
            retval.update({
                'mode':            buf[0],
                'type':            buf[1],
                'enable':          buf[2],
                'watchdog_sec':    buf[3] })

        if len(buf) >= 7: 
            retval.update({
                'mask':            buf[6],
                'interlock_closed':buf[6] & 0x01,
                'laser_firing':    buf[6] & 0x02 })

        return retval

    async def get_status(self):
        battery_state = await self.get_battery_state()
        battery_perc = f"{battery_state['perc']:3d}%"
        battery_charging = 'charging' if battery_state['charging'] else 'discharging'

        laser_state = await self.get_laser_state()
        laser_firing = laser_state['laser_firing']
        interlock_closed = 'closed (armed)' if laser_state['interlock_closed'] else 'open (safe)'

        amb_temp = await self.get_generic_value("GET_AMBIENT_TEMPERATURE")
        return f"Battery {battery_perc} ({battery_charging}), Laser {laser_firing}, Interlock {interlock_closed}, Amb {amb_temp}°C"

    ############################################################################
    # Generics
    ############################################################################

    # these methods belong in BLEDevice, rather than Generics, because they
    # use write_char
    
    async def get_generic_value(self, name):
        request = self.generics.generate_read_request(name)
        log.debug(f"get_generic: querying {name} ({to_hex(request)})")

        await self.write_char("GENERIC", request, callback=lambda data: self.generics.process_response(name, data))
        await self.generics.wait(name)

        value = self.generics.get_value(name)
        log.debug(f"get_generic: received {value}")
        return value

    ############################################################################
    # Spectra
    ############################################################################

    def acquire_data(self):
        log.debug("acquire_data: start")

        log.debug("acquire_data: calling get_spectrum")
        future = asyncio.run_coroutine_threadsafe(self.get_spectrum(), self.run_loop)
        spectrum = future.result()
        log.debug(f"acquire_data: back from get_spectrum (spectrum {spectrum})")

        log.debug(f"acquire_data: received spectrum of length {len(spectrum)}: {spectrum[:10]}")
        
        reading = Reading()
        reading.spectrum = spectrum

        log.debug(f"acquire_data: returning Reading {reading}")
        return SpectrometerResponse(data=reading)

    def parse_acquire_status(self, status, payload):
        if status not in self.ACQUIRE_STATUS_CODES:
            raise RuntimeError("ACQUIRE notification included unsupported status code 0x{status:02x}, payload {payload}")

        short, long = self.ACQUIRE_STATUS_CODES[status]
        msg = f"{short}: {long}"

        # special handling for status codes including payload
        if status == 32: 
            targetRatio = int(payload[0])
            msg += f" (target ratio {targetRatio}%)"
        elif status in [33, 34, 35, 36]:
            currentStep = int(payload[0] << 8 | payload[1])
            totalSteps  = int(payload[2] << 8 | payload[3])
            msg += f" (step {currentStep}/{totalSteps})" 

        return msg
    
    def acquire_notification(self, sender, data):
        ok = True
        if (len(data) < 3):
            raise RuntimeError(f"received invalid ACQUIRE notification of {len(data)} bytes: {data}")

        # first two bytes declare whether it's a status message or spectral data
        first_pixel = int((data[0] << 8) | data[1]) # big-endian int16

        if first_pixel == 0xffff:
            status = data[2]
            payload = data[3:]
            msg = self.parse_acquire_status(status, payload)
            log.debug(f"acquire_notification: {msg}")
            return

        ########################################################################
        # apparently it's spectral data
        ########################################################################

        # validate first_pixel
        if first_pixel != self.pixels_read:
            # raise RuntimeError(f"received first_pixel {first_pixel} when pixels_read {self.pixels_read}")
            log.error(f"received first_pixel {first_pixel} when pixels_read {self.pixels_read} (ignoring)")
            return

        spectral_data = data[2:]
        pixels_in_packet = int(len(spectral_data) / 2)

        for i in range(pixels_in_packet):
            # pixel intensities are little-endian uint16
            offset = i * 2
            intensity = int((spectral_data[offset+1] << 8) | spectral_data[offset])

            self.spectrum[self.pixels_read] = intensity
            self.pixels_read += 1

            if self.pixels_read == self.settings.pixels():
                # log.debug("read complete spectrum")
                if (i + 1 != pixels_in_packet):
                    raise RuntimeError(f"trailing pixels in packet")

    async def get_spectrum(self, spectrum_type=0):
        self.pixels_read = 0
        self.spectrum = [0] * self.settings.pixels()

        # send the ACQUIRE
        await self.write_char("ACQUIRE", [spectrum_type], quiet=True)

        # compute timeout
        timeout_ms = 4 * max(self.settings.state.prev_integration_time_ms, self.settings.state.integration_time_ms) * self.settings.state.scans_to_average + 6000 # 4sec latency + 2sec buffer

        # wait for spectral data to arrive
        keep_waiting = False
        start_time = datetime.now()
        while self.pixels_read < self.settings.pixels():
            if not keep_waiting and (datetime.now() - start_time).total_seconds() * 1000 > timeout_ms:
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
            
        return self.spectrum

    ############################################################################
    # EEPROM
    ############################################################################

    async def read_eeprom(self):
        await self.read_eeprom_pages()
        self.settings.eeprom.parse(self.pages)

    async def read_eeprom_pages(self):
        """ tweaked version of get_generic_value """
        start_time = datetime.now()

        self.eeprom = {}
        self.pages = []

        name = "EEPROM_DATA"
        for page in range(8):
            buf = bytearray()
            for subpage in range(4):
                
                request = self.generics.generate_read_request(name)
                request.append(0) # page is big-endian uint16
                request.append(page)
                request.append(subpage)

                log.debug(f"read_eeprom_pages: querying {name} ({to_hex(request)})")
                await self.write_char("GENERIC", request, callback=lambda data: self.generics.process_response(name, data))

                log.debug(f"read_eeprom_pages: waiting on {name}")
                await self.generics.wait(name)

                data = self.generics.get_value(name)
                log.debug(f"read_eeprom_pages: received page {page}, subpage {subpage}: {data}")

                for byte in data:
                    buf.append(byte)
            self.pages.append(buf)

        elapsed_sec = (datetime.now() - start_time).total_seconds()
        print(f"reading eeprom took {elapsed_sec:.2f} sec")

    def init_process_funcs(self): # -> dict[str, Callable[..., Any]] 
        f = {}

        # synchronous functions without arguments
        f["connect"]            = self.connect
        f["disconnect"]         = self.disconnect
        f["close"]              = self.disconnect
        f["acquire_data"]       = self.acquire_data

        # asynchronous functions with arguments
        f["scans_to_average"]   = lambda n:     asyncio.run_coroutine_threadsafe(self.set_scans_to_average(n),      self.run_loop)
        f["integration_time_ms"]= lambda ms:    asyncio.run_coroutine_threadsafe(self.set_integration_time_ms(ms),  self.run_loop)
        f["detector_gain"]      = lambda dB:    asyncio.run_coroutine_threadsafe(self.set_detector_gain(dB),        self.run_loop)
        f["laser_enable"]       = lambda flag:  asyncio.run_coroutine_threadsafe(self.set_laser_enable(flag),       self.run_loop)
        f["vertical_binning"]   = lambda roi:   asyncio.run_coroutine_threadsafe(self.set_vertical_roi(roi),        self.run_loop)

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

class Generic:
    """ encapsulates paired setter and getter accessors for a single attribute """

    def __init__(self, name, tier, setter, getter, size, epsilon):
        self.name    = name
        self.tier    = tier # 0, 1 or 2
        self.setter  = setter
        self.getter  = getter
        self.size    = size
        self.epsilon = epsilon

        self.value = None
        self.event = asyncio.Event()
    
    def serialize(self, value):
        data = []
        if self.name == "GAIN_DB":
            data.append(int(value) & 0xff)
            data.append(int((value - int(value)) * 256) & 0xff)
        else: 
            # assume big-endian uint[size]            
            for i in range(self.size):
                data.append((value >> (8 * (self.size - (i+1)))) & 0xff)
        return data

    def deserialize(self, data):
        # STEP SIXTEEN: deserialize the returned Generic response payload according to the attribute type
        if self.name == "GAIN_DB":
            return data[0] + data[1] / 256.0
        elif self.name == "EEPROM_DATA":
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
        request = [ 0xff for _ in range(self.tier) ]
        request.append(self.setter)
        request.extend(self.serialize(value))
        return request

    def generate_read_request(self):
        # STEP THREE: generate the "read request" payload for this attribute
        if self.getter is None:
            raise RuntimeError(f"Generic {self.name} is write-only")
        request = [ 0xff for _ in range(self.tier) ]
        request.append(self.getter)
        return request

class Generics:
    """ Facade to access all Generic attributes in the BLE interface """

    RESPONSE_ERRORS = [ 'OK', 'NO_RESPONSE_FROM_HOST', 'FPGA_READ_FAILURE', 'INVALID_ATTRIBUTE', 'UNSUPPORTED_COMMAND' ]

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

    def add(self, name, tier, setter, getter, size, epsilon=0):
        self.generics[name] = Generic(name, tier, setter, getter, size, epsilon)

    def generate_write_request(self, name, value):
        return self.generics[name].generate_write_request(value)

    def generate_read_request(self, name):
        # STEP TWO: generate the "read request" payload for the named attribute
        return self.generics[name].generate_read_request()

    def get_value(self, name):
        return self.generics[name].value

    async def wait(self, name):
        await self.generics[name].event.wait()
        self.generics[name].event.clear()

    async def process_acknowledgement(self, data, name):
        log.debug(f"received acknowledgement for {name}")
        generic = self.generics[name]
        generic.event.set()

    async def process_response(self, name, data):
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

    async def notification_callback(self, sender, data):
        # STEP EIGHT: we have received a response notification from the Generic Characteristic

        log.debug(f"received GENERIC notification from sender {sender}, data {to_hex(data)}")

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
            raise RuntimeError(f"GENERIC notification included error code {err} ({response_error}); data {to_hex(data)}")

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
        actual  = self.generics[name].value
        epsilon = self.generics[name].epsilon
        delta   = abs(actual - expected)
        return delta <= epsilon

