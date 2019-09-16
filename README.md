![Sample Console Views](https://github.com/WasatchPhotonics/Wasatch.PY/raw/master/screenshots/multiplatform.png)

# Overview

Wasatch.PY is a Python application-level driver for Wasatch Photonics spectrometers.
It has been tested on Windows, Linux and MacOS platforms, and is directly used by
Wasatch's own [ENLIGHTEN&trade;](https://wasatchphotonics.com/product-category/software/)
spectroscopy desktop GUI.

If you'd like simpler, smaller examples of how to perform individual operations
against Wasatch spectrometers from Python, please also see our 
[Python Example Scripts](https://github.com/WasatchPhotonics/Python-USB-WP-Raman-Examples)
repository.

## History 

This project can be viewed as a conceptual successor to the earlier 
[WasatchUSB](https://github.com/WasatchPhotonics/WasatchUSB).  The main 
differences are that while WasatchUSB was a copy-paste of key ENLIGHTEN 
functionality, Wasatch.PY literally is a dependency of ENLIGHTEN.  As a result, 
when we fix or add ENLIGHTEN features, Wasatch.PY will be updated by necessity;
making the same foundational interface available to all our users.  

WasatchUSB, in contrast, had the potential to drift out-of-sync with ENLIGHTEN 
internals, such that customers and company might experience different results.  
This new shared library exemplifies one of our core values: all about 
[dogfooding](https://en.wikipedia.org/wiki/Eating_your_own_dog_food)!

Finally, the updated project name reflects the fact that this is specifically a
Python binding and implementation; for other USB-capable interfaces, see our
[WasatchUSB](https://github.com/WasatchPhotonics/Wasatch.NET) or upcoming
Wasatch.CPP libraries!

# API

Rendered API documentation for classes and methods is available here:

- https://wasatchphotonics.com/api/Wasatch.PY/annotated.html

At writing, these are the string keys which can be passed to wasatch.WasatchDevice.change_setting():

- acquire
- acquisition_laser_trigger_delay_ms
- acquisition_laser_trigger_enable 
- allow_default_gain_reset
- area_scan_enable
- bad_pixel_mode
- degC_to_dac_coeffs
- detector_gain
- detector_gain_odd
- detector_offset
- detector_offset_odd
- detector_tec_enable
- detector_tec_setpoint_degC
- enable_secondary_adc
- free_running_mode
- graph_alternating_pixels
- high_gain_mode_enable
- integration_time_ms
- invert_x_axis
- laser_enable
- laser_power_high_resolution
- laser_power_mW
- laser_power_perc
- laser_power_ramp_increments
- laser_power_ramping_enable
- laser_power_require_modulation
- laser_temperature_setpoint_raw
- log_level
- max_usb_interval_ms
- min_usb_interval_ms
- overrides
- raise_exceptions
- replace_eeprom
- reset_fpga
- scans_to_average
- selected_laser
- swap_alternating_pixels
- trigger_source
- update_eeprom
- write_eeprom

For a full list of parameter strings, see the source code for FeatureIdentificationDevice.write_setting.

# Dependencies

Wasatch.PY uses the Python 3.x build of [Miniconda](https://conda.io/miniconda.html)
for dependencies and package management.

# Running the Demo

Following are the general usage instructions for the included command-line demo
scripts.  After find specific Anaconda setup instructions for Windows, Linux, MacOS 
and other tested platforms.

    mzieg-macbook.local [~/work/code/Wasatch.PY] mzieg  9:48PM $ conda activate wasatch3
    (wasatch3) mzieg-macbook.local [~/work/code/Wasatch.PY] mzieg  9:49PM $ python -u demo.py --help

	usage: demo.py [-h] [--log-level LOG_LEVEL]
				   [--integration-time-ms INTEGRATION_TIME_MS]
				   [--scans-to-average SCANS_TO_AVERAGE]
				   [--boxcar-half-width BOXCAR_HALF_WIDTH] [--delay-ms DELAY_MS]
				   [--outfile OUTFILE] [--max MAX] [--non-blocking] [--ascii-art]

	Simple demo to acquire spectra from command-line interface

	optional arguments:
	  -h, --help            show this help message and exit
	  --log-level LOG_LEVEL
							logging level [DEBUG,INFO,WARNING,ERROR,CRITICAL]
	  --integration-time-ms INTEGRATION_TIME_MS
							integration time (ms, default 10)
	  --scans-to-average SCANS_TO_AVERAGE
							scans to average (default 1)
	  --boxcar-half-width BOXCAR_HALF_WIDTH
							boxcar half-width (default 0)
	  --delay-ms DELAY_MS   delay between integrations (ms, default 1000)
	  --outfile OUTFILE     output filename (e.g. path/to/spectra.csv)
	  --max MAX             max spectra to acquire (default 0, unlimited)
	  --non-blocking        non-blocking USB interface (WasatchDeviceWrapper
							instead of WasatchDevice)
	  --ascii-art           graph spectra in ASCII

## Microsoft Windows 

The following session was run on Windows 10 using the Cmd shell of [Git for Windows](https://git-scm.com/)
and [Miniconda](https://conda.io/miniconda.html) (Python 3.x):

	C:\Users\mzieg>set PATH=%HOME%\miniconda3;%HOME%\miniconda3\scripts;%PATH%
	C:\Users\mzieg>cd work\code\Wasatch.PY
	C:\Users\mzieg\work\code\Wasatch.PY>conda update conda
	C:\Users\mzieg\work\code\Wasatch.PY>cp environments\conda-win10.yml environment.yml
	C:\Users\mzieg\work\code\Wasatch.PY>conda env create -n wasatch3
        Solving environment: done
        Preparing transaction: done
        Verifying transaction: done
        Executing transaction: done
        Requirement already satisfied: future==0.16.0 in c:\users\mzieg\miniconda3\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 1))
        Collecting pefile==2016.3.28 (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 2))
        Requirement already satisfied: pygtail==0.7.0 in c:\users\mzieg\miniconda3\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 3))
        Requirement already satisfied: pyside==1.2.4 in c:\users\mzieg\miniconda3\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 4))
        Requirement already satisfied: pytest-capturelog==0.7 in c:\users\mzieg\miniconda3\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 5))
        Requirement already satisfied: pytest-qt==2.1.0 in c:\users\mzieg\miniconda3\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 6))
        Requirement already satisfied: pyusb==1.0.0 in c:\users\mzieg\miniconda3\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 7))
        Requirement already satisfied: requests==2.13.0 in c:\users\mzieg\miniconda3\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 8))
        Requirement already satisfied: py>=1.1.1 in c:\users\mzieg\miniconda3\envs\wasatch3\lib\site-packages (from pytest-capturelog==0.7->-r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 5))
        Requirement already satisfied: pytest>=2.7.0 in c:\users\mzieg\miniconda3\envs\wasatch3\lib\site-packages (from pytest-qt==2.1.0->-r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 6))
        Requirement already satisfied: colorama in c:\users\mzieg\miniconda3\envs\wasatch3\lib\site-packages (from pytest>=2.7.0->pytest-qt==2.1.0->-r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 6))
        Installing collected packages: pefile
          Found existing installation: pefile 2017.11.5
            Uninstalling pefile-2017.11.5:
              Successfully uninstalled pefile-2017.11.5
        Successfully installed pefile-2016.3.28

	C:\Users\mzieg\work\code\Wasatch.PY>activate wasatch3

    C:\Users\mzieg\work\code\Wasatch.PY>python demo.py --outfile data.csv --integration-time-ms 100 --delay-ms 500
    2018-01-22 15:20:12,457 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 1
    2018-01-22 15:20:12,473 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 0
    2018-01-22 15:20:12,473 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 2
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 5
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO     EEPROM settings:
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Wavecal coeff0:   399.24130249
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Wavecal coeff1:   0.43601000309
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Wavecal coeff2:   -7.33139968361e-05
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Wavecal coeff3:   2.80489995674e-08
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Calibration date: 6/2
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Calibrated by:    NH
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Excitation (nm):  0
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       Slit size (um):   50
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       degCToDAC coeff0: 6.30511975963e-10
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       degCToDAC coeff1: 1.68748300666e-07
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       degCToDAC coeff2: 0.10000000149
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       adcToDegC coeff0: 66.0
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       adcToDegC coeff1: -0.00999999977648
    2018-01-22 15:20:12,490 MainProcess wasatch.fid_hardware INFO       adcToDegC coeff2: -9.99999974738e-05
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Det temp min:     20
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Det temp max:     10
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       TEC R298:         0
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       TEC beta:         0
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Detector name:    S10141
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Pixels:           1024
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Pixel height:     1
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Min integration:  1
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Max integration:  60000
    2018-01-22 15:20:12,505 MainProcess wasatch.fid_hardware INFO       Bad Pixels:       []
    2018-01-22 15:20:12,505 MainProcess wasatch.devices INFO     Connected to 0x24aa:0x1000
    2018-01-22 15:20:12,505 MainProcess wasatch.devices INFO     Connected to feature identification device
    2018-01-22 15:20:12,520 MainProcess wasatch.devices INFO     Serial:   WP-00154
    2018-01-22 15:20:12,520 MainProcess wasatch.devices INFO     Firmware: 10.0.0.6
    2018-01-22 15:20:12,520 MainProcess wasatch.devices INFO     Int Time: 100
    2018-01-22 15:20:12,520 MainProcess wasatch.devices INFO     FPGA:     026-007
    2018-01-22 15:20:12,520 MainProcess wasatch.devices INFO     Gain:     1.90234375
    2018-01-22 15:20:12,520 MainProcess wasatch.devices INFO     Model:    VIS
    2018-01-22 15:20:12,520 MainProcess __main__ INFO     connect: device connected
    2018-01-22 15:20:12,520 MainProcess __main__ INFO     Press Control-Break to interrupt...
    2018-01-22 15:20:12,630 MainProcess __main__ INFO     Reading:    1  Detector: 66.00 degC  Min:   909.00  Max: 31491.00  Avg:  2097.14
    2018-01-22 15:20:13,115 MainProcess __main__ INFO     Reading:    2  Detector: 66.00 degC  Min:   911.00  Max: 31394.00  Avg:  2097.02
    2018-01-22 15:20:13,630 MainProcess __main__ INFO     Reading:    3  Detector: 66.00 degC  Min:   916.00  Max: 31253.00  Avg:  2097.88
    2018-01-22 15:20:14,145 MainProcess __main__ INFO     Reading:    4  Detector: 66.00 degC  Min:   916.00  Max: 31432.00  Avg:  2098.49
    2018-01-22 15:20:14,661 MainProcess __main__ INFO     Reading:    5  Detector: 66.00 degC  Min:   913.00  Max: 31460.00  Avg:  2097.57
    ^C

    Z:\work\code\Wasatch.PY>head data.csv | cut -c1-50
    time,temp,399.24,399.68,400.11,400.55,400.98,401.4  # header row + wavelengths
    2018-01-22 15:20:12.630000,66.00,909.00,945.00,953
    2018-01-22 15:20:13.115000,66.00,918.00,920.00,932
    2018-01-22 15:20:13.630000,66.00,916.00,937.00,945
    2018-01-22 15:20:14.146000,66.00,916.00,924.00,956
    2018-01-22 15:20:14.661000,66.00,913.00,941.00,951

