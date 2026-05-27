from typing import Any
from enum import Enum
from dataclasses import dataclass

class ErrorLevel(Enum):
    ok     = 0
    low    = 1
    medium = 2
    high   = 3

@dataclass
class SpectrometerResponse:
    data:        Any  = None
    error_msg:   str  = ''
    error_lvl:   int  = ErrorLevel.ok
    poison_pill: bool = False
    keep_alive:  bool = False
    incomplete:  bool = False
    progress:    int  = 0

    def transfer_response(self, rhs):
        """ copy-ctor """
        self.data        = rhs.data
        self.error_msg   = rhs.error_msg
        self.error_lvl   = rhs.error_lvl
        self.poison_pill = rhs.poison_pill
        self.keep_alive  = rhs.keep_alive
        self.incomplete  = rhs.incomplete
        self.progress    = rhs.progress

    def __repr__(self):
        s = str(self.data)
        if len(s) > 10:
            s = s[:10] + "..."
        return f"SpectrometerResponse <data {s}, err_lvl {self.error_lvl.value}, keepalive {self.keep_alive}, poison {self.poison_pill}, err_msg {self.error_msg}>"

    def clear(self):
        self.data = None
        self.error_msg = ''
        self.error_lvl = ErrorLevel.ok
        self.poison_pill = False
        self.keep_alive = False
