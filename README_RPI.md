# RaspberryPi Instructions

Following is a quick-start guide for configuring Wasatch.PY on a brand-new or freshly imaged Raspberry Pi.

![RPI board](https://www.raspberrypi.org/app/uploads/2017/05/Raspberry-Pi-3-Ports-1-1833x1080.jpg)

# Dependencies

If desired, flash a fresh MicroSD card with a clean copy of Raspbian OS from:

- https://www.raspberrypi.org/downloads/raspbian/

Test log:

- This process was tested with 2019-09-26-raspbian-buster-full.zip
- This process was re-tested on 2020-08-04 using Raspberry Pi OS Imager 1.4 for MacOS
    - PRETTY\_NAME = Raspbian GNU/Linux 10 "buster" 
    - uname -a = Linux raspberrypi 5.4.51-v7l+ #1327 SMP Th Jul 23 11:04:39 BST 2020 armv7l GNU/Linux

## Confirm RPi boots up with default Python version

    $ python3 --version
    Python 3.7.3

## Confirm RPi can see Wasatch Photonics spectrometer on USB bus

    $ sudo lsusb
    Bus 001 Device 005: ID 413c:301a Dell Computer Corp. 
    Bus 001 Device 004: ID 413c:2113 Dell Computer Corp. 
    Bus 001 Device 006: ID 24aa:1000  
    Bus 001 Device 003: ID 0424:ec00 Standard Microsystems Corp. SMSC9512/9514 Fast Ethernet Adapter
    Bus 001 Device 002: ID 0424:9514 Standard Microsystems Corp. SMC9514 Hub
    Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub

(The third device is a WP-785...all WP USB devices use the VID 0x24aa.)

# Install Wasatch.PY

## Use Git to checkout the latest version of Wasatch.PY

    $ mkdir -p work/code
    $ cd work/code
    $ git clone https://github.com/WasatchPhotonics/Wasatch.PY.git

## Install Wasatch udev rules

    $ cd ~/work/code/Wasatch.PY
    $ sudo cp udev/10-wasatch.rules /etc/udev/rules.d
    $ sudo shutdown -r now

# Install Python dependencies

You can do this using either Anaconda3 or pip3.  I normally use Anaconda
on Windows, MacOS and Ubuntu, and it does work for Wasatch.PY on Raspbian.

However, a full ENLIGHTEN development environment on Raspbian 
apparently can't be done from Anaconda at writing (needs some apt-get
packages for PySide2), so you might just use pip3 for ARM at this time.

## Pip3 Process

    $ pip3 install numpy py six psutil future pygtail pyusb requests pexpect

## Miniconda3 Process

Follow the instructions posted here:

- https://gist.github.com/simoncos/a7ce35babeaf73f512be24135c0fbafb

Change the installation directory to /home/pi/miniconda3 when prompted:

    $ wget http://repo.continuum.io/miniconda/Miniconda3-latest-Linux-armv7l.sh
    $ sudo /bin/bash Miniconda3-latest-Linux-armv7l.sh

    Miniconda3 will now be installed into this location: /root/miniconda3
      - Press ENTER to confirm the location
      - Press CTRL-C to abort the installation
      - Or specify a different location below
    [/root/miniconda3] >>> /home/pi/miniconda3

Post-Install Miniconda3 cleanup:

    $ echo 'export PATH=/home/pi/miniconda3/bin:$PATH' >> ~/.bashrc
    $ sudo chown -R pi.pi /home/pi/miniconda3

Add Conda "channels" providing pre-built Raspberry Pi binaries of popular Python packages:

    $ conda update --all
    $ conda config --add channels raspberrypi
    $ conda config --add channels rpi

Select Raspberry Pi Conda environment:

    $ cd ~/work/code/Wasatch.PY
    $ cp environments/conda-rpi.yml environment.yml

Create Conda environment:

    $ cd ~/work/code/Wasatch.PY
    $ conda env create -n wasatch3
    #
    # To activate this environment, use:
    # $ source activate wasatch3
    #
    # To deactivate this environment, use:
    # $ source deactivate

    $ cd ~/work/code/Wasatch.PY
    $ source activate wasatch3

    $ python --version
    Python 3.6.6

# Test Demo.py

Run demo.py with "--help" for command-line options.  Note that you need to run "source activate wasatch3"
*each time you reboot* (or open a new Terminal window, depending on how your shell is configured), in
order to tell Linux and Miniconda "which version" of Python, and which set of package dependencies,
you want to use.


    $ python3 -u demo.py
    2019-05-06 14:25:32,498 MainProcess root WARNING  Top level log configuration (1 handlers)
    2019-05-06 14:25:32,799 MainProcess __main__ INFO     Wasatch.PY 1.0.25 Demo
    2019-05-06 14:25:32,804 MainProcess wasatch.FeatureIdentificationDevice ERROR    unable to control TEC: EEPROM reports no cooling
    Reading:    1  Detector:  0.00 degC  Min:   755.00  Max:  1067.00  Avg:   785.72  Memory:    22913024
    Reading:    2  Detector:  0.00 degC  Min:   757.00  Max:  1042.00  Avg:   785.75  Memory:    22913024
    Reading:    3  Detector:  0.00 degC  Min:   757.00  Max:  1048.00  Avg:   785.67  Memory:    22913024
    Reading:    4  Detector:  0.00 degC  Min:   755.00  Max:  1063.00  Avg:   785.64  Memory:    22913024
    Reading:    5  Detector:  0.00 degC  Min:   755.00  Max:  1050.00  Avg:   785.78  Memory:    22913024
    Reading:    6  Detector:  0.00 degC  Min:   755.00  Max:  1027.00  Avg:   785.51  Memory:    22913024
    Reading:    7  Detector:  0.00 degC  Min:   755.00  Max:  1057.00  Avg:   785.64  Memory:    22913024
    Interrupted by Ctrl-C...shutting down  

# Test WasatchShell

See [WasatchShell README](WasatchShell/README.md) for documentation, or type "help" at the "wp>" prompt.

    $ cd ~/work/code/Wasatch.PY
    $ export PYTHONPATH=$PWD

    $ cd WasatchShell
    WasatchShell $ python3 -u wasatch-shell.py
    --------------------------------------------------------------------------------
    wasatch-shell version 2.2.1 invoked (Wasatch.PY 1.0.25)
    wp> open
    1
    wp> set_laser_enable on
    1
    wp> set_integration_time_ms 100
    1
    wp> get_spectrum_pretty
    |       *                                                                         
    |       *                                                                         
    |       *                                                                         
    |       *                                                                         
    |       *                                                                         
    |       *                                                                         
    |       *                                                                         
    |       *                                                                         
    |       *                                                                         
    |       *                *                                                        
    |       *                *                                                        
    |       *                *                                                        
    |       *                *                                                        
    |       *                *                                                        
    |       *                *                                          *             
    |       *                *                                          *             
    |       *                **                                         *             
    |  *    *                **    *                                    *             
    |  *    *                **    *                                    *             
    | **    *               ***    *                                    **            
    | **   **        *      ***    *                                    **            
    | **** ***      **      ***    *     *  *                           **     **     
    | *******************************   ***************   *             ** ** ***     
    | ********************************************************************************
    +---------------------------------------------------------------------------------
      Min:   886.00  Max:  3679.00  Mean:  1032.14  (range 799.39, 930.76nm)
    wp> set_laser_enable off
    1
    wp> quit

![RPI Logo](https://www.raspberrypi.org/app/uploads/2018/03/RPi-Logo-Reg-SCREEN-199x250.png)
