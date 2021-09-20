import logging

log = logging.getLogger(__name__)

##
# This class represents a single Detector Region-of-Interest for a single
# "region" of DetectorRegions.
#
# We have not yet addressed the multiple wavecals required for DetectorRegions.
class DetectorROI:
    def __init__(self, region, y0, y1, x0, x1):
        self.region = region
        self.y0 = y0
        self.y1 = y1
        self.x0 = x0
        self.x1 = x1

    def crop(self, a):
        if self.x0 >= len(a) or self.x1 > len(a) + 1:
            log.error("can't crop array exceeding ROI")
            return a
        return a[self.x0 : self.x1]

    def width(self):
        return self.x1 - self.x0    # not +1

    def height(self):
        return self.y1 - self.y0    # not +1

    def __eq__(self, rhs):
        return self.__dict__ == rhs.__dict__

    def __str__(self):
        return f"[DetectorROI: region {self.region}, y0 {self.y0}, y1 {self.y1}, x0 {self.x0}, x1 {self.x1}, width {self.width()}, height {self.height()} ]"
