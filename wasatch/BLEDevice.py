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

class BLEDevice:

    def __init__(self, spec_name, eeprom):
        self.spec_name = spec_name
        self.device_type = self
        self.eeprom = eeprom
        self.ble_pid = str(hash(spec_name))
        self.device_id = DeviceID(label=f"USB:{self.ble_pid[:8]}:0x16384:111111:111111", device_type=self)
        self.device_id = self.device_id
        self.bus = self.device_id.bus
        self.address = self.device_id.address
        self.vid = self.device_id.vid
        self.pid = self.device_id.pid
        self.device_type = self
        self.is_ble = True

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

        self.default_ctrl_return = [1 for i in range(64)]
        # style is (bRequest,wValue) to allow for second tier op codes
        # if first tier, where wValue matters then wValue should be given as None
        self.cmd_dict = {
            (0xb2,None): self.cmd_set_int_time,
            (0xb6,None): self.cmd_set_offset,
            (0xb7,None): self.cmd_set_gain,
            (0xbe,None): self.cmd_toggle_laser,
            (0xd6,None): self.cmd_toggle_tec,
            (0xd7,None): self.cmd_get_detector_temp,
            (0xd8,None): self.cmd_set_setpoint,
            (0xda,None): self.cmd_get_tec_enable,
            (0x34,None): self.cmd_get_raw_ambient_temp,
            (0xd5,None): self.cmd_get_laser_temp,
            (0xd7,None): self.cmd_get_detect_temp,
            (0xe2,None): self.cmd_get_laser_enabled,
            (0xff,1): self.cmd_read_eeprom,
            }
        self.reading_cycles = {}
        # turn readings arrays into cycles so 
        # we have an infinite loop of spectra to go through
        for key,value in self.spec_readings.items():
            self.reading_cycles[key] = cycle(value)

    def cmd_get_laser_temp(self, *args):
        return [random.randint(0,255)]*2

    def cmd_get_detect_temp(self, *args):
        return [0, random.randint(0,255)]

    def cmd_get_raw_ambient_temp(self, *args):
        return [random.randint(0,255)]*2

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
        log.info(f"sending temp value of {value}")
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

    def to_dict():
        return str(self)

    def __str__(self):
        return "<BLEDevice 0x%04x:0x%04x:%d:%d>" % (self.vid, self.pid, self.bus, self.address)

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

    def get_default_data_dir(self):
        return os.getcwd()
