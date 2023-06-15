from dataclasses import dataclass, field

class SpectrometerRequest:
    cmd = ''
    args = field(default_factory=list)
    kwargs = field(default_factory=dict)

    def __init__(self, cmd, args=None, kwargs=None):
        self.cmd = cmd
        if args:
            self.args = args
        if kwargs:
            self.kwargs = kwargs

    def __str__(self):
        return (
            f'<SpectrometerResponse cmd {self.cmd}, args {self.args}, kwargs {self.kwargs}>'
            )

    def clear(self):
        self.cmd = ''
        args = []
