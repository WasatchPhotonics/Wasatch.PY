#!/usr/bin/env python -u

import re
import sys
import time
import json
import logging
import datetime
import argparse
import multiprocessing

import wasatch
from wasatch import applog

from wasatch.CommandSettings      import CommandSettings
from wasatch.WasatchDeviceWrapper import WasatchDeviceWrapper
from wasatch.WasatchBus           import WasatchBus

log = logging.getLogger(__name__)

class Shell(object):

    ############################################################################
    #                                                                          #
    #                               Lifecycle                                  #
    #                                                                          #
    ############################################################################

    def __init__(self):
        self.bus         = None
        self.device      = None
        self.main_logger = None
        self.exiting     = False

        self.args = self.parse_args(sys.argv)
        self.command_settings = CommandSettings()

        self.main_logger = applog.MainLogger(self.args.log_level, enable_stdout=False)
        log.info("-" * 80)
        log.info("Wasatch.PY %s Shell", wasatch.version)

    ############################################################################
    #                                                                          #
    #                             Command-Line Args                            #
    #                                                                          #
    ############################################################################

    def parse_args(self, argv):
        parser = argparse.ArgumentParser(description="Simple demo to acquire spectra from command-line interface")
        parser.add_argument("--log-level", type=str, default="info", help="logging level", choices=['debug', 'info', 'warning', 'error', 'critical'])
        args = parser.parse_args(argv[1:])

        # normalize log level
        args.log_level = args.log_level.upper()
        if not re.match("^(DEBUG|INFO|ERROR|WARNING|CRITICAL)$", args.log_level):
            print "Invalid log level: %s (defaulting to INFO)" % args.log_level
            args.log_level = "INFO"

        return args
        
    ############################################################################
    #                                                                          #
    #                              USB Devices                                 #
    #                                                                          #
    ############################################################################

    def connect(self):
        """ If the current device is disconnected, and there is a new device, 
            attempt to connect to it. """

        # if we're already connected, nevermind
        if self.device is not None:
            return False

        # lazy-load a USB bus
        if self.bus is None:
            log.debug("instantiating WasatchBus")
            self.bus = WasatchBus()

        if not self.bus.devices:
            log.error("No Wasatch USB spectrometers found.")
            return False

        uid = self.bus.devices[0]
        log.debug("connect: trying to connect to %s", uid)

        # this is still buggy on MacOS
        log.debug("instantiating WasatchDeviceWrapper")
        device = WasatchDeviceWrapper(
            uid=uid,
            bus_order=0,
            log_queue=self.main_logger.log_queue,
            log_level=self.args.log_level)

        ok = device.connect()
        if not ok:
            log.critical("connect: can't connect to %s", uid)
            return False

        log.info("connect: device connected")

        self.device = device
        return True

    ############################################################################
    #                                                                          #
    #                               Run-Time Loop                              #
    #                                                                          #
    ############################################################################

    def run(self):
        while not self.exiting:
            
            logging.debug("waiting for line")
            sys.stdout.write("wasatch> ")
            line = sys.stdin.readline().strip().lower()
            logging.debug("received line: " + line);
            
            tok = line.split(" ")
            command = tok[0]

            # ignore comments
            if line.startswith('#') or len(line) == 0:
                pass

            elif command == "open":
                if self.connect():
                    print 1
                else:
                    print 0

            elif command == "set":
                if not self.device:
                    self.do_disconnected()
                    continue
                    
                # note: float[] values should be comma-delimited (no extra spaces)
                if len(tok) != 3:
                    log.error("set syntax error: expected 3 tokens")
                else:
                    setting = tok[1]
                    if self.command_settings.valid(setting):
                        value = self.device.convert_type(setting, tok[2])
                        self.device.change_setting(setting, value)
                    else:
                        print "invalid setting: %s" % setting

            elif command == "get_reading":
                if not self.device:
                    self.do_disconnected()
                    continue

                reading = self.do_reading()
                if reading:
                    # can't dump Reading directly because of datetime :-(
                    print json.dumps(reading.__dict__, indent=4, sort_keys=True, default=str)

            elif command == "get_config":
                if not self.device:
                    self.do_disconnected()
                    continue

                print json.dumps(self.device.settings.__dict__, indent=4, sort_keys=True, default=str)

            elif command == "close" or command == "quit" or command == "exit":
                if self.device:
                    self.device.disconnect()
                self.exiting = True

            elif command == "help":
                self.do_help()

            else:
                log.error("unknown command: %s", command)

    ############################################################################
    #                                                                          #
    #                           Command Implementation                         #
    #                                                                          #
    ############################################################################

    def do_disconnected(self):
        log.error("not connected")
        print 0

    def do_reading(self):
        # block until we have a reading (TODO: add timeout)
        while True:
            try:
                reading = self.device.acquire_data()
                if reading:
                    return reading
                else:
                    log.debug("waiting on next reading")
                    time.sleep(0.01) # 10ms
            except Exception:
                log.critical("attempt_reading caught exception", exc_info=1)
                self.exiting = True
                return

            if reading.failure:
                log.critical("Hardware ERROR %s", reading.failure)
                log.critical("Device has been disconnected")
                self.device.disconnect()
                self.device = None
                self.exiting = True
                return

    def do_help(self):
        print "Wasatch.PY %s (C) 2018, Wasatch Photonics" % wasatch.version
        print ""
        print "Supported commands:"
        print "  get_reading            - retrieve a measurement as JSON string"
        print "  set <setting> <value>  - change a given spectrometer setting"
        print ""
        print "Supported command settings:"
        for setting in self.command_settings.get_settings():
            print "  %-7s %s" % (self.command_settings.get_datatype(setting), setting)

################################################################################
# main()
################################################################################

if __name__ == '__main__':
    multiprocessing.freeze_support()

    shell = Shell()
    shell.run()

    if shell.device:
        log.debug("closing background thread")
        shell.device.disconnect()

    if shell.main_logger:
        log.debug("closing logger")
        shell.main_logger.close()

    log.info("done")
