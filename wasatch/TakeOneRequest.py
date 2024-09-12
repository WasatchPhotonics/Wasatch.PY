import os
import time
import random
import threading

class TakeOneRequest:

    def __init__(self, take_dark=False, 
                       enable_laser_before=False, 
                       disable_laser_after=False, 
                       laser_warmup_ms=0, 
                       scans_to_average=1, 
                       auto_raman_request=None,
                       template=None):
        if template is not None:
            self.take_dark           = template.take_dark
            self.enable_laser_before = template.enable_laser_before
            self.disable_laser_after = template.disable_laser_after
            self.laser_warmup_ms     = template.laser_warmup_ms
            self.scans_to_average    = template.scans_to_average
            self.auto_raman_request  = template.auto_raman_request
        else:
            self.take_dark           = take_dark
            self.enable_laser_before = enable_laser_before
            self.disable_laser_after = disable_laser_after
            self.laser_warmup_ms     = laser_warmup_ms
            self.scans_to_average    = scans_to_average
            self.auto_raman_request  = auto_raman_request

        self.request_id = f"{os.getpid():05d}-{threading.get_native_id():05d}-{time.time_ns()}-{random.randrange(65536):05d}"

    def __repr__(self):
        return f"TakeOneRequest <id {self.request_id}, avg {self.scans_to_average}, take_dark {self.take_dark}, enable_laser_before {self.enable_laser_before}, disable_laser_after {self.disable_laser_after}, laser_warmup_ms {self.laser_warmup_ms}, auto_raman_request {self.auto_raman_request}>"

    def __eq__(self, rhs):
        return self.request_id == rhs.request_id

