import datetime
import logging
import csv
import re

from .Reading              import Reading
from .ProcessedReading     import ProcessedReading

log = logging.getLogger(__name__)

##
# A file parser to deserialize one Measurement from a column-ordered CSV file.
#
# It looks like the basic usage is to instantiate it with a CSV pathname, call
# load_data() with or without scalar_metadata, then access the .processed_reading
# and .metadata elements. So, arguably this could have been a separate 
# constructor for ProcessedReading?
#
# Given the similarity between the columnar CSV and "export" file formats, it 
# would be SO TEMPTING to imagine you could easily generalize them.  I thought
# they're just different enough that it would be a nightmare, so here we are.
#
# It is expected that this will be able to handle "raw" columnar CSV formats as
# well, which contain no metadata, but instead begin directly with the header
# row.  In that case, wavelength and wavenumber are used directly from the input
# data, as no wavecal coefficients or excitation are available.
#
# Currently this class is used by at least three callers:
#
# - enlighten.parser.ColumnFileParser
# - wasatch.MockUSBDevice
# - an OEM plugin
# - maybe a shell script or two? (check enlighten/scripts)
#
class CSVLoader:

    def __init__(self, pathname, encoding="utf-8"):
        self.pathname = pathname
        self.encoding = encoding

        # default
        self.timestamp = datetime.datetime.now()

        # temporarily store these if no wavecal is provided
        self.metadata = {
            "pixel": []
        }

        self.headers = []
        self.processed_reading = ProcessedReading()
        self.processed_reading.reading = Reading(device_id = "LOAD:" + pathname)

    def __repr__(self):
        return f"CSVLoader<{self.pathname}> with ProcessedReading {self.processed_reading} and metadata {self.metadata.keys()}"

    def _parse_metadata(self, line, scalar_metadata=False):
        """
        MZ: I'm not sure who is using this method and wants the metadata 
        values to be returned as lists, but it unnecessarily complicates 
        ColumnFileParser, so adding the scalar option.
        """
        line = list(line)
        key = line[0]
        if scalar_metadata:
            value = line[1]
        else:
            value = line[1:] # for lists with only one element this will give []
                             # Useful for cases like Declared Match,,,,,,,,
        self.metadata[key] = value

    def _parse_header(self, line):
        self.headers = [ x.lower().strip() for x in line ] # force lowercase
        if "processed" in self.headers: self.processed_reading.processed = []
        if "raw"       in self.headers: self.processed_reading.raw       = []
        if "dark"      in self.headers: self.processed_reading.dark      = []
        if "reference" in self.headers: self.processed_reading.reference = []
        if "corrected" in self.headers: self.processed_reading.processed = []
        # log.debug("_parse_header: headers = %s", self.headers)

    def load_data(self, scalar_metadata=False):

        # create empty arrays for all of these so we have someplace to put data as we load lines
        self.processed_reading.processed = []
        self.processed_reading.raw = []
        self.processed_reading.dark = []
        self.processed_reading.reference = []
        self.processed_reading.wavelengths = []
        self.processed_reading.wavenumbers = []

        state = "reading_metadata"
        data_rows_read = 0
        with open(self.pathname, "r", encoding=self.encoding) as infile:
            csv_lines = csv.reader(infile)
            for line in csv_lines:
                # skip comments and blanks
                if len(line) == 0 or line[0].startswith('#'):
                    continue

                line[-1] = line[-1].strip() # remove the \n
                # log.debug("load_data[%s]: %s", state, line)

                if state == "reading_metadata":
                    
                    # check for end of metadata
                    looks_like_header = False
                    for tok in [ part.strip().lower() for part in line ]:
                        if tok in ["pixel", "wavelength", "wavenumber", "processed", "intensity"]:
                            looks_like_header = True
                            break

                    if looks_like_header:
                        self._parse_header(line)
                        state = "reading_data"
                    else:
                        # still in metadata
                        self._parse_metadata(line, scalar_metadata)

                elif state == "reading_metadata_final":
                    self._parse_metadata(line, scalar_metadata)
                
                elif state == "reading_data":
                    values = [x.strip() for x in line]

                    # if we find more metadata after data ended, store it but 
                    # do not transition back
                    if not re.match(r'^[-+]?\d', values[0]):
                        state = "reading_metadata_final"
                        self._parse_metadata(line, scalar_metadata)
                        continue

                    # Assume each value read aligns with a known headers, but recognize that there
                    # could be more headers than there are values (some columns with headers may
                    # not actually have populated data, blank or otherwise).  This is the number of
                    # comma-delimited fields actually read (or the number of headers, if more values
                    # were read than had headers).
                    count = min(len(self.headers), len(values))
                    # log.debug(f"parsing {count} fields")

                    for i in range(count):
                        header = self.headers[i]
                        # log.debug(f"parsing header {i} ({header})")

                        # SKIP nulls.  Note we're APPENDING data to each list, so this means that 
                        # if there are blanks (rather than '0' zeros) in the MIDDLE of a column, 
                        # the resulting spectral matrix will have different-length columns and
                        # improperly-associated "rows".
                        value = values[i]
                        if len(value) == 0:
                            # log.debug(f"skipping value {i} ({value})")
                            continue

                        # MZ: honestly not sure if we should skip these or treat as zero
                        if value == "NA":
                            # log.debug(f"treating value {i} ({value}) as zero")
                            value = 0

                        # add to array
                        array = None
                        if   header in ["processed", "intensity"]:  array = self.processed_reading.processed
                        elif header == "corrected":  array = self.processed_reading.processed 
                        elif header == "raw":        array = self.processed_reading.raw
                        elif header == "dark":       array = self.processed_reading.dark
                        elif header == "reference":  array = self.processed_reading.reference
                        elif header == "wavelength": array = self.processed_reading.wavelengths
                        elif header == "wavenumber": array = self.processed_reading.wavenumbers
                        elif header == "pixel":      array = self.metadata["pixel"]

                        if array is not None:
                            # log.debug(f"appending to {header}: {value}")
                            array.append(float(value))
                        else:
                            log.error(f"load_data: null array? headers {self.headers}, header {header}, value {value}, line {line} ({self.pathname})")

                        if data_rows_read == 0 and header == "pixel" and int(value) != 0:
                            value = int(value)
                            # log.debug(f"first pixel = {value}")
                            self.processed_reading.first_pixel = value

                    data_rows_read += 1

        # clear any arrays we ended up not filling
        self.processed_reading.post_load_cleanup()

        return self.processed_reading, self.metadata
