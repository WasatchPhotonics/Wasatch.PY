import logging

log = logging.getLogger(__name__)

##
# This class encapsulates all of the DetectorROI regions that have been 
# configured for the current detetor. If no DetectorROI regions have been
# configured for a detector, SpectrometerState.detector_regions may be None
# (indicating the full detector is being vertically binned and read-out).
# 
# Note that if DetectorROI regions have been configured for a detector, the
# total number of pixels to be read-out from the detector may be either greater
# or less than the physical number of pixels.
#
# DetectorRegions are currently only supported on the Sony IMX series of 
# detector.
#
# There are two other spectrometer features that are similar to or conceptually
# overlap DetectorRegions: HorizontalROI (aka vignetting or cropping), and 
# start/stop lines (vertical ROI).  This feature is definitely related to 
# vertical ROI, but very different from vignetting because this actually 
# changes the number of pixels read-out by the spectrometer during an
# acquisition.
#
# We have not yet addressed the multiple wavecals required for DetectorRegions.
class DetectorRegions:

    def __init__(self):
        self.regions = {}

    ## 
    # Will add or replace if region already exists.
    #
    # @param roi: DetectorROI
    def add(self, roi):
        self.regions[roi.region] = roi

    def remove(self, roi):
        region = None
        if isinstance(roi, DetectorROI):
            region = roi.region
        else:
            region = roi
        self.regions.pop(region, None)

    def count(self):
        return len(self.regions)

    def has_region(self, region):
        return region in self.regions

    def get_roi(self, region):
        return self.regions.get(region, None)

    ## guaranteed to be in the same order as split spectra
    def get_region_list(self):
        return [self.regions[region] for region in sorted(self.regions)]

    def total_pixels(self):
        pixels = 0
        for region in self.regions:
            pixels += self.regions[region].width()
        return pixels

    ##
    # Given a long array (like wavelengths or wavenumbers) which was presumably
    # generated for a full detector, chop into the sections indicated by the
    # configured regions.  Some elements of the source array may appear in 
    # multiple output arrays, if the regions overlap in x-coordinates.
    #
    # Differs from split() in that some of the data will be trimmed and left on
    # the floor, if it doesn't fall within any configured region.  Also, some
    # input values may appear in multiple output arrays.
    def chop(self, a, flatten=False):
        log.debug(f"chopping array of {len(a)} pixels into {self.count()} subarrays")
        subarrays = []
        for region in sorted(self.regions):
            roi = self.regions[region]
            if roi.x1 > len(a):
                log.error(f"x1 {roi.x1} of region {roi.region} overran input array")
                return None
            subarray = a[roi.x0:roi.x1]
            log.debug(f"chop: region {roi.region} of width {roi.width()}: {subarray[:3]} .. {subarray[-3:]}")
            if flatten:
                subarrays.extend(subarray)
            else:
                subarrays.append(subarray)
        return subarrays

    ## 
    # Given a concatenated array (spectrum) which is logically composed of 
    # multiple shorter spectra, use the configured regions to split into the 
    # presumed constituent components.
    #
    # Differs from chop() in that no data is thrown on the floor; all is presumed
    # part of one of the configured regions.  Also any given source value will 
    # only go into a single output subspectrum.
    #
    # Note that split() could be called on the flattened result of chop() to dice
    # a concatenated list of wavelengths for multiples regions back into 
    # individual per-region blocks.
    def split(self, spectrum, flatten=False):
        log.debug("splitting spectrum of %d pixels into %d subspectra", len(spectrum), self.count())
        subspectra = []
        start = 0
        for region in sorted(self.regions):
            roi = self.regions[region]
            end = start + roi.width() 
            if end > len(spectrum):
                log.error("computed end %d of region %d overran colleted spectrum", end, region)
                return None
            subspectrum = spectrum[start:end]
            log.debug(f"split: region {roi.region} of width {roi.width()}: {subspectrum[:3]} .. {subspectrum[-3:]}")
            if flatten:
                subspectra.extend(subspectrum)
            else:
                subspectra.append(subspectrum)
            start = end
        return subspectra

    def __str__(self):
        s = f"[ DetectorRegions: count {self.count()}, total_pixels {self.total_pixels()}, regions: "
        for region in self.regions:
            s += "{ %s: %s } " % (region, str(self.regions[region]))
        s += "]"
        return s

