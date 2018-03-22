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

    def __init__(self, use_sim=False):
        super(WasatchBus, self).__init__()
        log.debug("%s setup", self.__class__.__name__)
        self.use_sim = use_sim

        self.devices = []

        if use_sim:
            self.simulation_bus = SimulationBus()

        self.hardware_bus = HardwareBus()

        # iterate buses on creation
        self.update()

    def update(self):
        """ Return a list of actual devices found on system libusb bus. """
        if self.use_sim:
            self.simulation_bus.update()

        self.hardware_bus.update()

        # no need to update simulation bus...?

        self.devices = []

        # Start with hardware bus list by default
        for device in self.hardware_bus.devices:
            self.devices.append(device)

        # add in any configured simulation devices
        if self.use_sim:
            for device in self.simulation_bus.devices:
                self.devices.append(device)

    def dump(self):
        log.debug("Bus list: %s", self.devices)

################################################################################
#                                                                              #
#                                 HardwareBus                                  #
#                                                                              #
################################################################################

class HardwareBus(object):
    """ Use libusb to list available devices on the system wide libusb bus. """

    def __init__(self):
        super(HardwareBus, self).__init__()
        log.debug("%s setup", self.__class__.__name__)

        self.backend_error_count = 0
        self.update()

    def update(self):
        """ Return a list of actual devices found on system lib usb bus. """
        log.debug("Update hardware bus")

        self.devices = []

        try:
            fid_bus = DeviceListFID()
            sp_bus  = DeviceListSP()

            list_fid = fid_bus.get_all_vid_pids()
            list_sp  =  sp_bus.get_all_vid_pids()
            log.debug("FID BUS: %s   SP BUS: %s", list_fid, list_sp)

            for item in list_fid:
                self.devices.append("%s:%s" % (item[0], item[1]))

            for item in list_sp:
                self.devices.append("%s:%s" % (item[0], item[1]))

        except USBError:
            # MZ: this seems to happen when I run from Git Bash shell
            #     (resolved on MacOS with 'brew install libusb')
            if self.backend_error_count == 0:
                log.warn("No libusb backend", exc_info=1)
            self.backend_error_count += 1

        except Exception:
            log.critical("LIBUSB error", exc_info=1)

################################################################################
#                                                                              #
#                                 SimulationBus                                #
#                                                                              #
################################################################################

# MZ: consider how to make non-default
class SimulationBus(object):
    """ Provide an interface to the ini file controlled simulation bus.  This
        indicates whether a simulated device is present on the simulated libusb
        bus. """

    def __init__(self, status=None):
        super(SimulationBus, self).__init__()
        log.debug("%s setup", self.__class__.__name__)

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
        return

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
        self.devices.append(config.get('LIBUSB_BUS', 'device_001'))
        self.devices.append(config.get('LIBUSB_BUS', 'device_002'))
        self.devices.append(config.get('LIBUSB_BUS', 'device_003'))

        return True

    def set_all_connected(self):
        """ Open the ini file, and set all bus entries to connected. """
        config = ConfigParser()
        config.read(self.filename)

        # MZ: hardcode
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
