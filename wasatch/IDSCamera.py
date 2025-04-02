import threading
import logging
import time
import sys
import os

from ids_peak import ids_peak as IDSPeak
from ids_peak import ids_peak_ipl_extension as EXT
from ids_peak_ipl import ids_peak_ipl as IPL

from wasatch.AreaScanImage import AreaScanImage

log = logging.getLogger(__name__)

class IDSCamera:
    """
    This class encapsulates access to the IDS Peak SDK. It is called and used by
    IDSDevice. IDSDevice "is-an" InterfaceDevice, and is mimicking the same 
    upstream API as WasatchDevice, TCPDevice, AndorDevice etc. This class, 
    IDSCamera, is implementing the downstream IDS Peak SDK API.

    We could have done this like AndorDevice and TCPDevice, and merged both into
    one class (upstream and downsteam APIs). However, I opted to follow the 
    example of WasatchDevice/FeatureInterfaceDevice and OceanDevice/
    SeaBreezeWrapper, both of which keep the two separate.

    @see Program Files/IDS/ids_peak/generic_sdk/samples/source/python/start_stop_acquisition_software_trigger/main.py

    @par Image Formats

    Per cpp/multi_camera_live_qtwidgets/acquisitionworker.cpp, it looks like:

    peak::ipl::PixelFormatName::BGRa8    --> QImage::Format_RGB32
    peak::ipl::PixelFormatName::RGB8     --> QImage::Format_RGB888
    peak::ipl::PixelFormatName::RGB10p32 --> QImage::Format_BGR30
    peak::ipl::PixelFormatName::BayerRG8 --> QImage::Format_Grayscale8
    peak::ipl::PixelFormatName::Mono8    --> QImage::Format_Grayscale8
    otherwise                            --> QImage::Format_RGB32

    per site-packages/ids_peak_ipl/ids_peak_ipl.py

    PixelFormatName_Invalid
    PixelFormatName_BayerGR8
    PixelFormatName_BayerGR10
    PixelFormatName_BayerGR12
    PixelFormatName_BayerRG8
    PixelFormatName_BayerRG10
    PixelFormatName_BayerRG12
    PixelFormatName_BayerGB8
    PixelFormatName_BayerGB10
    PixelFormatName_BayerGB12
    PixelFormatName_BayerBG8
    PixelFormatName_BayerBG10
    PixelFormatName_BayerBG12
    PixelFormatName_Mono8
    PixelFormatName_Mono10
    PixelFormatName_Mono12
    PixelFormatName_Mono16
    PixelFormatName_Confidence8
    PixelFormatName_Confidence16
    PixelFormatName_Coord3D_C8
    PixelFormatName_Coord3D_C16
    PixelFormatName_Coord3D_C32f
    PixelFormatName_Coord3D_ABC32f
    PixelFormatName_YUV420_8_YY_UV_SemiplanarIDS
    PixelFormatName_YUV420_8_YY_VU_SemiplanarIDS
    PixelFormatName_YUV422_8_UYVY
    PixelFormatName_RGB8
    PixelFormatName_RGB10
    PixelFormatName_RGB12
    PixelFormatName_BGR8
    PixelFormatName_BGR10
    PixelFormatName_BGR12
    PixelFormatName_RGBa8
    PixelFormatName_RGBa10
    PixelFormatName_RGBa12
    PixelFormatName_BGRa8
    PixelFormatName_BGRa10
    PixelFormatName_BGRa12
    PixelFormatName_BayerBG10p
    PixelFormatName_BayerBG12p
    PixelFormatName_BayerGB10p
    PixelFormatName_BayerGB12p
    PixelFormatName_BayerGR10p
    PixelFormatName_BayerGR12p
    PixelFormatName_BayerRG10p
    PixelFormatName_BayerRG12p
    PixelFormatName_Mono10p
    PixelFormatName_Mono12p
    PixelFormatName_RGB10p32
    PixelFormatName_BGR10p32
    PixelFormatName_BayerRG10g40IDS
    """

    INITIALIZED = False

    FORMAT_VERTICAL_BINNING = IPL.PixelFormatName_Mono16 # 
    FORMAT_AREA_SCAN = IPL.PixelFormatName_BGRa8

    ############################################################################
    # Lifecycle
    ############################################################################

    def __init__(self):

        self.device = None
        self.node_map = None
        self.long_name = None
        self.datastream = None

        self.save_area_scan_to_disk = True
        self.save_area_scan_image = False
        self.take_one_request = None
        self.taking_acquisition = False
        self.shutdown_in_progress = False

        self.buffers = []

        # correspond to EEPROM
        self.model_name = None
        self.serial_number = None
        self.width = 0
        self.height = 0
        self.start_line = 0
        self.stop_line = 0
        self.integration_time_ms = 15 # seems to be default?
        self.last_integration_time_ms = 15

        self.image_converter_vertical_binning = None
        self.image_converter_area_scan = None
        self.last_area_scan_image = None

        try:
            if self.INITIALIZED:
                log.debug("IDSPeak.Library already initialized")
            else:
                log.debug("initializing IDSPeak.Library")
                IDSPeak.Library.Initialize()
                self.INITIALIZED = True
            self.device_manager = IDSPeak.DeviceManager.Instance()
        except Exception as ex:
            log.critical("failed to instantiate IDSPeak.DeviceManager", exc_info=1)

    def close(self):
        log.debug("close: start")
        self.stop()
        try:
            if self.datastream is not None:
                log.debug("close: revoking buffers")
                for buffer in self.datastream.AnnouncedBuffers():
                    self.datastream.RevokeBuffer(buffer)
        except Exception as e:
            log.error(f"close: caught exception when clearing buffers: {e}", exc_info=1)
        log.debug("close: done")

    def __del__(self):
        self.close()

    def __repr__(self):
        return self.long_name

    def connect(self):
        log.debug("connect: start")
        self.device_manager.Update()
        if self.device_manager.Devices().empty():
            log.debug("connect: no devices found :-(")
            return False

        # List all available devices
        log.debug("connect: found device(s):")
        for device in self.device_manager.Devices():
            model_name  = device.ModelName()
            parent_intf = device.ParentInterface().DisplayName()
            parent_name = device.ParentInterface().ParentSystem().DisplayName()
            parent_ver  = device.ParentInterface().ParentSystem().Version()
            long_name = f"{model_name} {parent_intf} ({parent_name} {parent_ver})"
            log.debug(f"  {long_name}")
            if self.long_name is None:
                self.long_name = long_name

        # Open the first device in control mode
        self.device = self.device_manager.Devices()[0].OpenDevice(IDSPeak.DeviceAccessType_Control)

        # Get device's control nodes
        self.node_map = self.device.RemoteDevice().NodeMaps()[0]

        # Cache settings corresponding to Wasatch EEPROM
        self.model_name = device.ModelName()
        self.serial_number = self.node_map.FindNode("DeviceSerialNumber").Value()
        self.width = self.node_map.FindNode("Width").Value()
        self.height = self.node_map.FindNode("Height").Value()
        self.last_integration_time_ms = self.node_map.FindNode("ExposureTime").Value() / 1000.0

        self.start_line = 0
        self.stop_line = self.height - 1

        # Load the default settings
        self.node_map.FindNode("UserSetSelector").SetCurrentEntry("Default")
        self.node_map.FindNode("UserSetLoad").Execute()
        self.node_map.FindNode("UserSetLoad").WaitUntilDone()

        # initialize software trigger
        # @see https://www.ids-imaging.us/manuals/ids-peak/ids-peak-user-manual/2.15.0/en/acquisition-control.html
        self.dump()

        log.debug("TriggerSelector set to ExposureStart")
        self.node_map.FindNode("TriggerSelector").SetCurrentEntry("ReadOutStart") # "ExposureStart" not supported?
        self.node_map.FindNode("TriggerMode").SetCurrentEntry("On")
        self.node_map.FindNode("TriggerSource").SetCurrentEntry("Software")
        self.node_map.FindNode("ExposureTime").SetValue(400_000) # value in µs
        
        # nodeMapRemoteDevice.FindNode("SequencerMode")   .SetCurrentEntry("Off")
        # nodeMapRemoteDevice.FindNode("AcquisitionMode") .SetCurrentEntry("SingleFrame")
        # nodeMapRemoteDevice.FindNode("ExposureMode")    .SetCurrentEntry("Timed")
        # nodeMapRemoteDevice.FindNode("ExposureAuto")    .SetCurrentEntry("Off")
        # nodeMapRemoteDevice.FindNode("TriggerSelector") .SetCurrentEntry("ExposureStart")
        # nodeMapRemoteDevice.FindNode("ExposureTime")    .SetValue(400_000) # value in µs
        # nodeMapRemoteDevice.FindNode("AcquisitionStart").Execute()
        # nodeMapRemoteDevice.FindNode("AcquisitionStart").WaitUntilDone()

        self.dump()

        log.debug(f"connect: successfully connected to {self.long_name}")
        return True

    ############################################################################
    # Acquisition Loop (Device)
    ############################################################################

    def set_start_line(self, n):
        self.start_line = n
        
    def set_stop_line(self, n):
        self.stop_line = n

    def set_integration_time_ms(self, ms):
        # do we need to .stop and .start around this?
        log.debug(f"set_integration_time_ms: start")
        us = ms * 1000.0

        node = self.node_map.FindNode("ExposureTime")
        us = max(us, node.Minimum())
        us = min(us, node.Maximum())

        log.debug(f"set_integration_time_ms: setting integration time to {us} µs")
        self.node_map.FindNode("ExposureTime").SetValue(us)
        self.integration_time_ms = ms
        log.debug(f"set_integration_time_ms: done")

    def start(self):
        log.debug("start: starting")
        if self.device is None:
            log.debug("start: no device")
            return 

        if self.taking_acquisition:
            log.debug("start: already running")
            return

        # self.datastream = None # kludge
        if self.datastream is None:
            log.debug("start: initializing datastream")
            self.datastream = self.device.DataStreams()[0].OpenDataStream()
            log.debug(f"start: datastream {self.datastream}")
            self.reset()
        else:
            log.debug("start: datastream already initialized?")

        log.debug("start: queueing buffers")
        for buffer in self.buffers:
            self.datastream.QueueBuffer(buffer)

        try:
            # Lock parameters that should not be accessed during acquisition
            log.debug("start: locking parameters")
            self.node_map.FindNode("TLParamsLocked").SetValue(1)

            pixel_format = self.node_map.FindNode("PixelFormat").CurrentEntry().Value()
            input_pixel_format = IPL.PixelFormat(pixel_format)

            log.debug(f"start: Width {self.width}, Height {self.height}, PixelFormat {pixel_format}, input format {input_pixel_format}")

            # Pre-allocate conversion buffers to speed up first image conversion
            # while the acquisition is running
            #
            # NOTE: Lazy-load the image converters
            if self.image_converter_vertical_binning is None:
                log.debug("start: pre-allocating image converter for vertical binning")
                self.image_converter_vertical_binning = IPL.ImageConverter()
                self.image_converter_vertical_binning.PreAllocateConversion(input_pixel_format, self.FORMAT_VERTICAL_BINNING, self.width, self.height)

                log.debug("start: pre-allocating image converter for area scan")
                self.image_converter_area_scan = None
                if False: # kludge
                    self.image_converter_area_scan = IPL.ImageConverter()
                    self.image_converter_area_scan.PreAllocateConversion(input_pixel_format, self.FORMAT_AREA_SCAN, self.width, self.height)
                
                log.debug(f"start: supported conversions from input pixel format {input_pixel_format}:")
                for fmt in self.image_converter_vertical_binning.SupportedOutputPixelFormatNames(input_pixel_format):
                    log.debug(f"  {str(fmt)}")

            log.debug("start: starting acquisition")
            self.datastream.StartAcquisition()
            log.debug("start: executing")
            self.node_map.FindNode("AcquisitionStart").Execute()
            log.debug("start: waiting")
            self.node_map.FindNode("AcquisitionStart").WaitUntilDone()
            log.debug("start: done waiting")
            self.taking_acquisition = True

            log.debug("start: started")
        except Exception as e:
            log.error(f"Exception (start acquisition): {e}", exc_info=1)

        log.debug("start: done")

    def stop(self):
        """ Called during shutdown.  """
        if self.device is None:
            return

        if not self.taking_acquisition:
            log.debug("stop: not running")
            return

        try:
            log.debug("stop: stopping")
            self.node_map.FindNode("AcquisitionStop").Execute()

            self.datastream.StopAcquisition(IDSPeak.AcquisitionStopMode_Default)
            # Discard all buffers from the acquisition engine
            # They remain in the announced buffer pool
            self.datastream.Flush(IDSPeak.DataStreamFlushMode_DiscardAll)
            self.taking_acquisition = False

            # Unlock parameters
            self.node_map.FindNode("TLParamsLocked").SetValue(0)
        except Exception as e:
            log.error(f"stop: caught exception {e}", exc_info=1)
        log.debug("stop: done")

    def reset(self):
        log.debug("resetting datastream")
        if self.datastream is None:
            log.error("no datastream to reset?!")
            return

        try:
            # Check if buffers are already allocated
            if self.datastream is not None:
                # Remove buffers from the announced pool
                for buffer in self.datastream.AnnouncedBuffers():
                    self.datastream.RevokeBuffer(buffer)
                self.buffers = []

            payload_size = self.node_map.FindNode("PayloadSize").Value()
            buffer_amount = self.datastream.NumBuffersAnnouncedMinRequired()

            for _ in range(buffer_amount):
                buffer = self.datastream.AllocAndAnnounceBuffer(payload_size)
                self.buffers.append(buffer)

            log.debug("reset: allocated buffers")
        except Exception as e:
            log.error(f"reset: caught exception {e}", exc_info=1)

    ############################################################################
    # Acquisition Loop (Software)
    ############################################################################

    def acquisition_loop(self):
        """
        This method is only used by the main() demonstrator. This is essentially
        done by WrapperWorker in ENLIGHTEN.
        """
        while not self.shutdown_in_progress:
            try:
                if self.take_one_request is not None:
                    self.send_trigger()
                    self.get_spectrum()
                    self.take_one_request = None
            except Exception as e:
                log.error(f"acquisition_loop: caught exception {e}", exc_info=1)
                self.take_one_request = None

    def send_trigger(self):
        self.node_map.FindNode("TriggerSoftware").Execute()
        self.node_map.FindNode("TriggerSoftware").WaitUntilDone()

    def get_spectrum(self):
        log.debug("get_spectrum: start")
        cwd = os.getcwd()
        if self.datastream is None:
            log.error("get_spectrum: no datastream?!")
            return None

        timeout_ms = int(round(1000 + 2 * max(self.integration_time_ms, self.last_integration_time_ms)))
        try:
            log.debug(f"get_spectrum: calling WaitForFinishedBuffer timeout {timeout_ms}ms")
            buffer = self.datastream.WaitForFinishedBuffer(timeout_ms) # takes ms
            log.debug(f"get_spectrum: back from WaitForFinishedBuffer")
        except:
            log.error(f"failed on datastream.WaitForFinishedBuffer({timeout_ms}ms)", exc_info=1)
            return None

        # Get image from buffer (shallow copy)
        log.debug(f"get_spectrum: reading buffer")
        image = EXT.BufferToImage(buffer)
        log.debug(f"get_spectrum: read buffer")

        # This creates a deep copy of the image, so the buffer is free to be used again
        # NOTE: Use `ImageConverter`, since the `ConvertTo` function re-allocates
        #       the conversion buffers on every call
        log.debug(f"get_spectrum: converting for vertical binning")
        converted_vertical_binning = None
        if True: # False: # kludge
            converted_vertical_binning = self.image_converter_vertical_binning.Convert(image, self.FORMAT_VERTICAL_BINNING)
        log.debug(f"get_spectrum: converted_vertical_binning {converted_vertical_binning}")

        converted_area_scan = None
        self.last_area_scan_image = None
        if self.save_area_scan_image and self.image_converter_area_scan is not None:
            log.debug(f"get_spectrum: converting for area scan")
            converted_area_scan = self.image_converter_area_scan.Convert(image, self.FORMAT_AREA_SCAN)
            log.debug(f"get_spectrum: converted_area_scan {converted_area_scan}")
            data = converted_area_scan.get_numpy_1D().copy()
            log.debug(f"get_spectrum: area scan 1D len {len(data)}")

            # ENLIGHTEN will convert to QtGui.QImage.Format_RGB32
            self.last_area_scan_image = AreaScanImage(data, converted_area_scan.Width(), converted_area_scan.Height(), fmt="RGB32")
            log.debug(f"get_spectrum: last_area_scan_image {self.last_area_scan_image}")

        # we've converted the original image (possibly twice), so can now release the underlying buffer
        log.debug(f"get_spectrum: releasing buffer")
        self.datastream.QueueBuffer(buffer)

        if self.save_area_scan_to_disk:
            pathname_mono = self.next_name(cwd + "/image-mono", ".png")
            if converted_vertical_binning is not None:
                log.debug(f"get_spectrum: saving mono png to disk")
                IPL.ImageWriter.WriteAsPNG(pathname_mono, converted_vertical_binning)
                log.debug(f"Saved as {pathname_mono}")

            pathname_area = self.next_name(cwd + "/image-area", ".png")
            if converted_area_scan is not None and self.save_area_scan_image:
                log.debug(f"get_spectrum: saving area png to disk")
                IPL.ImageWriter.WriteAsPNG(pathname_area, converted_area_scan)
                log.debug(f"Saved as {pathname_area}")

        ########################################################################
        # Vertical Binning
        ########################################################################

        log.debug(f"get_spectrum: performing vertical binning")
        
        if converted_vertical_binning is not None:
            first_row = max(0, self.start_line)
            last_row = min(self.stop_line, converted_vertical_binning.Height() - 1)
            log.debug(f"get_spectrum: first_row {first_row}, last_row {last_row}")
            spectrum = [0] * converted_vertical_binning.Width()

            try:
                for row in range(first_row, last_row + 1):
                    # log.debug(f"get_spectrum: row {row}")
                    pixel_row = IPL.PixelRow(converted_vertical_binning, row)
                    channels = pixel_row.Channels() 
                    channel = channels[0]
                    values = channel.Values 
                    for pixel, intensity in enumerate(values):
                        spectrum[pixel] += intensity
            except Exception as e:
                log.error(f"Error vertically binning image: {e}", exc_info=1)
        else:
            spectrum = [0] * image.Width()
        log.debug(f"get_spectrum: done binning")

        if self.save_area_scan_to_disk:
            log.debug(f"saving area CSV to disk")
            pathname_csv = pathname_mono.replace(".png", ".csv")
            with open(pathname_csv, "w") as outfile:
                for pixel, intensity in enumerate(spectrum):
                    outfile.write(f"{pixel}, {intensity}\n")
            log.debug(f"Saved as {pathname_csv}")

        if spectrum is None:
            log.debug(f"get_spectrum: returning {spectrum}")
        else:
            log.debug(f"get_spectrum: returning {spectrum[:5]}")

        self.last_integration_time_ms = self.integration_time_ms
        log.debug("get_spectrum: done")
        return spectrum

    ############################################################################
    # Utility
    ############################################################################

    def next_name(self, path, ext):
        num = 0

        def build_string():
            return f"{path}_{num}{ext}"

        while os.path.exists(build_string()):
            num += 1
        return build_string()

    def dump(self):
        for name in ["DeviceSerialNumber", "DeviceUserID", "DeviceFamilyName", "DeviceModelName", 
                     "DeviceManufacturerInfo", "DeviceVendorName", "DeviceVersion", "DeviceFirmwareVersion", 
                     "DeviceSFNCVersionMajor", "DeviceSFNCVersionMinor", "DeviceSFNCVersionSubMinor" ]:
            self.dump_value(name)
        for name in ["ExposureTime"]:
            self.dump_min_max(name)
        for name in ["DeviceBootStatus", "DeviceScanType", "SensorOperationMode", "SequencerMode", 
                     "AcquisitionMode", "ExposureMode", "TriggerSelector", "SensorShutterMode"]:
            self.dump_entries(name)

    def dump_min_max(self, name):
        try:
            node = self.node_map.FindNode(name)
            if node is not None:
                value = None
                t = type(node).__name__ # vs .Type()
                if isinstance(node, (IDSPeak.IntegerNode, IDSPeak.FloatNode)):
                    value = node.Value()
                    min_ = node.Minimum()
                    max_ = node.Maximum()
                    log.debug(f"{t} {name}: {value} (min {min_}, max {max_})")
                else:
                    log.debug(f"'{name}' is type {t} (unsupported)")
            else:
                log.debug(f"dump_min_max: '{name}' not found in node_map")
        except Exception as ex:
            log.error(f"dump_min_max: error with name '{name}': {ex}", exc_info=1)

    def dump_value(self, name):
        try:
            node = self.node_map.FindNode(name)
            if node is not None:
                value = None
                t = type(node).__name__ # vs .Type()
                if   isinstance(node, IDSPeak.StringNode):      value = node.Value()
                elif isinstance(node, IDSPeak.BooleanNode):     value = node.Value()
                elif isinstance(node, IDSPeak.IntegerNode):     value = node.Value()
                elif isinstance(node, IDSPeak.FloatNode):       value = node.Value()
                elif isinstance(node, IDSPeak.EnumerationNode): value = node.CurrentEntry().SymbolicValue()
                if value is not None:
                    log.debug(f"{t} {name}: {value}")
                else:
                    log.debug(f"'{name}' is type {t}: '{value}'")
            else:
                log.debug(f"dump_value: '{name}' not found in node_map")
        except Exception as ex:
            log.error(f"dump_value: error with name '{name}': {ex}", exc_info=1)

    def dump_entries(self, name):
        log.debug(f"Supported values of '{name}':")
        current = self.node_map.FindNode(name).CurrentEntry().SymbolicValue()
        all_entries = self.node_map.FindNode(name).Entries()
        for entry in all_entries:
            if (  entry.AccessStatus() != IDSPeak.NodeAccessStatus_NotAvailable and 
                  entry.AccessStatus() != IDSPeak.NodeAccessStatus_NotImplemented):
                value = entry.SymbolicValue()
                if value == current:
                    log.debug(f"  {value} [CURRENT]")
                else:
                    log.debug(f"  {value}")