## Raspberry Pi

A standalone Raspberry Pi "Quick Start" tutorial is available here:

- [README RPI](README_RPI.md)

## Linux

**IMPORTANT:** For Linux, you must copy the file udev/10-wasatch.rules from the
Wasatch.PY distribution to /etc/udev/rules.d, then HUP udev or reboot.  This will
require root (sudo) privs, and is required to give userland applications access
to USB devices matching Wasatch VID/PID.

The following was tested under Ubuntu 16.04 LTS:

    $ uname -a
    Linux ubuntu 4.4.0-59-generic #80-Ubuntu SMP Fri Jan 6 17:47:47 UTC 2017 x86_64 x86_64 x86_64 GNU/Linux
    
    $ conda update -q conda
    $ ln -s environments/conda-linux.yml environment.yml
    $ conda env create -n wasatch3
	$ source activate wasatch3

    (wasatch) ubuntu [~/work/code/Wasatch.PY] parallels 07:17 PM $ python demo.py
    2018-01-22 19:17:12,041 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 1
    2018-01-22 19:17:12,052 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 0
    2018-01-22 19:17:12,061 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 2
    2018-01-22 19:17:12,071 MainProcess wasatch.fid_hardware INFO     reading EEPROM page 5
    2018-01-22 19:17:12,081 MainProcess wasatch.fid_hardware INFO     EEPROM settings:
    2018-01-22 19:17:12,081 MainProcess wasatch.fid_hardware INFO       Wavecal coeff0:   399.24130249
    2018-01-22 19:17:12,082 MainProcess wasatch.fid_hardware INFO       Wavecal coeff1:   0.43601000309
    2018-01-22 19:17:12,082 MainProcess wasatch.fid_hardware INFO       Wavecal coeff2:   -7.33139968361e-05
    2018-01-22 19:17:12,082 MainProcess wasatch.fid_hardware INFO       Wavecal coeff3:   2.80489995674e-08
    2018-01-22 19:17:12,082 MainProcess wasatch.fid_hardware INFO       Calibration date: 6/2
    2018-01-22 19:17:12,083 MainProcess wasatch.fid_hardware INFO       Calibrated by:    NH
    2018-01-22 19:17:12,083 MainProcess wasatch.fid_hardware INFO       Excitation (nm):  0
    2018-01-22 19:17:12,083 MainProcess wasatch.fid_hardware INFO       Slit size (um):   50
    2018-01-22 19:17:12,083 MainProcess wasatch.fid_hardware INFO       degCToDAC coeff0: 6.30511975963e-10
    2018-01-22 19:17:12,083 MainProcess wasatch.fid_hardware INFO       degCToDAC coeff1: 1.68748300666e-07
    2018-01-22 19:17:12,083 MainProcess wasatch.fid_hardware INFO       degCToDAC coeff2: 0.10000000149
    2018-01-22 19:17:12,083 MainProcess wasatch.fid_hardware INFO       adcToDegC coeff0: 66.0
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       adcToDegC coeff1: -0.00999999977648
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       adcToDegC coeff2: -9.99999974738e-05
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       Det temp min:     20
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       Det temp max:     10
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       TEC R298:         0
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       TEC beta:         0
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       Detector name:    S10141
    2018-01-22 19:17:12,084 MainProcess wasatch.fid_hardware INFO       Pixels:           1024
    2018-01-22 19:17:12,085 MainProcess wasatch.fid_hardware INFO       Pixel height:     1
    2018-01-22 19:17:12,085 MainProcess wasatch.fid_hardware INFO       Min integration:  1
    2018-01-22 19:17:12,085 MainProcess wasatch.fid_hardware INFO       Max integration:  60000
    2018-01-22 19:17:12,085 MainProcess wasatch.fid_hardware INFO       Bad Pixels:       []
    2018-01-22 19:17:12,085 MainProcess wasatch.devices INFO     Connected to 0x24aa:0x1000
    2018-01-22 19:17:12,085 MainProcess wasatch.devices INFO     Connected to feature identification device
    2018-01-22 19:17:12,112 MainProcess wasatch.devices INFO     Serial:   WP-00154
    2018-01-22 19:17:12,112 MainProcess wasatch.devices INFO     Firmware: 10.0.0.6
    2018-01-22 19:17:12,112 MainProcess wasatch.devices INFO     Int Time: 0
    2018-01-22 19:17:12,112 MainProcess wasatch.devices INFO     FPGA:     026-007
    2018-01-22 19:17:12,113 MainProcess wasatch.devices INFO     Gain:     1.90234375
    2018-01-22 19:17:12,113 MainProcess wasatch.devices INFO     Model:    VIS
    2018-01-22 19:17:12,113 MainProcess __main__ INFO     connect: device connected
    2018-01-22 19:17:12,113 MainProcess __main__ INFO     Press Control-Break to interrupt...
    2018-01-22 19:17:12,129 MainProcess __main__ INFO     Reading:    1  Detector: 66.00 degC  Min:   812.00  Max:  1333.00  Avg:   846.03
    2018-01-22 19:17:13,136 MainProcess __main__ INFO     Reading:    2  Detector: 66.00 degC  Min:   819.00  Max:  3068.00  Avg:   900.29
    2018-01-22 19:17:14,136 MainProcess __main__ INFO     Reading:    3  Detector: 66.00 degC  Min:   825.00  Max:  3043.00  Avg:   900.32
    2018-01-22 19:17:15,138 MainProcess __main__ INFO     Reading:    4  Detector: 66.00 degC  Min:   821.00  Max:  3119.00  Avg:   901.80
    2018-01-22 19:17:16,140 MainProcess __main__ INFO     Reading:    5  Detector: 66.00 degC  Min:   819.00  Max:   981.00  Avg:   844.84
    ^C

