import logging

log = logging.getLogger(__name__)

class ROI:
    """
    This class encapsulates a Region Of Interest, which may be either horizontal 
    (pixels) or vertical (rows/lines).

    Note that self.end is the LAST valid index, not LAST+1; (start, end) is an 
    open interval, not half-open.
    """

    def __init__(self, start, end):
        self.start = int(start)
        self.end = int(end)
        self.len = end - start + 1

    def valid(self):
        return self.start >= 0 and self.start < self.end

    def crop(self, spectrum):
        try:
            return spectrum[self.start:self.end+1]
        except:
            log.error(f"unable to crop spectrum of len {len(spectrum)} to roi {self}", exc_info=1)
            return 

    def contains(self, value):
        return self.start <= value <= self.end

    def __repr__(self):
        return f"({self.start}, {self.end}) inclusive"
