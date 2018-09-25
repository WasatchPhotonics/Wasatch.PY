#!/usr/bin/env python -u
################################################################################
#                               wasatch-shell.py                               #
################################################################################
#                                                                              #
#  DESCRIPTION:  An interactive wrapper over a wasatch.WasatchDevice.          #
#                                                                              #
#  EXAMPLE:      $ ./wasatch-shell.py [--logfile path]                         #
#                  open                                                        #
#                  set_integration_time_ms                                     #
#                  100                                                         #
#                  get_spectrum                                                #
#                  close                                                       #
#                                                                              #
################################################################################

import platform
import argparse
import readline
import logging
import time
import sys
import os
import re

import wasatch

from wasatch import utils
from wasatch.WasatchBus         import WasatchBus
from wasatch.WasatchDevice      import WasatchDevice
from wasatch.BalanceAcquisition import BalanceAcquisition

VERSION = "2.0.3"

log = logging.getLogger(__name__)

class WasatchShell(object):
    
    def __init__(self):
        self.device = None
        self.interpolated_x_axis_cm = None

        # process command-line options
        parser = argparse.ArgumentParser()
        parser.add_argument("--logfile", help="where to write log messages")
        parser.add_argument("--log-level", type=str, default="info", help="logging level", choices=['debug', 'info', 'warning', 'error', 'critical'])
        self.args = parser.parse_args()

        self.configure_logging()

        # pass-through calls to any of these gettors (note names are lowercased)
        self.gettors = {}
        for func_name in [ 
            "get_actual_frames",
            "get_actual_integration_time_us",
            "get_ccd_sensing_threshold",
            "get_ccd_threshold_sensing_mode",
            "get_ccd_trigger_source",
            "get_dac",
            "get_detector_gain",
            "get_detector_offset",
            "get_detector_temperature_degC",
            "get_detector_temperature_raw",
            "get_external_trigger_output",
            "get_fpga_firmware_version",
            "get_integration_time_ms",
            "get_interlock",
            "get_laser_enabled",
            "get_laser_mod_duration",
            "get_laser_mod_enabled",
            "get_laser_mod_period",
            "get_laser_mod_pulse_delay",
            "get_laser_mod_pulse_width",
            "get_laser_power_ramping_enabled",
            "get_laser_temperature_degC",
            "get_laser_temperature_raw",
            "get_link_laser_mod_to_integration_time",
            "get_microcontroller_firmware_version",
            "get_opt_actual_integration_time",
            "get_opt_area_scan",
            "get_opt_cf_select",
            "get_opt_data_header_tab",
            "get_opt_horizontal_binning",
            "get_opt_integration_time_resolution",
            "get_opt_has_laser",
            "get_opt_laser_control",
            "get_secondary_adc_calibrated",
            "get_secondary_adc_raw",
            "get_selected_adc",
            "get_sensor_line_length",
            "get_tec_enabled",
            "get_tec_enabled",
            "get_trigger_delay",
            "get_vr_continuous_ccd",
            "get_vr_num_frames" ]:
            self.gettors[func_name.lower()] = func_name

    # ##############################################################################
    # Utility Functions
    # ##############################################################################

    def usage(self):
        print "Version: %s" % VERSION
        print """The following commands are supported:
        help                           - this screen
        version                        - program and library versions
                                       
        open                           - initialize connected spectrometer
        close                          - exit program (synonyms 'exit', 'quit')
        connection_check               - confirm communication
                                       
        set_integration_time_ms        - takes integer argument
        set_laser_enable               - takes bool argument (on/off, true/false, 1/0)
        set_laser_power_mw             - takes float argument
        set_laser_power_ramping_enable - gradually ramp laser power in software
        set_tec_enable                 - takes bool argument
        set_detector_tec_setpoint_degc - takes float argument
        set_detector_offset            - override the "offset" added to pixel readings

        set_interpolated_x_axis_cm     - takes start, end, incr (zero incr to disable)
        balance_acquisition            - takes mode [integ, laser, laser_and_integ], 
                                            intensity, threshold, x, unit [px, nm, cm]

        get_spectrum                   - print received spectrum
        get_spectrum_pretty            - graph received spectrum
        get_spectrum_save              - save spectrum to filename as CSV
        get_config_json                - return EEPROM as JSON string
        get_all                        - calls all gettors
        """
        print "The following gettors are also available:"
        for k in sorted(self.gettors.keys()):
            print "        %s" % k

    def disconnected(self):
        self.display("ERROR: no device connected")

    def parse_result(self, result):
        return "1" if result else "0"

    ## encapsulating in case any platforms don't like GNU readline
    def get_line(self, prompt):
        return raw_input(prompt).strip()

    def get_next(self, tok):
        if not tok:
            line = self.get_line()
            log.info("<< %s", line)
            line = line.lower()
            for s in line.split():
                tok.append(s)
        return tok.pop(0)

    def read_bool(self, tok):
        s = self.get_next(tok)
        return re.match("1|true|yes|on", s.lower())

    def read_int(self, tok):
        return int(self.get_next(tok))

    def read_float(self, tok):
        return float(self.get_next(tok))

    def display(self, msg):
        log.info(">> %s", msg)
        print msg
        sys.stdout.flush()

    def configure_logging(self):
        logging.basicConfig(filename=(self.args.logfile if self.args.logfile else ("wasatch-%s.log" % utils.timestamp())),
                            level=self.args.log_level.upper(),
                            format='%(asctime)s.%(msecs)03d %(name)s %(levelname)-8s %(message)s', 
                            datefmt='%m/%d/%Y %I:%M:%S')

    # ##############################################################################
    # command loop
    # ##############################################################################

    def run(self):
        self.display("-" * 80)
        self.display("wasatch-shell version %s invoked (Wasatch.PY %s)" % (VERSION, wasatch.version))

        try:
            while True:
                line = self.get_line('wp> ')

                # ignore comments
                if line.startswith('#') or len(line) == 0:
                    continue

                log.info("<< %s", line)
                line = line.lower()

                # tokenize
                tok = line.split()
                command = tok.pop(0)

                # these commands always work
                if command == "help":
                    self.usage()
                    continue

                elif command == "version":
                    self.display("WasatchShell version: %s" % VERSION)
                    self.display("Wasatch.PY   version: %s" % wasatch.version)
                    continue

                if re.match("close|quit|exit", command):
                    break

                # these commands work if currently closed
                if self.device is None:
                    if command == "open":
                        self.display("1" if self.open() else "0")
                    else:
                        self.display("ERROR: must open spectrometer first")

                else:
                    try:
                        # anything past this point assumes spectrometer already open

                        # pass-through gettors
                        if command in self.gettors:
                            self.run_gettor(command)
                            
                        # special processing for these
                        elif command == "get_spectrum":
                            self.get_spectrum(quiet=False)

                        elif command == "get_spectrum_pretty":
                            self.get_spectrum_pretty()

                        elif command == "get_spectrum_save":
                            self.get_spectrum_save(tok)

                        elif command == "get_config_json":
                            self.display(self.device.settings.eeprom.json())

                        elif command == "get_all":
                            self.get_all()

                        elif command == "connection_check":
                            self.run_gettor("get_integration_time_ms")

                        # currently these are the only setters implemented
                        elif command == "set_integration_time_ms":
                            self.device.hardware.set_integration_time_ms(self.read_int(tok))
                            self.display(1)

                        elif command == "set_laser_power_mw":
                            self.device.hardware.set_laser_power_mW(self.read_float(tok))
                            self.display(1)

                        elif command == "set_laser_enable":
                            self.device.hardware.set_laser_enable(self.read_bool(tok))
                            self.display(1)

                        elif command == "set_tec_enable":
                            self.device.hardware.set_tec_enable(self.read_bool(tok))
                            self.display(1)

                        elif command == "set_detector_tec_setpoint_degc":
                            self.device.hardware.set_detector_tec_setpoint_degC(self.read_float(tok))
                            self.display(1)

                        elif command == "set_laser_power_ramping_enable":
                            self.device.hardware.set_laser_power_ramping_enable(self.read_bool(tok))

                        elif command == "set_detector_offset":
                            self.device.hardware.set_detector_offset(self.read_int(tok))

                        elif command == "balance_acquisition":
                            self.balance_acquisition(tok)

                        elif command == "set_interpolated_x_axis_cm":
                            self.set_interpolated_x_axis_cm(start = self.read_float(tok),
                                                            end   = self.read_float(tok),
                                                            incr  = self.read_float(tok))
                        else:
                            self.display("ERROR: unknown command: " + command)
                    except Exception as ex:
                        log.critical("caught exception")
                        log.info("disconnecting")
                        self.device.disconnect()
                        self.device = None

                        log.info("sleeping 5sec")
                        time.sleep(5.100)
                        
                        log.info("re-opening")
                        if self.open():
                            log.info("successfully re-opened")
                        else:
                            log.error("could not re-open...giving up")
                            break

                # whatever happend, flush stdout
                try:
                    sys.stdout.flush()
                except:
                    self.display("ERROR: caller has closed stdout...exiting")
                    break

        except Exception as e:
            log.error(e, exc_info=1)
            raise

        # disable the laser if connected
        if self.device is not None:
            self.device.hardware.set_laser_enable(False)
            self.device.disconnect()
            self.device = None

        log.info("wasatch-shell exiting")

    ## 
    # If the current device is disconnected, and there is a new device, 
    # attempt to connect to it. """
    def open(self):
        # if we're already connected, nevermind
        if self.device is not None:
            return False

        # lazy-load a USB bus
        log.debug("instantiating WasatchBus")
        bus = WasatchBus()
        if not bus.devices:
            self.display("No Wasatch USB spectrometers found.")
            return False

        log.debug("open: trying to connect to new device on bus 1")
        uid = bus.devices[0]
        device = WasatchDevice(uid)

        ok = device.connect()
        if not ok: 
            log.critical("open: can't connect to device on bus 1")
            return

        log.info("open: device connected")
        self.device = device

        # enable exceptions
        self.device.hardware.raise_exceptions = True

        # throw random errors
        # self.device.hardware.random_errors = True

        # validate gettors (in case this is used against a StrokerProtocol unit, for instance)
        for func_name in self.gettors.keys():
            if not hasattr(device.hardware, self.gettors[func_name]):
                self.display("WARNING: gettor %s (%s) not found in device" % (func_name, self.gettors[func_name]))
                del self.gettors[func_name]

        # default to minimum integration time
        self.device.hardware.set_integration_time_ms(self.device.settings.eeprom.min_integration_time_ms)

        return True

    def run_gettor(self, command):
        func_name = self.gettors[command]
        value = getattr(self.device.hardware, func_name)()
        if isinstance(value, bool):
            self.display(1 if value else 0)
        else:
            self.display(value)

    def get_spectrum(self, quiet=True):
        reading = self.device.acquire_data()
        if reading is None or reading.spectrum is None:
            self.display("ERROR: get_spectrum failed")
            return
        spectrum = reading.spectrum

        log.debug("received %d pixels", len(spectrum))

        if self.interpolated_x_axis_cm and self.device.settings.wavenumbers:
            spectrum = utils.interpolate_array(spectrum, 
                                               self.device.settings.wavenumbers, 
                                               self.interpolated_x_axis_cm)

        if quiet:
            return spectrum
        else:
            for pixel in spectrum:
                print pixel

    def get_spectrum_save(self, tok):
        spectrum = self.get_spectrum()
        if spectrum is None:
            return 

        filename = tok[0] if tok[0] else datetime.datetime.now().strftime("%Y%m%d-%H%M%S.csv")
        with open(filename, "w") as outfile:
            for i in range(len(spectrum)):
                if self.interpolated_x_axis_cm:
                    x = self.interpolated_x_axis_cm[i]
                elif self.device.settings.wavenumbers:
                    x = self.device.settings.wavenumbers[i]
                else:
                    x = self.device.settings.wavelengths[i]
                outfile.write("%.2f,%d\n" % (x, spectrum[i]))

    def get_spectrum_pretty(self):
        spectrum = self.get_spectrum()
        if spectrum is None:
            return 

        if self.interpolated_x_axis_cm:
            x_axis = self.interpolated_x_axis_cm
            x_unit = "cm-1"
        else:
            x_axis = self.device.settings.wavelengths
            x_unit = "nm"

        lines = utils.ascii_spectrum(spectrum=spectrum, rows=24, cols=80, x_axis=x_axis, x_unit=x_unit)
        for line in lines:
            self.display(line)

    def set_interpolated_x_axis_cm(self, start, end, incr):
        if incr == 0:
            self.interpolated_x_axis_cm = None
            self.display(0)
            return

        axis = []
        x = start
        while x <= end:
            axis.append(x)
            x += incr

        self.interpolated_x_axis_cm = axis
        self.display(1)

    def balance_acquisition(self, tok):
        unit = "px"
        x_value = None
        threshold = 2500
        intensity = 45000
        mode = "integration"
        
        if len(tok) > 5:
            return self.display("ERROR: balance takes mode [integ, laser, laser_and_integ], intensity, threshold, x-value, unit [px, nm, cm]")

        if len(tok) == 5:
            s = tok[4].strip().lower()
            if re.match('(px|cm|nm)$', s):
                unit = s
            else:
                return self.display("ERROR: invalid unit " + s)
        
        if len(tok) >= 4:
            x_value = float(tok[3])

        if len(tok) >= 3:
            threshold = float(tok[2])

        if len(tok) >= 2:
            intensity = float(tok[1])

        if len(tok) >= 1:
            mode = tok[0]

        if x_value is not None:
            if unit == "px":
                pixel = int(x_value)
            elif unit == "nm":
                pixel = utils.find_nearest_index(self.device.settings.wavelengths, x_value)
            elif unit == "cm" and self.device.settings.wavenumbers:
                pixel = utils.find_nearest_index(self.device.settings.wavenumbers, x_value)
            else:
                return self.display("ERROR: can't determine pixel from %s %s" % (x_value, unit))

        if self.device.balance_acquisition(mode, intensity, threshold, pixel):
            self.display("Ok integration_time_ms %s laser_power %s %s" % (
                self.device.settings.state.integration_time_ms,
                self.device.settings.state.laser_power, 
               "mW" if self.device.settings.state.laser_power_in_mW else "percent"))

    def get_all(self):
        for command in sorted(self.gettors):
            func_name = self.gettors[command]
            value = getattr(self.device.hardware, func_name)()
            if isinstance(value, bool):
                value = 1 if value else 0
            self.display("%-40s: %s" % (command, value))

# ##############################################################################
# main()
# ##############################################################################

shell = None
if __name__ == "__main__":
    shell = WasatchShell()
    shell.run()
