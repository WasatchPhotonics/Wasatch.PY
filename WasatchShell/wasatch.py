#!/usr/bin/env python
import usb.core
import datetime
import sys
from time import sleep
import logging
import platform

logging.basicConfig(filename='wasatch.log',level=logging.DEBUG, format='%(asctime)s.%(msecs)03d %(message)s', datefmt='%m/%d/%Y %I:%M:%S')

# Newer ARM based products
#dev=usb.core.find(idVendor=0x24aa, idProduct=0x4000)

# Legacy products
dev=None

#print dev
H2D=0x40
D2H=0xC0
BUFFER_SIZE=8
ZZ = [0,0,0,0,0,0,0,0]
TIMEOUT=1000
PixelCount = 1024
OPEN = "OPEN"
CLOSE = "CLOSE"
GETTEMP = "GETTEMP"
SETLSI = "SETLSI"
GETCONFIG = "GETCONFIG"
SETLSE = "SETLSE"
SETTECE = "SETTECE"
GETTEMPSET = "GETTEMPSET"
SETTEMPSET = "SETTEMPSET"
SETINTTIME = "SETINTTIME"
GETSPECTRUM = "GETSPECTRUM"
STARTACQUISITION = "STARTACQUISITION"
GETDATA = "GETDATA"
GETTECENABLE = "GETTECENABLE"
GETLASERTEMP = "GETLASERTEMP"
GET_FPGA_REV="GET_FPGA_REV"
GET_INTEGRATION_TIME="GET_INTEGRATION_TIME"
GET_CODE_REVISION="GET_CODE_REVISION"
GET_MOD_DURATION="GET_MOD_DURATION"
GET_CCD_OFFSET="GET_CCD_OFFSET"
GET_CCD_GAIN="GET_CCD_GAIN"
GET_MOD_PULSE_DELAY="GET_MOD_PULSE_DELAY"
GET_MOD_PERIOD="GET_MOD_PERIOD"
GET_CCD_THRESHOLD_SENSING_MODE="GET_CCD_THRESHOLD_SENSING_MODE"
GET_CCD_SENSING_THRESHOLD="GET_CCD_SENSING_THRESHOLD"
GET_CCD_TRIGGER_SOURCE="GET_CCD_TRIGGER_SOURCE"
GET_LASER_TEMP="GET_LASER_TEMP"
GET_ACTUAL_FRAMES="GET_ACTUAL_FRAMES"
GET_LASER_MOD_PULSE_WIDTH="GET_LASER_MOD_PULSE_WIDTH"
GET_LINK_LASER_MOD_TO_INTEGRATION_TIME="GET_LINK_LASER_MOD_TO_INTEGRATION_TIME"
GET_ACTUAL_INTEGRATION_TIME="GET_ACTUAL_INTEGRATION_TIME"
GET_EXTERNAL_TRIGGER_OUTPUT="GET_EXTERNAL_TRIGGER_OUTPUT"
GET_LASER="GET_LASER"
GET_LASER_MOD="GET_LASER_MOD"
GET_LASER_TEMP_SETPOINT="GET_LASER_TEMP_SETPOINT"
GET_LASER_RAMPING_MODE="GET_LASER_RAMPING_MODE"
GET_INTERLOCK="GET_INTERLOCK"
GET_SELECTED_LASER="GET_SELECTED_LASER"
GET_HORIZ_BINNING="GET_HORIZ_BINNING"
GET_LINE_LENGTH="GET_LINE_LENGTH"
VR_GET_CONTINUOUS_CCD="VR_GET_CONTINUOUS_CCD"
VR_GET_NUM_FRAMES="VR_GET_NUM_FRAMES"
READ_COMPILATION_OPTIONS="READ_COMPILATION_OPTIONS"
OPT_INT_TIME_RES="OPT_INT_TIME_RES"
OPT_DATA_HDR_TAB="OPT_DATA_HDR_TAB"
OPT_CF_SELECT="OPT_CF_SELECT"
OPT_LASER="OPT_LASER"
OPT_LASER_CONTROL="OPT_LASER_CONTROL"
OPT_AREA_SCAN="OPT_AREA_SCAN"
OPT_ACT_INT_TIME="OPT_ACT_INT_TIME"
OPT_HORIZONTAL_BINNING="OPT_HORIZONTAL_BINNING"
CONNECTION_CHECK="CONNECTION_CHECK"


CUSTOMSET = "CUSTOMSET"
CUSTOMGET = "CUSTOMGET"
CUSTOMGET12 = "CUSTOMGET12"
CUSTOMGET3 = "CUSTOMGET3"


def Get_Value(Command, ByteCount, wValue=0):
	RetVal = 0
	if(Command == 0):
		return 0
	RetArray = dev.ctrl_transfer(D2H, Command, wValue,0, ByteCount, TIMEOUT)
	for i in range (0, ByteCount):
		RetVal = RetVal*256 + RetArray[ByteCount - i - 1]
	return RetVal
