""" Custom logging setup and helper functions. This is based heavily on
    http://plumberjack.blogspot.com/2010/09/using-logging-with-multiprocessing.html

    The general approach is that the control portion of the application 
    instantiates a MainLogger object below. This will create a separate process 
    that looks for log events on a queue. Each process of the application then 
    registers a queue handler, and writes its log events. The MainLogger loop 
    will collect and write these log events to file and any other defined 
    logging location.
"""

import os
import sys
import traceback
import logging
import platform
import multiprocessing

################################################################################
#                                                                              #
#                    Semi-static, module-level functions                       #
#                                                                              #
################################################################################

def get_location():
    """ Determine the location to store the log file. Current directory
        on Linux, or %PROGRAMDATA% on windows - usually c:\ProgramData """

    # For convenience, replace any dots with underscores to help windows know
    # it is a text file.
    module_name = __name__.replace(".", "_")
    filename = "%s.txt" % module_name

    if "Linux" in platform.platform():
        return filename

    if "Darwin" in platform.platform():
        return filename

    #print("platform.platform() = %s", platform.platform())

    log_dir = ""
    try:
        import ctypes
        from ctypes import wintypes, windll
        CSIDL_COMMON_APPDATA = 35
        _SHGetFolderPath = windll.shell32.SHGetFolderPathW
        _SHGetFolderPath.argtypes = [wintypes.HWND,
                                    ctypes.c_int,
                                    wintypes.HANDLE,
                                    wintypes.DWORD, wintypes.LPCWSTR]

        path_buf = wintypes.create_unicode_buffer(wintypes.MAX_PATH)
        result = _SHGetFolderPath(0, CSIDL_COMMON_APPDATA, 0, 0, path_buf)
        log_dir = path_buf.value
    except:
        print("Problem assigning log directory")

    pathname = "%s/%s" % (log_dir, filename)
    return pathname

def process_log_configure(log_queue, log_level=logging.DEBUG):
    """ Called at the beginning of every process, including the main process.
        Adds a queue handler object to the root logger to be processed in the 
        main listener.

        Only on Windows though. Apparently Linux will pass the root logger 
        amongst processes as expected, so if you add another queue handler you 
        will get double log prints. """

    root_log = logging.getLogger()
    if "Windows" in platform.platform():
        queue_handler = QueueHandler(log_queue)
        root_log.addHandler(queue_handler)
        root_log.setLevel(log_level)

    # MZ: how to log from the logger
    root_log.debug("applog.process_log_configure: process_id %s", os.getpid())

def get_text_from_log():
    """ Mimic the capturelog style of just slurping the entire log file contents. """
    log_text = ""
    with open(get_location()) as log_file:
        for line_read in log_file:
            log_text += line_read
    return log_text

def log_file_created():
    return os.path.exists(get_location())

def delete_log_file_if_exists():
    """ Remove the specified log file and return True if succesful. """
    filename = get_location()

    if os.path.exists(filename):
        os.remove(filename)

    if os.path.exists(filename):
        print "Problem deleting: %s", filename
        return False
    return True

# MZ: do we need to call this from Controller or EnlightenApplication?
def explicit_log_close():
    """ Apparently, tests run in py.test will not remove the existing handlers 
        as expected. This mainfests as hanging tests during py.test runs, or 
        after non-termination hang of py.test after all tests report 
        succesfully. Only on linux though, windows appears to Do What I Want. 
        Use this function to close all of the log file handlers, including the 
        QueueHandler custom objects. """
    root_log = logging.getLogger()
    root_log.debug("applog.explicit_log_close: closing and removing all handlers")
    handlers = root_log.handlers[:]
    for handler in handlers:
        handler.close()
        root_log.removeHandler(handler)

################################################################################
#                                                                              #
#                                QueueHandler                                  #
#                                                                              #
################################################################################

class QueueHandler(logging.Handler):
    """ Copied verbatim from PlumberJack (see above).  This is a logging handler 
        which sends events to a multiprocessing queue.  

        The plan is to add it to Python 3.2, but this can be copy pasted into
        user code for use with earlier Python versions.  """

    def __init__(self, log_queue):
        """ Initialise an instance, using the passed queue. """
        logging.Handler.__init__(self)
        self.log_queue = log_queue

    def emit(self, record):
        """ Emit a record. Writes the LogRecord to the queue. """
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

################################################################################
#                                                                              #
#                                MainLogger                                    #
#                                                                              #
################################################################################

class MainLogger(object):
    FORMAT = '%(asctime)s %(processName)-10s %(name)s %(levelname)-8s %(message)s'

    def __init__(self, log_level=logging.DEBUG):
        self.log_queue = multiprocessing.Queue(-1)
        self.log_level = log_level

        # kick-off a listener in a separate process
        self.listener = multiprocessing.Process(target=self.listener_process,
                                                args=(self.log_queue, self.listener_configurer))
        self.listener.start()

        # Remember you have to add a local log configurator for each
        # process, including this, the parent process
        top_handler = QueueHandler(self.log_queue)
        root_log = logging.getLogger()
        root_log.addHandler(top_handler)
        root_log.setLevel(self.log_level)
        root_log.debug("Top level log configuration")

    def add_handler(self, fh):
        """ see https://stackoverflow.com/a/14058475 """
        channel = logging.StreamHandler(fh)
        channel.setLevel(self.log_level)
        formatter = logging.Formatter(self.FORMAT)
        channel.setFormatter(formatter)
        self.root.addHandler(channel)

    def listener_configurer(self):
        """ Setup file handler and command window stream handlers. Every log
            message received on the queue handler will use these log configurers. """

        log_dir = get_location()

        root = logging.getLogger()
        h = logging.FileHandler(log_dir, 'w') # Overwrite previous run
        frmt = logging.Formatter(self.FORMAT)
        h.setFormatter(frmt)
        root.addHandler(h)

        # Specifing stderr as the log output location will cause the creation of
        # a _module_name_.exe.log file when run as a post-freeze windows
        # executable.
        strm = logging.StreamHandler(sys.stdout)
        strm.setFormatter(frmt)
        root.addHandler(strm)

        self.root = root

    # This is the listener process top-level loop: wait for logging events
    # (LogRecords)on the queue and handle them, quit when you get a None for a
    # LogRecord.
    def listener_process(self, log_queue, configurer):
        configurer()
        while True:
            try:
                record = log_queue.get()
                if record is None: # We send this as a sentinel to tell the listener to quit.
                    break
                logger = logging.getLogger(record.name)
                logger.handle(record) # No level or filter logic applied - just do it!
            except (KeyboardInterrupt, SystemExit):
                break
            except:
                print >> sys.stderr, 'Whoops! Problem:'
                traceback.print_exc(file=sys.stderr)

    def close(self):
        """ Wrapper to add a None poison pill to the listener process queue to
            ensure it exits. """
        self.log_queue.put_nowait(None)
        self.listener.join()
