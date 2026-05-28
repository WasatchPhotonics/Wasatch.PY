

class GPIOState:
    MODE_FUNCTION                   = 0
    MODE_MANUAL                     = 1

    DIR_INPUT                       = 0
    DIR_OUTPUT                      = 1

    GPIO_FUNCTION_DISABLED          = 0

    GPIO1_FUNCTION_EXT_TRIGGER      = 1
    GPIO1_FUNCTION_LASER_OVERRIDE   = 2

    GPIO2_FUNCTION_CONT_STROBE      = 1
    GPIO2_FUNCTION_DATA_READY       = 2
    GPIO2_FUNCTION_LASER_MIRROR     = 3
    GPIO2_FUNCTION_LASER_ERROR      = 4

    def __init__(self, num):
        self.num = num
        self.mode = GPIOState.MODE_FUNCTION
        self.dir = GPIOState.DIR_INPUT
        self.function = GPIOState.GPIO_FUNCTION_DISABLED
        self.value = 0

    def serialize(self):
        mask = 0x00
        mask |= 0x01 if self.mode == self.MODE_MANUAL else 0
        mask |= 0x02 if self.dir == self.DIR_OUTPUT else 0
        if self.dir == self.DIR_OUTPUT:
            mask |= 0x04 if self.value else 0
        # bit 3 reserved
        mask |= (self.function << 4)

    def deserialize(self, mask):
        self.mode = mask & 0x01
        self.dir = mask & 0x02
        if self.dir == self.DIR_INPUT:
            self.value = mask & 0x04
        self.function = (mask >> 4) & 0xf

    def __repr__(self):
        return f"GPIO{self.num}< mode {self.mode}, dir {self.dir}, func {self.function}, value {self.value} >"

class AccessoryConnector:

    def __init__(self):
        self.acc_gpio_enabled = False
        self.acc_5v_enabled = False
        self.acc_5v_good = False # read-only

        self.state_gpio1 = GPIOState(1)
        self.state_gpio2 = GPIOState(2)

        self.strobe_period_us = None
        self.strobe_width_us = None
        self.strobe_delay_us = None
        self.strobe_count = None

    def serialize_acc_state(self):
        mask = 0x0000
        mask |= 0x0001 if self.acc_gpio_enabled else 0
        mask |= 0x0002 if self.acc_5v_enabled else 0
        # acc_5v_good read-only
        # bits 3-15 reserved
        return mask

    def deserialize_acc_state(self, mask):
        self.acc_gpio_enabled   = 0 != mask & 0x01
        self.acc_5v_enabled     = 0 != mask & 0x02
        self.acc_5v_good        = 0 != mask & 0x04 # read-only

    def __repr__(self):
        return f"AccessoryConnector < gpio_enabled {self.acc_gpio_enabled}, 5v_enabled {self.acc_5v_enabled}, 5v_good {self.acc_5v_good}"