def Get_Value_12bit(Command):
	RetVal = 0
	if(Command == 0):
		return 0
	RetArray = dev.ctrl_transfer(D2H, Command, 0,0, 2, TIMEOUT)
	RetVal = RetArray[0] * 256 + RetArray[1];
	return RetVal
def Test_Set(setCommand, getCommand, wValue, wIndex, RetLen):
	return Test_Set(setCommand, getCommand, wValue | wIndex * 0x10000, RetLen)
def Test_Set(SetCommand, GetCommand, SetValue, RetLen):
	SetValueHigh = SetValue/0x10000
	SetValueLow = SetValue & 0xFFFF
	
	Ret = dev.ctrl_transfer(H2D, SetCommand, SetValueLow, int(SetValueHigh), ZZ, TIMEOUT)# set configuration
	if BUFFER_SIZE != Ret:
		logging.debug('Set {0:x}	Fail'.format(SetCommand))
		return False
	else:
		RetValue = Get_Value(GetCommand, RetLen)
		if RetValue is not None and SetValue == RetValue:
			return True
		else:
			logging.debug('Get {0:x} Failure. Txd:0x{1:x} Rxd:0x{2:x}'.format(GetCommand, SetValue, RetValue))
			return False
def Heighest_Peak():
	max_data = 0
	Data = dev.read(0x82,PixelCount*2)
	for j in range (0, int((PixelCount*2)/32), 1):
		for i in range (0, 31, 2):
			NewData = Data[j*32+i+1]*256+Data[j*32+i]
			max_data = max(max_data,NewData)
	return max_data
def data_poll():
	count = 0
	while(Get_Value(0xd4, 4) == 0):
		count += 1
def setIntTime(time):
	print(Test_Set(0xb2, 0xbf, time, 6))      # Set integration time
def getSpectra():
	startAcquisition()
	getData()
def startAcquisition():
	Test_Set(0xad, 0, 0, 0)      # Set acquisition
def getData():
	data_poll()
	Data = dev.read(0x82,PixelCount*2)
	for j in range (0, int((PixelCount*2)/32), 1):
		for i in range (0, 31, 2):
			print(Data[j*32+i+1]*256+Data[j*32+i])
def Open_Spectrometers():
	logging.debug("in open spectrometers")
	global dev
	dev=usb.core.find(idVendor=0x24aa, idProduct=0x1000)
	logging.debug("opened spectrometer")
	print(dev.bNumConfigurations)

def getTemp():
	print(Get_Value_12bit(0xd7))
def setLSI(period,width):
	if(Test_Set(0xc7, 0xcb, period, 5)):
		print(Test_Set(0xdb, 0xdc, width, 5))
	else:
		print(False)
def getConfig(index):
	logging.debug("getting config with index " + str(index))
	buff = dev.ctrl_transfer(D2H, 0xff, 1, index, 64, TIMEOUT)
	logging.debug("config: " + str(buff))
	for b in buff:
		#logging.debug("b: " + str(b))
		print(str(b))
def setLightSourceEnable(enable):
	if(enable):
		if(Test_Set(0xbd, 0xe3, 1, 1)):
			print(Test_Set(0xbe, 0xe2, 1, 1))
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
	RetArray = dev.ctrl_transfer(D2H, 0xd9, 0,0, 2, TIMEOUT)
	RetVal = RetArray[0] + RetArray[1] * 256;
	print(RetVal)
def setTempSetPoint(val):
	print(Test_Set(0xd8, 0xd9, val, 2))
def getTECEnable():
	print(Get_Value(0xda,1))
def getLaserTemp():
	print(Get_Value(0xd5,2))
def getIntegrationTime():
	RetArray = dev.ctrl_transfer(D2H, 0xbf, 0,0, 6, TIMEOUT)
	RetVal = RetArray[0] + RetArray[1] * 256 + RetArray[2] * 65536;
	print(RetVal)
def getCCDGain():
	RetArray = dev.ctrl_transfer(D2H, 0xc5, 0,0, 2, TIMEOUT)
	RetVal = RetArray[1] + RetArray[0] / 256.0;
	print(RetVal)
