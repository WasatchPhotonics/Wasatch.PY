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

    ## sum of widths of enabled regions
    def total_pixels(self):
        pixels = 0
        for region in self.regions:
            roi = self.regions[region]
            if roi.enabled:
                pixels += self.regions[region].width()
        log.debug(f"total_pixels = {pixels}")
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
    #
    # Ignores disabled regions.
    #
    # Example: 
    # - passed 1920 wavelengths (based on active_pixels_horizontal)
    # - configured region 0 is from (300, 1932) (pixel space)
    # - function doesn't know that 1920 was defined as 1932 - 12 (the physical 
    #   pixel range to which the wavelengths actually correspond)
    # - all function knows is that new range is 1632 pixels wide -- not where
    #   those 1632 pixels fall within the 1920 (in this case, they SHOULD
    #   start at 300 - 12 = 288
    # - basically, DetectorRegions needs to know the (x, y) in pixel
    #   space corresponding to 'a' -- (12, 1932) in this case
    # - that is what orig_roi now provides
    def chop(self, a, flatten=False, orig_roi=None):
        log.debug(f"chopping array of {len(a)} pixels into {self.count()} subarrays")
        subarrays = []
        for region in sorted(self.regions):
            roi = self.regions[region]
            if not roi.enabled:
                continue

            if roi.width() > len(a):
                log.error(f"width ({roi.width}) of {roi.region} exceeds input array")
                return None

            start = roi.x0 if orig_roi is None else orig_roi.start - roi.x0
            log.debug(f"chop: region {roi.region} of width {roi.width()} from orig {orig_roi}")
            subarray = a[start : start + roi.width() + 1]
            log.debug(f"chop: subarray = {subarray[:3]} .. {subarray[-3:]}")

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
    #
    # Ignores disabled regions.
    def split(self, spectrum, flatten=False):
        log.debug("splitting spectrum of %d pixels into %d subspectra", len(spectrum), self.count())
        subspectra = []
        start = 0
        for region in sorted(self.regions):
            roi = self.regions[region]
            if not roi.enabled:
                continue

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
        tok = []
        for region in self.regions:
            tok.append(str(self.regions[region]))
        concat = ", ".join(tok)
        return f"[DetectorRegions: count {self.count()}, total_pixels {self.total_pixels()}, regions: {concat}]"
