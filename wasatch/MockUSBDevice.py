import os
import re
import math
import copy
import json
import time
import random
import struct
import logging
from itertools import cycle

import numpy as np

from . import utils
from wasatch.DeviceID import DeviceID
from .AbstractUSBDevice import AbstractUSBDevice
from .CSVLoader import CSVLoader
from wasatch.EEPROM import EEPROM

log = logging.getLogger(__name__)

class MockUSBDevice(AbstractUSBDevice):

    DEFAULT_CYCLE_LENGTH = 3

    def __init__(self, spec_name, eeprom_name, eeprom_overrides=None, spectra_option=None):
        if spec_name == "":
            self.rasa_virtual = True # From Tabula Rasa, want to distinguish file virtual from pure virtual
            self.spec_name = "WP-MOCK"
        else:
            self.rasa_virtual = False
            self.spec_name = spec_name
        self.device_type = self
        self.eeprom_name = eeprom_name
        self.eeprom_overrides = eeprom_overrides
        self.spectra_option = spectra_option
        self.fake_pid = str(hash(self.spec_name))
        self.device_id = DeviceID(label=f"USB:{self.fake_pid[:8]}:0x16384:111111:111111")
        self.device_id = self.device_id
        self.bus = self.device_id.bus
        self.address = self.device_id.address
        self.vid = self.device_id.vid
        self.pid = self.device_id.pid
        self.active_readings = "default"

        #path attributes
        if not self.rasa_virtual:
            self.test_spec_dir = os.path.join(self.get_default_data_dir(), 'testSpectrometers')
            self.spectrometer_folder = self.get_spec_folder()
            self.test_spec_readings = os.path.join(self.test_spec_dir, self.spectrometer_folder,'readings')
            self.test_spec_eeprom = os.path.join(self.test_spec_dir, self.spectrometer_folder,'eeprom')

        #init attributes
        self.spec_readings = {}
        self.int_time = 1000
        self.laser_power = 1.0 
        self.detector_gain = 1
        self.detector_offset = 1
        self.detector_setpoint = 1
        self.detector_temp_raw = 40.0
        self.mod_period = 1000
        self.mod_width = 1000
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

        if not self.rasa_virtual:
            self.load_readings()
            self.load_eeprom(self.test_spec_eeprom)
            self.convert_eeprom()
            self.reading_len = len(self.spec_readings)
            if len(self.spec_readings["default"]):
                num_px = self.eeprom_obj.active_pixels_horizontal # other instance uses eeprom_obj but that hasnt been instantiated yet
                darks = [np.random.randint(0, 390, size=num_px) for _ in range(self.DEFAULT_CYCLE_LENGTH)]
                self.spec_readings["default"] = [struct.pack('H' * num_px, *d) for d in darks]
        else:
            self.eeprom_obj = EEPROM()
            self.mock_eeprom()
        self.default_ctrl_return = [1 for i in range(64)]
        if self.eeprom_overrides:
            self.override_eeprom()
        # style is (bRequest,wValue) to allow for second tier op codes
        # if first tier, where wValue matters then wValue should be given as None
        self.cmd_dict = {
            (0xb2,None): self.cmd_set_int_time,
            (0xb6,None): self.cmd_set_offset,
            (0xb7,None): self.cmd_set_gain,
            (0xbe,None): self.cmd_toggle_laser,
            (0xc0,0xe2): self.cmd_get_laser_state,
            (0xd6,None): self.cmd_toggle_tec,
            (0xd7,None): self.cmd_get_detector_temp,
            (0xd8,None): self.cmd_set_setpoint,
            (0xda,None): self.cmd_get_tec_enable,
            (0x34,None): self.cmd_get_raw_ambient_temp,
            (0xd5,None): self.cmd_get_laser_temp,
            (0xd7,None): self.cmd_get_detect_temp,
            (0xe2,None): self.cmd_set_mod_width,
            (0xdb,None): self.cmd_set_mod_period,
            (0xff,1): self.cmd_read_eeprom,
            }
        self.reading_cycles = {}
        self.reading_peak_locs = {}
        self.read_count = cycle(range(self.DEFAULT_CYCLE_LENGTH))
        self.generate_readings()
        log.debug(f"After generate reading call spec readings is {self.spec_readings.keys()}")
        # turn readings arrays into cycles so 
        # we have an infinite loop of spectra to go through
        for key, value in self.spec_readings.items():
            log.debug(f"MOCK CYCLES ADDING KEY {key}")
            self.reading_cycles[key] = cycle(value)

    def is_andor(self):
        return False

    def cmd_get_laser_state(self, *args):
        return int(self.laser_enable).to_bytes(1, byteorder='big')

    def cmd_set_mod_period(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        self.mod_period = wValue
        return [1]

    def cmd_set_mod_width(self, *args):
        device, host, bRequest, wValue, wIndex, wLength = args
        self.mod_width = wValue
        return [1]

    def cmd_get_laser_temp(self, *args):
        return [random.randint(0,255)]*2

    def cmd_get_detect_temp(self, *args):
        return [0, random.randint(1,255)] # 1-255, dont return a 0

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
        elif host == 192:
            cmd_func = self.cmd_dict.get((192, bRequest),None)
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
        log.debug(f"setting laser state to {wValue}")
        self.laser_enable = bool(wValue)
        log.debug(f"mock laser state is now {self.laser_enable}")
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
                ret_reading = self.spec_readings["default"][0]
                return struct.pack("H"*len(ret_reading), *ret_reading)
            count = next(self.read_count)
            if not self.laser_enable:
                has_dark = self.reading_cycles.get("dark", None)
                if has_dark is None:
                    ret_reading = next(self.reading_cycles["default"])
                else:
                    ret_reading = next(self.reading_cycles["dark"])
            else:
                log.debug(f"active reading is {self.active_readings} while possible is {self.reading_cycles}")
                ret_reading = next(self.reading_cycles[self.active_readings])
            time.sleep(self.int_time*10**-3)
            log.debug(f"calling generate a reading for data {ret_reading}")
            ret_reading = self.generate_a_reading(self.active_readings, ret_reading, self.int_time)
            if self.eeprom_obj.active_pixels_horizontal == 2048:
                return ret_reading[:len(ret_reading)//2] if args[1] == 0x82 else ret_reading[len(ret_reading)//2:]
            else:
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
        log.debug("Mock USB EEPROM results are the following:")
        log.debug(self.eeprom)

    def parse_wpsc_eeprom(self,eeprom_file):
        translated_eeprom = {}
        eeprom = eeprom_file["EEPROM"]
        for key, value in eeprom.items():
            k = re.sub(self.re_pattern_1,r'\1_\2',key)
            camel_key = re.sub(self.re_pattern_2,r'\1_\2',k).lower()
            translation = self.wpsc_translate.get(camel_key,None)
            if translation is not None:
                camel_key = translation
            if camel_key == "excitation_nm":
                translated_eeprom["excitation_nm_float"] = value
            translated_eeprom[camel_key] = value
        self.eeprom = translated_eeprom
        self.parse_measurements(eeprom_file["measurements"])

    def parse_measurements(self, measurements):
        for compound, int_time in measurements.items():
            for int_time, spectra in int_time.items():
                spectra_name = (str(compound) + '_' + str(int_time)).lower()
                log.debug(f"MOCK PARSE MEASUER SAMPLE {spectra_name} data is {spectra}")
                self.spec_readings[spectra_name] = []
                self.spec_readings[spectra_name].append([int(val) if val > 0 else 0 for val in spectra]) # append here else cycle will return first element and not list
                if "dark" in spectra_name:
                    self.spec_readings["default"].extend(spectra)

    def override_eeprom(self):
        for key, value in self.eeprom_overrides.items():
            self.eeprom[key] = value

    def load_readings(self):
        dir_items = os.walk(self.test_spec_readings)
        reading_files = [os.path.join(path,file) for path,dir,files in dir_items for file in files]
        parse_objects = [CSVLoader(file) for file in reading_files]
        self.spec_readings["default"] = []
        for idx, object in enumerate(parse_objects):
            object.load_data()
            object.processed_reading.processed = [int(val) if val > 0 else 0 for val in object.processed_reading.processed]
            pr = object.processed_reading.processed
            self.spec_readings["default"].extend(pr)
            if self.spec_readings.get(os.path.basename(reading_files[idx])[:10], None) != None:
                self.spec_readings[os.path.basename(reading_files[idx])[:10]].extend(pr)
            else:
                self.spec_readings[os.path.basename(reading_files[idx])[:10]] = pr

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
        if self.eeprom.get("format", None):
            self.eeprom_obj.write_buffers[0][63] = self.eeprom["format"]

    def get_default_data_dir(self):
        return os.getcwd()

    def generate_a_reading(self, sample_name, data, int_time):
        if sample_name.lower() == "dark":
            return struct.pack("H"*len(data), *data)
        if sample_name.lower() == "default":
            return struct.pack("H"*len(data), *data)
        if data == []:
            return
        data = copy.deepcopy(data)
        peaks = self.reading_peak_locs[sample_name]
        laser_pow = (self.mod_width*100)/self.mod_period
        log.debug(f"new laser power is {laser_pow}")
        for px in peaks:
            data[px]  = min(data[px] * ((int_time/10)/self.eeprom_obj.startup_integration_time_ms) * laser_pow, 0xffff)
        
        return struct.pack('H'*len(data), *data)

    def generate_readings(self, data = None):
        num_px = self.eeprom_obj.active_pixels_horizontal
        wavelengths = utils.generate_wavelengths(num_px, self.eeprom_obj.wavelength_coeffs)
        if self.eeprom_obj.excitation_nm == 0.0:
            self.eeprom_obj.excitation_nm = 785.0
        wavenumbers = utils.generate_wavenumbers(self.eeprom_obj.excitation_nm, wavelengths)
        darks = [np.random.randint(0, 390, size=num_px) for _ in range(self.DEFAULT_CYCLE_LENGTH)]
        self.spec_readings["default"] = darks
        self.spec_readings["dark"] = darks
        if data is None:
            return
        log.debug(f"data was not none, was {data} so generating readings")
        for sample in data.keys():
            self.create_sample(sample, data, wavenumbers, darks)

    def create_sample(self, sample_name, spectra, wavenumbers, darks):
        log.debug(f"creating sample with name {sample_name}")
        num_px = self.eeprom_obj.active_pixels_horizontal
        wavelengths = utils.generate_wavelengths(num_px, self.eeprom_obj.wavelength_coeffs)
        width = int(self.eeprom_obj.avg_resolution/2)
        if "peak_location_cm" in spectra[sample_name].keys():
            axis = wavenumbers
            peaks = zip(spectra[sample_name]["peak_location_cm"], 
                    spectra[sample_name]["peak_intensity"], 
                    [width]*len(spectra[sample_name]["peak_intensity"]))
        elif "peak_location_nm" in spectra[sample_name].keys():
            axis = wavelengths
            peaks = zip(spectra[sample_name]["peak_location_nm"], 
                    spectra[sample_name]["peak_intensity"], 
                    [width]*len(spectra[sample_name]["peak_intensity"]))

        sample = copy.deepcopy(darks)
        x_min = axis[0]
        x_max = axis[len(axis)-1]
        log.debug(f"FOR SAMPLE {sample_name} min is {x_min} and max is {x_max}")

        counter = 0
        peak_px_loc = []
        for loc, height, width in peaks:
            gauss_c = width/2.35482 # gauss function to fwhm see gauss function wikipedia page
            if loc < x_min or loc > x_max:
                continue
            while loc > axis[counter]:
                counter += 1
            for s in sample:
                for i in range(width):
                    try:
                        peak_px_loc.append(counter-i)
                        peak_px_loc.append(counter+i)
                        lower_val = height * math.exp(-(i)**2/(2*((gauss_c)**2))) + s[counter-i] # gauss function to fwhm
                        s[counter-i] = lower_val if lower_val > s[counter-i] else s[counter-i]
                        higher_val = height * math.exp(-(i)**2/(2*((gauss_c)**2))) + s[counter+i]
                        s[counter+i] = higher_val if higher_val > s[counter+i] else s[counter+i] 
                    except:
                        # ignore out of bounds
                        pass
                peak_px_loc.append(counter)
                s[counter] = height
        for s in sample:
            s = utils.apply_boxcar(s, 2*width)
        log.debug(f"adding sample to spec_readings number of pixels is {num_px}")
        self.reading_peak_locs[sample_name.lower()] = peak_px_loc
        self.spec_readings[sample_name.lower()] = sample
        self.reading_cycles[sample_name.lower()] = cycle(sample)
        self.active_readings = "default"

    def get_available_spectra(self) -> list[str]:
        return self.spec_readings.keys()

    def mock_eeprom(self):
        self.eeprom_obj.model = "WP-MOCK"
        self.eeprom_obj.serial_number = "0000"
        self.eeprom_obj.has_laser = True
        # Important note, for our 2048 spectrometers the data is read across 2 endpoints
        # Virtual spectrometers don't recognize that so if the px is set to 2048
        # it reads the same array twice. I've fixed this in the read func but it was enough of a 
        # gotcha I felt it deserved a note
        self.eeprom_obj.active_pixels_horizontal = 2048
        self.eeprom_obj.excitation_nm = 785
        self.eeprom_obj.excitation_nm_float = 785.0
        self.eeprom_obj.wavelength_coeffs = [700.25, 0.20039179921150208,
                                             -1.0060509794129757e-06, -2.3662950709990582e-08,
                                              0]
        self.eeprom_obj.generate_write_buffers()

    def set_active_readings(self, reading_name: str) -> None:
        log.debug(f"setting active reading name to {reading_name}")
        self.active_readings = reading_name

