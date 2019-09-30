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

# Notes on Multiprocess Logging

A standalone example of the multiprocess logging mechanism is extracted here for clarity:

- https://github.com/WasatchPhotonics/ApplogDemo

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

See [Changelog](README_CHANGELOG.md)

