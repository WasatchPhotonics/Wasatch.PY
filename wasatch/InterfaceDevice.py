import logging

from .SpectrometerResponse import SpectrometerResponse, ErrorLevel
from .StatusMessage            import StatusMessage

log = logging.getLogger(__name__)

class InterfaceDevice:

    def __init__(self, device_id, message_queue=None, alert_queue=None):
        """
        Any class that communicates to a spectrometer should inherit this class.
        It provides the common functions that avoid repeated code.

        @param device_id     either DeviceID or string equiv ("USB:0x24aa:0x1000:1:24")
        @param message_queue if provided, used to send status back to caller
        @param alert_queue   if provided, used to receive hints and realtime 
                             interrupts from caller, including cancellation of 
                             long-running tasks like AutoRaman

        @todo move scan averaging, TakeOneRequest logic upstream from 
              WasatchDevice, AndorDevice etc to here (simplify children of this 
              class while while increasing code-reuse and consistency across all
              InterfaceDevice variants)
        """
        self.device_id      = device_id
        self.message_queue  = message_queue # outgoing status back to ENLIGHTEN 
        self.alert_queue    = alert_queue   # incoming alerts from ENLIGHTEN 

        # if passed a string representation of a DeviceID, deserialize it
        if type(device_id) is str:
            device_id = DeviceID(label=device_id)

        self.process_f = {}
        self.remaining_throwaways = 0
        self.alerts = set()

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

    def queue_message(self, setting, value):
        """
        If an upstream queue is defined, send the name-value pair.  Does nothing
        if the caller hasn't provided a queue.

        "setting" is application (caller) dependent, but ENLIGHTEN currently uses
        "marquee_info" and "marquee_error".
        """
        if self.message_queue is None:
            return SpectrometerResponse(data=False)

        msg = StatusMessage(setting, value)
        try:
            self.message_queue.put(msg) 
        except:
            log.error("failed to enqueue StatusMessage (%s, %s)", setting, value, exc_info=1)
            return SpectrometerResponse(data=False, error_msg="failed to enqueue messsage")
        return SpectrometerResponse(data=True)

    def refresh_alerts(self):
        if self.alert_queue is None:
            return

        if self.alert_queue.empty():
            return

        while not self.alert_queue.empty():
            alert = self.alert_queue.get_nowait()
            if alert is None:
                continue
            elif isinstance(alert, ControlObject):
                if alert.value:
                    log.debug(f"raised alert {alert.setting}")
                    self.alerts.add(alert.setting)
                else:
                    log.debug(f"cleared alert {alert.setting}")
                    self.alerts.discard(alert.setting)
            else:
                log.error(f"non-ControlObject found in alerts_queue: {alert}")

    def check_alert(self, s):
        log.debug(f"checking for alert {s}")
        self.refresh_alerts()
        if s in self.alerts:
            log.debug(f"found {s} (clearing)")
            self.alerts.remove(s)
            return True

class InterfaceDeviceClassUnavailable(Exception):
    """
    InterfaceDevice objects can raise this to quietly indicate that they have 
    been already deemed unavailable at runtime, and don't need to repeatedly 
    generate CRITICAL error messages
    """
    pass
