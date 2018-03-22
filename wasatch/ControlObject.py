import logging

log = logging.getLogger(__name__)

class ControlObject(object):
    """ A simple abstraction containing a setting to control and the value to set. """
    def __init__(self, setting=None, value=None):
        super(ControlObject, self).__init__()
        log.debug("%s ctor(%s, %s)", self.__class__.__name__, setting, value)
        self.setting = setting
        self.value = value
