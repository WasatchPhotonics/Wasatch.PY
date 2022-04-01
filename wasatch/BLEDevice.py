import os
import re
import json
import time
import random
import struct
import logging
import asyncio

from bleak import discover, BleakClient, BleakScanner
from bleak.exc import BleakError

from . import utils
from .Reading import Reading
from .CSVLoader import CSVLoader
from wasatch.EEPROM import EEPROM
from wasatch.DeviceID import DeviceID
from .ControlObject import ControlObject
from .SpectrometerResponse import ErrorLevel
from .AbstractUSBDevice import AbstractUSBDevice
from .SpectrometerSettings import SpectrometerSettings
from .SpectrometerResponse import SpectrometerResponse

log = logging.getLogger(__name__)

INT_UUID = "d1a7ff01-af78-4449-a34f-4da1afaf51bc"
GAIN_UUID = "d1a7ff02-af78-4449-a34f-4da1afaf51bc"
LASER_STATE_UUID = "d1a7ff03-af78-4449-a34f-4da1afaf51bc"
DEVICE_ACQUIRE_UUID = "d1a7ff04-af78-4449-a34f-4da1afaf51bc"
SPECTRUM_PIXELS_UUID = "d1a7ff05-af78-4449-a34f-4da1afaf51bc"
READ_SPECTRUM_UUID = "d1a7ff06-af78-4449-a34f-4da1afaf51bc"
SELECT_EEPROM_PAGE_UUID = "d1a7ff07-af78-4449-a34f-4da1afaf51bc"
READ_EEPROM_UUID = "d1a7ff08-af78-4449-a34f-4da1afaf51bc"
DETECTOR_ROI_UUID = "d1a7ff0A-af78-4449-a34f-4da1afaf51bc"
MAX_RETRIES = 4
THROWAWAY_SPECTRA = 6

