import logging
import struct
import socket

from .SpectrometerResponse  import SpectrometerResponse, ErrorLevel
from .SpectrometerSettings  import SpectrometerSettings
from .InterfaceDevice       import InterfaceDevice
from .DeviceID              import DeviceID
from .Reading               import Reading
from .ROI                   import ROI

log = logging.getLogger(__name__)

class TCPDevice(InterfaceDevice):

    SUCCESS = 0x00 # byte response from setter and command (ACQUIRE, DISCONNECT) opcodes

    ############################################################################
    # lifecycle
    ############################################################################

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        super().__init__()

        self.device_id = device_id
        if device_id.type != "TCP":
            raise RuntimeError("TCPDevice can only be constructed with TCP DeviceID")

        self.addr = device_id.address
        self.port = device_id.port

        self.process_f = self.init_process_funcs()

        self.reset()

    def reset(self):
        self.sock = None
        self.mode = "ascii"
    
    def connect(self):
        """
        This method is called by WrapperWorker.run. At the end of the method,
        an InterfaceDevice should have a non-null .settings with a populated 
        SpectrometerSettings.
        """
        log.debug(f"connect: trying to connect to {self.device_id}")
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3)
            self.sock.connect((self.addr, self.port))
        except:
            log.error(f"connect: failed to connect to {self.addr} port {self.port}", exc_info=1)
            return SpectrometerResponse(error_lvl=ErrorLevel.high, error_msg=f"Failed to connect to {self.device_id}")

        log.debug(f"connect: connected to {self.device_id}")

        self.set_binary_mode()

        self.protocol_version = self.get_protocol_version()
        log.debug(f"Protocol version: {self.protocol_version}")

        # use this to hold settings that already have a standard place
        self.settings = SpectrometerSettings()

        self.settings.state.microcontroller_firmware_version = self.get_firmware_version()

        # this is not currently a SpectrometerSettings attribute
        self.product_id = self.get_product_id()
        log.debug(f"Product ID: {self.product_id}")

        self.settings.eeprom.model = self.get_model_name()
        log.debug(f"Model Name: {self.settings.eeprom.model}")

        self.settings.eeprom.serial_number = self.get_serial_number()
        log.debug(f"Serial Number: {self.settings.eeprom.serial_number}")

        self.settings.eeprom.active_pixels_horizontal = self.get_line_length()
        self.settings.eeprom.active_pixels_vertical = self.get_line_count()
        log.debug(f"Detector Size: {self.settings.pixels()} (H) x {self.settings.eeprom.active_pixels_vertical} (V)")

        self.settings.eeprom.excitation_nm_float = self.get_excitation()
        log.debug(f"Excitation: {self.settings.excitation()}")

        # read wavecal
        self.settings.eeprom.wavecal_coeffs = []
        for i in range(5):
            self.settings.eeprom.wavecal_coeffs.append(self.get_wavecal_coeff(i))
        self.settings.update_wavecal()

        return SpectrometerResponse(True)

    def disconnect(self):
        if self.sock:
            self.send_cmd(0xff, 0xaa14, label="DISCONNECT")
        self.reset()

    ############################################################################
    # ascii protocol
    ############################################################################

    def set_binary_mode(self):
        log.debug("awaiting ready")
        data = self.read_data(3)
        log.debug("<< " + "".join([chr(c) for c in data]))
        for a, b in zip(data, bytes("OK\n", encoding='ascii')):
            if a != b:
                raise(RuntimeError("failed handshaking"))

        log.debug("setting binary mode")
        self.send_string("BIN\n")
        if 0 == self.read_data(1)[0]:
            self.mode = "binary"
        else:
            raise(RuntimeError("failed to set BIN mode"))

    ############################################################################
    # binary protocol
    ############################################################################

    def get_protocol_version(self):
        data = self.get_cmd(0xff, 0xaa13, length=4, label="GET_PROTOCOL_VERSION")
        return ".".join([str(v) for v in data])

    def get_firmware_version(self):
        data = self.get_cmd(0xc0, length=4, label="GET_FIRMWARE_VERSION")
        return ".".join([str(v) for v in data])

    def get_product_id(self):
        data = self.get_cmd(0xff, 0xaa01, length=4, label="GET_PRODUCT_ID")
        vid = (data[0] << 8) | data[1]
        pid = (data[2] << 8) | data[3]
        return f"0x{vid:04x}:0x{pid:04x}"

    def get_model_name(self):
        return self.get_cmd(0xff, 0xaa0b, str_len=32, label="GET_MODEL_NAME")

    def get_serial_number(self):
        return self.get_cmd(0xff, 0xaa09, str_len=16, label="GET_SERIAL_NUMBER")

    def get_line_length(self):
        return self.get_cmd(0x03, lsb_len=2, label="GET_LINE_LENGTH")

    def get_line_count(self):
        return self.get_cmd(0xff, 0xaa10, lsb_len=2, label="GET_LINE_COUNT")

    def get_excitation(self):
        data = self.get_cmd(0xff, 0xaa12, length=4, label="GET_EXCITATION")
        return self.to_float32(data, label="GET_EXCITATION")

    def get_wavecal_coeff(self, exponent):
        label = f"GET_WAVECAL_COEFF({exponent})"
        data = self.get_cmd(0xff, 0xaa0d, exponent, length=4, label=label)
        return self.to_float32(data, label=label)

    def set_integration_time_ms(self, ms):
        lsw = ms & 0xffff
        msb = (ms >> 16) & 0xff
        self.send_cmd(0xb2, lsw, msb, label="SET_INTEGRATION_TIME_MS")

    def get_integration_time_ms(self):
        return self.get_cmd(0xbf, lsb_len=3, label="GET_INTEGRATION_TIME_MS")

    def set_detector_gain(self, db):
        # word = self.float_to_uint16(db, label="SET_DETECTOR_GAIN")
        tenx = int(round(10 * db, 0))

        # needs to be sent big-endian
        lsb = tenx & 0xff
        msb = (tenx >> 8) & 0xff
        word = (lsb << 8) | msb

        self.send_cmd(0xb7, word, label="SET_DETECTOR_GAIN")

    def get_detector_gain(self):
        tenx = self.get_cmd(0xc5, lsb_len=2, label="GET_DETECTOR_GAIN")
        return int(round(float(data) / 10, 1))

    def set_vertical_roi(self, roi):
        if isinstance(roi, ROI):
            self.set_start_line(roi.start)
            self.set_stop_line(roi.end)
        else:
            self.set_start_line(roi[0])
            self.set_stop_line(roi[1])

    def set_start_line(self, line):
        self.send_cmd(0xff, 0x21, line, label="SET_START_LINE")

    def get_start_line(self):
        return self.get_cmd(0xff, 0x22, lsb_len=2, label="GET_START_LINE")

    def set_stop_line(self, line):
        self.send_cmd(0xff, 0x23, line, label="SET_STOP_LINE")

    def get_stop_line(self):
        return self.get_cmd(0xff, 0x24, lsb_len=2, label="GET_STOP_LINE")

    def get_spectrum(self):
        self.send_cmd(0xad, label="ACQUIRE")
        data = self.read_data(self.settings.pixels() * 2, label="GET_SPECTRUM", quiet=True)

        spectrum = []
        for i in range(self.settings.pixels()):
            intensity = data[i*2] | (data[i*2 + 1] << 8) # little-endian
            spectrum.append(intensity)

        return spectrum

    def not_implemented(self, label):
        log.debug("{label} is not implemented for TCP spectrometers")

    # not implemented:
    #
    # - SET_AREA_SCAN_MODE
    # - SET_IMX_SENSOR_MODE
    # - GET_IMX_SENSOR_MODE
    # - SET_START_PIXEL
    # - GET_START_PIXEL
    # - SET_STOP_PIXEL
    # - GET_STOP_PIXEL
    # - SET_SERIAL_NUMBER
    # - SET_MODEL_NAME
    # - SET_WAVELENGTH_COEFF
    # - SET_BAD_PIXEL
    # - GET_BAD_PIXEL

    ############################################################################
    # operations
    ############################################################################

    def acquire_data(self):
        reading = Reading(self.device_id)

        spectrum = self.get_spectrum()

        reading.spectrum = spectrum

        return SpectrometerResponse(data=reading)

    ############################################################################
    # utility
    ############################################################################

    def to_hex(self, data):
        return "[ " + " ".join([f"0x{v:02x}" for v in data]) + " ]"

    def get_cmd(self, bRequest, wValue=0, wIndex=0, lsb_len=None, msb_len=None, str_len=None, length=None, label=None):
        """
        bRequest is required, and one length parameter must be provided. The
        length parameter used determines the return data format.
        """
        # send outgoing request
        packet = MessagePacket(bRequest, wValue, wIndex)
        serialized = packet.serialize()
        log.debug(f">> {packet} ({label})")
        log.debug(f">> {self.to_hex(serialized)} ({label})")
        self.sock.sendall(serialized)

        # determine response length
        if   lsb_len: expected_len = lsb_len
        elif msb_len: expected_len = msb_len
        elif str_len: expected_len = str_len
        elif length:  expected_len = length
        else:
            raise(RuntimeError(f"get_cmd called without length parameter [{label}]"))

        data = self.read_data(expected_len, label=label)

        # demarshall response
        value = 0
        if lsb_len:
            for i, byte in enumerate(data):
                value |= (byte << (i * 8))  # little-endian
        elif msb_len:
            for byte in data:
                value = (value << 8) | byte # big-endian
        elif str_len:
            value = ""
            for c in data:
                if c == 0:                  # null-terminated
                    break
                value += chr(c)
        else:
            value = data                    # raw data

        # log.debug(f"get_cmd: received {value} ({label})")
        return value

    def send_cmd(self, bRequest, wValue=0, wIndex=0, payload=None, readback_len=None, label=None):
        packet = MessagePacket(bRequest, wValue, wIndex, payload)
        data = packet.serialize()
        log.debug(f">> {packet} ({label})")
        log.debug(f">> {self.to_hex(data)} ({label})")
        self.sock.sendall(data)

        if readback_len is None:
            # by default, read back one byte
            response = self.read_data(1)[0]
            if response != self.SUCCESS:
                raise(RuntimeError(f"send_cmd received {response} from {packet} [{label}]"))
            log.debug(f"<< SUCCESS ({label})")
        else:
            response = self.read_data(readback_len, label=label)
            log.debug(f"<< {self.to_hex(response)} ({label})")
            return response

    def read_data(self, length, label=None, quiet=False):
        if length == 0:
            return

        response = []
        while True:
            data = self.sock.recv(1)
            if len(data):
                response.append(data[0])
                if length == len(response):
                    retval = bytes(response)
                    if quiet:
                        log.debug(f"<< ({length} bytes) ({label})")
                    else:
                        log.debug(f"<< {self.to_hex(response)} ({length} bytes) ({label})")
                    return retval

    def send_string(self, msg, length=None, label=None):
        if length is None:
            length = len(msg)
        data = []
        for i in range(length):
            if i < len(msg):
                data.append(ord(msg[i]))
            else:
                data.append(0)
        log.debug(f">> {data} ({msg.strip()}) ({label})")
        self.sock.sendall(bytes(data))

    def to_float32(self, data, label=None):
        value = struct.unpack('f', data)[0]
        return value

    def float_to_uint16(self, f, label=None):
        msb = int(round(f, 5)) & 0xff
        lsb = int((f - msb) * 256) & 0xff
        value = (msb << 8) | lsb
        return value

    def init_process_funcs(self):
        process_f = {}

        process_f["connect"]             = self.connect
        process_f["disconnect"]          = self.disconnect
        process_f["close"]               = self.disconnect

        process_f["acquire_data"]        = self.acquire_data
                                         
        process_f["integration_time_ms"] = lambda x: self.set_integration_time_ms(x)
        process_f["detector_gain"]       = lambda x: self.set_detector_gain(x)
        process_f["laser_enable"]        = lambda x: self.not_implemented("set_laser_enable")
        process_f["vertical_binning"]    = lambda x: self.set_vertical_roi(x)

        return process_f

