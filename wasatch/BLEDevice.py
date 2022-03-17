import os
import re
import json
import time
import random
import struct
import logging
import asyncio
from itertools import cycle

from wasatch.DeviceID import DeviceID
from .AbstractUSBDevice import AbstractUSBDevice
from .CSVLoader import CSVLoader
from .SpectrometerSettings import SpectrometerSettings
from wasatch.EEPROM import EEPROM

log = logging.getLogger(__name__)

class BLEDevice:

    def __init__(self, device):
        self.ble_pid = str(hash(device.address))
        self.device_id = DeviceID(label=f"USB:{self.ble_pid[:8]}:0x16384:111111:111111", device_type=self)
        self.device_id = self.device_id
        self.bus = self.device_id.bus
        self.address = self.device_id.address
        self.vid = self.device_id.vid
        self.pid = self.device_id.pid
        self.device_type = self
        self.is_ble = True
        self.settings = SpectrometerSettings(self.device_id)

    def connect(self):
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
