# Virtual Spectrometers

Example virtual spectrometers can be found in the [ENLIGHTEN repository](https://github.com/WasatchPhotonics/ENLIGHTEN/tree/main/testSpectrometers)

## Running demo_virtual.py

Copy the [testSpectrometers](https://github.com/WasatchPhotonics/ENLIGHTEN/tree/main/testSpectrometers/) folder to the program working directory.

This should already be done if you cloned the repo.

After this, you can run the script with `python demo_virtual.py` and it should work.

An important note, you don't need to run the open command. The virtual spectrometer is already connected

## Creating a virtual spectrometer

Two things are needed to create a virtual spectrometer:
1. An eeprom file
2. readings

The readings can be contained in the eeprom file as part of the *measurements* key in the JSON.

These files need to be placed in an eeprom and readings folder in a named folder under the working directory of your program

The pathing then should look like this for someone on Windows:

- {program_running_location}\testSpectrometers\{insert_your_spectrometer_name}\eeprom
- {program_running_location}\testSpectrometers\{insert_your_spectrometer_name}\readings

The readings folder can be blank if they are included in the eeprom JSON file. If there are readings in the eeprom JSON file,
then anything in the readings folder is ignored. You **cannot** mix and match.

Once the folder has been created, in a python file you can create a DeviceID based on it as follows.

```sim_spec = DeviceID(label="MOCK:{insert_your_spectrometer_name}:{insert_the_eeprom_filename}.json")
device = WasatchDevice(device_id=sim_spec)```

The device object can now be interacted with the same as a regular spectrometer device object.

