from typing import Any
from enum import IntEnum
from dataclasses import dataclass

class ErrorLevel(IntEnum):
    ok = 0
    low = 1
    medium = 2
    high = 3

@dataclass
class SpectrometerResponse:
    data: Any           = None
    error_msg: str      = ''
    error_lvl: int      = ErrorLevel.low
    poison_pill: bool   = False
    keep_alive: bool    = False

    def transfer_response(self,old_response):
        self.data = old_response.data
        self.error_msg = old_response.error_msg
        self.error_lvl = old_response.error_lvl
        self.poison_pill = old_response.poison_pill

    def clear(self):
        self.data = None
        self.error_msg = ''
        self.error_lvl = ErrorLevel.low
        self.poison_pill = False
        self.keep_alive = False
