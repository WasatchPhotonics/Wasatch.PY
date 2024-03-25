import logging
import numpy as np
from copy import copy

from wasatch import utils as wasatch_utils

log = logging.getLogger(__name__)


##
# This class encapsulates anything done to a wasatch.Reading (usually generated 
# by wasatch.WasatchDevice) after it has been received by ENLIGHTEN (usually
# Controller.process_reading).  This would include any post-processing like dark
# subtraction, conversion into a processed transmission, reflectance, absorbance
# or irradiance measurement, etc.  
#
# The ProcessedReading is an important attribute of a Measurement, which higher-
# level class also encapsulates things like the graphical ThumbnailWidget 
# rendering the .processed spectrum, the .curve drawing the spectrum on the 
# Graph, CSV or Excel pathnames saved from the Measurement, etc.
# 
# ProcessedReadings generated directly from a spectrometer will contain the 
# original Reading object (which typically provides the .raw spectrum).  If a
# Measurement has been deserialized from disk, then any spectra loaded from
# the file will be stored in stubbed ProcessedReading, which therefore will not
# have an original Reading object.  You should be able to tell whether a 
# ProcessedReading was collected live, or loaded from disk, by the presence of
# a Reading attribute.  (Also, the loaded Measurement should have a 
# source_pathname attribute.)
#
# @see ORDER_OF_OPERATIONS.md in ENLIGHTEN/docs 
#
class ProcessedReading:

    session_count = 0

    def clear(self):
        self.reading = None
        self.device_id = None

        self.processed = None
        self.raw = None
        self.dark = None
        self.reference = None
        self.wavelengths = None
        self.wavenumbers = None

        self.settings = None

        self.dark_corrected = False
        self.raman_intensity_corrected = False
        self.used_reference = False
        self.deconvolved = False

        self.recordable_reference = None
        self.recordable_dark = None

        self.declared_match = None
        self.first_pixel = -1 # only used in .cropped (set by enlighten.HorizROI.process)
        self.plugin_metadata = None

        # these are optional handles to secondary ProcessedReadings
        # (just proc/raw/dark/ref/wl/wn, no further recursion)
        self.cropped = None
        self.interpolated = None

    ##
    # @param d (Input) if instantiating from a dict (External API or loaded JSON),
    #                  this is the dictionary containing parsed values
    def __init__(self, reading=None, d=None, settings=None):

        self.clear()
    
        # the raw measurement coming in from the spectrometer process
        # (may have bad-pixel removal and x-axis inversion applied)
        self.reading = reading

        if reading is not None:
    
            self.device_id = reading.device_id

            # the 'original' reading (may contain bad_pixel correction, x-axis 
            # inversion and scan averaging, as those are applied within the 
            # subprocess for encapsulation and efficiency)
            self.raw = np.copy(reading.spectrum)

            # The "final" version of the spectrum after boxcar, dark correction, 
            # absorbance / transmission / whatever has been applied.  Processed
            # and raw should always be populated; dark and reference are optional,
            # depending on technique and user elections.  Note that dark and 
            # reference are the components actually used (along with raw) to generate
            # 'processed,' and likely do not match recordable_dark or 
            # recordable_reference. 
            self.processed = np.array(reading.spectrum, dtype=np.float64)

            # During conversion from raw to processed, we "snapshot" key stages where 
            # the in-process spectrum becomes POTENTIALLY worth keeping for 
            # application use.
            #
            # These are NOT saved darks or saved references; these are partially-
            # processed descendents of raw which COULD be used as a new dark
            # or reference spectrum, if the user clicked "pause" and then clicked
            # "store dark" or "store reference".  These are versions of the latest 
            # reading which could be used as dark or reference if one was requested 
            # from recent acquisition history.
            #
            # It is debateable whether these belong in ProcessedReading or 
            # SpectrometerApplicationState.  I opted for this as they are literally
            # partially-processed products of this PARTICULAR reading, and until the
            # user CHOOSES to store them, they are not [yet] part of "application state".
            self.recordable_dark = np.copy(self.processed)

        # a way to distinguish unique ProcessedReadings, for instance to let
        # KnowItAll track whether the "current" ProcessedReading is still the one
        # it used for a particular request.
        self.session_count = ProcessedReading.session_count
        ProcessedReading.session_count += 1

        if d is not None:
            self.load_from_dict(d)

        self.post_load_cleanup(settings)

    def get_pixel_count(self):
        spectrum = self.get_processed()
        if spectrum is not None:
            return len(spectrum)
        return 0

    def has_dark(self): 
        return self.dark is not None

    def has_reference(self): 
        return self.reference is not None

    def is_cropped(self): 
        return self.cropped is not None 

    def is_interpolated(self): 
        return self.interpolated is not None 

    def has_processed(self): 
        return self.processed is not None 

    def _get_array(self, name, stage):
        sources = [self.interpolated, self.cropped, self]
        if stage:
            stage = stage.lower()
            if stage == "cropped":
                sources = [self.cropped, self]
            elif stage == "orig":
                sources = [self]

        for obj in sources:
            if obj is not None:
                if hasattr(obj, name):
                    v = getattr(obj, name)
                    if v is not None:
                        return v
        # log.debug(f"_get_array: could not find {name} in {sources}")
        # self.dump()

    def get_processed(self, stage=None):
        return self._get_array("processed", stage)
        
    def get_raw(self, stage=None):
        return self._get_array("raw", stage)

    def get_dark(self, stage=None):
        return self._get_array("dark", stage)

    def get_reference(self, stage=None):
        return self._get_array("reference", stage)

    def get_wavelengths(self, stage=None):
        return self._get_array("wavelengths", stage)

    def get_wavenumbers(self, stage=None):
        return self._get_array("wavenumbers", stage)

    def get_pixel_axis(self):
        if self.is_cropped() and not self.is_interpolated() and self.cropped.first_pixel >= 0:
            return range(self.cropped.first_pixel, self.cropped.first_pixel + self.get_pixel_count())
        return range(self.get_pixel_count())

    def set_processed(self, spectrum):
        """
        Called by enlighten.post_processing.(RichardsonLucy, AbsorbanceFeature, 
        BoxcarFeature, TransmissionFeature, BaselineCorrection).

        This never updates .interpolated, so all calls to this should definitely
        occur BEFORE .interpolated is created by enlighten.post_processing.InterpolationFeature.
        """

        if self.interpolated:
            log.error("set_processed: clearing interpolation")
            self.interpolated = None

        if self.cropped:
            log.debug(f"set_processed: updating cropped to {len(spectrum)} px {spectrum[:5]}")
            self.cropped.processed = spectrum
        else:
            log.debug(f"set_processed: updating non-cropped to {len(spectrum)} px {spectrum[:5]}")
            self.processed = spectrum

    # should be done before .cropped created
    def correct_dark(self, dark):
        if self.dark_corrected:
            log.debug("already dark-corrected")
            return

        if self.cropped and len(self.cropped.processed) != len(dark):
            log.error(f"correct_dark: len(cropped.processed) {len(self.cropped.processed)} != len(dark) {len(dark)}")
            return

        if dark is None:
            self.dark = None
            self.dark_corrected = False
        elif len(dark) == len(self.processed):
            self.dark = np.copy(dark)
            self.processed -= self.dark
            self.dark_corrected = True

        self.recordable_reference = np.copy(self.processed)

    ##
    # Resets spectral component arrays which were somehow initialized (perhaps
    # while parsing a textfile and getting overly hopeful based on declared
    # header rows), but ultimately never populated.
    def post_load_cleanup(self, settings=None):
        # keep settings if passed and had none
        if settings and not self.settings:
            self.settings = settings

        # keep x-axes if available and had none
        if self.settings and self.wavelengths is None:
            self.wavelengths = copy(self.settings.wavelengths)
        if self.settings and self.wavenumbers is None:
            self.wavenumbers = copy(self.settings.wavenumbers)

        for field in [ "processed", "raw", "dark", "reference", "recordable_dark", "recordable_reference", "wavelengths", "wavenumbers" ]:
            if hasattr(self, field):
                array = getattr(self, field)
                if array is not None:
                    try:
                        if len(array) == 0:
                            setattr(self, field, None)
                    except TypeError:
                        log.debug(f"post_load_cleanup: zeroing {field} because it is not iterable")
                        setattr(self, field, None)

        # if they didn't save a raw, assume same as processed.
        if self.raw is None and self.processed is not None:
            self.raw = self.processed

    def dump(self, indent=0):
        gap = " " * indent
        n = 5
        log.info(f"{gap}ProcessedReading:")
        log.info(f"{gap}  Device ID:            %s", self.device_id)
        log.info(f"{gap}  Processed:            %s", (0, None) if self.processed            is None else (len(self.processed),            self.processed[:n]))
        log.info(f"{gap}  Raw:                  %s", (0, None) if self.raw                  is None else (len(self.raw),                  self.raw[:n]))
        log.info(f"{gap}  Dark:                 %s", (0, None) if self.dark                 is None else (len(self.dark),                 self.dark[:n]))
        log.info(f"{gap}  Reference:            %s", (0, None) if self.reference            is None else (len(self.reference),            self.reference[:n]))
        log.info(f"{gap}  Recordable Dark:      %s", (0, None) if self.recordable_dark      is None else (len(self.recordable_dark),      self.recordable_dark[:n]))
        log.info(f"{gap}  Recordable Reference: %s", (0, None) if self.recordable_reference is None else (len(self.recordable_reference), self.recordable_reference[:n]))
        log.info(f"{gap}  Wavelengths:          %s", (0, None) if self.wavelengths          is None else (len(self.wavelengths),          self.wavelengths[:n]))
        log.info(f"{gap}  Wavenumbers:          %s", (0, None) if self.wavenumbers          is None else (len(self.wavenumbers),          self.wavenumbers[:n]))
        log.info(f"{gap}  Pixels:               %d",         0 if self.wavelengths          is None else  len(self.wavelengths))
        if self.cropped:
            log.info("  Cropped:")
            self.cropped.dump(indent=indent+4)
        if self.interpolated:
            log.info("  Interpolated:")
            self.interpolated.dump(indent=indent+4)

    def load_from_dict(self, d):
        if d is None:
            return

        self.processed   = wasatch_utils.dict_get_norm(d, "Processed")
        self.reference   = wasatch_utils.dict_get_norm(d, "Reference")
        self.dark        = wasatch_utils.dict_get_norm(d, "Dark")
        self.raw         = wasatch_utils.dict_get_norm(d, "Raw")
        self.wavelengths = wasatch_utils.dict_get_norm(d, "Wavelengths")
        self.wavenumbers = wasatch_utils.dict_get_norm(d, "Wavenumbers")

        # make sure they're all Numpy for consistency
        for attr in ['processed', 'reference', 'dark', 'raw', 'wavelengths', 'wavenumbers']:
            if hasattr(self, attr):
                a = getattr(self, attr)
                if a is not None:
                    setattr(self, attr, np.array(a, dtype=np.float64))

        if "Cropped" in d:
            self.cropped = ProcessedReading(d=d["Cropped"])
        if "Interpolated" in d:
            self.interpolated = ProcessedReading(d=d["Interpolated"])

    def to_dict(self):
        return {
            "Processed": self.processed,
            "Reference": self.reference,
            "Dark": self.dark,
            "Raw": self.raw,
            "Dark Corrected": self.dark_corrected,
            "Recordable Dark": self.recordable_dark,
            "Recordable Reference": self.recordable_reference,
            "Used Reference": self.used_reference,
            "Raman Intensity Corrected": self.raman_intensity_corrected,
            "Deconvolved": self.deconvolved,
            "First Pixel": self.first_pixel,
            "Plugin Metadata": self.plugin_metadata,
            "Wavelengths": self.wavelengths,
            "Wavenumbers": self.wavenumbers,
            "Cropped": self.cropped.to_dict() if self.cropped else None,
            "Interpolated": self.interpolated.to_dict() if self.interpolated else None
        }

    # The thought here is that ProcessedReading could retain a dict[OoO] of
    # Readings, where each could retain a unique copy of (processed, dark, 
    # reference, wavelengths, wavenubers). If a given transform didn't change a 
    # particular array for a particular measurement, just leave that None.
    # 
    # Each array would support a getter with the intended OoO level, and
    # would traverse DOWN the stack from the requested level to reach the first 
    # non-None array.
    #
    # ENLIGHTEN would allow users to save any array at any level.
    ORDER_OF_OPERATIONS = [ 
        "RAW",
        "EVEN_ODD_INGAAS_CORRECTION",
        "STOMP_ARRAY_ENDS",
        "INVERT_X_AXIS",
        "BAD_PIXEL_CORRECTION",
        "BIN_2X2",
        "DARK_CORRECTION",
        "HORIZ_ROI",
        "REFERENCE_PROCESSING",
        "RAMAN_INTENSITY_CORRECTION",
        "BASELINE_CORRECTION",
        "PEAK_SHARPENING",
        "DESPIKING",
        "BOXCAR",
        "FINAL" ]
    ARRAYS = { "PROCESSED", "DARK", "REFERENCE", "WAVELENGTHS", "WAVENUMBERS" }
    def get_array(self, array_name, level_name):
        if level_name.upper() not in self.ORDER_OF_OPERATIONS:
            return
        if array_name.upper() not in self.ARRAYS:
            return
        pass
