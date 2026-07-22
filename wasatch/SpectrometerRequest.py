from typing import Any
from dataclasses import dataclass, field

@dataclass
class SpectrometerRequest:
    """
    As far as I can tell, this was _literally_ an attempt to pass a Python 
    function call across a pickled multiprocess.Queue.

    foo(a, b, x=1, y=2) -> list(int)
        became 
    SpectrometerRequest("foo", args=[a, b], kwargs={"x": 1, "y": 2}) -> SpectrometerResponse(data=list(int))

    And SpectrometerResponse, in turn, was an attempt to return values (including
    exceptions) back across a multiprocess.Queue.

    I think we ended up here because we attempted to support return-values
    (necessitating SpectrometerResponse) before we moved from multiprocess to 
    multithreaded. 

    It prompts one to wonder whether we could obviate this architecture entirely,
    however...

    We honestly don't WANT ENLIGHTEN calling directly into InterfaceDevices,
    because that could create all manner of multi-threaded contention on the USB
    _serial_ bus. The fact that these requests can be neatly queued, dedupped and
    serviced in order is a feature, not a bug.
    """
    cmd: str = ''
    args: list[Any] = field(default_factory=list)
    kwargs: dict[Any, Any] = field(default_factory=dict) 

    # MZ: is kwargs key really 'Any'? I don't think that would survive **expansion

    def __str__(self):
        return f"SpectrometerRequest <cmd {self.cmd}, args {self.args}, kwargs {self.kwargs}>"

    def clear(self):
        self.cmd = ''
        self.args = []
        self.kwargs = {}
