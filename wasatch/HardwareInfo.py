class HardwareInfo:
    """ Really this is just USB info... """

    def __init__(self, vid=None, pid=None):
        self.vid = vid
        self.pid = pid
    
    def is_ingaas(self):
        return self.pid == 0x2000

    def is_arm(self):
        return self.pid == 0x4000

    def to_dict(self):
        return self.__dict__
