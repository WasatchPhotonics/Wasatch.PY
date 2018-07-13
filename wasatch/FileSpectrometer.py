import logging
import json
import os

from SpectrometerSettings import SpectrometerSettings

log = logging.getLogger(__name__)

##
# Interface to a remote "virtual spectrometer" with which ENLIGHTEN exchanges
# commands and spectra via a watch-directory.
#
# @section theory Theory of Operation
# 
# We pass in a monitor_dir to WasatchBus.  WasatchBus now knows to check
# that directory whenever WasatchBus.update() is called.
# 
# When WasatchBus.update() finds that monitor_dir exists, is writable and
# contains "spectrometer.json", it will add a device with UID /path/to/monitor_dir
# to bus.devices.
# 
# The next time Controller.connect_new() is called, it will iterate over all
# of bus.devices to see if any are not yet in Controller.serial_by_device
# (whose keys are WasatchDevice objects, each of which has a .uid attribute
# for comparison with the new bus.devices uid).
# 
# Assuming the FileSpectrometer is not yet connected, connect_new will then
# instantiate a WasatchDeviceWrapper with the UID (/path/to/monitor_dir),
# and then call connect() on that wrapper.
# 
# The wrapper will then fork a new process, and 
# WasatchDeviceWrapper<subprocess>.continuous_poll will instantiate a 
# WasatchDevice with the given UID (/path/to/monitor_dir).
# 
# WasatchDevice will see that the UID is not a VID:PID pair, but a path, and
# will attempt to instantiate a FileSpectrometer with the UID.
# 
# On instantiation (or on connect()), the FileSpectrometer will load the 
# spectrometer.json file and use it to populate a SpectrometerSettings object.
# 
# On successfull connect(), WasatchDeviceWrapper<subprocess> will pass the 
# SpectrometerSettings object back to WasatchDeviceWrapper, which will then
# notify Controller that the connection was successful.  Controller.connect_new
# will retain a reference to the <MainProcess> version of WasatchDeviceWrapper
# as "self.device" (future: member of self.devices) and kick-off 
# Controller.initialize_new_device to configure the GUI.
# 
# Meanwhile, Controller.acquire_reading standard main_timer will continue calling
# device.acquire_data (technically, Controller.tick -> Controller.attempt_reading 
# -> Controller.acquire_data), which will check the interprocess queue
# used for Reading objects to see if any are available to be graphed and
# processed as spectra.
# 
# When the Controller GUI needs to send commands and settings "downstream"
# to the FileSpectrometer, it will use the standard device.change_setting
# function call, which will get routed to FileSpectrometer exactly the same
# as ControlObjects get routed to FID and SP spectrometers.  In this case,
# each ControlObject will be serialized to a sequentially named file and 
# dropped into monitor_dir for pickup and processing by the other application.
# 
# @section use_case Use Case
# 
# This is for the unusual use-case where ENLIGHTEN is not actually talking
# (directly) to a live spectrometer over USB, but being used as a display
# GUI for some other program or process.  
# 
# As an initial (inefficient) communication method, we're going to monitor 
# a specific directory for files of the form "spectra*.csv" (which we will
# read and display), and write files of the form "cmd*.csv" (which the other
# program will have the opportunity to read and execute).  
# 
# The assumption is that both programs will delete files as they are read and 
# processed, and that files will be named with ascending timestamps to 
# indicate order of processing.
# 
# This is basically an informal, unsynchronized read-write queue.  We could
# of course use sockets, FIFOs, SOAP or REST interfaces etc, but I'm starting
# with this.
# 
# The class expects the directory to exist, be writable, and to contain a
# file "spectrometer.json" which it will use to initialize SpectrometerSettings.
# 
# Basic idea:
#     - human operator creates spectrometer.json with number of pixels, wavecal, 
#       other basic SpectrometerSettings requirements
#     - human launches other application with default settings, which starts
#       dumping spectra into the directory
#     - human launches ENLIGHTEN, which reads spectrometer.json, initializes a
#       virtual spectrometer, and starts picking up spectra*.csv files and 
#       feeding those back up through WasatchDeviceWrapper's queue to be graphed
#       by the GUI.
#
class FileSpectrometer(object):
    
    def __init__(self, directory):
        self.directory = directory

        self.configfile = os.path.join(self.directory, "spectrometer.json")
        self.command_count = 0

        self.settings = SpectrometerSettings()

    def connect(self):
        if not os.access(self.directory, os.W_OK):
            return False
            
        if not os.path.isfile(self.configfile):
            return False

        self.erase_commands()

        try:
            with open(self.configfile, 'r') as infile:
                json = infile.read()
            self.settings.update_from_json(json) 
        except:
            log.critical("unable to parse %s", self.configfile, exc_info=1)
            return False

        self.erase_commands()
        return True

    def disconnect(self):
        pass

    # ##########################################################################
    #                                                                          #
    #                               Business Logic                             #
    #                                                                          #
    # ##########################################################################

    ## Start by deleting all command files in the monitor directory,
    #  assuming they are cruft from previous runs
    def erase_commands(self):
        for filename in os.listdir(self.directory):
            if filename.startswith("command") and filename.endswith("csv"):
                pathname = os.path.join(self.directory, filename)
                log.debug("erase_commands: deleting %s", pathname)
                os.remove(pathname)

    def write_setting(self, control_object):
        cmd = "%s,%s" % (control_object.setting, control_object.value)
        self.command_count += 1
        filename = "command-%08d.csv" % self.command_count
        pathname = os.path.join(self.directory, filename)
        with open(pathname + ".tmp", "w") as outfile:
            outfile.write(cmd)

        # atomic rename to reduce conflicts with remote application
        os.rename(pathname + ".tmp", pathname)
        log.debug("wrote (%s) to %s", cmd, pathname)

        # needed to enable acquire
        if control_object.setting == "integration_time_ms":
            self.settings.state.integration_time_ms = int(control_object.value)

    def get_line(self):
        pathname = None
        for filename in sorted(os.listdir(self.directory)):
            if filename.startswith("spectrum") and filename.endswith("csv"):
                pathname = os.path.join(self.directory, filename)
                break

        if not pathname:
            return # no spectra to load...still "acquiring"

        # read the spectrum
        spectrum = []
        with open(pathname, "r") as infile:
            for line in infile:
                values = [x.strip() for x in line.split(',')]
                if len(values) > 1:
                    y = values[1]
                else:
                    y = values[0]
                spectrum.append(int(y))

        # now that we've read the spectrum, delete the file
        try:
            os.remove(pathname)
        except:
            log.debug("error deleting %s", pathname, exc_info=1)

        # truncate or pad spectrum to match expected number of pixels
        pixels = len(spectrum)
        if pixels != self.settings.pixels():
            log.warn("Read spectrum of %d pixels from %s (expected %d per %s)", 
                pixels, pathname, self.settings.pixels(), self.configfile)
            if pixels < self.settings.pixels():
                spectrum.extend([0] * (self.settings.pixels() - pixels))
            else:
                spectrum = spectrum[:-self.settings.pixels()]

        # note that no normalization is applied; it is possible that the input 
        # spectrum may include negative values, or values greater than 2^16 etc

        return spectrum

    # ##########################################################################
    #                                                                          #
    #                                 Accessors                                #
    #                                                                          #
    # ##########################################################################

    def get_microcontroller_firmware_version(self):
        pass

    def get_fpga_firmware_version(self):
        pass

    def get_integration_time(self):
        pass

    def get_detector_gain(self):
        pass

    def select_adc(self):
        pass

    def get_secondary_adc_raw(self):
        return 0

    def get_secondary_adc_calibrated(self, raw):
        return 0

    def get_detector_temperature_raw(self):
        return 0

    def get_detector_temperature_degC(self, raw):
        return 0
