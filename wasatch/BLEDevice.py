import os
import logging
import asyncio
import threading

from bleak import BleakClient

from wasatch.EEPROM               import EEPROM
from wasatch.Reading              import Reading
from wasatch.ControlObject        import ControlObject
from wasatch.InterfaceDevice      import InterfaceDevice
from wasatch.SpectrometerRequest  import SpectrometerRequest
from wasatch.SpectrometerSettings import SpectrometerSettings
from wasatch.SpectrometerResponse import SpectrometerResponse, ErrorLevel

log = logging.getLogger(__name__)

class Generic:
    """ 
    Encapsulates paired setter and getter accessors for a single Generic
    attribute, including any per-attribute storage and processing like
    """

    def __init__(self, name, tier, setter, getter, size, epsilon):
        self.name    = name
        self.tier    = tier         # 0, 1 or 2
        self.setter  = setter
        self.getter  = getter
        self.size    = size
        self.epsilon = epsilon

        self.value = None
        self.event = asyncio.Event()
    
    def serialize(self, value):
        data = []
        if self.name == "CCD_GAIN":
            data.append(int(value) & 0xff)
            data.append(int((value - int(value)) * 256) & 0xff)
        else: 
            # assume big-endian uint[size]            
            for i in range(self.size):
                data.append((value >> (8 * (self.size - (i+1)))) & 0xff)
        return data

    def deserialize(self, data):
        """ Updates the internal value from the deserialized data """
        if self.name == "CCD_GAIN":
            self.value = data[0] + data[1] / 256.0
        else:
            self.value = 0
            for byte in data:
                self.value <<= 8
                self.value |= byte

    def generate_write_request(self, value):
        if self.setter is None:
            raise RuntimeError(f"Generic {self.name} is read-only")
        request = [ 0xff for _ in range(self.tier) ]
        request.append(self.setter)
        request.extend(self.serialize(value))
        return request

    def generate_read_request(self):
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
            self.callbacks[self.seq] = callback
        return self.seq

    def get_callback(self, seq):
        return self.callbacks.pop(seq)

    def add(self, name, tier, setter, getter, size, epsilon=0):
        self.generics[name] = Generic(name, tier, setter, getter, size, epsilon)

    def generate_write_request(self, name, value):
        return self.generics[name].generate_write_request(value)

    def generate_read_request(self, name):
        return self.generics[name].generate_read_request()

    def get_value(self, name):
        return self.generics[name].value

    async def wait(self, name):
        await self.generics[name].event.wait()
        self.generics[name].event.clear()

    async def process_response(self, name, data):
        generic = self.generics[name]
        generic.deserialize(data)
        generic.event.set()

    async def notification_callback(self, sender, data):
        # log.debug(f"{datetime.now()} received GENERIC notification from sender {sender}, data {to_hex(data)}")
        if len(data) < 3:
            raise RuntimeError(f"received GENERIC response with only {len(data)} bytes")

        seq, err, result = data[0], data[1], data[2:]

        response_error = self.RESPONSE_ERRORS[err]
        if response_error != "OK":
            raise RuntimeError(f"GENERIC notification included error code {err} ({response_error}); data {to_hex(data)}")

        # pass the response data, minus the sequence and error-code header, to 
        # the registered callback function for that sequence ID
        callback = self.get_callback(seq)
        await callback(result)
        
    def equals(self, name, expected):
        actual  = self.generics[name].value
        epsilon = self.generics[name].epsilon
        delta   = abs(actual - expected)
        # print("Generics.equals: name {name}, actual {actual}, expected {expected}, delta {delta}, epsilon {epsilon}")
        return delta <= epsilon

