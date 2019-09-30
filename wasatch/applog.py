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

import os
import sys
import queue            # for exception
import logging
import platform
import traceback
import multiprocessing

# ##############################################################################
#                                                                              #
#                    Semi-static, module-level functions                       #
#                                                                              #
# ##############################################################################

explicit_path = None

##
# Determine the location to store the log file. Current directory
# on Linux, or %PROGRAMDATA% on windows - usually C:\ProgramData 
def get_location():
    if explicit_path is not None:
        return explicit_path

    # For convenience, replace any dots with underscores to help windows know
    # it is a text file.
    module_name = __name__.replace(".", "_") # "wasatch.applog" -> "wasatch_applog"
    filename = "%s.txt" % module_name        # "wasatch_applog.txt"

    if "Linux" in platform.platform():
        return filename

    if "Darwin" in platform.platform():
        return filename

    pathname = os.path.join("C:\\ProgramData", filename)
    return pathname

    # The following doesn't seem to work so well under Python 3.4 :-/

    # print "applog.get_location: platform.platform() = %s" % platform.platform()
    log_dir = ""
    try:
        # get pathname to C:\ProgramData (but with a lot more work)
        import ctypes
        from ctypes import wintypes, windll
        CSIDL_COMMON_APPDATA = 35
        _SHGetFolderPath = windll.shell32.SHGetFolderPathW
        _SHGetFolderPath.argtypes = [wintypes.HWND,
                                     ctypes.c_int,
                                     wintypes.HANDLE,
                                     wintypes.DWORD, 
                                     wintypes.LPCWSTR]

        path_buf = wintypes.create_unicode_buffer(wintypes.MAX_PATH)
        result = _SHGetFolderPath(0, CSIDL_COMMON_APPDATA, 0, 0, path_buf)
        log_dir = path_buf.value
    except:
        print("applog.get_location: problem assigning log directory")

    pathname = "%s/%s" % (log_dir, filename)
    print("applog.get_location: pathname = %s" % pathname)
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
def process_log_configure(log_queue, log_level=logging.DEBUG):
    root_log = logging.getLogger()
    if "Windows" in platform.platform():
        queue_handler = QueueHandler(log_queue)
        root_log.addHandler(queue_handler)

    root_log.setLevel(log_level)

    # MZ: how to log from the logger
    root_log.debug("applog.process_log_configure: process_id %s", os.getpid())

## 
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

    if os.path.exists(pathname):
        print("Problem deleting: %s", pathname)
        return False
    return True

##
# Apparently, tests run in py.test will not remove the existing handlers 
# as expected. This mainfests as hanging tests during py.test runs, or 
# after non-termination hang of py.test after all tests report 
# succesfully. Only on linux though, windows appears to Do What I Want. 
# Use this function to close all of the log file handlers, including the 
# QueueHandler custom objects.
def explicit_log_close():
    root_log = logging.getLogger()
    root_log.debug("applog.explicit_log_close: closing and removing all handlers")
    handlers = root_log.handlers[:]
    for handler in handlers:
        handler.close()
        root_log.removeHandler(handler)

# ##############################################################################
#                                                                              #
#                                QueueHandler                                  #
#                                                                              #
# ##############################################################################

##
# Copied verbatim from PlumberJack (see above).  This is a logging handler 
# which sends events to a multiprocessing queue.  
# 
# The plan is to add it to Python 3.2, but this can be copy pasted into
# user code for use with earlier Python versions.
class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        logging.Handler.__init__(self)
        self.log_queue = log_queue

    def emit(self, record):
        try:
            ei = record.exc_info
            if ei:
                dummy = self.format(record) # just to get traceback text into record.exc_text
                record.exc_info = None  # not needed any more
            self.log_queue.put_nowait(record)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)

# ##############################################################################
#                                                                              #
#                                MainLogger                                    #
#                                                                              #
# ##############################################################################

