#!/usr/bin/env python -u
################################################################################
#                               wasatch-shell.py                               #
################################################################################
#                                                                              #
#  DESCRIPTION:  A simple interactive shell allowing the caller to control     #
#                a spectrometer via a blocking ASCII request-response          #
#                pattern from 'expect' or similar.                             #
#                                                                              #
#  EXAMPLE:      $ ./wasatch-shell.py [--logfile path]                         #
#                  open                                                        #
#                  setinttime                                                  #
#                  100                                                         #
#                  startacquisition                                            #
#                  getspectrum                                                 #
#                  close                                                       #
#                                                                              #
################################################################################

import usb.core
import argparse
import logging
import sys
import os

from EEPROM import EEPROM

# constants
SCRIPT_VERSION = "1.0.6"
HOST_TO_DEVICE = 0x40
DEVICE_TO_HOST = 0xC0
BUFFER_SIZE    = 8
ZZ             = [0] * BUFFER_SIZE
TIMEOUT_MS     = 1000
PIXEL_COUNT    = 1024

################################################################################
# Utility Functions
################################################################################

def printHelp():
    print """
    Version: %s
    The following commands are supported:

        OPEN, CLOSE, SETINTTIME, GETSPECTRUM, STARTACQUISITION,
        GETDATA, GETTEMP, SETLSI, GETCONFIG, SETLSE, SETTECE,
        GETTEMPSET, SETTEMPSET, GET_INTEGRATION_TIME, GET_CCD_GAIN,
        GET_LASER_RAMPING_MODE, GET_HORIZ_BINNING, SELECT_LASER,
        CUSTOMSET, CUSTOMGET, CUSTOMGET12, CUSTOMGET3, 
        CONNECTION_CHECK, SCRIPT_VERSION, GET_LASER_TEMP, 
        GET_PHOTODIODE, GET_PHOTODIODE_MW, SET_LSI_MW, 
        SET_TEMP_SETPOINT_DEGC, GET_TEMP_DEGC, GET_CONFIG_JSON,
        HAS_PHOTODIODE_CALIBRATION, AUTO_BALANCE

    The following getters are also available:""" % SCRIPT_VERSION
    print sorted(getters.keys())

def Get_Value(Command, ByteCount, wValue=0, wIndex=0, raw=False):
    RetVal = 0
    if Command == 0:
        return 0
    RetArray = dev.ctrl_transfer(DEVICE_TO_HOST, Command, wValue, wIndex, ByteCount, TIMEOUT_MS)
    if raw:
        return RetArray
    for i in range (0, ByteCount):
        RetVal = RetVal * 256 + RetArray[ByteCount - i - 1]
    return RetVal

def Get_Value_12bit(Command):
    RetVal = 0
    if Command == 0:
        return 0
    RetArray = dev.ctrl_transfer(DEVICE_TO_HOST, Command, 0, 0, 2, TIMEOUT_MS)
    RetVal = RetArray[0] * 256 + RetArray[1];
    return RetVal

def Test_Set(SetCommand, GetCommand, SetValue, RetLen):
    SetValueHigh = (SetValue >> 16) & 0xffff
    SetValueLow  =  SetValue        & 0xffff
    
    Ret = dev.ctrl_transfer(HOST_TO_DEVICE, SetCommand, SetValueLow, int(SetValueHigh), ZZ, TIMEOUT_MS)
    if BUFFER_SIZE != Ret:
        logging.debug('Set {0:x}    Fail'.format(SetCommand))
        return False
    else:
        RetValue = Get_Value(GetCommand, RetLen)
        if RetValue is not None and SetValue == RetValue:
            return True
        else:
            logging.debug('Get {0:x} Failure. Txd:0x{1:x} Rxd:0x{2:x}'.format(GetCommand, SetValue, RetValue))
            return False

################################################################################
# Spectrometer Features
################################################################################

def data_poll():
    while Get_Value(0xd4, 4) == 0:
        pass

def setIntTime(time):
    global integration_time_ms
    print(Test_Set(0xb2, 0xbf, time, 6))      # Set integration time
    integration_time_ms = time
    logging.debug("integration_time_ms -> %d", time)

