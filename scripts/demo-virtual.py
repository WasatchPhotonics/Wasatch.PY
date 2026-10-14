import sys
import importlib

from wasatch.DeviceID import DeviceID
from wasatch.WasatchDevice import WasatchDevice

# need to do this because wasatch-shell is invalid syntax
sys.path.append('WasatchShell') # exposes wasatch-shell since it is in a subfolder
wasatch_shell = importlib.import_module("wasatch-shell")

sim_spec_id = DeviceID(label="MOCK:WP-00887:WP-00887-mock.json")
device = WasatchDevice(device_id=sim_spec_id)
device.connect()

shell = wasatch_shell.WasatchShell()
shell.device = device
shell.run()
