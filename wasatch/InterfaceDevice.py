import logging

from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerResponse import ErrorLevel

log = logging.getLogger(__name__)

class InterfaceDevice:
    def __init__(self):
        """
        Any class that communicates to a spectrometer should inherit this class.
        It provides the common functions that avoid repeated code.

        @todo move scan averaging, TakeOneRequest logic upstream from 
              WasatchDevice, AndorDevice etc to here (simplify children of this 
              class while while increasing code-reuse and consistency across all
              InterfaceDevice variants)
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
                    log.error(f"handle_requests: unsupported command {request.cmd}")
                    responses.append(SpectrometerResponse(error_msg=f"unsupported cmd {request.cmd}", error_lvl=ErrorLevel.low))
                elif request.args == [] and request.kwargs == {}:
                    # log.debug(f"handle_requests: relaying {request.cmd} w/o args to {proc_func}")
                    responses.append(proc_func())
                else:
                    # log.debug(f"handle_requests: relaying {request.cmd} with args {request.args}, kwargs {request.kwargs} to proc_func {proc_func}")
                    responses.append(proc_func(*request.args, **request.kwargs))
            except Exception as e:
                log.error(f"error in handling request {request} of {e}", exc_info=1)
                responses.append(SpectrometerResponse(error_msg="error processing cmd", error_lvl=ErrorLevel.medium))
        return responses