def getSpectrum(quiet=True):
    startAcquisition()
    return getData(quiet=quiet)

def startAcquisition():
    Test_Set(0xad, 0, 0, 0)      # Set acquisition

def getData(quiet=True):
    data_poll()
    Data = dev.read(0x82, PIXEL_COUNT * 2)

    spectrum = []
    for j in range (0, int((PIXEL_COUNT * 2)/32), 1):
        for i in range (0, 31, 2):
            spectrum.append(Data[j*32+i+1]*256+Data[j*32+i])
    logging.debug("returned %d pixels", len(spectrum))

    if quiet:
        return spectrum
    else:
        for pixel in spectrum:
            print(pixel)
        logging.debug("getSpectrum: max = %d", get_max(spectrum))

def Open_Spectrometers():
    logging.debug("in open spectrometers")
    dev = usb.core.find(idVendor=0x24aa, idProduct=0x1000)
    logging.debug("opened spectrometer")
    print(dev.bNumConfigurations)

    return dev

def getTemp():
    return Get_Value_12bit(0xd7)

def getTempDegC():
    raw = getTemp()
    coeffs = eeprom.adc_to_degC_coeffs
    degC = coeffs[0] \
         + coeffs[1] * raw \
         + coeffs[2] * raw * raw
    return degC
    
def setLSI(period, width):
    if width > period:
        logging.error("setLSI: width %d exceeded period %d", width, period)
        print False
        return

    if(Test_Set(0xc7, 0xcb, period, 5)):        # SET_MOD_PERIOD
        logging.debug("laser_pulse_period -> %d", period)
        print(Test_Set(0xdb, 0xdc, width, 5))   # SET_LASER_MOD_PULSE_WIDTH
        logging.debug("laser_pulse_width -> %d", width)
    else:
        print(False)

def setLaserPowerMW(requestedMW):
    global laser_power_mW
    coeffs = eeprom.laser_power_coeffs
    mW = min(eeprom.max_laser_power_mW, max(eeprom.min_laser_power_mW, requestedMW))

    # note: the laser_power_coeffs convert mW to percent, not tenth_percent
    perc = coeffs[0] \
         + coeffs[1] * mW \
         + coeffs[2] * mW * mW \
         + coeffs[3] * mW * mW * mW

    MAX_TENTHS = 1000
    tenth_percent = int(max(0, min(MAX_TENTHS, round(perc * 10))))
    setLSI(MAX_TENTHS, tenth_percent)

    laser_power_mW = mW
    logging.debug("laser_power_mW -> %d", mW)

def getConfig(index):
    logging.debug("getting config with index " + str(index))
    buf = dev.ctrl_transfer(DEVICE_TO_HOST, 0xff, 1, index, 64, TIMEOUT_MS)
    logging.debug("config: " + str(buf))
    for b in buf:
        #logging.debug("b: " + str(b))
        print(str(b))

def setLightSourceEnable(enable):
    if enable:
        if Test_Set(0xbd, 0xe3, 1, 1):          # SET_LASER_MOD_ENABLED
            enabled = Test_Set(0xbe, 0xe2, 1, 1)   # SET_LASER_ENABLED
        else:
            enabled = False
    else:
        enabled = Test_Set(0xbe, 0xe2, 0, 1)
    logging.debug("laser_enabled -> %s", enabled)
    return enabled

def setTECEnable(enable):
    if(enable):
        print(Test_Set(0xd6, 0xda, 1, 1))
    else:
        print(Test_Set(0xd6, 0xda, 0, 1))

def getTempSetPoint():
    RetArray = dev.ctrl_transfer(DEVICE_TO_HOST, 0xd9, 0,0, 2, TIMEOUT_MS)
    RetVal = RetArray[0] + RetArray[1] * 256;
    print(RetVal)

def setTempSetPoint(val):
    return Test_Set(0xd8, 0xd9, val, 2)

def setTempSetPointDegC(requestedDegC):
    degC = max(eeprom.min_temp_degC, min(eeprom.max_temp_degC, requestedDegC))
    coeffs = eeprom.degC_to_dac_coeffs
    raw = coeffs[0] \
        + coeffs[1] * degC \
        + coeffs[2] * degC * degC
    raw = int(max(0, min(0xfff, raw)))
    return setTempSetPoint(raw)

