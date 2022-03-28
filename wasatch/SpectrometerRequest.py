from typing import Any
from dataclasses import dataclass, field

@dataclass
class SpectrometerResponse:
    cmd: str = ''
    args: list[Any] = field(default_factory=list)
    kwargs: Dict[Any, Any] = field(default_factory=dict)

    def __str__(self):
        return f"<SpectrometerResponse cmd {cmd}, args {args}>"

    def clear(self):
        self.cmd = ''
        args = []
