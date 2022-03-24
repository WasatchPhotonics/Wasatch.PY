from typing import Any
from enum import Enum
from dataclasses import dataclass

class ErrorLevel(Enum):
    ok = 0
    low = 1
    medium = 2
    high = 3

@dataclass
class SpectrometerResponse:
    data: Any           = None
    error_msg: str      = ''
    error_lvl: int      = ErrorLevel.ok
    poison_pill: bool   = False
    keep_alive: bool    = False

    def transfer_response(self,old_response):
        self.data = old_response.data
        self.error_msg = old_response.error_msg
        self.error_lvl = old_response.error_lvl
        self.poison_pill = old_response.poison_pill
        self.keep_alive = old_response.keep_alive

    def __str__(self):
        return f"<SpectrometerResponse ({id(self)}), {self.error_lvl}, Keep Alive {self.keep_alive}, Poison Pill {self.poison_pill}, msg {self.error_msg[:5]}>"

    def clear(self):
        self.data = None
        self.error_msg = ''
        self.error_lvl = ErrorLevel.low
        self.poison_pill = False
        self.keep_alive = False