def getIntegrationTime():
    RetArray = dev.ctrl_transfer(DEVICE_TO_HOST, 0xbf, 0,0, 6, TIMEOUT_MS)
    RetVal = RetArray[0] + RetArray[1] * 256 + RetArray[2] * 65536;
    print(RetVal)

def getCCDGain():
    RetArray = dev.ctrl_transfer(DEVICE_TO_HOST, 0xc5, 0,0, 2, TIMEOUT_MS)
    RetVal = RetArray[1] + RetArray[0] / 256.0;
    print(RetVal)

def getLaserTemp():
    Test_Set(0xed, 0xee, 0, 1)  # select the primary ADC
    Get_Value(0xd5, 2)          # throwaway read
    return Get_Value(0xd5, 2)    # stable read

def getPhotodiode():
    Test_Set(0xed, 0xee, 1, 1)  # select the secondary ADC
    Get_Value(0xd5, 2)          # throwaway read
    return Get_Value(0xd5, 2)   # stable read

def getPhotodiodeMW():
    raw = getPhotodiode()
    coeffs = eeprom.linearity_coeffs
    mW = coeffs[0] \
       + coeffs[1] * raw \
       + coeffs[2] * raw * raw \
       + coeffs[3] * raw * raw * raw
    return mW

def hasPhotodiodeCalibration():
    coeffs = eeprom.linearity_coeffs
    for i in range(4):
        if coeffs[i] != 0.0 and coeffs[i] != -1:
            return 1
    return 0

def initializeGetters():
    getters = {}
    getters["GETTECENABLE"]                             = (0xda, 1)
    getters["GET_ACTUAL_FRAMES"]                        = (0xe4, 2)
    getters["GET_ACTUAL_INTEGRATION_TIME"]              = (0xdf, 6)
    getters["GET_CCD_OFFSET"]                           = (0xc4, 2)
    getters["GET_CCD_SENSING_THRESHOLD"]                = (0xd1, 2)
    getters["GET_CCD_THRESHOLD_SENSING_MODE"]           = (0xcf, 1)
    getters["GET_CCD_TRIGGER_SOURCE"]                   = (0xd3, 1)
    getters["GET_CODE_REVISION"]                        = (0xc0, 4)
    getters["GET_EXTERNAL_TRIGGER_OUTPUT"]              = (0xe1, 1)
    getters["GET_FPGA_REV"]                             = (0xb4, 7)
    getters["GET_INTERLOCK"]                            = (0xef, 1)
    getters["GET_LASER"]                                = (0xe2, 1)
    getters["GET_LASER_MOD"]                            = (0xe3, 1)
    getters["GET_LASER_MOD_PULSE_WIDTH"]                = (0xdc, 5)
    getters["GET_LASER_TEMP_SETPOINT"]                  = (0xe8, 6)
    getters["GET_LINK_LASER_MOD_TO_INTEGRATION_TIME"]   = (0xde, 1)
    getters["GET_MOD_DURATION"]                         = (0xc3, 5)
    getters["GET_MOD_PERIOD"]                           = (0xcb, 5)
    getters["GET_MOD_PULSE_DELAY"]                      = (0xc4, 2)
    getters["GET_SELECTED_LASER"]                       = (0xee, 1)
    getters["VR_GET_CONTINUOUS_CCD"]                    = (0xcc, 1)
    getters["VR_GET_NUM_FRAMES"]                        = (0xcd, 1)
    getters["GET_LINE_LENGTH"]                          = (0xff, 2, 0x03)
    getters["OPT_ACT_INT_TIME"]                         = (0xff, 1, 0x0b)
    getters["OPT_AREA_SCAN"]                            = (0xff, 1, 0x0a)
    getters["OPT_CF_SELECT"]                            = (0xff, 1, 0x07)
    getters["OPT_DATA_HDR_TAB"]                         = (0xff, 1, 0x06)
    getters["OPT_HORIZONTAL_BINNING"]                   = (0xff, 1, 0x0c)
    getters["OPT_INT_TIME_RES"]                         = (0xff, 1, 0x05)
    getters["OPT_LASER"]                                = (0xff, 1, 0x08)
    getters["OPT_LASER_CONTROL"]                        = (0xff, 1, 0x09)
    getters["READ_COMPILATION_OPTIONS"]                 = (0xff, 2, 0x04)
    return getters