## MacOS 

The following was tested under MacOS 10.13.2 ("High Sierra"):

    $ conda update -q conda
    $ ln -s environments/conda-macos.yml environment.yml
	$ conda env create -n wasatch3
		Solving environment: done
		Preparing transaction: done
		Verifying transaction: done
		Executing transaction: done
		Collecting future==0.16.0 (from -r /Users/mzieg/work/code/Wasatch.PY/condaenv.LqSNvr.requirements.txt (line 1))
		Collecting pygtail==0.7.0 (from -r /Users/mzieg/work/code/Wasatch.PY/condaenv.LqSNvr.requirements.txt (line 2))
		Collecting pyusb==1.0.0 (from -r /Users/mzieg/work/code/Wasatch.PY/condaenv.LqSNvr.requirements.txt (line 3))
		Collecting requests==2.13.0 (from -r /Users/mzieg/work/code/Wasatch.PY/condaenv.LqSNvr.requirements.txt (line 4))
		  Using cached requests-2.13.0-py2.py3-none-any.whl
		Installing collected packages: future, pygtail, pyusb, requests
		Successfully installed future-0.16.0 pygtail-0.7.0 pyusb-1.0.0 requests-2.13.0
	$ source activate wasatch
	$ python demo.py --outfile spectra.csv
    2018-01-22 16:45:10,611 MainProcess __main__ INFO     connect: device connected
    2018-01-22 16:45:10,611 MainProcess __main__ INFO     Press Control-Break to interrupt...
    2018-01-22 16:45:10,628 MainProcess __main__ INFO     Reading:    1  Detector: 66.00 degC  Min:   833.00  Max:  3888.00  Avg:   940.95
    2018-01-22 16:45:11,631 MainProcess __main__ INFO     Reading:    2  Detector: 66.00 degC  Min:   829.00  Max:  3897.00  Avg:   940.13
    2018-01-22 16:45:12,635 MainProcess __main__ INFO     Reading:    3  Detector: 66.00 degC  Min:   829.00  Max:  3909.00  Avg:   941.80
    2018-01-22 16:45:13,637 MainProcess __main__ INFO     Reading:    4  Detector: 66.00 degC  Min:   829.00  Max:  3878.00  Avg:   940.21

