import usb
import logging

log = logging.getLogger(__name__)

class DeviceListFID(object):

    class USBDiscoveryRec:

        ##
        # Each record contains the USB VID/PID, plus the overall index on the USB 
        # discovery chain (see DeviceListFID.get_usb_discovery_recs), plus the sub-
        # index of that particular pid.
        #
        # That is, if we discover these four devices in order:
        #
        # - vid 24aa pid 1000
        # - vid 24aa pid 4000
        # - vid 24aa pid 1000
        # - vid 24aa pid 1000
        #
        # They will generate the following records in order:
        #
        # - vid 24aa pid 1000 vidOrder 0 pidOrder 0
        # - vid 24aa pid 4000 vidOrder 1 pidOrder 0
        # - vid 24aa pid 1000 vidOrder 2 pidOrder 1
        # - vid 24aa pid 1000 vidOrder 3 pidOrder 2
        #
        # We need to track absolute order because that is how individual spectrometers
        # are traditionally identified and isolated from the ENLIGHTEN command-line.
        #
        # We need to track pidOrder because that is how FeatureIdentificationDevice
        # rediscovers them (generating an initial list using PID as well as VID).
        def __init__(self, pid, lister):
            self.vid = 0x24aa
            self.pid = pid
            self.vidOrder = lister.total

            if pid not in lister.pidCount:
                lister.pidCount[pid] = -1
            lister.pidCount[pid] += 1
            lister.total += 1

            self.pidOrder = lister.pidCount[pid]

        def get_uuid(self):
            return "%s:%s:%d:%d" % (hex(self.vid), hex(self.pid), self.vidOrder, self.pidOrder)

    def __init__(self):
        # We want these to re-initialize each time a Lister is instantiated, but to be used
        # and updated for each record, so declare them as instance variables of the Lister
        # and pass parent reference into record.
        self.total = 0
        self.pidCount = {}

    ## This list is presumed to be generated from PyUSB in strictly deterministic order!
    def get_usb_discovery_recs(self):
        recs = []
        for bus in usb.busses():
            for device in bus.devices:
                vid = device.idVendor
                pid = device.idProduct

                log.debug("get_usb_discovery_recs: discovered vid 0x%04x, pid 0x%04x", vid, pid)

                if vid != 0x24aa:
                    continue

                if pid not in [ 0x1000, 0x2000, 0x4000 ]:
                    continue

                rec = self.USBDiscoveryRec(pid, self)
                log.debug("get_usb_discovery_recs: instantiated %s", rec.get_uuid())

                recs.append(rec)
        return recs