if __name__ == '__main__':
    """ invoke from Wasatch.PY folder as: python -u wasatch/IDSCamera.py """

    from wasatch import applog
    logger = applog.MainLogger("DEBUG")

    log.debug("getting IDSPeak.DeviceManager instance")
    camera = None
    use_threading = False

    try:
        log.info("main: instantiating Camera")
        camera = IDSCamera()

        log.info("main: calling camera.connect")
        if not camera.connect():
            log.critical("No IDS camera found")
            sys.exit(1)

        log.info("main: starting camera acquisition")
        camera.start()

        if use_threading:
            log.info("main: spawning acquisition loop")
            thread = threading.Thread(target=camera.acquisition_loop, args=())
            thread.start()

        log.info("main: monitoring acquisitions")
        try:
            # break loop with ctrl-C
            while True:

                if use_threading:
                    log.info("main: dropping request into acquisition loop")
                    camera.take_one_request = object()
                    while camera.take_one_request is not None:
                        time.sleep(0.01)
                        pass
                else:
                    log.info("main: taking spectrum")
                    camera.send_trigger()
                    camera.get_spectrum()

                log.info("main: measurement completed, sleeping 5sec")
                time.sleep(5)
        except Exception as ex:
            log.error("main: exception during measuremnet loop: {ex}", exc_info=1)
        finally:
            # make sure to always stop the acquisition_thread, otherwise
            # we'd hang, e.g. on KeyboardInterrupt
            log.info("main: shutting down (joining thread)")
            camera.shutdown_in_progress= True
            if use_threading:
                thread.join()

    except KeyboardInterrupt:
        log.critical("User interrupt: Exiting...")
    except Exception as e:
        log.error(f"Exception (main): {e}", exc_info=1)
    finally:
        # Close camera and library after program ends
        if camera is not None:
            log.info("main: closing camera")
            camera.close()
        log.info("main: closing Library")
        IDSPeak.Library.Close()
