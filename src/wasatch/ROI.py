##
# This class encapsulates a Region Of Interest, which may be either horizontal 
# (pixels) or vertical (rows/lines).
class ROI:
    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.len = end - start + 1

    def valid(self):
        return self.start >= 0 and self.start < self.end

    def crop(self, spectrum):
        return spectrum[self.start:self.end+1]

    def contains(self, value):
        return self.start <= value <= self.end
