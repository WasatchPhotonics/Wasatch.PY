import re
import os
import time
import queue
import psutil
import logging
import datetime
import threading

import seabreeze
import seabreeze.spectrometers as sb
from seabreeze.spectrometers import Spectrometer, list_devices
from configparser import ConfigParser

from . import utils

from .FeatureIdentificationDevice import FeatureIdentificationDevice
from .SpectrometerSettings        import SpectrometerSettings
from .BalanceAcquisition          import BalanceAcquisition
from .SpectrometerState           import SpectrometerState
from .ControlObject               import ControlObject
from .WasatchBus                  import WasatchBus
from .DeviceID                    import DeviceID
from .Reading                     import Reading

log = logging.getLogger(__name__)

class OceanDevice:

    def __init__(self, device_id, message_queue=None):

        # if passed a string representation of a DeviceID, deserialize it
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.device_id      = device_id
        self.message_queue  = message_queue
        self.device = None
        devices = list_devices()
        for device in devices:
            # cseabreeze does not readily expose the pid and vid
            # this is the good work around I found
            # pyseabreeze does readily expose both, but it has connection 
            # issues sometimes that cseabreeze doesnt
            if device.model == self.device_id.product and device.serial_number == self.device_id.serial:
                self.device = device
        self.spec = Spectrometer(self.device)

        #self.lock = threading.Lock()

        self.connected = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. Although a logical queue, has nothing to do with multiprocessing.
        self.command_queue = []

        self.immediate_mode = False

        self.settings = SpectrometerSettings()
        self.settings.eeprom.model = self.device.model
        self.settings.eeprom.serial_number = self.device.serial_number
        self.settings.eeprom.detector = "Ocean" # Ocean API doesn't have access to detector info
        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0
        self.take_one               = False

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0
        self.init_lambdas()

    def init_lambdas(self):
        f = {}
        f["integration_time_ms"] = lambda x: self.spec.integration_time_micros(int(round(x)))
        self.lambdas = f

    def acquire_data(self):
        reading = Reading(self.device_id)
        self.sum_count += 1
        reading.sum_count = self.sum_count
        reading.spectrum = list(self.spec.intensities())
        self.settings.wavelengths = self.spec.wavelengths()
        return reading

    def change_setting(self,setting,value):
        f = self.lambdas.get(setting, None)
        if f is None:
            # quietly fail no-ops
            return False

        log.info(f"about to set integation time using func {f} to value {value}")
        value = value * 1000 # conversion from millisec to microsec
        return f(value)