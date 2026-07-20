import logging
import json

log = logging.getLogger(__name__)

"""
An XSAccessoryConnector encapsulates the 10-pin OEM Accessory Connector on a 
220250 Rev5 XS V2 Main Board.

This object is normally accessed via SpectrometerState.acc_connector.

An XSAccessoryConnector itself is made of four sub-objects formed from three
classes:

- self.acc_state is an XSAccState, encapsulating gpio_enable and acc_5V_enable 
- self.state_gpio1 and state_gpio2 are both instances of XSGPIOState, 
  encapsulating the state of the two GPIO pins (note there is no GPIO 0)
- self.cont_strobe encapsulates the four parameters used by GPIO2's Continuous 
  Strobe function. This object is only relevant if the Continuous Strobe
  function is configured on GPIO2.

For additional information on these classes, see:

- ENG-0034 EEPROM Format (see ACC_STATE, ACC_GPIO1_STATE and ACC_GPIO2_STATE)
- ENG-0218 XS V2 OEM Accessory Connector
- 170132 FPGA Register Map (note that external USB/BLE enums may not match 
  internal FPGA register definitions)
"""

class XSAccState:

    def __init__(self):
        self.gpio_enabled = False

        # these are only for ACC_5V_OUT...ACC_5V_IN is configured via HW
        self.acc_5V_enabled = False
        self.acc_5V_good = False

    def serialize(self):
        """
        Generate a uint16 we can write via set_acc_state, which represents current object state.
        """
        value = 0x0000 # potentially a 16-bit mask

        if self.gpio_enabled:   value |= 0x0001
        if self.acc_5V_enabled: value |= 0x0002
        if self.acc_5V_good:    value |= 0x0004 # this bit is read-only, so FW won't actually use it, but included for completeness (won't hurt anything)

        return value

    def deserialize(self, value):
        """
        Update object state from a uint16 value we read via get_acc_state.
        """
        self.gpio_enabled   = (0 != value & 0x0001)
        self.acc_5V_enabled = (0 != value & 0x0002)
        self.acc_5V_good    = (0 != value & 0x0004) # bit is read-only, so this WILL work

    def __repr__(self):
        return f"XSAccState < gpio enabled {self.gpio_enabled}, 5V enabled {self.acc_5V_enabled}, 5V good {self.acc_5V_good} >"

    def to_json(self): 
        return vars(self)

class XSGPIOState:

    # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # Note these are the OPPOSITE of FpgaRegisterMap.xml, but match ENG-0218
    # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    CONTROL_MANUAL = 0      # user can set GPIO direction (in, out) and value (hi, lo) manually
    CONTROL_FUNCTION = 1    # GPIO is configured to automatically execute a pre-program function implemented in the FPGA (supported values listed below)

    DIR_INPUT = 0
    DIR_OUTPUT = 1

    VALUE_LOW = 0
    VALUE_HIGH = 1

    FUNC_DISABLED = 0

    GPIO1_FUNC_EXT_TRIGGER_RISING_EDGE = 1
    GPIO1_FUNC_LASER_OVERRIDE = 2

    GPIO2_FUNC_CONT_STROBE = 1
    GPIO2_FUNC_DATA_READY = 2
    GPIO2_FUNC_LASER_MIRROR = 3

    def __init__(self, num):

        if num not in [1, 2]:
            raise ValueError("XS has GPIO1 and GPIO2, but no {num}")
        self.num = num

        self.control = self.CONTROL_MANUAL
        self.direction = self.DIR_INPUT
        self.value = self.VALUE_LOW
        self.function = self.FUNC_DISABLED

    def serialize(self):
        mask = 0x00

        mask |= 0x01 if self.control == self.CONTROL_FUNCTION else 0
        mask |= 0x02 if self.direction == self.DIR_OUTPUT else 0
        if self.direction == self.DIR_OUTPUT:
            mask |= 0x04 if self.value == self.VALUE_HIGH else 0
        mask |= (self.function << 4)    

        log.debug(f"XSGPIOState.serialize: 0x{mask:02x} from {self}")
        
        return mask

    def deserialize(self, value):
        self.control   = self.CONTROL_FUNCTION if value & 0x01 else self.CONTROL_MANUAL
        self.direction = self.DIR_OUTPUT if value & 0x02 else self.DIR_INPUT
        self.function  = (value >> 4) & 0xf

        if self.direction == self.DIR_INPUT:
            self.value = self.VALUE_HIGH if value & 0x04 else self.VALUE_LOW

        log.debug(f"XSGPIOState.deserialize: 0x{value:02x} -> {self}")

    def doing_continuous_strobe(self):
        return self.num == 2 and \
               self.control == self.CONTROL_FUNCTION and \
               self.function == self.GPIO2_FUNC_CONT_STROBE

    def get_control_str(self):
        return "MANUAL" if self.control == self.CONTROL_MANUAL else "FUNCTION"

    def get_value_str(self):
        return "HIGH" if self.value == self.VALUE_HIGH else "LOW"

    def get_direction_str(self):
        return "OUTPUT" if self.direction == self.DIR_OUTPUT else "INPUT"

    def get_function_str(self):
        if self.num == 1:
            if self.function == self.FUNC_DISABLED: return "DISABLED"
            if self.function == self.GPIO1_FUNC_EXT_TRIGGER_RISING_EDGE: return "EXT_TRIGGER_RISING_EDGE"
            if self.function == self.GPIO1_FUNC_LASER_OVERRIDE: return "LASER_OVERRIDE"
        elif self.num == 2:
            if self.function == self.FUNC_DISABLED: return "DISABLED"
            if self.function == self.GPIO2_FUNC_CONT_STROBE: return "CONT_STROBE"
            if self.function == self.GPIO2_FUNC_DATA_READY: return "DATA_READY"
            if self.function == self.GPIO2_FUNC_LASER_MIRROR: return "LASER_MIRROR"

        return f"INVALID (num {self.num}, func {self.function})"

    def __repr__(self):
        return f"XSGPIOState < num {self.num}, control {self.get_control_str()}, direction {self.get_direction_str()}, value {self.get_value_str()}, func {self.get_function_str()} >"

    def to_json(self): 
        return vars(self)

class XSContinuousStrobe:

    def __init__(self):
        self.period_us = 2
        self.width_us = 1
        self.delay_us = 0
        self.repeat_count = 0

    def __repr__(self):
        return f"XSContinuousStrobe < period {self.period_us}us, width {self.width_us}us, delay {self.delay_us}us, repeat {self.repeat_count} >"

    def to_json(self): 
        return vars(self)

class XSAccessoryConnector:

    def __init__(self):
        self.acc_state = XSAccState()
        self.state_gpio1 = XSGPIOState(1)
        self.state_gpio2 = XSGPIOState(2)
        self.cont_strobe = XSContinuousStrobe()

    def doing_continuous_strobe(self):
        if self.acc_state is None or self.state_gpio2 is None:
            return False
        if not self.acc_state.gpio_enabled:
            return False
        return self.state_gpio2.doing_continuous_strobe()

    def __repr__(self):
        return f"XSAccessoryConnector < acc_state {self.acc_state}, GPIO1 {self.state_gpio1}, GPIO2 {self.state_gpio2}, strobe {self.cont_strobe} >"

    def to_json(self): 
        return vars(self)

    def dump(self):
        log.debug(f"XSAccessoryConnector:")
        log.debug(f"  acc_state {self.acc_state}")
        log.debug(f"  gpio1 {self.state_gpio1}")
        log.debug(f"  gpio2 {self.state_gpio2}")
        log.debug(f"  cont_strobe {self.cont_strobe}")
