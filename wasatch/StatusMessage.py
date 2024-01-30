class StatusMessage:
    """
    An outbound analog to ControlObject.  Not re-using the same class so that
    it can be extended in different ways, perhaps subclassed etc. 
    
    There is no enumeration of supported status messages, but ENLIGHTEN's
    Controller.process_status_message() would be a good source.
    """
    def __init__(self, setting, value):
        self.setting = setting  # probably a string
        self.value   = value    # don't presume type