class BLEDevice(InterfaceDevice):
    """
    This is the basic implementation of our interface with BLE Spectrometers.

    ##########################################################################
    This class adopts the external device interface structure.
    This involves receiving a request through the handle_request function.
    A request is processed based on the key in the request.
    The processing function passes the commands to the requested device.
    Once it receives a response from the connected device it then passes that
    back up the chain.
                               Enlighten Request
                                       |
                                handle_requests
                                       |
                                 ------------
                                /   /  |  \  \
             { get_laser status, acquire, set_laser_watchdog, etc....}
                                \   \  |  /  /
                                 ------------
                                       |
                               {self.bleak_call}
    ############################################################################
    """

    WASATCH_SERVICE   = "D1A7FF00-AF78-4449-A34F-4DA1AFAF51BC"
   #DISCOVERY_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"

    ############################################################################
    # static attributes and methods (for sharing with DeviceFinderBLE)
    ############################################################################

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        super().__init__()
        self.device_id = device_id
        self.label = "BLE Device"

        # MZ: what do bus and address represent in the BLE protocol?
        self.bus = self.device_id.bus
        self.address = self.device_id.address

        self.is_ble = True
        self.is_andor = lambda : False 
        self.sum_count = 0
        self.performing_acquire = False
        self.disconnect = False
        self.total_pixels_read = 0
        self.session_reading_count = 0
        self.settings = SpectrometerSettings(self.device_id)
        self.settings.eeprom.detector = "ble" # MZ: why?
        self.retry_count = 0
        self.pixels_read = 0

        self.process_f = self.init_process_funcs()

        # create an async thread in which to run THIS Bluetooth spectrometer's 
        # methods
        #
        # MZ: I'm not clear if this would be needed when running inside a 
        #     WrapperWorker thread, but it probably is needed when running 
        #     BLEDevice from a blocking client.
        self.loop = asyncio.new_event_loop()

        self.code_by_name = { "INTEGRATION_TIME_MS":     0xff01, 
                              "GAIN_DB":                 0xff02,
                              "LASER_STATE":             0xff03,
                              "ACQUIRE_SPECTRUM":        0xff04,
                              "SPECTRUM_CMD":            0xff05,
                              "READ_SPECTRUM":           0xff06,
                              "EEPROM_CMD":              0xff07,
                              "EEPROM_DATA":             0xff08,
                              "BATTERY_STATUS":          0xff09,
                              "GENERIC":                 0xff0a }
        self.name_by_uuid = { self.wrap_uuid(code): name for name, code in self.code_by_name.items() }

        self.generics = Generics()                # Tier Setter Getter Size
        self.generics.add("LASER_TEC_MODE",            0, 0x84,  0x85, 1)
        self.generics.add("CCD_GAIN",                  0, 0xb7,  0xc5, 2, epsilon=0.01)
        self.generics.add("INTEGRATION_TIME_MS",       0, 0xb2,  0xbf, 3)
        self.generics.add("LASER_WARNING_DELAY_SEC",   0, 0x8a,  0x8b, 1)
        self.generics.add("START_LINE",                1, 0x21,  0x22, 2)
        self.generics.add("STOP_LINE",                 1, 0x23,  0x24, 2)
        self.generics.add("AMBIENT_TEMPERATURE_DEG_C", 1, None,  0x2a, 1)
        self.generics.add("POWER_WATCHDOG_SEC",        1, 0x30,  0x31, 2)
        self.generics.add("SCANS_TO_AVERAGE",          1, 0x62,  0x63, 2)

    def __str__ (self): return f"<BLEDevice {self.device_id.name} {self.device_id.address}>"
    def __hash__(self): return hash(str(self))
    def __repr__(self): return str(self)
    def __eq__(self, rhs): return hash(self) == hash(rhs)
    def __ne__(self, rhs): return str (self) != str(rhs)
    def __lt__(self, rhs): return str (self) <  str(rhs)

    ###############################################################
    # Private Methods
    ###############################################################

    def init_process_funcs(self): # -> dict[str, Callable[..., Any]] 
        process_f = {}

        process_f["connect"]            = self.connect
        process_f["disconnect"]         = self.close
        process_f["close"]              = self.close
        process_f["acquire_data"]       = self.acquire_data

        # MZ: why are these different? do they need to be? do the others?
        process_f["scans_to_average"]   = lambda n:     asyncio.run_coroutine_threadsafe(self.set_scans_to_average(n),      self.loop)
        process_f["integration_time_ms"]= lambda ms:    asyncio.run_coroutine_threadsafe(self.set_integration_time_ms(ms),  self.loop)
        process_f["detector_gain"]      = lambda dB:    asyncio.run_coroutine_threadsafe(self.set_detector_gain(dB),        self.loop)
        process_f["laser_enable"]       = lambda flag:  asyncio.run_coroutine_threadsafe(self.set_laser_enable(flag),       self.loop)
        process_f["vertical_binning"]   = lambda roi:   asyncio.run_coroutine_threadsafe(self.set_vertical_roi(roi),        self.loop)

        return process_f

    async def disconnect(self) -> SpectrometerResponse:
        await self.client.disconnect()
        return SpectrometerResponse(True)

    async def _connect_spec(self) -> SpectrometerResponse:
        log.debug(f"_connect_spec: instantiating BleakClient from device_id {self.device_id} address {self.device_id.address}")
        self.client = BleakClient(self.device_id.address)

        log.debug(f"_connect_spec: awaiting client.connect")
        await self.client.connect()

        log.debug(f"Connected: {self.client.is_connected}")
        return SpectrometerResponse(True)

    async def _set_gain(self, value: int) -> SpectrometerResponse:
        log.debug(f"BLE setting gain to {value}")
        #value_bytes = int(value).to_bytes(2, byteorder='big')
        try:
            msb = int(value)
            lsb = int((value - int(value)) * 256) & 0xff
            value_bytes = ((msb << 8) | lsb).to_bytes(2, byteorder='big')
            await self.client.write_gatt_char(GAIN_UUID, value_bytes, response = True)
        except Exception as e:
            log.error(f"Error trying to write gain {e}")
            return SpectrometerResponse(False, error_msg="error trying to write gain", error_lvl=ErrorLevel.medium)
        return SpectrometerResponse(True)

    async def _ble_acquire(self) -> SpectrometerResponse:
        if self.disconnect:
            log.debug("ble spec is set to disconnect, returning False")
            return SpectrometerResponse(False)
        _ = await self.client.write_gatt_char(DEVICE_ACQUIRE_UUID, bytes(0), response = True)
        pixels = self.settings.eeprom.active_pixels_horizontal
        request_retry = False
        averaging_enabled = (self.settings.state.scans_to_average > 1)
        if self.pixels_read == 0:
            self.spectrum = [0 for pix in range(pixels)]

        if averaging_enabled and not self.settings.state.free_running_mode:
            self.sum_count = 0
            loop_count = self.settings.state.scans_to_average
        else:
            # we're in free-running mode
            loop_count = 1

        reading = None
        for _ in range(0, loop_count):
            reading = Reading(self.device_id)
            reading.integration_time_ms = self.settings.state.integration_time_ms
            reading.laser_power_perc    = self.settings.state.laser_power_perc
            reading.laser_power_mW      = self.settings.state.laser_power_mW
            reading.laser_enabled       = self.settings.state.laser_enabled
            header_len = 2
            if self.disconnect:
                log.debug("ble spec is set to disconnect, returning False")
                log.info("Disconnecting, stopping spectra acquire and returning None")
                return SpectrometerResponse(False)
            if request_retry:
                self.retry_count += 1
                if (self.retry_count > MAX_RETRIES):
                    log.error(f"giving up after {MAX_RETRIES} retries")
                    return SpectrometerResponse(False)

            delay_ms = int(self.retry_count**5)

            # if this is the first retry, assume that the sensor was
            # powered-down, and we need to wait for some throwaway
            # spectra 
            if (self.retry_count == 1):
                delay_ms = int(self.settings.state.integration_time_ms * THROWAWAY_SPECTRA)

            log.error(f"Retry requested, so waiting for {delay_ms}ms")
            if self.disconnect:
                log.debug("ble spec is set to disconnect, returning False")
                return SpectrometerResponse(False)
            await asyncio.sleep(delay_ms)

            request_retry = False

            log.debug(f"requesting spectrum packet starting at pixel {self.pixels_read}")
            request = self.pixels_read.to_bytes(2, byteorder="big")
            await self.client.write_gatt_char(SPECTRUM_PIXELS_UUID, request, response = True)

            log.debug(f"reading spectrumChar (pixelsRead {self.pixels_read})")
            response = await self.client.read_gatt_char(READ_SPECTRUM_UUID)

            # make sure response length is even, and has both header and at least one pixel of data
            response_len = len(response)
            if (response_len < header_len or response_len % 2 != 0):
                log.error(f"received invalid response of {response_len} bytes")
                request_retry = True
                continue
            if self.disconnect:
                log.debug("ble spec is set to disconnect, returning False")
                return SpectrometerResponse(False)

            # firstPixel is a big-endian UInt16
            first_pixel = int((response[0] << 8) | response[1])
            if (first_pixel > 2048 or first_pixel < 0):
                log.error(f"received NACK (first_pixel {first_pixel}, retrying")
                request_retry = True
                continue

            pixels_in_packet = int((response_len - header_len) / 2)

            log.debug(f"received spectrum packet starting at pixel {first_pixel} with {pixels_in_packet} pixels")

            for i in range(pixels_in_packet):
                # pixel intensities are little-endian UInt16
                offset = header_len + i * 2
                intensity = int((response[offset+1] << 8) | response[offset])
                self.spectrum[self.pixels_read] = intensity
                if self.disconnect:
                    log.debug("ble spec is set to disconnect, returning False")
                    return SpectrometerResponse(False)

                self.pixels_read += 1

                if self.pixels_read == pixels:
                    log.debug("read complete spectrum")
                    self.session_reading_count += 1
                    if (i + 1 != pixels_in_packet):
                        log.error(f"ignoring {pixels_in_packet - (i + 1)} trailing pixels")
                    break
            response = None
            #### WHILE LOOP ENDS HERE ####
            for i in range(4):
                self.spectrum[i] = self.spectrum[4]

            self.spectrum[pixels-1] = self.spectrum[pixels-2]

            reading.session_count = self.session_reading_count
            reading.sum_count = self.sum_count
            log.debug("Spectrometer.takeOneAsync: returning completed spectrum")
            reading.spectrum = self.spectrum

            if not reading.failure:
                if averaging_enabled:
                    if self.sum_count == 0:
                        self.summed_spectra = [float(i) for i in reading.spectrum]
                    else:
                        log.debug("device.take_one_averaged_reading: summing spectra")
                        for i in range(len(self.summed_spectra)):
                            self.summed_spectra[i] += reading.spectrum[i]
                    self.sum_count += 1
                    log.debug("device.take_one_averaged_reading: summed_spectra : %s ...", self.summed_spectra[0:9])

            # have we completed the averaged reading?
            if averaging_enabled:
                if self.sum_count >= self.settings.state.scans_to_average:
                    reading.spectrum = [ x / self.sum_count for x in self.summed_spectra ]
                    log.debug("device.take_one_averaged_reading: averaged_spectrum : %s ...", reading.spectrum[0:9])
                    reading.averaged = True

                    # reset for next average
                    self.summed_spectra = None
                    self.sum_count = 0
            else:
                # if averaging isn't enabled...then a single reading is the
                # "averaged" final measurement (check reading.sum_count to confirm)
                reading.averaged = True

        response = SpectrometerResponse()
        response.data = reading
        response.progress = int(round(100 * self.pixels_read / pixels, 0))
        if response.progress >= 100:
            self.spectrum = [0 for pix in range(pixels)]
            self.pixels_read = 0

        return response

    async def _get_eeprom(self) -> list[list[int]]:
        log.debug("Trying BLE eeprom read")
        pages = []
        for i in range(EEPROM.MAX_PAGES):
            buf = bytearray()
            for j in range(EEPROM.SUBPAGE_COUNT):
                page_ids = bytearray([i, j])
                log.debug(f"Writing SELECT_EEPROM_PAGE(page {i}, subpage {j})")
                _ = await self.client.write_gatt_char(SELECT_EEPROM_PAGE_UUID, page_ids, response = True)
                log.debug("Attempting to read page data")
                response = await self.client.read_gatt_char(READ_EEPROM_UUID)
                for byte in response:
                    buf.append(byte)
            pages.append(buf)
        return pages

    def _get_default_data_dir(self) -> str:
        return os.getcwd()

    ###############################################################
    # Public Methods
    ###############################################################

    def connect(self) -> SpectrometerResponse:
        log.debug("connect: creating thread")
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

        log.debug("connect: starting thread")
        self.thread.start()

        log.debug("running _connect_spec on thread")
        future = asyncio.run_coroutine_threadsafe(self._connect_spec(), self.loop)

        log.debug("awaiting _connect_spec result")
        future.result()

        log.debug("BLEDevice succeeded in connection")

        log.debug("running _get_eeprom on thread")
        future = asyncio.run_coroutine_threadsafe(self._get_eeprom(), self.loop)

        log.debug("awaiting _get_eeprom result")
        eeprom_contents = future.result()

        log.debug("processing retrieved EEPROM")
        self.settings.eeprom.parse(eeprom_contents)
        self.settings.update_wavecal()
        self.label = f"{self.settings.eeprom.serial_number} ({self.settings.eeprom.model})"

        log.debug("connection successful")
        return SpectrometerResponse(True)

    def acquire_data(self) -> SpectrometerResponse:
        if self.performing_acquire:
            return SpectrometerResponse(True)
        if self.disconnect:
            log.debug("ble spec is set to disconnect, returning False")
            return SpectrometerResponse(False)
        self.performing_acquire = True
        self.session_reading_count += 1
        future = asyncio.run_coroutine_threadsafe(self._ble_acquire(), self.loop)
        self.performing_acquire = False
        result = future.result()
        return result

    # both this and change_setting are needed
    # change_device_setting is called by the controller
    # while change_setting is being called by the wrapper_worker
    def change_device_setting(self, setting: str, value: int) -> None:
        control_object = ControlObject(setting, value)
        log.debug("BLEDevice.change_setting: %s", control_object)

        # Since scan averaging lives in WasatchDevice, handle commands which affect
        # averaging at this level
        if control_object.setting == "scans_to_average":
            self.sum_count = 0
            self.settings.state.scans_to_average = int(value)
            return
        else:
            req = SpectrometerRequest(setting, args=[value])
            self.handle_requests([req])

    def get_pid_hex(self) -> str:
        return str(hex(self.pid))[2:]

    def get_vid_hex(self) -> str:
        return str(self.vid)

    def to_dict(self) -> str:
        return str(self)

    def close(self) -> None:
        log.info("BLE close called, trying to disconnect spec")
        self.disconnect = True

        # MZ: I guess this is the equivalent to 'await' in a non-async method?
        future = asyncio.run_coroutine_threadsafe(self.disconnect(), self.loop)
        _ = future.result()

        self.loop.stop()

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
        print(f"{datetime.now()} received BATTERY_STATUS notification: level {perc}%, charging {charging} (data {data})")

    def laser_state_notification(self, sender, data):
        status = self.parse_laser_state(data)
        print(f"{datetime.now()} received LASER_STATE notification: sender {sender}, data {data}: {status}")

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
        print(f"setting laser enable {flag}")
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
        print(f"setting integration time to {ms}ms")
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
        print(f"setting gain to {db}dB")
        msb = int(db) & 0xff
        lsb = int((db - int(db)) * 256) & 0xff
        await self.write_char("GAIN_DB", [msb, lsb])
        return SpectrometerResponse(True)

    async def set_scans_to_average(self, n):
        print(f"setting scan averaging to {n}")
        await self.write_char("GENERIC", self.generics.generate_write_request("SCANS_TO_AVERAGE", n))
        self.scans_to_average = n
        return SpectrometerResponse(True)

    async def set_start_line(self, n):
        print(f"setting start line to {n}")
        await self.write_char("GENERIC", self.generics.generate_write_request("START_LINE", n))
        return SpectrometerResponse(True)

    async def set_stop_line(self, n):
        print(f"setting stop line to {n}")
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
                        print("trailing data after NAK error code: {to_hex(response)}")
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
        print(msg)

    def display_eeprom(self):
        print("EEPROM:")
        for name, value in self.eeprom.items():
            print(f"  {name:30s} {value}")

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
            print("error unpacking EEPROM page %d, offset %d, len %d as %s: invalid page (field %s)" % ( 
                page, start_byte, length, data_type, field))
            return

        buf = self.pages[page]
        if buf is None or end_byte > len(buf):
            print("error unpacking EEPROM page %d, offset %d, len %d as %s: buf is %s (field %s)" % ( 
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
                print("error unpacking EEPROM page %d, offset %d, len %d as %s (field %s): %s" % (page, start_byte, length, data_type, field, ex))
                return

        # log.debug(f"Unpacked page {page:02d}, offset {start_byte:02d}, len {length:02d}, datatype {data_type}: {unpack_result} {field}")
        self.eeprom[field] = unpack_result

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
