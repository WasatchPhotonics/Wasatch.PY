import os
import re
import json
import time
import random
import struct
import logging
from itertools import cycle

from wasatch.DeviceID import DeviceID
from .AbstractUSBDevice import AbstractUSBDevice
from .CSVLoader import CSVLoader
from wasatch.EEPROM import EEPROM

log = logging.getLogger(__name__)

class MockUSBDevice(AbstractUSBDevice):

    def __init__(self, spec_name, eeprom_name, eeprom_overrides=None, spectra_option=None):
        self.spec_name = spec_name
        self.device_type = self
        self.eeprom_name = eeprom_name
        self.eeprom_overrides = eeprom_overrides
        self.spectra_option = spectra_option
        self.fake_pid = str(hash(spec_name))
        self.device_id = DeviceID(label=f"USB:{self.fake_pid[:8]}:0x16384:111111:111111")
        self.device_id = self.device_id
        self.bus = self.device_id.bus
        self.address = self.device_id.address
        self.vid = self.device_id.vid
        self.pid = self.device_id.pid

        #path attributes
        self.test_spec_dir = os.path.join(self.get_default_data_dir(), 'testSpectrometers')
        self.spectrometer_folder = self.get_spec_folder()
        self.test_spec_readings = os.path.join(self.test_spec_dir, self.spectrometer_folder,'readings')
        self.test_spec_eeprom = os.path.join(self.test_spec_dir, self.spectrometer_folder,'eeprom')

        #init attributes
        self.spec_readings = {}
        self.int_time = 1000
        self.detector_gain = 1
        self.detector_offset = 1
        self.detector_setpoint = 1
        self.detector_temp_raw = 40.0
        self.disconnect = False
        self.single_reading = False
        self.got_start_int = False
        self.laser_enable = False
        self.got_start_detector_gain = False
        self.got_start_detector_offset = False
        self.got_start_detector_setpoint = False
        self.detector_tec_enable = False

        #set up functions
        self.re_pattern_1 = re.compile('(.)([A-Z][a-z]+)')
        self.re_pattern_2 = re.compile('([a-z0-9])([A-Z])')
        self.wpsc_translate = {
            "wavecal_coeffs":"wavelength_coeffs",
            "temp_to_dac_coeffs":"degC_to_dac_coeffs",
            "adc_to_temp_coeffs":"adc_to_degC_coeffs",
            "serial": "serial_number",
            "inc_laser": "has_laser",
            "inc_battery": "has_battery",
            "inc_cooling": "has_cooling",
            "max_laser_power_mw": "max_laser_power_mW",
            "excitation_wavelength_nm": "excitation_nm",
            "detector_name": "detector",
            "flip_x_axis": "invert_x_axis",
            }

        self.load_readings()
        self.load_eeprom(self.test_spec_eeprom)
        self.convert_eeprom()
        self.reading_len = len(self.spec_readings)
        self.default_ctrl_return = [1 for i in range(64)]
        if self.eeprom_overrides:
            self.override_eeprom()
        # style is (bRequest,wValue) to allow for second tier op codes
        # if first tier, where wValue matters then wValue should be given as None
        self.cmd_dict = {
            (178,None): self.cmd_set_int_time,
            (182,None): self.cmd_set_offset,
            (183,None): self.cmd_set_gain,
            (190,None): self.cmd_toggle_laser,
            (214,None): self.cmd_toggle_tec,
            (215,None): self.cmd_get_detector_temp,
            (216,None): self.cmd_set_setpoint,
            (218,None): self.cmd_get_tec_enable,
            (226,None): self.cmd_get_laser_enabled,
            (255,1): self.cmd_read_eeprom,
            }
        self.reading_cycles = {}
        # turn readings arrays into cycles so 
        # we have an infinite loop of spectra to go through
        for key,value in self.spec_readings.items():
            self.reading_cycles[key] = cycle(value)

    def get_spec_folder(self):
        spec_match = []
        for item in os.listdir(self.test_spec_dir):
            item_path = os.path.join(self.test_spec_dir,item)
            if self.spec_name == item and os.path.isdir(item_path):
                spec_match.append(item)
        if len(spec_match) == 1:
            return spec_match[0]
        else:
            raise NameError(f'Multiple or No folders found matching {self.spec_name}, matches are {spec_match}')

    def find(self,*args,**kwargs):
        return [self]

    def set_configuration(self):
        pass

    def reset(self):
        pass

    def claim_interface(self, *args, **kwargs):
        # connecting
        return True

    def release_interface(self):
        # disconnecting
        return True

    def ctrl_transfer(self, *args, **kwargs):
        device, host, bRequest, wValue, wIndex, wLength = args
        log.info(f"Mock spec received ctrl transfer of host {host}, request {bRequest}, wValue {wValue}, wIndex {wIndex}, len {wLength}")
        if bRequest == 255:
            cmd_func = self.cmd_dict.get((bRequest,wValue),None)
        else:
            cmd_func = self.cmd_dict.get((bRequest,None),None)
        if cmd_func:
            return cmd_func(*args)
        else:
            return self.default_ctrl_return

    def cmd_read_eeprom(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        page = wIndex
        return self.eeprom_obj.write_buffers[page]

    def cmd_set_int_time(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        if not self.got_start_int: self.got_start_int = True
        self.set_int_time(wIndex << 8 | wValue)
        return [1]

    def cmd_get_laser_enabled(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        return [int(self.laser_enable)]

    def cmd_get_detector_temp(self, *args):
        bytes = struct.pack('>e',self.detector_temp_raw)
        value = int.from_bytes(bytes,byteorder='big')
        value = value & 0x0F
        return value.to_bytes(2, byteorder='big')


    def cmd_toggle_laser(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        self.laser_enable = bool(wValue)
        return [int(self.laser_enable)]

    def cmd_set_gain(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        if not self.got_start_detector_gain: self.got_start_detector_gain = True
        wValB = wValue.to_bytes(2,byteorder='little')#struct.unpack('f',bytearray(wValue))
        lsb = wValB[0] # LSB-MSB
        msb = wValB[1]
        raw = (msb << 8) | lsb

        gain = msb + lsb / 256.0
        self.detector_gain = gain
        return [1]

    def cmd_set_offset(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        if not self.got_start_detector_offset: self.got_start_detector_offset = True
        self.detector_offset = wValue
        return [1]

    def cmd_set_setpoint(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        if not self.got_start_detector_setpoint: self.got_start_detector_setpoint
        self.detector_setpoint = wValue
        return [1]

    def cmd_toggle_tec(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        self.detector_tec_enable = bool(wValue)

    def cmd_get_tec_enable(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        return [int(self.detector_tec_enable)]

    def get_int_time(self):
        return self.int_time

    def set_int_time(self, value):
        self.int_time = value
        return True

    def read(self, *args, **kwargs):
        if self.disconnect:
            return False
        if self.spectra_option is None:
            if self.single_reading:
                return self.spec_readings["default"][0]
            ret_reading = next(self.reading_cycles["default"])
            time.sleep(self.int_time*10**-3)
        return ret_reading

    def send_code(self):
        pass

    def is_usb(self):
        return True

    def get_pid_hex(self):
        return str(hex(self.pid))[2:]

    def get_vid_hex(self):
        return str(self.vid)

    def load_eeprom(self, eeprom_file_loc):
        dir_items = os.walk(eeprom_file_loc)
        files = [os.path.join(path,file) for path,dir,files in dir_items for file in files]
        log.info(f"files is {files}, looking for {self.eeprom_name}")
        for file in files:
            if os.path.basename(file) == self.eeprom_name:
                eeprom_file = file
        with open(eeprom_file,'r') as file:
            eeprom_json = json.load(file)

        eeprom = dict(eeprom_json)
        if "EEPROM" in eeprom.keys() and "measurements" in eeprom.keys():
            self.parse_wpsc_eeprom(eeprom)
        else:
            self.eeprom = eeprom

    def parse_wpsc_eeprom(self,eeprom_file):
        translated_eeprom = {}
        eeprom = eeprom_file["EEPROM"]
        for key, value in eeprom.items():
            k = re.sub(self.re_pattern_1,r'\1_\2',key)
            camel_key = re.sub(self.re_pattern_2,r'\1_\2',k).lower()
            if translation := self.wpsc_translate.get(camel_key,None):
                camel_key = translation
            if camel_key == "excitation_nm":
                translated_eeprom["excitation_nm_float"] = value
            translated_eeprom[camel_key] = value
        self.eeprom = translated_eeprom
        self.parse_measurements(eeprom_file["measurements"])

    def parse_measurements(self, measurements):
        for compound, int_time in measurements.items():
            for int_time, spectra in int_time.items():
                self.spec_readings[str(compound) + '_' + str(int_time)] = []
                byte_array = [struct.pack('e' * len(spectra),*spectra)]
                self.spec_readings[str(compound) + '_' + str(int_time)].extend(byte_array)
                self.spec_readings["default"].extend(byte_array)

    def override_eeprom(self):
        for key,value in self.eeprom_overrides.items():
            self.eeprom[key] = value

    def load_readings(self):
        dir_items = os.walk(self.test_spec_readings)
        reading_files = [os.path.join(path,file) for path,dir,files in dir_items for file in files]
        parse_objects = [CSVLoader(file) for file in reading_files]
        self.spec_readings["default"] = []
        for object in parse_objects:
            object.load_data()
        self.spec_readings["default"].extend([struct.pack('e' * len(object.processed_reading.processed),*object.processed_reading.processed) for object in parse_objects])

    def to_dict():
        return str(self)

    def __str__(self):
        return "<MockUSBDevice 0x%04x:0x%04x:%d:%d>" % (self.vid, self.pid, self.bus, self.address)

    def __hash__(self):
        return hash(str(self))

    def __repr__(self):
        return str(self)

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __ne__(self, other):
        return str(self) != str(other)

    def __lt__(self, other):
        return str(self) < str(other)

    def close(self):
        self.disconnect = True

    def convert_eeprom(self):
        self.eeprom_obj = EEPROM()
        for key, value in self.eeprom.items():
            try:
                setattr(self.eeprom_obj,key,value)
            except:
                log.error(f"Unable to set {key} on eeprom object")
        self.eeprom_obj.generate_write_buffers()

    def get_default_data_dir(self):
        return os.getcwd()
