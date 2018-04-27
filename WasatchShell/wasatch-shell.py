#!/usr/bin/env python
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

# constants
SCRIPT_VERSION = "1.0.3"
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
        GET_PHOTODIODE

    The following getters are also available:""" % SCRIPT_VERSION
    print sorted(getters.keys())

def Get_Value(Command, ByteCount, wValue=0):
    RetVal = 0
    if Command == 0:
        return 0
    RetArray = dev.ctrl_transfer(DEVICE_TO_HOST, Command, wValue, 0, ByteCount, TIMEOUT_MS)
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
    SetValueHigh = SetValue / 0x10000
    SetValueLow  = SetValue & 0xFFFF
    
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
    print(Test_Set(0xb2, 0xbf, time, 6))      # Set integration time

def getSpectrum():
    startAcquisition()
    getData()

def startAcquisition():
    Test_Set(0xad, 0, 0, 0)      # Set acquisition

def getData():
    data_poll()
    Data = dev.read(0x82, PIXEL_COUNT * 2)
    for j in range (0, int((PIXEL_COUNT * 2)/32), 1):
        for i in range (0, 31, 2):
            print(Data[j*32+i+1]*256+Data[j*32+i])
    logging.debug("returned %d pixels", PIXEL_COUNT)

def Open_Spectrometers():
    logging.debug("in open spectrometers")
    dev = usb.core.find(idVendor=0x24aa, idProduct=0x1000)
    logging.debug("opened spectrometer")
    print(dev.bNumConfigurations)
    return dev

def getTemp():
    print(Get_Value_12bit(0xd7))

def setLSI(period, width):
    if(Test_Set(0xc7, 0xcb, period, 5)): # SET_MOD_PERIOD
        print(Test_Set(0xdb, 0xdc, width, 5)) # SET_LASER_MOD_PULSE_WIDTH
    else:
        print(False)

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
            print(Test_Set(0xbe, 0xe2, 1, 1))   # SET_LASER_ENABLED
        else:
            print(False)
    else:
        print(Test_Set(0xbe, 0xe2, 0, 1))

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
    print(Test_Set(0xd8, 0xd9, val, 2))

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
    print Get_Value(0xd5, 2)    # stable read
    
def getPhotodiode():
    Test_Set(0xed, 0xee, 1, 1)  # select the secondary ADC
    Get_Value(0xd5, 2)          # throwaway read
    print Get_Value(0xd5, 2)    # stable read

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
logging.debug("wasatch-shell version %s" % SCRIPT_VERSION)

dev = None
getters = initializeGetters()
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
            except Exception as e:
                logging.error(e,exc_info=1)
                print(0)
                break

        elif command == "CLOSE":
            break

        elif command == "SETINTTIME":
            setIntTime(int(sys.stdin.readline()))
        elif(command == "GETSPECTRUM"):
            getSpectrum()
        elif command == "STARTACQUISITION":
            startAcquisition()
        elif command == "GETDATA":
            getData()
        elif command == "GETTEMP":
            getTemp()
        elif command == "SETLSI":
            setLSI(int(sys.stdin.readline()), int(sys.stdin.readline()))
        elif command == "GETCONFIG":
            getConfig(int(sys.stdin.readline()))
        elif command == "SETLSE":
            setLightSourceEnable(int(sys.stdin.readline()))
        elif command == "SETTECE":
            setTECEnable(int(sys.stdin.readline()))
        elif command == "GETTEMPSET":
            getTempSetPoint()
        elif command == "SETTEMPSET":
            setTempSetPoint(int(sys.stdin.readline()))
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
            getPhotodiode()

        elif command == "GET_LASER_TEMP":
            getLaserTemp()

        elif command == "HELP": 
            printHelp()

        elif command == "SCRIPT_VERSION":
            print(SCRIPT_VERSION)

        else:
            logging.debug("Unknown command: " + str(command))
            break

        sys.stdout.flush()

    # disable the laser if not connected
    if dev is not None:
        setLightSourceEnable(0)

except Exception as e:
    logging.error(e, exc_info=1)
    raise
