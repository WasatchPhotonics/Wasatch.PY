# WasatchDeviceWrapper Settings

## History

Unlike the more "functional" and "property-based" Wasatch.NET, for historical
reasons much of Wasatch.PY functionality is exposed through (name, value) key-value
pairs which can be sent through WasatchDevice.change\_setting().  

Essentially, this is because Wasatch.PY started as the hardware-facing "back-end"
to ENLIGHTEN, running in a separate process, and so ENLIGHTEN would send 
"commands" and "settings" to the subprocess via easily-pickled string objects
passed through a multiprocessing.Pipe.  (That's still how it happens today, using
WasatchDeviceWrapper).

What this means to you as a third-party developer is that the Wasatch.PY 
interface may feel a little weird, bloated and inelegant, because it was 
designed for a specific use-case that you may not share.

As you can see in wasatch.FeatureIdentificationDevice.init\_lambdas(), most
of these settings tuples are simply pass-throughs to more traditional method
calls on FeatureIdentificationDevice.  You may well find it simpler simply
to instantiate a FeatureIdentificationDevice directly and interact with the
spectrometer at that level.

However, some of the functionality provided by the driver, such as scan 
averaging, is only available through the key-value settings described below.

## Supported Settings

At writing, these are the string keys which can be passed to 
wasatch.WasatchDevice.change_setting():

- acquire 
    - (value ignored) triggers an acquisition
- acquisition_take_dark_enable
    - (bool) if acquisition_laser_trigger_enable, automatically takes a dark before 
      enabling the laser and attach to Reading
- acquisition_laser_trigger_delay_ms 
    - (delay in ms) sets the delay AFTER firing the laser BEFORE the acquisition 
      starts
- acquisition_laser_trigger_enable 
    - (bool) dis/enable automatic laser triggering (in driver; raman_mode_enable is in firmware))
- allow_default_gain_reset 
    - (bool) allow the legacy "default" gain of 1.9 to be "set", as this is 
      traditionally disabled
- area_scan_enable 
    - (bool) dis/enable area scan mode, in which each horizontal detector row is 
      read-out separately
- bad_pixel_mode 
    - (see SpectrometerState.BAD_PIXEL_MODE) turns bad-pixel averaging on or off
- degC_to_dac_coeffs 
    - (list of 3 floats) sets new detector TEC coefficients
- detector_gain 
    - (float) sets a new detector gain
- detector_gain_odd 
    - (float) sets gain for the odd pixels of an InGaAs detector
- detector_offset 
    - (int16) sets additive offset for detector
- detector_offset_odd 
    - (int16) sets additive offset for odd pixels of an InGaAs detector
- detector_roi
    - (uint8 region, uint16[4] roi) configures detector region of interest
- detector_tec_enable 
    - (bool) turns the detector TEC on or off
- detector_tec_setpoint_degC 
    - (float) detector TEC setpoint in degC
- dfu_enable (no arguments)
- enable_secondary_adc 
    - (bool) experimental (photodiode support)
- free_running_mode 
    - (bool) automatically start a new spectrum when the last completes (ticked 
      by WasatchDeviceWrapper)
- graph_alternating_pixels 
    - (bool) automatically average-over odd pixels (average neighboring adjacent 
      event pixels)
- high_gain_mode_enable 
    - (bool) dis/enable high-gain mode on InGaAs detectors
- integration_time_ms 
    - (uint24) set integration time in milliseconds
- invert_x_axis 
    - (bool) flip the spectrum horizontally
- laser_enable 
    - (bool) turn the laser on or off
- laser_power_high_resolution 
    - (bool) switch the laser power resolution (modulation period) between 100us 
      (low-resolution) and 1000us (high-resolution)
- laser_power_mW 
    - (float) set the laser output power in mW, if a laser power calibration is 
      provided
- laser_power_perc 
    - (float) set the laser output power in the range (0.0, 100.0) of its full 
      power via modulation (not guaranteed linear)
- laser_power_ramp_increments 
    - (uint) experimental (laser power ramping)
- laser_power_ramping_enable 
    - (bool) experimental (laser power ramping)
- laser_power_require_modulation 
    - (bool) force laser power to be modulated, even at full power
- laser_temperature_setpoint_raw 
    - (uint12) desired laser temperature setpoint as raw DAC value (production only)
- log_level 
    - ("DEBUG", "INFO" etc) set logging level
- max_usb_interval_ms 
    - (uint) when injecting random USB comms delays, set the delay ceiling
- min_usb_interval_ms 
    - (uint) when injecting random USB comms delays, set the delay floor
- pixel_mode
    - 10/12-bit detector pixel depth and ADC range
- raise_exceptions 
    - (bool) in the event of an exception, raise() rather than simply log()
- raman_delay_ms
    - (uint) when Raman Mode is enabled, the delay between firing the laser and 
      starting the integration
- raman_mode_enable
    - (bool) if enabled, automatically turn on laser before an acquisition, and 
      turn off after (in fw; acquisition_laser_trigger_enable is in driver)
- replace_eeprom 
    - (serial, EEPROM tuple) replace the in-memory EEPROM instance with that passed
- reset_fpga 
    - (ignore arg) attempt to reset the FPGA (experimental)
- scans_to_average 
    - (uint) set scan averaging to the requested value
- selected_laser 
    - (uint) experimental (multi-laser systems)
- swap_alternating_pixels 
    - (bool) swap each pair of pixels (so 0, 1, 2, 3...) becomes (1, 0, 3, 2...)
- trigger_source 
    - (SpectrometerState.TRIGGER_SOURCE) set hardware or software acquisition 
      triggering
- update_eeprom 
    - ((serial, EEPROM) tuple) updates the "editable" fields of the in-memory 
      EEPROM from the passed object
- vertical_binning
    - ((start, stop) tuple) sets start- and end-line for vertical binning on the detector
- write_eeprom 
    - (no arg) writes the in-memory EEPROM to the spectrometer (voids warranty)
