# Overview

Extracting the device communication layer from ENLIGHTEN to make it more readily
accessible to customer applications.

# Running

## Microsoft Windows 

The following session was run on Windows 10 using the Cmd shell of [Git for Windows](https://git-scm.com/)
and [Miniconda](https://conda.io/miniconda.html) (Python 2.7):

	C:\Users\mzieg>set PATH=%HOME%\miniconda2;%HOME%\miniconda2\scripts;%PATH%

	C:\Users\mzieg>cd work\code\Wasatch.PY

	C:\Users\mzieg\work\code\Wasatch.PY>conda update conda

	C:\Users\mzieg\work\code\Wasatch.PY>conda env create -f win10environment.yml
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

	C:\Users\mzieg\work\code\Wasatch.PY>activate conda_wasatch

    C:\Users\mzieg\work\code\Wasatch.PY>python demo.py --help
    usage: demo.py [-h] [-b] [-l LOG_LEVEL] [-o BUS_ORDER]
                   [-i INTEGRATION_TIME_MS] [-s SCANS_TO_AVERAGE]
                   [-w BOXCAR_HALF_WIDTH] [-d DELAY_MS] [-f OUTFILE]

    Simple demo to acquire spectra from command-line interface

    optional arguments:
      -h, --help            show this help message and exit
      -b, --blocking        blocking USB interface
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

# Backlog

- [x] update ENLIGHTEN build to use this module
- [x] add small (probably cmd-line) Python demo to this package
- [x] provide build and test instructions for Windows
- [ ] provide build and test instructions for Linux
- [ ] provide build and test instructions for MacOS
- [ ] add independent API documentation
- [ ] add API to obtain Wasatch.PY version (independent of ENLIGHTEN version)

# Version History

2018-01-22 0.2.0 - added demo.py, Windows run instructions
2018-01-08 0.1.2 - swapped LSB/MSB on high-gain mode
2018-01-05 0.1.1 - fixed laser\_enable
                 - updated NIR high-gain mode (untested)
2018-01-05 0.1.0 - initial import from ENLIGHTEN