def Load_EEPROM():
    global eeprom
    eeprom = EEPROM()
    buffers = []
    for page in range(6):
        buffers.append(Get_Value(0xff, 64, 0x01, wIndex=page, raw=True))
    eeprom.parse(buffers)
    eeprom.dump()

################################################################################
#                                                                              #
#                             Balance Acquisitions                             #
#                                                                              #
################################################################################

def autoBalance(mode, wavenumber, intensity, threshold):
    global balance_mode, balance_wavenumber, balance_intensity, balance_threshold

    balance_wavenumber = wavenumber
    balance_intensity = max(0, min(65000, intensity))
    balance_threshold = max(0, min(15000, threshold))

    mode = mode.upper().strip()
    if mode not in ['INTEGRATION', 'LASER', 'LASER_THEN_INTEGRATION']:
        msg = "Error: unsupported balance mode [%s]" % mode
        logging.error(msg)
        print msg
        return

    balance_mode = mode

    if mode == "INTEGRATION":
        autoBalanceIntegration()
    if mode == "LASER":
        autoBalanceLaser()
    if mode == "LASER_THEN_INTEGRATION":
        autoBalanceLaser()
        autoBalanceIntegration()

def autoBalanceIntegration():
    overshoot_count = 0
    while True:
        spectrum = getSpectrum()
        peak = get_max(spectrum)
        delta = abs(peak - balance_intensity)

        # exit case
        if delta <= balance_threshold:
            print "Ok integration_time_ms %d, laser_power_mW %d" % (integration_time_ms, laser_power_mW)
            return

        logging.debug("BalanceAcquisition: peak %d, integration_time_ms %d, laser_power_mW %d" % (peak, integration_time_ms, laser_power_mW))

        # adjust
        if peak > balance_intensity:
            new_integ = int(integration_time_ms / 2)
            overshoot_count += 1
        else:
            new_integ = int(1.0 * integration_time_ms * balance_intensity / peak)

        # round
        new_integ = max(10, min(5000, new_integ))

        if overshoot_count > 5:
            logging.error("BalanceAcquisition: too many overshoots")
            print "ERROR: failed to balance acquisition"
            return

        logging.debug("BalanceAcquisition: new_integ = %d", new_integ)
        setIntTime(new_integ)

# trying not to add Numpy, etc dependencies
def get_max(spectrum):
    if spectrum is None:
        return 0
    peak = spectrum[0]
    for pixel in spectrum:
        if pixel > peak:
            peak = pixel
    return peak

################################################################################
#                                                                              #
#                                  main()                                      #
#                                                                              #
################################################################################

# process command-line options
parser = argparse.ArgumentParser()
parser.add_argument("--logfile", default="wasatch.log")
args = parser.parse_args()

# configure logging
logging.basicConfig(filename=args.logfile, 
                    level=logging.DEBUG, 
                    format='%(asctime)s.%(msecs)03d %(message)s', 
                    datefmt='%m/%d/%Y %I:%M:%S')
logging.info("-" * 80)
logging.info("wasatch-shell version %s invoked" % SCRIPT_VERSION)

dev = None
eeprom = None
getters = initializeGetters()

balance_intensity   = 45000
balance_threshold   =  2000 
balance_wavenumber  =  1000
balance_mode        = "INTEGRATION"
laser_power_mW      = 0
integration_time_ms = 0