try:
	while True:
		logging.debug("waiting for command")
		command = sys.stdin.readline().strip()
		logging.debug("received command: " + command);
		if(command == OPEN):
			try:
				Open_Spectrometers()
			except Exception as e:
				logging.error(e,exc_info=1)
				print(0)
				break
		elif(command == CLOSE):
			break
		elif(command == SETINTTIME):
			setIntTime(int(sys.stdin.readline()))
		elif(command == GETSPECTRUM):
			getSpectra()
		elif(command == STARTACQUISITION):
			startAcquisition()
		elif(command == GETDATA):
			getData()
		elif(command == GETTEMP):
			getTemp()
		elif(command == SETLSI):
			setLSI(int(sys.stdin.readline()),int(sys.stdin.readline()))
		elif(command == GETCONFIG):
			getConfig(int(sys.stdin.readline()))
		elif(command == SETLSE):
			setLightSourceEnable(int(sys.stdin.readline()))
		elif(command == SETTECE):
			setTECEnable(int(sys.stdin.readline()))
		elif(command == GETTEMPSET):
			getTempSetPoint()
		elif(command == SETTEMPSET):
			setTempSetPoint(int(sys.stdin.readline()))
		elif(command == GETTECENABLE):
			getTECEnable()
		elif(command == GETLASERTEMP):
			getLaserTemp()
		elif(command==GET_FPGA_REV):
			print(Get_Value(0xb4,7))
		elif(command==GET_INTEGRATION_TIME):
			getIntegrationTime()
		elif(command==GET_CODE_REVISION):
			print(Get_Value(0xc0,4))
		elif(command==GET_MOD_DURATION):
			print(Get_Value(0xc3,5))
		elif(command==GET_CCD_OFFSET):
			print(Get_Value(0xc4,2))
		elif(command==GET_CCD_GAIN):
			getCCDGain()
		elif(command==GET_MOD_PULSE_DELAY):
			print(Get_Value(0xc4,2))
		elif(command==GET_MOD_PERIOD):
			print(Get_Value(0xcb,5))
		elif(command==GET_CCD_THRESHOLD_SENSING_MODE):
			print(Get_Value(0xcf,1))
		elif(command==GET_CCD_SENSING_THRESHOLD):
			print(Get_Value(0xd1,2))
		elif(command==GET_CCD_TRIGGER_SOURCE):
			print(Get_Value(0xd3,1))
		elif(command==GET_LASER_TEMP):
			print(Get_Value(0xd5,2))
		elif(command==GET_ACTUAL_FRAMES):
			print(Get_Value(0xe4,2))
		elif(command==GET_LASER_MOD_PULSE_WIDTH):
			print(Get_Value(0xdc,5))
		elif(command==GET_LINK_LASER_MOD_TO_INTEGRATION_TIME):
			print(Get_Value(0xde,1))
		elif(command==GET_ACTUAL_INTEGRATION_TIME):
			print(Get_Value(0xdf,6))
		elif(command==GET_EXTERNAL_TRIGGER_OUTPUT):
			print(Get_Value(0xe1,1))
		elif(command==GET_LASER):
			print(Get_Value(0xe2,1))
		elif(command==GET_LASER_MOD):
			print(Get_Value(0xe3,1))
		elif(command==GET_LASER_TEMP_SETPOINT):
			print(Get_Value(0xe8,6))
		elif(command==GET_LASER_RAMPING_MODE):
			if(Get_Value(0xff,1,0x09) == 2):
				print(Get_Value(0xea,1))
			else:
				print(0)
		elif(command==GET_INTERLOCK):
			print(Get_Value(0xef,1))
		elif(command==GET_SELECTED_LASER):
			print(Get_Value(0xee,1))
		elif(command==GET_HORIZ_BINNING):
			if(Get_Value(0xff,1,0x0c) == 1):
				print(Get_Value(0xbc,1))
			else:
				print(0)
		elif(command==GET_LINE_LENGTH):
			print(Get_Value(0xff,2,0x03))
		elif(command==VR_GET_CONTINUOUS_CCD):
			print(Get_Value(0xcc,1))
		elif(command==VR_GET_NUM_FRAMES):
			print(Get_Value(0xcd,1))
		elif(command==READ_COMPILATION_OPTIONS):
			print(Get_Value(0xff,2,0x04))
		elif(command==OPT_INT_TIME_RES):
			print(Get_Value(0xff,1,0x05))
		elif(command==OPT_DATA_HDR_TAB):
			print(Get_Value(0xff,1,0x06))
		elif(command==OPT_CF_SELECT):
			print(Get_Value(0xff,1,0x07))
		elif(command==OPT_LASER):
			print(Get_Value(0xff,1,0x08))
		elif(command==OPT_LASER_CONTROL):
			print(Get_Value(0xff,1,0x09))
		elif(command==OPT_AREA_SCAN):
			print(Get_Value(0xff,1,0x0a))
		elif(command==OPT_ACT_INT_TIME):
			print(Get_Value(0xff,1,0x0b))
		elif(command==OPT_HORIZONTAL_BINNING):
			print(Get_Value(0xff,1,0x0c))
		elif(command == CUSTOMSET):
			print(Test_Set(int(sys.stdin.readline(),16), int(sys.stdin.readline(),16), int(sys.stdin.readline(),16), int(sys.stdin.readline(),16)))
		elif(command == CUSTOMGET):
			print(Get_Value(int(sys.stdin.readline(),16), int(sys.stdin.readline(),16)))
		elif(command == CUSTOMGET12):
			print(Get_Value_12bit(int(sys.stdin.readline(),16)))
		elif(command == CUSTOMGET3):
			print(Get_Value(int(sys.stdin.readline(),16), int(sys.stdin.readline(),16), int(sys.stdin.readline(),16)))
		elif(command == CONNECTION_CHECK):
			try:
				Get_Value(0xe3,1)
				print(True)
			except Exception as e:
				print(False)
		else:
			logging.debug("Unknown command: " + str(command))
			break;
		sys.stdout.flush()
	if dev is not None:
		setLightSourceEnable(0)
except Exception as e:
	logging.error(e,exc_info=1)
	raise



