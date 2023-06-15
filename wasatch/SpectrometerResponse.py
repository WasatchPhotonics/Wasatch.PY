from typing import Any
from enum import Enum
from dataclasses import dataclass

class ErrorLevel(Enum):
    ok = 0
    low = 1
    medium = 2
    high = 3

class SpectrometerResponse:
    data: Any           = None
    error_msg: str      = ''
    error_lvl: int      = ErrorLevel.ok
    poison_pill: bool   = False
    keep_alive: bool    = False
    incomplete: bool    = False
    progress: int       = 0


    def __init__(self, data=None, error_msg='', error_lvl=ErrorLevel.ok, poison_pill=False, keep_alive=False, incomplete=False, progress=0):
        self.data = data
        self.error_msg = error_msg
        self.error_lvl = ErrorLevel.ok
        self.poison_pill = False
        self.keep_alive = False
        self.incomplete = False
        self.progress = 0

    def transfer_response(self,old_response):
        self.data = old_response.data
        self.error_msg = old_response.error_msg
        self.error_lvl = old_response.error_lvl
        self.poison_pill = old_response.poison_pill
        self.keep_alive = old_response.keep_alive
        self.incomplete = old_response.incomplete
        self.progress = old_response.progress

    def __str__(self):
        return f"<SpectrometerResponse ({id(self)}), {self.error_lvl}, Keep Alive {self.keep_alive}, Poison Pill {self.poison_pill}, error_msg {self.error_msg}, incomplete {self.incomplete}, progress {self.progress}>"

    def clear(self):
        self.data = None
        self.error_msg = ''
        self.error_lvl = ErrorLevel.low
        self.poison_pill = False
        self.keep_alive = False