try:
    while True:
        logging.debug("waiting for command")
        command = sys.stdin.readline().strip().upper()
        logging.debug("received command: " + command);

        # ignore comments
        if command.startswith('#') or len(command) == 0:
            pass

        elif command in getters:
            args = getters[command]
            if len(args) == 2:
                print Get_Value(args[0], args[1])
            else:
                print Get_Value(args[0], args[1], args[2])
            
        elif command == "OPEN":
            try:
                dev = Open_Spectrometers()
                Load_EEPROM()
            except Exception as e:
                logging.error(e, exc_info=1)
                print(0)
                break

        elif command == "CLOSE":
            break

        elif command == "SETINTTIME":
            setIntTime(int(sys.stdin.readline()))
        elif(command == "GETSPECTRUM"):
            getSpectrum(quiet=False)
        elif command == "STARTACQUISITION":
            startAcquisition()
        elif command == "GETDATA":
            getData()
        elif command == "GETTEMP":
            print getTemp()
        elif command == "GET_TEMP_DEGC":
            print getTempDegC()
        elif command == "SETLSI":
            setLSI(int(sys.stdin.readline()), int(sys.stdin.readline()))
        elif command == "SET_LSI_MW":
            setLaserPowerMW(requestedMW=float(sys.stdin.readline()))
        elif command == "GETCONFIG":
            getConfig(int(sys.stdin.readline()))
        elif command == "GET_CONFIG_JSON":
            print eeprom.json()
        elif command == "SETLSE" or command.startswith("SET_LASER_ENABLE"):
            print setLightSourceEnable(int(sys.stdin.readline()))
        elif command == "SETTECE":
            setTECEnable(int(sys.stdin.readline()))
        elif command == "GETTEMPSET":
            getTempSetPoint()
        elif command == "SETTEMPSET":
            print setTempSetPoint(int(sys.stdin.readline()))
        elif command == "SET_TEMP_SETPOINT_DEGC":
            print setTempSetPointDegC(float(sys.stdin.readline()))
        elif command == "GET_INTEGRATION_TIME":  # MZ: some have underbars, some don't...?
            getIntegrationTime()
        elif command == "GET_CCD_GAIN":
            getCCDGain()
        elif command == "GET_LASER_RAMPING_MODE":
            if Get_Value(0xff, 1, 0x09) == 2:
                print(Get_Value(0xea, 1))
            else:
                print(0)
        elif command == "GET_HORIZ_BINNING":
            if Get_Value(0xff, 1, 0x0c) == 1:
                print(Get_Value(0xbc, 1))
            else:
                print(0)

        elif command == "CUSTOMSET":
            print(Test_Set(int(sys.stdin.readline(), 16), 
                           int(sys.stdin.readline(), 16), 
                           int(sys.stdin.readline(), 16), 
                           int(sys.stdin.readline(), 16)))

        elif command == "CUSTOMGET":
            print(Get_Value(int(sys.stdin.readline(), 16), 
                            int(sys.stdin.readline(), 16)))

        elif command == "CUSTOMGET12":
            print(Get_Value_12bit(int(sys.stdin.readline(), 16)))
            
        elif command == "CUSTOMGET3":
            print(Get_Value(int(sys.stdin.readline(), 16), 
                            int(sys.stdin.readline(), 16), 
                            int(sys.stdin.readline(), 16)))

        elif command == "CONNECTION_CHECK":
            try:
                Get_Value(0xe3, 1) # GET_LASER_MOD_ENABLED
                print(True)
            except Exception as e:
                print(False)

        # MZ: added
        elif command == "SELECT_LASER":
            print(Test_Set(0xed, 0xee, int(sys.stdin.readline(), 16), 1))

        elif command == "GET_PHOTODIODE":
            print getPhotodiode()

        elif command == "GET_PHOTODIODE_MW":
            print getPhotodiodeMW()

        elif command == "HAS_PHOTODIODE_CALIBRATION":
            print hasPhotodiodeCalibration()

        elif command == "GET_LASER_TEMP":
            print getLaserTemp()

        elif command == "AUTO_BALANCE":
            autoBalance(mode=sys.stdin.readline(),
                        wavenumber=int(sys.stdin.readline()),
                        intensity=int(sys.stdin.readline()), 
                        threshold=int(sys.stdin.readline()))

        elif command == "HELP": 
            printHelp()

        elif command == "SCRIPT_VERSION":
            print(SCRIPT_VERSION)

        else:
            logging.debug("Unknown command: " + str(command))
            break

        try:
            sys.stdout.flush()
        except:
            logging.error("caller has closed stdout...exiting")
            break

    # disable the laser if connected
    if dev is not None:
        setLightSourceEnable(0)

except Exception as e:
    logging.error(e, exc_info=1)
    raise

logging.info("wasatch-shell exiting")
