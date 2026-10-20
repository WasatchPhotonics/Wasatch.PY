![Sample Console Views](https://github.com/WasatchPhotonics/Wasatch.PY/raw/master/screenshots/multiplatform.png)

# Overview

Wasatch.PY is a Python application-level driver for Wasatch Photonics 
spectrometers. It has been tested on Windows, MacOS and Linux platforms 
(including Raspberry Pi Zero), and is directly used by Wasatch's own 
[ENLIGHTEN&trade;](https://wasatchphotonics.com/product-category/software/)
spectroscopy desktop GUI.

If you'd like simpler, smaller examples of how to perform individual operations
against Wasatch spectrometers from Python, please also see our 
[Python Example Scripts](https://github.com/WasatchPhotonics/Python-USB-WP-Raman-Examples)
repository.

## History 

This project started as a successor to the earlier 
[WasatchUSB](https://github.com/WasatchPhotonics/WasatchUSB).  The main 
difference is that while WasatchUSB was a one-time copy-pasta of legacy ENLIGHTEN 
classes, Wasatch.PY is an actively maintained and supported HAL (hardware 
abstraction layer) dependency of the current 
[ENLIGHTEN](https://wasatchphotonics.com/product-category/software/) GUI. As a 
result, when we add or update ENLIGHTEN features, Wasatch.PY is typically updated
by necessity, making the same foundational interface available to all our users.  

This shared library exemplifies two of our core values: transparency and 
[dogfooding](https://en.wikipedia.org/wiki/Eating_your_own_dog_food).

Finally, the project name reflects the fact that this is specifically a Python 
binding and implementation; for other USB-capable interfaces, see 
[Wasatch.NET](https://github.com/WasatchPhotonics/Wasatch.NET),
[Wasatch.VCPP](https://github.com/WasatchPhotonics/Wasatch.VCPP)
and other [software drivers](https://wasatchphotonics.com/software-support/software-drivers/).

# API

A significant portion of Wasatch.PY's command interface is exposed through 
key-value settings, originally provided to simplify multi-process applications 
and summarized in docs/README_SETTINGS.md.

# PyPi

Looking for Wasatch.PY from `pip` ([PyPi](https://pypi.org/))?  Look no further! The
latest released version of Wasatch.PY can be loaded into your environment with a simple
pip command:

    $ pip install wasatch

Package info:

- https://pypi.org/project/wasatch/

For more information about PyPi package management, see 
[README_PYPI](https://github.com/WasatchPhotonics/Wasatch.PY/blob/master/docs/README_PYPI.md).

# Dependencies

    $ pip install .

# Running the Demo

Following are the general usage instructions for the included command-line demo
scripts.  After find specific Anaconda setup instructions for Windows, Linux, MacOS 
and other tested platforms.

    $ python -m venv venv
    $ . venv/*/activate
    $ pip install .
    $ python -u scripts/demo.py --help

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

## Raspberry Pi

A standalone Raspberry Pi "Quick Start" tutorial is available here:

- [README RPI](https://github.com/WasatchPhotonics/Wasatch.PY/blob/master/docs/README_RPI.md)

## Linux

**IMPORTANT:** For Linux, you must copy the file udev/10-wasatch.rules from the
Wasatch.PY distribution to /etc/udev/rules.d, then HUP udev or reboot.  This will
require root (sudo) privs, and is required to give userland applications access
to USB devices matching Wasatch VID/PID.

# Common Errors

## LIBUSB error: No backend available (MacOS)

Using [Homebrew](https://brew.sh/):

    $ brew install libusb

# Version History

See [Changelog](CHANGELOG.md)
