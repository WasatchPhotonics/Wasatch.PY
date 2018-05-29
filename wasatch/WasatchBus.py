import logging

from DeviceListFID import DeviceListFID
from DeviceListSP  import DeviceListSP

from usb import USBError

log = logging.getLogger(__name__)

################################################################################
#                                                                              #
#                                 WasatchBus                                   #
#                                                                              #
################################################################################

class WasatchBus(object):
    """ Use Simulation and real hardware bus to populate a device list. """

    def __init__(self, 
            use_sim     = False,
            monitor_dir = None):

        self.use_sim     = use_sim
        self.monitor_dir = monitor_dir

        self.devices = []

        self.simulation_bus = SimulationBus() if self.use_sim else None
        self.file_bus = FileBus(self.monitor_dir) if self.monitor_dir else None
        self.hardware_bus = HardwareBus()

        # iterate buses on creation
        self.update()

    # called by Controller.update_connections
    def update(self):
        """ return a list of UIDs found on any bus """
        self.devices = []

        if self.simulation_bus:
            self.devices.extend(self.simulation_bus.update())

        if self.file_bus:
            self.devices.extend(self.file_bus.update())

        if self.hardware_bus:
            self.devices.extend(self.hardware_bus.update())

    # called by Controller.update_connections
    def dump(self):
        log.debug("Bus list: %s", self.devices)

################################################################################
#                                                                              #
#                                   Buses                                      #
#                                                                              #
################################################################################

# The different bus classes don't use inheritance and don't follow a common ABC
# or interface, but each should have an update() method, and each should have a 
# 'devices' array.  
#
# 'devices' should be an array of strings, where the values are meaningful to 
# that particular bus ("VID:PID" for HardwareBus or SimulationBus, "/foo/bar" 
# for MonitorBus).  The devices array can be empty, or the first element can
# be "disconnected".

################################################################################
#                                                                              #
#                                   FileBus                                    #
#                                                                              #
################################################################################

class FileBus(object):
    def __init__(self, directory):
        self.directory = directory
        self.configfile = os.path.join(self.directory, "spectrometer.json")

    def update(self):
        devices = []
        if os.access(self.directory, os.W_OK) and os.path.isfile(self.configfile):
            devices.append(self.directory)
        return devices

################################################################################
#                                                                              #
#                                 HardwareBus                                  #
#                                                                              #
################################################################################

class HardwareBus(object):
    """ Use libusb to list available devices on the system wide libusb bus. """

    def __init__(self):
        self.backend_error_raised = False
        self.update()

    def update(self):
        """ Return a list of actual devices found on system lib usb bus. """
        log.debug("Update hardware bus")

        devices = []

        try:
            for item in DeviceListFID().get_all_vid_pids():
                devices.append("%s:%s" % (item[0], item[1]))

            for item in DeviceListSP().get_all_vid_pids():
                devices.append("%s:%s" % (item[0], item[1]))

        except USBError:
            # MZ: this seems to happen when I run from Git Bash shell
            #     (resolved on MacOS with 'brew install libusb')
            if not self.backend_error_raised:
                log.warn("No libusb backend", exc_info=1)
                self.backend_error_raised = True

        except Exception:
            log.critical("LIBUSB error", exc_info=1)

        return devices

################################################################################
#                                                                              #
#                                 SimulationBus                                #
#                                                                              #
################################################################################

class SimulationBus(object):
    """ Provide an interface to the ini file controlled simulation bus.  This
        indicates whether a simulated device is present on the simulated libusb
        bus. """

    def __init__(self, status=None):
        self.status = status
        self.filename = "enlighten/assets/example_data/simulated_bus.ini"
        self.devices = []

        if self.status == "all_connected":
            log.info("Set all devices connected")
            self.set_all_connected()

        elif self.status == "all_disconnected":
            log.warn("Disconnect all devices")
            self.set_all_disconnected()

        self.update()

    def dump(self):
        """ Return a list of simulated devices, or actual devices found on system lib usb bus. """
        log.debug("Start of list status: %s", self.bus_type)
        self.update()

        # MZ: hardcode
        if self.devices and self.devices[0] != "disconnected":
            conn = ["0x24aa", "0x1000"]
        else:
            conn = []

        log.info("Simulation BUS: [] FID BUS: %s" % conn)

    def update(self):
        """ Open the ini file, update the class attributes with the status of each device. """
        if not os.path.isfile(self.filename):
            log.error("SimulationBus.update: %s not found", self.filename)
            return

        # Read from the file
        config = ConfigParser()
        config.read(self.filename)
        log.debug("SimulationBus.update: loaded %s", self.filename)

        # look at the returned dict
        # MZ: consider if config.has_section("LIBUSB_BUS"):
        self.devices = []

        self.devices.append(config.get('LIBUSB_BUS', 'device_001'))
        self.devices.append(config.get('LIBUSB_BUS', 'device_002'))
        self.devices.append(config.get('LIBUSB_BUS', 'device_003'))

        return self.devices

    def set_all_connected(self):
        """ Open the ini file, and set all bus entries to connected. """
        config = ConfigParser()
        config.read(self.filename)

        config.set("LIBUSB_BUS", "device_001", "0x24aa:0x0512")
        config.set("LIBUSB_BUS", "device_002", "0x24aa:0x1024")
        config.set("LIBUSB_BUS", "device_003", "0x24aa:0x2048")
        with open(self.filename, "wb") as config_file:
            config.write(config_file)

        self.update()

    def set_all_disconnected(self):
        """ Open the ini file, and set all bus entries to disconnected. """
        config = ConfigParser()
        config.read(self.filename)

        config.set("LIBUSB_BUS", "device_001", "disconnected")
        config.set("LIBUSB_BUS", "device_002", "disconnected")
        config.set("LIBUSB_BUS", "device_003", "disconnected")
        with open(self.filename, "wb") as config_file:
            config.write(config_file)

        self.update()
