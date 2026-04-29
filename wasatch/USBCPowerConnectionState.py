import json
from . import utils

class USBCPowerConnectionState(dict):
    """
    Making this a standalone class because might end up using from both FID (USB)
    and BLEDevice.
    """
    BC_12_ADAPTER_TYPES = {
        1: "SDP", # Standard Downstream Port
        2: "CDP", # Charging Downstream Port
        3: "DCP"  # Dedicated Charging Port
    }

    BC_12_CHARGER_TYPES = {
        1: "Samsung 2A",
        2: "Apple 0.5A",
        3: "Apple 1A",
        4: "Apple 2A",
        5: "Apple 12W",
        6: "DCP 3A", # Dedicated Charging Port
        7: "Unknown"
    }

    TYPE_C_CC_CURRENT_CAPABILITY = {
        1: "500 mA",
        2: "1500 mA",
        3: "3000 mA"
    }

    def __init__(self, data=None):
        dict.__init__(self) # https://stackoverflow.com/a/31207881

        self.bc_12_adapter_type = None
        self.bc_12_charger_type = None
        self.type_c_cc_current_capability = None
        self.current_limit_mA = None

        self.parse_data(data)

    def parse_data(self, data):
        """
        This is based on the data structure defined in ENG-0120 Rev 9.

        BLE: 0x01 00 03 0b b8
        USB: GET_POWER_CONNECTION_STATE: _get_code: request 0xff value 0x0078 index 0x0000 length 5 = [00 07 00 00 00]
        """
        if data is None or len(data) < 1:
            return

        if len(data) >= 1: self.bc_12_adapter_type = self.BC_12_ADAPTER_TYPES.get(data[0], None)
        if len(data) >= 2: self.bc_12_charger_type = self.BC_12_CHARGER_TYPES.get(data[1], None)
        if len(data) >= 3: self.type_c_cc_current_capability = self.TYPE_C_CC_CURRENT_CAPABILITY.get(data[2], None)
        if len(data) >= 5: self.current_limit_mA = (data[3] << 8) + data[4]

    def short(self):
        tok = []
        if self.bc_12_adapter_type: 
            tok.append(self.bc_12_adapter_type)

        if self.bc_12_charger_type: 
            tok.append(self.bc_12_charger_type)

        if self.type_c_cc_current_capability: 
            tok.append(self.type_c_cc_current_capability)

        if self.current_limit_mA: 
            tok.append(f"{self.current_limit_mA}mA")

        return "/".join(tok)

    def long(self):
        tok = []
        if self.bc_12_adapter_type: 
            tok.append(f"BC 1.2 Adapter Type {self.bc_12_adapter_type}")

        if self.bc_12_charger_type: 
            tok.append(f"BC 1.2 Proprietary Charger Type {self.bc_12_charger_type}")

        if self.type_c_cc_current_capability: 
            tok.append(f"Type-C CC Current Capability {self.type_c_cc_current_capability}")

        if self.current_limit_mA: 
            tok.append(f"Current Limit {self.current_limit_mA}mA")

        return ", ".join(tok)

    def __repr__(self):
        return self.short()

    def toJSON(self):
        return json.dumps(self, default=lambda o: o.__dict__, sort_keys=True, indent=2)