##
# @warning Memory Leak
#
# This code appears to leak memory under Linux.  In ENLIGHTEN it hasn't really
# been a problem under normal operation, but if run with DEBUG logging enabled
# can grow quite significant.
#
# The leak has not been thoroughly analyzed as a workaround exists (--log-level info).
#
# This code uses queues rather than pipes because pipes are point-to-point
# (would only support one producer) while queues can have multiple producers
# (e.g. Controller + WasatchDeviceWrapper instances) feeding one consumer.
class MainLogger(object):
    FORMAT = '%(asctime)s %(processName)-10s %(name)s %(levelname)-8s %(message)s'

    def __init__(self, 
            log_level=logging.DEBUG, 
            enable_stdout=True,
            logfile=None,
            timeout_sec=5):
        self.log_queue     = multiprocessing.Queue() 
        self.log_level     = log_level
        self.enable_stdout = enable_stdout
        self.explicit_path = logfile
        self.timeout_sec   = timeout_sec

        # kick-off a listener in a separate process
        # Specifically, create a process running the listener_process() function
        # and pass it the arguments log_queue and listener_configurer (which is the
        # first function it will call)
        self.listener = multiprocessing.Process(target=self.listener_process,
                                                args=(self.log_queue, self.listener_configurer, self.explicit_path, self.timeout_sec))
        self.listener.start()

        # Remember you have to add a local log configurator for each
        # process, including this, the parent process
        root_log = logging.getLogger()

        top_handler = QueueHandler(self.log_queue)
        root_log.addHandler(top_handler)
        root_log.setLevel(self.log_level)
        root_log.warning("Top level log configuration (%d handlers)", len(root_log.handlers))

    ## @see https://stackoverflow.com/a/14058475
    def add_handler(self, fh):
        channel = logging.StreamHandler(fh)
        channel.setLevel(self.log_level)
        formatter = logging.Formatter(self.FORMAT)
        channel.setFormatter(formatter)
        self.root.addHandler(channel)

    ## Setup file handler and command window stream handlers. Every log
    #  message received on the queue handler will use these log configurers. 
    def listener_configurer(self, explicit_path=None):
        if explicit_path is not None:
            pathname = explicit_path
        elif self.explicit_path is not None:
            pathname = self.explicit_path
        else:
            pathname = get_location()

        root_logger = logging.getLogger()
        fh = logging.FileHandler(pathname, 'w') # Overwrite previous run (does not append!)
        formatter = logging.Formatter(self.FORMAT)
        fh.setFormatter(formatter)
        root_logger.addHandler(fh)

        # Specifing stderr as the log output location will cause the creation of
        # a _module_name_.exe.log file when run as a post-freeze windows
        # executable.
        if self.enable_stdout:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            root_logger.addHandler(stream_handler)

        self.root = root_logger

    # This is the listener process top-level loop: wait for logging events
    # (LogRecords) on the queue and handle them, quit when you get a None for a
    # LogRecord (poison pill).
    def listener_process(self, log_queue, configurer, explicit_path, timeout_sec):
        configurer(explicit_path=explicit_path)
        while True:
            try:
                if timeout_sec <= 0:
                    record = log_queue.get()
                else:
                    record = log_queue.get(timeout=timeout_sec)

                if record is None: # We send this as a sentinel to tell the listener to quit.
                    break
                logger = logging.getLogger(record.name)
                logger.handle(record) # No level or filter logic applied - just do it!
            except queue.Empty:
                print("wasatch.applog shutting down after %s sec of no log messages" % timeout_sec)
                break
            except (KeyboardInterrupt, SystemExit):
                break
            except:
                print('wasatch.applog exception:', file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                break

    ## Wrapper to add a None poison pill to the listener process queue to
    #  ensure it exits. 
    def close(self):
        self.log_queue.put_nowait(None)
        # causes problem with Demo.py on Linux?
        try:
            self.listener.join()
        except:
            pass
