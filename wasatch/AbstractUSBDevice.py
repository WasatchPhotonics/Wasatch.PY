
class AbstractUSBDevice:

    def __init__(self):
        pass

    def find(self):
        pass

    def set_configuration(self):
        pass

    def reset(self):
        pass

    def claim_interface(self):
        pass

    def release_interface(self):
        pass

    def ctrl_transfer(self):
        pass

    def read(self):
        pass

    def send_code(self):
        pass