class MessagePacket:
    """
    Essentially a 6-byte simplified USB Setup Packet, with bmRequestType removed
    and wLength reduced to 1 byte.

    This format was deliberately chosen to minimize porting complexity from 
    the existing ENG-0001 USB FID API.

    Offset Length Datatype Field     Description
    ------ ------ -------- --------  ---------------------
    0      1      uint8    bRequest  The command/opcode
    1      2      uint16   wValue    Parameter #1 (or 2nd-tier opcode if bRequest was 0xff)
    3      2      uint16   wIndex    Parameter #2
    5      1      uint8    bLength   Payload length
    """
    
    def __init__(self, bRequest=None, wValue=0, wIndex=0, payload=None, serialized=None):
        if serialized:
            self.deserialize(serialized)
        else:
            self.bRequest = bRequest
            self.wValue   = wValue
            self.wIndex   = wIndex
            self.payload  = None

            if payload is None:
                self.bLength = 0
            elif isinstance(payload, bytes):
                self.bLength = len(payload)
                self.payload = payload
            else:
                raise(RuntimeError(f"payload must be 'bytes' (currently {type(payload)})"))

    def __repr__(self):
        return f"MessagePacket(bRequest 0x{self.bRequest:02x}, wValue 0x{self.wValue:04x}, wIndex 0x{self.wIndex:04x}, bLength {self.bLength})"

    def serialize(self):
        data = []
        data.append(self.bRequest)
        data.append((self.wValue >> 8) & 0xff)
        data.append((self.wValue     ) & 0xff)
        data.append((self.wIndex >> 8) & 0xff)
        data.append((self.wIndex     ) & 0xff)
        data.append(self.bLength)

        if self.payload is not None:
            for v in self.payload:
                data.append(v)

        return bytes(data)

    def deserialize(self, serialized):
        if len(serialized) < 6:
            raise(RuntimeError(f"len({serialized}) < 6"))

        self.bRequest =  serialized[0]
        self.wValue   = (serialized[1] << 8) | serialized[2]
        self.wIndex   = (serialized[3] << 8) | serialized[4]
        self.bLength  =  serialized[5]

        payload_len = len(serialized) - 6
        if payload_len != self.bLength:
            raise(RuntimeError(f"payload_len {payload_len} != bLength {self.bLength}"))

        self.payload = serialized[6:]
