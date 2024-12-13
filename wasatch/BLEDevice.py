import os
import logging
import asyncio
from threading import Thread

from bleak import BleakClient

from wasatch.EEPROM               import EEPROM
from wasatch.Reading              import Reading
from wasatch.ControlObject        import ControlObject
from wasatch.InterfaceDevice      import InterfaceDevice
from wasatch.SpectrometerRequest  import SpectrometerRequest
from wasatch.SpectrometerSettings import SpectrometerSettings
from wasatch.SpectrometerResponse import SpectrometerResponse, ErrorLevel

log = logging.getLogger(__name__)

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
    (local_name, rssi and DeviceID (containing bleak.BLEDevice)) back to 
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
    only offers an async API (as well they should). Therefore we need to wrap
    calls to the Bleak objects using asyncio.

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
    with WrapperWorker to make it friendly for BLE, but maybe break something
    else.

    So WrapperWorker is going to stay "synchronous", and we will let 
    wasatch.BLEDevice manage its own asyncio.run_loop for turning WrapperWorker 
    requests into await-able async calls.

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
    
    @par bleak.BleakScanner

    bleak.BleakScanner issues a series of notifications including 
    (bleak.backends.device.BLEDevice, bleak.backends.scanner.AdvertisementData).

    AdvertisementData contains these useful attributes:
        - local_name            <-- WP-12345
        - rssi                  <-- 
        - manufacturer_data
        - platform_data
        - service_data
        - service_uuids
        - tx_power

    wasatch.DeviceFinderBLE generate a wasatch.DeviceID with both displayable
    (local_name/serial_number and rssi) and hidden (bleak_ble_device) attributes.

    wasatch.DeviceFinderBLE 

    To generate a bleak.BleakClient, we need the bleak.backends.device.BLEDevice from BleakScanner.
    Therefore, that handle needs to be 

    @todo consider https://github.com/django/asgiref#function-wrappers
    """

    WASATCH_SERVICE   = "D1A7FF00-AF78-4449-A34F-4DA1AFAF51BC"
   #DISCOVERY_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"

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

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        log.debug("init: start")

        super().__init__()
        self.device_id = device_id

        # Characteristics
        self.code_by_name = { "LASER_STATE":             0xff03,
                              "ACQUIRE":                 0xff04,
                              "BATTERY_STATE":           0xff09,
                              "GENERIC":                 0xff0a }
        self.name_by_uuid = { self.wrap_uuid(code): name for name, code in self.code_by_name.items() }

        #                  Name                        Lvl  Set   Get Size
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
        self.run_loop = asyncio.new_event_loop()

        log.debug("init: instantiating Thread")
        self.thread = Thread(target=self.make_run_loop, daemon=True)

        log.debug("init: starting thread")
        self.thread.start()

        log.debug("init: initializing process funcs")
        self.process_f = self.init_process_funcs()

        log.debug("init: done")

    def make_run_loop(self):
        log.debug("make_run_loop: setting run_loop")
        asyncio.set_event_loop(self.run_loop)
        
        log.debug("make_run_loop: running run_loop forever")
        self.run_loop.run_forever()

        log.debug("make_run_loop: done")

    def  __str__(self): return f"<BLEDevice device_id {self.device_id}>"
    def __hash__(self): return hash(str(self))
    def __repr__(self): return str(self)
    def  __eq__ (self, rhs): return hash(self) == hash(rhs)
    def  __ne__ (self, rhs): return str (self) != str(rhs)
    def  __lt__ (self, rhs): return str (self) <  str(rhs)

    def disconnect(self):
        log.debug("disconnect: start")
        try:
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.run_loop)
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

        ble_device = self.device_id.ble_device
        if ble_device is None:
            msg = f"can't connect w/o ble_device (given {self.device_id})"
            log.critical(f"connect: {msg}")
            return SpectrometerResponse(False, error_msg=msg)

        log.debug("connect_async: instantiating BleakClient")
        self.client = BleakClient(address_or_ble_device=ble_device, 
                                  disconnected_callback=self.disconnected_callback,
                                  timeout=self.args.search_timeout_sec)
        log.debug(f"connect_async: BleakClient instantiated: {self.client}")

        log.debug(f"connect_async: calling client.connect")
        await self.client.connect()

        log.debug(f"connect_async: grabbing device information")
        await self.load_device_information()

        log.debug(f"connect_async: loading Characteristics")
        await self.load_characteristics()

        log.debug("connect_async: processing retrieved EEPROM")
        self.settings.eeprom.parse(eeprom_contents)
        self.settings.update_wavecal()

        elapsed_sec = (datetime.now() - self.start_time).total_seconds()
        log.debug(f"connect_async: initial connection took {elapsed_sec:.2f} sec")

        log.debug("connect_async: connection successful")

        log.debug("connect_async: done")
        return SpectrometerResponse(True)

    ############################################################################
    # 
    #                           from ble-util.py
    # 
    ############################################################################

    def disconnected_callback(self):
        log.debug("disconnected")

    async def load_device_information(self):
        log.debug(f"address {self.client.address}")
        log.debug(f"mtu_size {self.client.mtu_size} bytes")

        device_info = {}
        for service in self.client.services:
            if "Device Information" in str(service):
                for char in service.characteristics:
                    name = char.description
                    value = self.decode(await self.client.read_gatt_char(char.uuid))
                    device_info[name] = value

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
                if name == "BATTERY_STATUS":
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

    async def stop_notifications(self):
        for uuid in self.notifications:
            await self.client.stop_notify(uuid)

    def battery_notification(self, sender, data):
        charging = data[0] != 0
        perc = int(data[1])
        log.debug(f"received BATTERY_STATUS notification: level {perc}%, charging {charging} (data {data})")

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
        self.last_integration_time_ms = self.integration_time_ms
        self.integration_time_ms = ms
        return SpectrometerResponse(True)

    async def set_gain_db(self, db):
        # using dedicated Characteristic, although 2nd-tier version now exists
        log.debug(f"setting gain to {db}dB")
        msb = int(db) & 0xff
        lsb = int((db - int(db)) * 256) & 0xff
        await self.write_char("GAIN_DB", [msb, lsb])
        return SpectrometerResponse(True)

    async def set_scans_to_average(self, n):
        log.debug(f"setting scan averaging to {n}")
        await self.write_char("GENERIC", self.generics.generate_write_request("SCANS_TO_AVERAGE", n))
        self.scans_to_average = n
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
        buf = await self.read_char("BATTERY_STATUS", 2)
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
        spectrum = asyncio.run_coroutine_threadsafe(self.get_spectrum, self.run_loop)
        log.debug(f"acquire_data: back from get_spectrum (spectrum {spectrum})")
        
        reading = Reading()
        reading.spectrum = spectrum

        log.debug("acquire_data: done")
        return SpectrometerResponse(data=reading)

    async def get_spectrum(self):
        header_len = 2 # the 2-byte first_pixel
        pixels_read = 0
        spectrum = [0] * self.pixels

        # determine which type of measurement
        if self.args.auto_raman: 
            arg = 2
        elif self.args.auto_dark: 
            arg = 1
        else: 
            arg = 0

        # send the ACQUIRE
        await self.write_char("ACQUIRE_SPECTRUM", [arg], quiet=True)

        # compute timeout
        timeout_ms = 4 * max(self.last_integration_time_ms, self.integration_time_ms) * self.scans_to_average + 6000 # 4sec latency + 2sec buffer
        start_time = datetime.now()

        # read the spectral data
        while pixels_read < self.pixels:
            if (datetime.now() - start_time).total_seconds() * 1000 > timeout_ms:
                raise RuntimeError(f"failed to read spectrum within timeout {timeout_ms}ms")

            # log.debug(f"requesting spectrum packet starting at pixel {pixels_read}")
            data = pixels_read.to_bytes(2, byteorder="big")
            await self.write_char("SPECTRUM_CMD", data, quiet=True)

            # log.debug(f"reading spectrum data (hopefully from pixels_read {pixels_read})")
            response = await self.read_char("READ_SPECTRUM", quiet=True)

            ####################################################################
            # validate response
            ####################################################################

            ok = True
            response_len = len(response)
            if (response_len < header_len):
                raise RuntimeError(f"received invalid READ_SPECTRUM response of {response_len} bytes (missing header): {response}")
            else:
                # check for official NAK
                first_pixel = int((response[0] << 8) | response[1]) # big-endian int16
                if first_pixel == 0xffff:
                    # this is a NAK, check for detail
                    ok = False
                    if len(response) > 2:
                        error_code = response[2]
                        if error_code < len(self.ACQUIRE_ERRORS):
                            error_str = self.ACQUIRE_ERRORS[error_code]
                            if error_str != "NONE":
                                raise RuntimeError(f"READ_SPECTRUM returned {error_str}")
                        else:
                            raise RuntimeError(f"unknown READ_SPECTRUM error_code {error_code}")
                    if len(response) > 3:
                        log.debug("trailing data after NAK error code: {to_hex(response)}")
                elif first_pixel != pixels_read:
                    # this still happens on 2.8.7
                    # log.debug(f"WARNING: received unexpected first pixel {first_pixel} (pixels_read {pixels_read})")
                    ok = False
                elif (response_len < header_len or response_len % 2 != 0):
                    raise RuntimeError(f"received invalid READ_SPECTRUM response of {response_len} bytes (odd length): {response}")

            if not ok:
                await asyncio.sleep(0.2)
                continue
            
            ####################################################################
            # apparently it was a valid response
            ####################################################################

            pixels_in_packet = int((response_len - header_len) / 2)
            for i in range(pixels_in_packet):
                # pixel intensities are little-endian uint16
                offset = header_len + i * 2
                intensity = int((response[offset+1] << 8) | response[offset])

                spectrum[pixels_read] = intensity
                pixels_read += 1

                if pixels_read == self.pixels:
                    # log.debug("read complete spectrum")
                    if (i + 1 != pixels_in_packet):
                        raise RuntimeError(f"trailing pixels in packet")

        if self.args.bin_2x2:
            # note, this needs updated for 633XS
            log.debug("applying 2x2 binning")
            binned = []
            for i in range(len(spectrum)-1):
                binned.append((spectrum[i] + spectrum[i+1]) / 2.0)
            binned.append(spectrum[-1])
            spectrum = binned
            
        return spectrum

    ############################################################################
    # EEPROM
    ############################################################################

    async def read_eeprom(self):
        await self.read_eeprom_pages()
        self.parse_eeprom_pages()
        self.generate_wavecal()

        # grab initial integration time (used for acquisition timeout)
        self.integration_time_ms = self.eeprom["startup_integration_time_ms"]

        msg  = f"Connected to {self.eeprom['model']} {self.eeprom['serial_number']} with {self.pixels} pixels "
        msg += f"from ({self.wavelengths[0]:.2f}, {self.wavelengths[-1]:.2f}nm)"
        if self.wavenumbers:
            msg += f" ({self.wavenumbers[0]:.2f}, {self.wavenumbers[-1]:.2f}cm⁻¹)"
        log.debug(msg)

    def display_eeprom(self):
        log.debug("EEPROM:")
        for name, value in self.eeprom.items():
            log.debug(f"  {name:30s} {value}")

    def generate_wavecal(self):
        self.pixels = self.eeprom["active_pixels_horizontal"]
        coeffs = [ self.eeprom[f"wavecal_c{i}"] for i in range(5) ]

        self.wavelengths = []
        for i in range(self.pixels):
            nm = (  coeffs[0] +
                  + coeffs[1] * i
                  + coeffs[2] * i * i
                  + coeffs[3] * i * i * i
                  + coeffs[4] * i * i * i * i)
            self.wavelengths.append(nm)

        self.excitation = self.eeprom["excitation_nm_float"]
        self.wavenumbers = None
        if self.excitation:
            self.wavenumbers = [ (1e7/self.excitation - 1e7/nm) for nm in self.wavelengths ]

    async def read_eeprom_pages(self):
        start_time = datetime.now()

        self.eeprom = {}
        self.pages = []

        cmd_uuid = self.get_uuid_by_name("EEPROM_CMD")
        data_uuid = self.get_uuid_by_name("EEPROM_DATA")
        for page in range(8):
            buf = bytearray()
            for subpage in range(4):
                await self.write_char("EEPROM_CMD", [page, subpage], quiet=True)
                #await self.client.write_gatt_char(cmd_uuid, page_ids, response = True)

                response = await self.read_char("EEPROM_DATA", quiet=True)
                #response = await self.client.read_gatt_char(data_uuid)
                for byte in response:
                    buf.append(byte)
            self.pages.append(buf)

        elapsed_sec = (datetime.now() - start_time).total_seconds()
        log.debug(f"reading eeprom took {elapsed_sec:.2f} sec")

    def parse_eeprom_pages(self):
        for name, field in self.eeprom_field_loc.items():
            self.unpack_eeprom_field(field.pos, field.data_type, name)

    def unpack_eeprom_field(self, address, data_type, field):
        page       = address[0]
        start_byte = address[1]
        length     = address[2]
        end_byte   = start_byte + length

        if page > len(self.pages):
            log.error("error unpacking EEPROM page %d, offset %d, len %d as %s: invalid page (field %s)" % ( 
                page, start_byte, length, data_type, field))
            return

        buf = self.pages[page]
        if buf is None or end_byte > len(buf):
            log.error("error unpacking EEPROM page %d, offset %d, len %d as %s: buf is %s (field %s)" % ( 
                page, start_byte, length, data_type, buf, field))
            return

        if data_type == "s":
            unpack_result = ""
            for c in buf[start_byte:end_byte]:
                if c == 0:
                    break
                unpack_result += chr(c)
        else:
            unpack_result = 0 
            try:
                unpack_result = struct.unpack(data_type, buf[start_byte:end_byte])[0]
            except Exception as ex:
                log.error("error unpacking EEPROM page %d, offset %d, len %d as %s (field %s): %s" % (page, start_byte, length, data_type, field, ex))
                return

        # log.debug(f"Unpacked page {page:02d}, offset {start_byte:02d}, len {length:02d}, datatype {data_type}: {unpack_result} {field}")
        self.eeprom[field] = unpack_result

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
        debug(f"get_callback: seq {seq} not found in callbacks")

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
        debug(f"received acknowledgement for {name}")
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

        debug(f"received GENERIC notification from sender {sender}, data {to_hex(data)}")

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

