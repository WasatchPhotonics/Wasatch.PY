import logging

from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerResponse import ErrorLevel

log = logging.getLogger(__name__)

class InterfaceDevice:
    def __init__(self):
        """
        Any class that communicates to a spectrometer should inherit this class.
        It provides the common functions that avoid repeated code.
        """
        self.process_f = {}
        self.remaining_throwaways = 0

    def handle_requests(self, requests):
        responses = []
        for request in requests:
            try:
                cmd = request.cmd
                proc_func = self.process_f.get(cmd, None)
                if proc_func is None:
                    responses.append(SpectrometerResponse(error_msg=f"unsupported cmd {request.cmd}", error_lvl=ErrorLevel.low))
                elif request.args == [] and request.kwargs == {}:
                    responses.append(proc_func())
                else:
                    responses.append(proc_func(*request.args, **request.kwargs))
            except Exception as e:
                log.error(f"error in handling request {request} of {e}", exc_info=1)
                responses.append(SpectrometerResponse(error_msg="error processing cmd", error_lvl=ErrorLevel.medium))
        return responses
