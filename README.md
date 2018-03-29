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

Until we draft proper API documentation, these are the standard settings which 
can be passed to device.change\_setting().  Booleans should be passed with an
argument of 1 (enable) or 0 (disable).

- "laser\_enable" (bool)
- "integration" (int (milliseconds))
- "detector\_tec\_setpoint\_degC" (int)
- "detector\_tec\_enable" (bool)
- "degC\_to\_dac\_coeffs" (string of 3 space-delimited floats, e.g. "1.1 2.2 3.3")
- "laser\_power\_perc" (int, 0 to 100 inclusive)
- "laser\_temperature\_setpoint\_raw" (int, 0 to 127 inclusive)
- "ccd\_gain" (float)
- "high\_gain\_mode\_enable" (bool)
- "ccd\_trigger" (bool)
- "scans\_to\_average" (int, 0 or 1 to disable)
- "bad\_pixel\_mode" (wasatch.common.bad\_pixel\_mode\_none or \_average))
- "log\_level" (int, see [Python logging levels](https://docs.python.org/2/library/logging.html#levels))

Note that there are many more functions available in fid\_hardware.py and 
sp\_hardware.py (via devices.hardware) but these are not yet fully documented.

# Dependencies

Wasatch.PY uses the Python 2.7 build of [Miniconda](https://conda.io/miniconda.html)
for dependencies and package management.

# Running the Demo

Following are the general usage instructions for the included command-line demo
scripts.  After find specific Anaconda setup instructions for Windows, MacOS and 
other tested platforms.

	usage: demo.py [-h] [-l LOG_LEVEL] [-o BUS_ORDER] [-i INTEGRATION_TIME_MS]
				   [-s SCANS_TO_AVERAGE] [-w BOXCAR_HALF_WIDTH] [-d DELAY_MS]
				   [-f OUTFILE] [-m MAX] [-b]

	Simple demo to acquire spectra from command-line interface

	optional arguments:
	  -h, --help            show this help message and exit
	  -l LOG_LEVEL, --log-level LOG_LEVEL
							logging level [DEBUG,INFO,WARNING,ERROR,CRITICAL]
	  -o BUS_ORDER, --bus-order BUS_ORDER
							usb device ordinal to connect
	  -i INTEGRATION_TIME_MS, --integration-time-ms INTEGRATION_TIME_MS
							integration time (ms, default 10)
	  -s SCANS_TO_AVERAGE, --scans-to-average SCANS_TO_AVERAGE
							scans to average (default 1)
	  -w BOXCAR_HALF_WIDTH, --boxcar-half-width BOXCAR_HALF_WIDTH
							boxcar half-width (default 0)
	  -d DELAY_MS, --delay-ms DELAY_MS
							delay between integrations (ms, default 1000)
	  -f OUTFILE, --outfile OUTFILE
							output filename (e.g. path/to/spectra.csv)
	  -m MAX, --max MAX     max spectra to acquire (default 0, unlimited)
	  -b, --non-blocking    non-blocking USB interface

## Microsoft Windows 

The following session was run on Windows 10 using the Cmd shell of [Git for Windows](https://git-scm.com/)
and [Miniconda](https://conda.io/miniconda.html) (Python 2.7):

	C:\Users\mzieg>set PATH=%HOME%\miniconda2;%HOME%\miniconda2\scripts;%PATH%
	C:\Users\mzieg>cd work\code\Wasatch.PY
	C:\Users\mzieg\work\code\Wasatch.PY>conda update conda
	C:\Users\mzieg\work\code\Wasatch.PY>cp environments\conda-win10.yml environment.yml
	C:\Users\mzieg\work\code\Wasatch.PY>conda env create -n wasatch
        Solving environment: done
        Preparing transaction: done
        Verifying transaction: done
        Executing transaction: done
        Requirement already satisfied: future==0.16.0 in c:\users\mzieg\miniconda2\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 1))
        Collecting pefile==2016.3.28 (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 2))
        Requirement already satisfied: pygtail==0.7.0 in c:\users\mzieg\miniconda2\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 3))
        Requirement already satisfied: pyside==1.2.4 in c:\users\mzieg\miniconda2\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 4))
        Requirement already satisfied: pytest-capturelog==0.7 in c:\users\mzieg\miniconda2\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 5))
        Requirement already satisfied: pytest-qt==2.1.0 in c:\users\mzieg\miniconda2\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 6))
        Requirement already satisfied: pyusb==1.0.0 in c:\users\mzieg\miniconda2\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 7))
        Requirement already satisfied: requests==2.13.0 in c:\users\mzieg\miniconda2\envs\conda_enlighten\lib\site-packages (from -r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 8))
        Requirement already satisfied: py>=1.1.1 in c:\users\mzieg\miniconda2\envs\conda_wasatch\lib\site-packages (from pytest-capturelog==0.7->-r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 5))
        Requirement already satisfied: pytest>=2.7.0 in c:\users\mzieg\miniconda2\envs\conda_wasatch\lib\site-packages (from pytest-qt==2.1.0->-r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 6))
        Requirement already satisfied: colorama in c:\users\mzieg\miniconda2\envs\conda_wasatch\lib\site-packages (from pytest>=2.7.0->pytest-qt==2.1.0->-r C:\Users\mzieg\work\code\Wasatch.PY\condaenv.xq_jth.requirements.txt (line 6))
        Installing collected packages: pefile
          Found existing installation: pefile 2017.11.5
            Uninstalling pefile-2017.11.5:
              Successfully uninstalled pefile-2017.11.5
        Successfully installed pefile-2016.3.28

	C:\Users\mzieg\work\code\Wasatch.PY>activate wasatch

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
    $ conda env create -n wasatch
	$ source activate wasatch

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
	$ conda env create -n wasatch
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

# Common Errors

## LIBUSB error: No backend available (MacOS)

Using [Homebrew](https://brew.sh/), type:

    $ brew install libusb

# Backlog

- [ ] provide simplified blocking API
- [ ] provide API documentation
- [ ] provide queriable non-blocking interface?

# Version History

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
