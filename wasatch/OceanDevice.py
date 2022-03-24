import re
import os
import usb
import time
import queue
import logging
import datetime

import seabreeze
seabreeze.use("pyseabreeze")
import seabreeze.spectrometers as sb
from seabreeze.spectrometers import Spectrometer, list_devices

from .SpectrometerSettings        import SpectrometerSettings
from .SpectrometerResponse        import SpectrometerResponse
from .SpectrometerResponse        import ErrorLevel
from .SpectrometerState           import SpectrometerState
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

        #self.lock = threading.Lock()

        self.connected = False

        # Receives ENLIGHTEN's 'change settings' commands in the spectrometer
        # process. Although a logical queue, has nothing to do with multiprocessing.
        self.command_queue = []

        self.immediate_mode = False

        self.settings = SpectrometerSettings(self.device_id)
        self.summed_spectra         = None
        self.sum_count              = 0
        self.session_reading_count  = 0
        self.take_one               = False
        self.failure_count          = 0

        self.process_id = os.getpid()
        self.last_memory_check = datetime.datetime.now()
        self.last_battery_percentage = 0
        self.init_lambdas()

    def connect(self):
        self.device = None
        try:
            devices = list_devices()
        except:
            devices = list_devices()
        for device in devices:
            pyusb_device = device._raw_device.pyusb_device
            if pyusb_device.idVendor == self.device_id.vid and pyusb_device.idProduct == self.device_id.pid:
                self.device = device
        if self.device == None:
            log.error("Ocean Device: No ocean device found. Returning")
            self.message_queue.put_nowait(None)
            return SpectrometerResponse(data=False,error_msg="No ocean devices found")
        self.spec = Spectrometer(self.device)
        self.settings.eeprom.model = self.device.model
        self.settings.eeprom.serial_number = self.device.serial_number
        self.settings.eeprom.active_pixels_horizontal = self.device.features['spectrometer'][0]._spectrum_num_pixel 
        self.settings.eeprom.detector = "Ocean" # Ocean API doesn't have access to detector info
        return SpectrometerResponse(data=True)


    def init_lambdas(self):
        f = {}
        f["integration_time_ms"] = lambda x: self.spec.integration_time_micros(int(round(x*1000))) # conversion from millisec to microsec
        self.lambdas = f

    def acquire_data(self):
        self.settings.wavelengths = self.spec.wavelengths()# setting wavelengths one init doesn't work for some reaons
        reading = self.take_one_averaged_reading()
        return reading

    def take_one_averaged_reading(self):
        averaging_enabled = (self.settings.state.scans_to_average > 1)

        if averaging_enabled and not self.settings.state.free_running_mode:
            # collect the entire averaged spectrum at once (added for
            # BatchCollection with laser delay)
            #
            # So: we're NOT in "free-running" mode, so we're basically being
            # slaved to parent process and doing exactly what is requested
            # "on command."  That means we can perform a big, heavy blocking
            # scan average all at once, because they requested it.
            self.sum_count = 0
            loop_count = self.settings.state.scans_to_average
        else:
            # we're in free-running mode
            loop_count = 1

        log.debug("take_one_averaged_reading: loop_count = %d", loop_count)

        # either take one measurement (normal), or a bunch (blocking averaging)
        reading = None
        for loop_index in range(0, loop_count):

            # start a new reading
            # NOTE: reading.timestamp is when reading STARTED, not FINISHED!
            reading = Reading(self.device_id)

            # TODO...just include a copy of SpectrometerState? something to think
            # about. That would actually provide a reason to roll all the
            # temperature etc readouts into the SpectrometerState class...
            try:
                reading.integration_time_ms = self.settings.state.integration_time_ms
                reading.laser_power_perc    = self.settings.state.laser_power_perc
                reading.laser_power_mW      = self.settings.state.laser_power_mW
                reading.laser_enabled       = self.settings.state.laser_enabled
                reading.spectrum = list(self.spec.intensities())
            except usb.USBError:
                self.failure_count += 1
                log.error(f"Ocean Device: encountered USB error in reading for device {self.device}")

            if reading.spectrum is None or reading.spectrum == []:
                if self.failure_count > 3:
                    return SpectrometerResponse(data=False,error_msg="failed to acquire spectra")

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

            # count spectra
            self.session_reading_count += 1
            reading.session_count = self.session_reading_count
            reading.sum_count = self.sum_count

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

            # were we told to only take one (potentially averaged) measurement?
            if self.take_one and reading.averaged:
                log.debug("completed take_one")
                self.change_setting("cancel_take_one", True)

        log.debug("device.take_one_averaged_reading: returning %s", reading)
        if reading.spectrum is not None and reading.spectrum != []:
            self.failure_count = 0
        # reading.dump_area_scan()
        return SpectrometerResponse(data=reading)

    def change_setting(self,setting,value):
        if setting == "scans_to_average":
            self.sum_count = 0
            self.settings.state.scans_to_average = int(value)
            return
        f = self.lambdas.get(setting, None)
        if f is None:
            # quietly fail no-ops
            return SpectrometerResponse(data=False)

        return f(value)