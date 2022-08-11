import datetime
import logging
import copy
import csv
import re

from .Reading              import Reading
from .ProcessedReading     import ProcessedReading

log = logging.getLogger(__name__)

##
# A file parser to deserialize one Measurement from a column-ordered CSV file.
#
# Given the similarity between the columnar CSV and "export" file formats, it 
# would be SO TEMPTING to imagine you could easily generalize them.  I thought
# they're just different enough that it would be a nightmare, so here we are.
#
# It is expected that this will be able to handle "raw" columnar CSV formats as
# well, which contain no metadata, but instead begin directly with the header
# row.  In that case, wavelength and wavenumber are used directly from the input
# data, as no wavecal coefficients or excitation are available.
class CSVLoader(object):

    def __init__(self, pathname, save_options=None, encoding="utf-8"):
        self.pathname = pathname
        self.save_options = save_options
        self.encoding = encoding

        # default
        self.timestamp = datetime.datetime.now()

        # temporarily store these if no wavecal is provided
        self.metadata = {
            "pixel": [],
            "wavelength": [],
            "wavenumber": []
        }

        self.headers = []
        self.processed_reading = ProcessedReading()
        self.processed_reading.reading = Reading(device_id = "LOAD:" + pathname)

    def parse_metadata(self, line):
        line = list(line)
        key   = line[0]
        value = line[1:] # for lists with only one element this will give []
                         # Useful for cases like Declared Match,,,,,,,,

        self.metadata[key] = value

    def parse_header(self, line):
        self.headers = [ x.lower().strip() for x in line ] # force lowercase
        if "processed" in self.headers: self.processed_reading.processed = []
        if "raw"       in self.headers: self.processed_reading.raw       = []
        if "dark"      in self.headers: self.processed_reading.dark      = []
        if "reference" in self.headers: self.processed_reading.reference = []
        if "corrected" in self.headers: self.processed_reading.processed = []
        log.debug("parse_header: headers = %s", self.headers)

    def load_data(self):
        state = "reading_metadata"
        data_rows_read = 0
        with open(self.pathname, "r", encoding=self.encoding) as infile:
            csv_lines = csv.reader(infile)
            for line in csv_lines:
                # skip comments and blanks
                log.debug(line)
                if len(line) == 0 or line[0].startswith('#'):
                    continue

                # log.debug("load_data[%s]: %s", state, line)

                line[-1] = line[-1].strip() # remove the \n
                if state == "reading_metadata":
                    
                    # check for end of metadata (note trailing comma!)
                    cleanup_line = lambda x: x.strip().lower()
                    line = [cleanup_line(part) for part in line]
                    check_present = lambda x: x in line
                    contains_header = [check_present(header) 
                                       for header in ["pixel", "wavelength", "wavenumber", "processed"]]
                    if any(contains_header):
                        self.parse_header(line)
                        state = "reading_data"
                    else:
                        # still in metadata
                        self.parse_metadata(line)

                elif state == "reading_metadata_final":
                    self.parse_metadata(line)
                
                elif state == "reading_data":
                    values = [x.strip() for x in line]

                    # if we find more metadata after data ended, store it but 
                    # do not transition back
                    if not re.match(r'^[-+]?\d', values[0]):
                        state = "reading_metadata_final"
                        self.parse_metadata(line)
                        continue

                    # Assume each value read aligns with a known headers, but recognize that there
                    # could be more headers than there are values (some columns with headers may
                    # not actually have populated data, blank or otherwise).  This is the number of
                    # comma-delimited fields actually read (or the number of headers, if more values
                    # were read than had headers).
                    count = min(len(self.headers), len(values))

                    for i in range(count):
                        header = self.headers[i]

                        # SKIP nulls.  Note we're APPENDING data to each list, so this means that 
                        # if there are blanks (rather than '0' zeros) in the MIDDLE of a column, 
                        # the resulting spectral matrix will have different-length columns and
                        # improperly-associated "rows".
                        value = values[i]
                        if len(value) == 0:
                            continue

                        # add to array
                        array = None
                        if   header == "processed":  array = self.processed_reading.processed
                        elif header == "corrected":  array = self.processed_reading.processed 
                        elif header == "raw":        array = self.processed_reading.raw
                        elif header == "dark":       array = self.processed_reading.dark
                        elif header == "reference":  array = self.processed_reading.reference
                        elif header == "pixel":      array = self.metadata['pixel']      
                        elif header == "wavelength": array = self.metadata['wavelength'] 
                        elif header == "wavenumber": array = self.metadata['wavenumber'] 

                        if array is not None:
                            # log.debug("appending to %s: %s", header, value)
                            array.append(float(value))
                        else:
                            log.error("load_data: null array?")

                        if data_rows_read == 0 and header == "pixel" and int(value) != 0:
                            self.processed_reading.first_pixel = int(value)

                    data_rows_read += 1