# Known Issues

## Non-Blocking doesn't work on MacOS

MacOS doesn't allow usb.core.find() to be called from a forked background process.
This is the error message you get:

    "The process has forked and you cannot use this CoreFoundation functionality 
     safely. You MUST exec()."

It probably traces back to this:

https://discussions.apple.com/message/5829688#message5829688

Will investigate workarounds pending prioritization, but since the default
blocking mode works, this shouldn't be a major problem until we port ENLIGHTEN 
to MacOS.

## applog leaks on Linux

If you run demo.py with "--log-level DEBUG --delay-ms 0" for extended periods on
Linux, you may see the memory size creeping up.  This has been observed under 
Python 2.7 and 3.4 on Ubuntu 16, but not on Windows or MacOS.  Currently under 
investigation.

# Common Errors

## PyUSB usb.core error: No backend available (Windows)

I have seen this occur when the default device driver for 0x24aa:0x1000 had been
switched for FX2 firmware updates.  Solution was:

- Device Manager -> Universal Serial Bus controllers -> Wasatch Photonics device -> Update Driver -> Browse My Computer -> Let Me Pick -> Wasatch Photonics Spectrometer

Spectrometer should then appear in Device Manager under "libusb-win32 devices"

## LIBUSB error: No backend available (MacOS)

