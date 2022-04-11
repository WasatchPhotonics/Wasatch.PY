from .SpectrometerResponse import SpectrometerResponse
from .SpectrometerRequest  import SpectrometerRequest

class InterfaceDevice:
    def __init__(self) -> None:
        """
        Any class that communicates to a spectrometer should inherit this class.
        It provides the common functions that avoid repeated code.
        """
        self.process_f = []

    def handle_requests(self, requests: list[SpectrometerRequest]) -> list[SpectrometerResponse]:
        responses = []
        for request in requests:
            try:
                cmd = request.cmd
                proc_func = self.process_f.get(cmd, None)
                if proc_func == None:
                    responses.append(SpectrometerResponse(error_msg=f"unsupported cmd {request.cmd}", error_lvl=ErrorLevel.low))
                elif request.args == [] and request.kwargs == {}:
                    responses.append(proc_func())
                else:
                    responses.append(proc_func(*request.args, **request.kwargs))
            except Exception as e:
                log.error(f"error in handling request {request} of {e}")
                responses.append(SpectrometerResponse(error_msg="error processing cmd", error_lvl=ErrorLevel.medium))
        return responses