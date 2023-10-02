##
# Custom logging setup and helper functions. 
#
# The general approach is that the control portion of the application 
# instantiates a MainLogger object. This will create a separate process 
# that looks for log events on a queue. Each process of the application then 
# registers a queue handler, and writes its log events. The MainLogger loop 
# will collect and write these log events to file and any other defined 
# logging location.
#
# @see http://plumberjack.blogspot.com/2010/09/using-logging-with-multiprocessing.html
#
# The overall level of support for this file is basically "someone borrowed and
# modified it from an open-source example on the internet, and it basically works
# so I haven't putzed with it much" (except to try to understand when it breaks).
#
# @note on Windows, define PYTHONUTF8 environment variable to avoid error messages
#       when log messages contain Unicode (default stdout/stderr streams are cp1252)

import os
import sys
import queue            # for exception
import logging
import platform
import traceback
import multiprocessing
from . import utils
from queue import Queue

# ##############################################################################
#                                                                              #
#                    Semi-static, module-level functions                       #
#                                                                              #
# ##############################################################################

explicit_path = None     

def set_location(path):
    global explicit_path
    explicit_path = path

def get_location():
    global explicit_path
    if explicit_path is not None:
        return explicit_path

    module_name = __name__.replace(".", "_") # "wasatch.applog" -> "wasatch_applog"
    filename = "%s.txt" % module_name        # "wasatch_applog.txt"

    if "Linux" in platform.platform():
        return filename

    if "Darwin" in platform.system():
        return filename

    if "macOS" in platform.platform():
        return filename

    pathname = os.path.join("C:\\ProgramData", filename)
    return pathname

##
# Called at the beginning of every subprocess, to tie into the existing
# root logger (owned and created by the MainProcess).
#
# Adds a queue handler object to the root logger to be processed in the 
# main listener.
# 
# Only on Windows though. Apparently Linux will pass the root logger 
# amongst processes as expected, so if you add another queue handler you 
# will get double log prints.
#
# Mimic the capturelog style of just slurping the entire log file contents.
#
# MZ: if we're just interested in the 'tail' of the log, this will be horribly
# inefficient for memory as the log file grows!
def get_text_from_log():
    log_text = ""
    with open(get_location()) as log_file:
        # MZ: why not just 'return log_file.read()'?
        for line_read in log_file:
            log_text += line_read
    return log_text

def log_file_created():
    return os.path.exists(get_location())

## Remove the specified log file and return True if succesful.
def delete_log_file_if_exists():
    pathname = get_location()
    if os.path.exists(pathname):
        os.remove(pathname)
    return not os.path.exists(pathname)

def explicit_log_close():
    root_log = logging.getLogger()
    root_log.debug("applog.explicit_log_close: closing and removing all handlers")
    handlers = root_log.handlers[:]
    for handler in handlers:
        handler.close()
        root_log.removeHandler(handler)

# ##############################################################################
#                                                                              #
#                                MainLogger                                    #
#                                                                              #
# ##############################################################################

class MainLogger(object):
    FORMAT = u'%(asctime)s [0x%(thread)08x] %(name)s %(levelname)-8s %(message)s'

    def __init__(self, 
            log_level=logging.DEBUG, 
            enable_stdout=True,
            logfile=None,
            timeout_sec=5,
            append_arg="True"):
        self.log_queue     = Queue() 
        self.log_level     = log_level
        self.enable_stdout = enable_stdout
        self.logfile       = logfile            
        self.timeout_sec   = timeout_sec

        if self.logfile is not None:
            set_location(self.logfile)

        # append file size limits are enforced upon program restart
        if append_arg.lower() == "true":
            # limit to 300mb if --log-append is explicitly set to "True"
            append = True
        if append_arg.lower() == "false":
            # when append is a falsy value, the log file is always reset on reboot
            append = False
        if append_arg.lower() == "limit":
            # the default --log-append keeps up to 2mb between sessions
            append = 2*1024*1024

        root_log = logging.getLogger()
        self.log_configurer(self.logfile, append)
        root_log.setLevel(self.log_level)
        root_log.warning("Top level log configuration (%d handlers, get_location %s)", len(root_log.handlers), get_location())

    ## Setup file handler and command window stream handlers. Every log
    #  message received on the queue handler will use these log configurers. 
    def log_configurer(self, logfile=None, append=False):
        if logfile is not None:
            pathname = logfile
        elif self.logfile is not None:
            pathname = self.logfile
        else:
            pathname = get_location()

        try:
            if type(append) == int:
                utils.resize_file(path=pathname, nbytes=append)
        except (IOError, FileNotFoundError):
            print("Unable to truncate log file.")

        root_logger = logging.getLogger()
        fh = logging.FileHandler(pathname, mode='a' if append else 'w', encoding='utf-8') 
        formatter = logging.Formatter(self.FORMAT)
        fh.setFormatter(formatter)
        root_logger.addHandler(fh) 

        if self.enable_stdout:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            root_logger.addHandler(stream_handler)

        self.root = root_logger

    ## Wrapper to add a None poison pill to the listener process queue to
    #  ensure it exits. 
    def close(self):
        self.log_queue.put_nowait(None)
        try:
            self.listener.join()
        except:
            pass