Using [Homebrew](https://brew.sh/), type:

    $ brew install libusb

# Backlog

- update .inf files to deprecate "Stroker"

# Version History

- 2019-09-16 1.0.39
    - added EEPROM.get_horizontal_roi()
- 2019-08-20 1.0.38
    - WasatchShell updates
    - added laser_power_require_modulation
- 2019-07-30 1.0.37
    - added laser\_power\_high\_resolution
    - stopped sending FX2 fake buffers on laser pulse width/period
- 2019-07-16 1.0.36
    - added allow\_nan to EEPROM.json()
- 2019-07-16 1.0.35
    - added --eod to WasatchShell 
- 2019-06-17 1.0.34
    - round negatives to zero when writing unsigned EEPROM fields
- 2019-06-05 1.0.33
    - made write\_eeprom 2nd-tier on ARM, legacy offset on FX2
- 2019-06-05 1.0.32
    - disable "fake buffer length from value" on ARM
- 2019-05-31 1.0.31
    - updated scripts/deploy
    - still working on reading.laser\_enabled
- 2019-05-31 1.0.30
    - moved write\_eeprom to 2nd-tier command
- 2019-05-29 1.0.29
    - enable Area Scan for IMX detectors
    - added EEPROM.product\_configuration
    - changed min/max\_integration\_time\_ms to 32-bit
- 2019-05-15 1.0.28
    - fixed DeviceFinderUSB bug in Linux
- 2019-05-13 1.0.27
    - fallback bus/addr implementation
- 2019-05-10 1.0.26
    - README-RPI.md
    - added conda-rpi.yml
    - cleanup Queue references in exception cases
    - added DeviceID.__repr__()
- 2019-04-30 1.0.25
    - made linearity\_coeffs, laser\_power\_coeffs and min/max\_laser\_power\_mW customer-editable
- 2019-04-29 1.0.24
    - support for area scan on FX2
- 2019-04-25 1.0.23
    - fixed utils.truthy() (Py3)
- 2019-04-24 1.0.22
    - added max\_tries and max\_integration\_time\_ms to balance\_acquisition
    - WasatchShell updates
    - logging fixes for ENLIGHTEN under Windows
- 2019-04-18 1.0.21
    - added get\_detector\_tec\_setpoint\_degC
    - added get\_detector\_tec\_setpoint\_raw
    - added get\_selected\_laser
- 2019-04-15 1.0.20
    - merging Pipes and Py3
- 2019-04-15 1.0.19
    - moved multiprocessing.Queue to .Pipe
- 2019-04-11 py3-1.1.0
    - initial Python 3 version (works on Linux)
- 2019-04-10 1.0.18
    - fixed for Windows (reverted multiprocessing.Manager to multiprocessing)
- 2019-04-10 1.0.17
    - memory profiling
    - removed Zynq delay
- 2019-04-05 1.0.16
    - made allow\_default\_gain\_reset default
- 2019-04-04 1.0.15
    - added swap\_alternating\_pixels
    - added allow\_default\_gain\_reset
- 2019-04-02 1.0.14
    - clear response queue when disabling free-running mode
    - Zynq fix
- 2019-04-01 1.0.13
    - Enable ENG-0034 Rev 4
- 2019-03-29 1.0.12
    - disable select\_laser if no laser present
    - kludge SiG-VIS to bare\_readings
- 2019-03-28 1.0.11
    - validate set\_laser\_enable with gettor
    - replace WasatchDevice internal multiprocessing.Queue with array
- 2019-03-26 1.0.10
    - added set\_selected\_laser to WasatchShell
    - add is\_zynq() with 250ms min USB interval
    - ignore NULLs/control chars in reading FPGA revision string
- 2019-03-22 1.0.9
    - added set\_selected\_laser
    - improved robustness when recovering from disabled triggering
- 2019-03-15 1.0.8
    - added bare\_readings so WasatchShell wouldn't double-sample photodiode
    - added immediate\_mode so WasatchShell could use change\_setting
    - fixed BalanceAcquisition to support non-free-running mode
    - moved auto-triggered laser disable to after laser temperature and photodiode readouts
- 2019-03-14 1.0.7
    - stubbed select\_laser
    - tweaked poison-pill logic
- 2019-02-16 1.0.6
    - added DeviceID
    - renamed DeviceListFID -> DeviceFinderUSB
    - removed bus\_order
- 2019-02-16 1.0.5
    - disabled EEPROM range-checks on integration time
- 2019-02-07 1.0.4
    - added default\_detector\_setpoint\_degC
    - tweaked auto-laser behavior
    - default to DEBUG logging until initialized
- 2019-02-04 1.0.3
    - fixed demo.py
    - renamed get\_interlock to get\_laser\_interlock
- 2019-01-21 1.0.2
    - improved hotplug support
- 2019-01-18 1.0.1
    - better support for hotplug / unplug events (poison pill updates)
    - added SpectrometerSettings.excitation()
- 2019-01-16 1.0.0
    - added UUID for tracking multiple spectrometers
    - deprecated StrokerProtocol devices
- 2018-01-04 0.9.18
    - updated EEPROM field definitions to latest draft of ENG-0034 Rev 4
    - added battery support
- 2018-11-28 0.9.17
    - fixed scan averaging in non-free-running mode
- 2018-11-27 0.9.16
    - bugfixes
- 2018-11-27 0.9.15
    - changed detector\_offset to SInt16
    - added SpectrometerState.free\_running\_mode, .acquisition\_laser\_trigger\_enable, .acquisition\_laser\_trigger\_delay\_ms
    - added "acquire" device command (letting ENLIGHTEN trigger individual acquisitions)
- 2018-10-03 0.9.14
    - fixed demo.py --outfile
- 2018-09-27 0.9.13
    - fixed demo.py integration time
- 2018-09-25 0.9.12
    - ARM triggering
- 2018-08-22 0.9.11
    - improved exception handling
- 2018-08-14 0.9.10
    - InGaAs offset/gain processing in software
- 2018-07-31 0.9.9
    - added dependency on pexpect for testing
- 2018-07-31 0.9.8
    - added utils.interpolate\_array
- 2018-07-13 0.9.7
    - converted WasatchShell into a wrapper over Wasatch.PY
    - added numerous getters
    - added BalanceAcquisition
- 2018-07-13 0.9.6
    - added Doxyfile
    - moved class/method docs to Doxygen format
- 2018-07-11 0.9.5
    - added comms\_init
- 2018-07-10 0.9.4
    - added StatusMessage
- 2018-07-05 0.9.3
    - added graph\_alternating\_pixels
- 2018-06-13 0.9.2
    - internally track FileSpectrometer integration time state
- 2018-06-12 0.9.1
    - fixed shell.py's "get\_config"
- 2018-06-12 0.9.0
    - taking spectra from IMX
- 2018-06-08 0.8.9
    - detector\_ccd/offset\_odd stubbed
    - fixed command de-dupping
- 2018-06-07 0.8.8
    - peak math
- 2018-06-06 0.8.7
    - added area\_under\_peak
- 2018-06-04 0.8.6
    - added CommandSettings.py
    - added wasatch.applog.MainLogger(enable\_stdout=True)
- 2018-06-04 0.8.5
    - added shell.py
- 2018-06-01 0.8.4
    - added Overrides
- 2018-05-31 0.8.3
    - FileSpectrometer mostly working
- 2018-05-29 0.8.2
    - initial version of FileSpectrometer
    - added JSON support
- 2018-05-17 0.8.1
    - ARM debugs
    - added set\_laser\_power\_mW
- 2018-05-15 0.8.0
    - EEPROM writing works
- 2018-05-14 0.7.4
    - fixed get\_ccd\_gain in StrokerProtocol devices
- 2018-05-09 0.7.3
    - raise exception on reading unexpected pixel count
- 2018-05-09 0.7.2
    - added support for 2048-pixel FID spectrometers
- 2018-05-08 0.7.1
    - updates for ENLIGHTEN 1.3.0
- 2018-04-30 0.7.0
    - added SpectrometerSettings
    - added SpectrometerState
    - added EEPROM
- 2018-04-21 0.6.10
    - Reading.session\_count
    - Reading.laser\_power
    - robustness
- 2018-04-20 0.6.9
    - additional lasersec
- 2018-04-19 0.6.8
    - fixed laser ramp rounding
- 2018-04-18 0.6.7
    - draft area scan implementation
- 2018-04-17 0.6.6
    - parameterized laser\_ramp\_increments
- 2018-04-16 0.6.5
    - updated laser power ramping
- 2018-04-13 0.6.4
    - initial laser power ramping
- 2018-04-12 0.6.3
    - added get\_secondary\_adc\_calibrated
    - reads linearity, ROI from EEPROM
- 2018-04-06 0.6.2
    - fixed secondary ADC endian order
- 2018-04-05 0.6.1
    - StrokerProtocolDevice fixes
    - FPGAOptions fixes to laser\_control and laser\_type
    - added enable\_secondary\_adc
    - added invert\_x\_axis
    - better FID USB logging
- 2018-03-22 0.6.0
    - starting multi-spectrometer support
    - tagging before attempting switch to MonoLibUsb
- 2018-03-06 0.5.6
    - added FPGAOptions
    - supported more EEPROM options
    - added fpga\_reset()
    - don't read laser temp unless has\_laser
- 2018-03-02 0.5.5
    - added "max\_usb\_interval\_ms"
    - de-dupe USB commands
- 2018-02-15 0.5.4
    - added "min\_usb\_interval\_ms"
- 2018-02-14 0.5.3
    - added set\_ccd\_offset()
- 2018-01-26 0.5.2
    - added set\_ccd\_trigger() 
- 2018-01-24 0.5.1
    - added get/set\_laser\_temperature\_setpoint\_raw() 
- 2018-01-22 0.5.0
    - initial customer release
    - analyzed non-blocking issue on MacOS
    - default TEC to min 
- 2018-01-22 0.2.2 
    - tested and documented for Linux
- 2018-01-22 0.2.1 
    - tested and documented for MacOS
- 2018-01-22 0.2.0 
    - added demo.py, Windows run instructions
- 2018-01-08 0.1.2 
    - swapped LSB/MSB on high-gain mode
- 2018-01-05 0.1.1 
    - fixed laser\_enable
    - updated NIR high-gain mode
- 2018-01-05 0.1.0 
    - initial import from ENLIGHTEN