class BLEDevice:
    """
    This is the basic implementation of our interface with BLE Spectrometers.

    ##########################################################################
    This class adopts the external device interface structure
    This invlovles receiving a request through the handle_request function
    A request is processed based on the key in the request
    The processing function passes the commands to the requested device
    Once it recevies a response from the connected device it then passes that
    back up the chain
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

    def __init__(self, device, loop):
        self.ble_pid = str(hash(device.address))
        self.device_id = DeviceID(label=f"USB:{self.ble_pid[:8]}:0x16384:111111:111111", device_type=self)
        self.device_id = self.device_id
        self.label = "BLE Device"
        self.bus = self.device_id.bus
        self.address = self.device_id.address
        self.vid = self.device_id.vid
        self.pid = self.device_id.pid
        self.device_type = self
        self.is_ble = True
        self.loop = loop
        self.sum_count = 0
        self.performing_acquire = False
        self.disconnect = False
        self.disconnect_event = asyncio.Event()
        self.client = BleakClient(device)
        self.total_pixels_read = 0
        self.session_reading_count = 0
        self.settings = SpectrometerSettings(self.device_id)
        self.settings.eeprom.detector = "ble"
        self.init_lambdas()

        self.process_f = self._init_process_funcs()

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

    ###############################################################
    # Private Methods
    ###############################################################

    def _init_process_funcs(self) -> dict[str, Callable[..., Any]]:
        process_f = {}

        process_f["connect"] = self.connect
        process_f["close"] = self.close
        process_f["acquire_data"] = self.acquire_data
        process_f["scans_to_average"] = self.scans_to_average
        ##################################################################
        # What follows is the old init-lambdas that are squashed into process_f
        # Long term, the upstream requests should be changed to match the new format
        # This is an easy fix for the time being to make things behave
        ##################################################################
        process_f["integration_time_ms"] = lambda x: asyncio.run_coroutine_threadsafe(self._set_integration_time_ms(x), self.loop)
        process_f["detector_gain"] = lambda x: asyncio.run_coroutine_threadsafe(self._set_gain(x), self.loop)
        process_f["laser_enable"] = lambda x: asyncio.run_coroutine_threadsafe(self._set_laser(x), self.loop)
        process_f["vertical_binning"] = lambda x: asyncio.run_coroutine_threadsafe(self._set_vertical_roi(x), self.loop)

        return process_f

    async def _set_laser(self, value: int) -> SpectrometerResponse:
        log.debug(f"BLE setting laser to {value}")
        buf = bytearray()
        laser_mode = 0
        laser_type = 0
        laser_watchdog = 0
        try:
            buf.append(laser_mode)
            buf.append(laser_type)
            buf.append(value)
            buf.append(laser_watchdog)
            await self.client.write_gatt_char(LASER_STATE_UUID, buf)
        except Exception as e:
            log.error(f"Error trying to set laser {e}")
            return SpectrometerResponse(False,error_msg="error setting laser",error_lvl=ErrorLevel.high)
        return SpectrometerResponse(True)

    async def _set_vertical_roi(self, lines: tuple[int, int]) -> SpectrometerResponse:
        log.debug(f"vertical roi setting to {lines}")
        buf = bytearray()
        try:
            if not self.settings.is_micro():
                log.debug("Detector ROI only configurable on microRaman")
                return SpectrometerResponse(False, error_msg="ROI not supported", error_lvl=ErrorLevel.low)
            try:
                start = lines[0]
                end   = lines[1]
            except:
                log.error("set_vertical_binning requires a tuple of (start, stop) lines")
                return SpectrometerResponse(False, error_msg="invalid start stop lines", error_lvl=ErrorLevel.low)

            if start < 0 or end < 0:
                log.error("set_vertical_binning requires a tuple of POSITIVE (start, stop) lines")
                return SpectrometerResponse(False, error_msg="invalid start stop lines", error_lvl=ErrorLevel.low)

            # enforce ascending order (also, note that stop line is "last line binned + 1", so stop must be > start)
            if start >= end:
                # (start, end) = (end, start)
                log.error("set_vertical_binning requires ascending order (ignoring %d, %d)", start, end)
                return SpectrometerResponse(False, error_msg="invalid start stop lines", error_lvl=ErrorLevel.low)

            b_start = int(start).to_bytes(2, byteorder='big')
            b_end = int(end).to_bytes(2, byteorder='big')
            buf.extend(b_start)
            buf.extend(b_end)
            log.debug(f"making BLE call to set vertical roi to {buf}")
            await self.client.write_gatt_char(DETECTOR_ROI_UUID, buf)
        except Exception as e:
            log.error(f"error trying to set vertical over ble of {e} with values {start} and {end}")
            return SpectrometerResponse(False, error_msg="error trying to set roi", error_lvl=ErrorLevel.low)
        return SpectrometerResponse(True)

    async def _set_integration_time_ms(self, value: int) -> SpectrometerResponse:
        log.debug(f"BLE setting int time to {value}")
        try:
            value_bytes = value.to_bytes(2, byteorder='big')
            await self.client.write_gatt_char(INT_UUID, value_bytes)
        except Exception as e:
            log.error(f"Error trying to write int time {e}")
            return SpectrometerResponse(False, error_msg="error setting int time", error_lvl=ErrorLevel.low)
        return SpectrometerResponse(True)

    async def _disconnect_spec(self) -> SpectrometerResponse:
        await self.client.disconnect()
        return SpectrometerResponse(True)

    async def _connect_spec(self) -> SpectrometerResponse:
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
            await self.client.write_gatt_char(GAIN_UUID, value_bytes)
        except Exception as e:
            log.error(f"Error trying to write gain {e}")
            return SpectrometerResponse(False, error_msg="error trying to write gain", error_lvl=ErrorLevel.medium)
        return SpectrometerResponse(True)

    async def _ble_acquire(self) -> SpectrometerResponse:
        if self.disconnect_event.is_set():
            return SpectrometerResponse(False)
        request = await self.client.write_gatt_char(DEVICE_ACQUIRE_UUID, bytes(0))
        pixels = self.settings.eeprom.active_pixels_horizontal
        spectrum = [0 for pix in range(pixels)]
        request_retry = False
        averaging_enabled = (self.settings.state.scans_to_average > 1)

        if averaging_enabled and not self.settings.state.free_running_mode:

            self.sum_count = 0
            loop_count = self.settings.state.scans_to_average

        else:
            # we're in free-running mode
            loop_count = 1
        reading = None
        for loop_index in range(0, loop_count):
            reading = Reading(self)
            reading.integration_time_ms = self.settings.state.integration_time_ms
            reading.laser_power_perc    = self.settings.state.laser_power_perc
            reading.laser_power_mW      = self.settings.state.laser_power_mW
            reading.laser_enabled       = self.settings.state.laser_enabled
            retry_count = 0
            pixels_read = 0
            header_len = 2
            while (pixels_read < pixels):
                if self.disconnect_event.is_set():
                    return SpectrometerResponse(False)
                if self.disconnect:
                    log.info("Disconnecting, stopping spectra acquire and returning None")
                    return SpectrometerResponse(False)
                if request_retry:
                    retry_count += 1
                    if (retry_count > MAX_RETRIES):
                        log.error(f"giving up after {MAX_RETRIES} retries")
                        return SpectrometerResponse(False)

                delay_ms = int(retry_count**5)

                # if this is the first retry, assume that the sensor was
                # powered-down, and we need to wait for some throwaway
                # spectra 
                if (retry_count == 1):
                    delay_ms = int(self.settings.state.integration_time_ms * THROWAWAY_SPECTRA)

                log.error(f"Retry requested, so waiting for {delay_ms}ms")
                if self.disconnect_event.is_set():
                    return SpectrometerResponse(False)
                await asyncio.sleep(delay_ms)

                request_retry = False

                log.debug(f"requesting spectrum packet starting at pixel {pixels_read}")
                request = pixels_read.to_bytes(2, byteorder="big")
                await self.client.write_gatt_char(SPECTRUM_PIXELS_UUID, request)

                log.debug(f"reading spectrumChar (pixelsRead {pixels_read})");
                response = await self.client.read_gatt_char(READ_SPECTRUM_UUID)

                # make sure response length is even, and has both header and at least one pixel of data
                response_len = len(response);
                if (response_len < header_len or response_len % 2 != 0):
                    log.error(f"received invalid response of {response_len} bytes")
                    request_retry = True
                    continue
                log.info(f"event being set is {self.disconnect_event.is_set()}")
                if self.disconnect_event.is_set():
                    return SpectrometerResponse(False)

                # firstPixel is a big-endian UInt16
                first_pixel = int((response[0] << 8) | response[1])
                if (first_pixel > 2048 or first_pixel < 0):
                    log.error(f"received NACK (first_pixel {first_pixel}, retrying")
                    request_retry = True
                    continue

                pixels_in_packet = int((response_len - header_len) / 2);

                log.debug(f"received spectrum packet starting at pixel {first_pixel} with {pixels_in_packet} pixels");

                for i in range(pixels_in_packet):
                    # pixel intensities are little-endian UInt16
                    offset = header_len + i * 2
                    intensity = int((response[offset+1] << 8) | response[offset])
                    spectrum[pixels_read] = intensity
                    if self.disconnect_event.is_set():
                        return SpectrometerResponse(False)

                    pixels_read += 1

                    if (pixels_read == pixels):
                        log.debug("read complete spectrum")
                        if (i + 1 != pixels_in_packet):
                            log.error(f"ignoring {pixels_in_packet - (i + 1)} trailing pixels");
                        break
                response = None;
            for i in range(4):
                spectrum[i] = spectrum[4]

            spectrum[pixels-1] = spectrum[pixels-2]

            self.session_reading_count += 1
            reading.session_count = self.session_reading_count
            reading.sum_count = self.sum_count
            log.debug("Spectrometer.takeOneAsync: returning completed spectrum");
            reading.spectrum = spectrum

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

        return SpectrometerResponse(reading)

    async def _get_eeprom(self) -> list[list[int]]:
        log.debug("Trying BLE eeprom read")
        pages = []
        for i in range(EEPROM.MAX_PAGES):
            buf = bytearray()
            pos = 0
            for j in range(EEPROM.SUBPAGE_COUNT):
                page_ids = bytearray([i, j])
                log.debug(f"Writing to tell gateway to get page {i} ands ubpage {j}")
                request = await self.client.write_gatt_char(SELECT_EEPROM_PAGE_UUID, page_ids)
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
        fut = asyncio.run_coroutine_threadsafe(self._connect_spec(), self.loop)
        log.debug("asyncio connected to device")
        fut.result()
        fut = asyncio.run_coroutine_threadsafe(self._get_eeprom(), self.loop)
        self.settings.eeprom.buffers = fut.result()
        self.settings.eeprom.read_eeprom()
        self.label = f"{self.settings.eeprom.serial_number} ({self.settings.eeprom.model})"
        return SpectrometerResponse(True)

    def acquire_data(self) -> SpectrometerResponse:
        if self.performing_acquire:
            return SpectrometerResponse(True)
        if self.disconnect:
            return SpectrometerResponse(False)
        self.performing_acquire = True
        self.session_reading_count += 1
        fut = asyncio.run_coroutine_threadsafe(self._ble_acquire(), self.loop)
        self.performing_acquire = False
        result = fut.result()
        return SpectrometerResponse(data=result)

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
            f = self.lambdas.get(setting,None)
            if f is None:
                return
            f(value)

    def get_pid_hex(self) -> str:
        return str(hex(self.pid))[2:]

    def get_vid_hex(self) -> str:
        return str(self.vid)

    def to_dict() -> str:
        return str(self)

    def scans_to_average(self) -> None:
        self.sum_count = 0
        self.settings.state.scans_to_average = int(value)

    def close(self) -> None:
        log.info("BLE close called, trying to disconnect spec")
        self.disconnect = True
        self.disconnect_event.set()
        fut = asyncio.run_coroutine_threadsafe(self._disconnect_spec(), self.loop)
        result = fut.result()

    def handle_requests(self, requests: list[SpectrometerRequest]) -> SpectrometerResponse:
        responses = []
        for request in requests:
            try:
                cmd = request.cmd
                proc_func = self.process_f.get(cmd, None)
                if proc_func == None:
                    responses.append(SpectrometerResponse(error_msg=f"unsupported cmd {request.cmd}", error_lvl=ErrorLevel.low))
                elif request.args == [] and request.kwargs == {}:
                    responses.append(proc_func())
                else:
                    responses.append(proc_func(*request.args, **request.kwargs))
            except Exception as e:
                log.error(f"error in handling request {request} of {e}")
                responses.append(SpectrometerResponse(error_msg="error processing cmd", error_lvl=ErrorLevel.medium))
        return responses

