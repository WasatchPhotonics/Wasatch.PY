import os
import json
import time
import struct
import logging

from wasatch.DeviceID import DeviceID
from .AbstractUSBDevice import AbstractUSBDevice
from .CSVLoader import CSVLoader
from wasatch.EEPROM import EEPROM

log = logging.getLogger(__name__)

class MockUSBDevice(AbstractUSBDevice):

    def __init__(self, spec_name, eeprom_name):
        self.spec_name = spec_name
        self.eeprom_name = eeprom_name
        self.device_id = DeviceID(label=f"USB:0x111111:0x4000:111111:111111")
        self.device_id = self.device_id
        self.test_spec_dir = os.path.join(self.get_default_data_dir(), 'testSpectrometers')
        self.spectrometer_folder = self.get_spec_folder()
        self.test_spec_readings = os.path.join(self.test_spec_dir, self.spectrometer_folder,'readings')
        self.test_spec_eeprom = os.path.join(self.test_spec_dir, self.spectrometer_folder,'eeprom')
        self.bus = self.device_id.bus
        self.address = self.device_id.address
        self.vid = self.device_id.vid
        self.pid = self.device_id.pid
        self.int_time = 1000
        self.detector_gain = 10
        self.detector_offset = 1
        self.detector_setpoint = 1
        self.disconnect = False
        self.single_reading = False
        self.got_start_int = False
        self.got_start_detector_gain = False
        self.got_start_detector_offset = False
        self.got_start_detector_setpoint = False


        self.load_readings()
        self.load_eeprom(self.test_spec_eeprom)
        self.covnert_eeprom()
        self.reading_index = 0
        self.reading_len = len(self.spec_readings)
        self.default_ctrl_return = [1 for i in range(64)]
        # style is (bRequest,wValue) to allow for second tier op codes
        # if first tier, where wValue matters then wValue should be given as None
        self.cmd_dict = {
            (255,1): self.cmd_read_eeprom,
            (178,None): self.cmd_set_int_time,
            (183,None): self.cmd_set_gain,
            (182,None): self.cmd_set_offset,
            (216,None): self.cmd_set_setpoint,
            }

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

    def cmd_set_gain(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        if not self.got_start_detector_gain: self.got_start_detector_gain = True
        self.detector_gain = wValue
        return [1]

    def cmd_set_offset(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        if not self.got_start_detector_offset: self.got_start_detector_offset = True
        self.detector_offset = wValue
        return [1]

    def cmd_set_setpoint(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        if not self.got_start_detector_setpoint: self.got_start_detector_setpoint
        self.detector_setpoint = (wValue & 0x0FFF)
        return [1]

    def get_int_time(self):
        return self.int_time

    def set_int_time(self, value):
        self.int_time = value
        return True

    def read(self, *args, **kwargs):
        if self.single_reading:
            return self.spec_readings[0]
        ret_reading = self.spec_readings[self.reading_index]
        self.reading_index += 1
        self.reading_index %= self.reading_len
        time.sleep(self.int_time*10**-3)
        if self.disconnect:
            return False
        return ret_reading

    def send_code(self):
        pass

    def is_usb(self):
        return True

    def get_pid_hex(self):
        return str(self.pid)

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

        self.eeprom = dict(eeprom_json)

    def load_readings(self):
        dir_items = os.walk(self.test_spec_readings)
        reading_files = [os.path.join(path,file) for path,dir,files in dir_items for file in files]
        parse_objects = [CSVLoader(file) for file in reading_files]
        for object in parse_objects:
            object.load_data()
        self.spec_readings = [struct.pack('e' * len(object.processed_reading.processed),*object.processed_reading.processed) for object in parse_objects]

    def __str__(self):
        return "<MockUSBDevice 0x%04x:0x%04x:%d:%d>" % (self.vid, self.pid, self.bus, self.address)

    def __hash__(self):
        return hash(str(self))

    def __repr__(self):
        return str(self)

    def __eq__(self, other):
        return str(self) == str(other)

    def __ne__(self, other):
        return str(self) != str(other)

    def __lt__(self, other):
        return str(self) < str(other)

    def close(self):
        self.disconnect = True

    def covnert_eeprom(self):
        self.eeprom_obj = EEPROM()
        for key, value in self.eeprom.items():
            try:
                setattr(self.eeprom_obj,key,value)
            except:
                log.error(f"Unable to set {key} on eeprom object")
        self.eeprom_obj.generate_write_buffers()

    def get_default_data_dir(self):
        if os.name == "nt":
            return os.path.join(os.path.expanduser("~"), "Documents", "EnlightenSpectra")
        return os.path.join(os.environ["HOME"], "EnlightenSpectra")
