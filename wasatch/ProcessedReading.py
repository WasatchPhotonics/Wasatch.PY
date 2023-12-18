import logging
import numpy as np

from wasatch import utils as wasatch_utils

log = logging.getLogger(__name__)

##
# This class encapsulates anything done to a wasatch.Reading after it has been
# received by ENLIGHTEN.  This would include any post-processing like dark
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
class ProcessedReading(object):

    session_count = 0

    def clear(self):
        self.reading = None
        self.device_id = None
        self.raw = None
        self.processed = None
        self.processed_cropped = None
        self.reference = None
        self.dark = None
        self.dark_corrected = False
        self.recordable_reference = None
        self.recordable_dark = None
        self.used_reference = False
        self.declared_match = None
        self.raman_intensity_corrected = False
        self.deconvolved = False
        self.wavelengths_cropped = None
        self.wavenumbers_cropped = None
        self.first_pixel = None
        self.plugin_metadata = None # extra data added by Plugin

    ##
    # @param d (Input) if instantiating from a dict (External API or loaded JSON),
    #                  this is the dictionary containing parsed values
    def __init__(self, reading=None, d=None):

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

    def has_dark(self): # -> bool 
        return self.dark is not None

    def has_reference(self): # -> bool 
        return self.reference is not None

    def is_cropped(self): # -> bool 
        return self.processed_cropped is not None and len(self.processed_cropped) > 0

    def has_processed(self): # -> bool 
        return self.processed is not None or self.processed_cropped is not None

    def get_processed(self):
        if self.processed_cropped is None:
            return self.processed
        else:
            return self.processed_cropped

    def set_processed(self, spectrum):
        if self.is_cropped():
            log.debug("set_processed: updating cropped")
            self.processed_cropped = spectrum
        else:
            log.debug("set_processed: updating non-cropped")
            self.processed = spectrum

    def correct_dark(self, dark):
        if self.dark_corrected:
            log.debug("already dark-corrected")
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
    def post_load_cleanup(self):
        for field in [ "processed", "raw", "dark", "reference" ]:
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

    def dump(self):
        n = 5
        log.info("ProcessedReading:")
        log.info("  Device ID:            %s", self.device_id)
        log.info("  Processed:            %s", None if self.processed            is None else self.processed[:n])
        log.info("  Raw:                  %s", None if self.raw                  is None else self.raw[:n])
        log.info("  Dark:                 %s", None if self.dark                 is None else self.dark[:n])
        log.info("  Reference:            %s", None if self.reference            is None else self.reference[:n])
        log.info("  Recordable Dark:      %s", None if self.recordable_dark      is None else self.recordable_dark[:n])
        log.info("  Recordable Reference: %s", None if self.recordable_reference is None else self.recordable_reference[:n])

    def load_from_dict(self, d):
        if d is None:
            return

        self.processed = wasatch_utils.dict_get_norm(d, "Processed")
        self.reference = wasatch_utils.dict_get_norm(d, "Reference")
        self.dark      = wasatch_utils.dict_get_norm(d, "Dark")
        self.raw       = wasatch_utils.dict_get_norm(d, "Raw")

        self.post_load_cleanup()

    def to_dict(self):
        return {
            "Processed": self.processed,
            "Reference": self.reference,
            "Dark": self.dark,
            "Raw": self.raw,
            "Processed Cropped": self.processed_cropped,
            "Dark Corrected": self.dark_corrected,
            "Recordable Dark": self.recordable_dark,
            "Recordable Reference": self.recordable_reference,
            "Used Reference": self.used_reference,
            "Raman Intensity Corrected": self.raman_intensity_corrected,
            "Deconvolved": self.deconvolved,
            "Wavelengths Cropped": self.wavelengths_cropped,
            "Wavenumbers Cropped": self.wavenumbers_cropped,
            "First Pixel": self.first_pixel,
            "Plugin Metadata": self.plugin_metadata
        }
