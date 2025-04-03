import threading
import logging
import pathlib 
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
    """

    INITIALIZED = False

    # @see site-packages/ids_peak_ipl/ids_peak_ipl.py
    ALL_PIXEL_FORMAT_NAMES = [
        "BGR10",        "BGR10p32",        "BGR12",          "BGR8",        "BGRa10",          "BGRa12",       "BGRa8",
        "BayerBG10",    "BayerBG10g40IDS", "BayerBG10p",     "BayerBG12",   "BayerBG12g24IDS", "BayerBG12p",   "BayerBG8",
        "BayerGB10",    "BayerGB10g40IDS", "BayerGB10p",     "BayerGB12",   "BayerGB12g24IDS", "BayerGB12p",   "BayerGB8",
        "BayerGR10",    "BayerGR10g40IDS", "BayerGR10p",     "BayerGR12",   "BayerGR12g24IDS", "BayerGR12p",   "BayerGR8",
        "BayerRG10",    "BayerRG10g40IDS", "BayerRG10p",     "BayerRG12",   "BayerRG12g24IDS", "BayerRG12p",   "BayerRG8",
        "Confidence16", "Confidence8",     "Coord3D_ABC32f", "Coord3D_C16", "Coord3D_C32f",    "Coord3D_C8",
        "Mono10",       "Mono12",          "Mono10p",        "Mono12p",     "Mono10g40IDS",    "Mono12g24IDS", "Mono16", "Mono8",
        "RGB10",        "RGB10p32",        "RGB12",          "RGB8",        "RGBa10",          "RGBa12",       "RGBa8",
        "YUV420_8_YY_UV_SemiplanarIDS",    "YUV420_8_YY_VU_SemiplanarIDS",  "YUV422_8_UYVY",   "Invalid"
    ]

    # generated via ImageConverter.SupportedOutputPixelFormatNames(PixelFormat(IPL.PixelFormatName_Mono12g24IDS))
    # "Mono10g40IDS" omitted as unusable (can't be accessed as line data, can't be output to PNG)
    SUPPORTED_CONVERSIONS = [ 
        "Mono16", "Mono12", "Mono10", "Mono8",
        "RGB12",  "RGB10",  "RGB8",
        "BGR12",  "BGR10",  "BGR8",
        "RGBa12", "RGBa10", "RGBa8",
        "BGRa12", "BGRa10", "BGRa8"
    ] 

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

        self.image_converter = None
        self.last_area_scan_image = None

        self.vertical_binning_format_name = "Mono16"
        self.area_scan_format_name = "BGRa8"

        try:
            if self.INITIALIZED:
                log.debug("IDSPeak.Library already initialized")
            else:
                log.debug("initializing IDSPeak.Library")
                IDSPeak.Library.Initialize()
                self.INITIALIZED = True
            self.device_manager = IDSPeak.DeviceManager.Instance()
            self.init_pixel_format_names()
        except Exception as ex:
            log.critical("failed to instantiate IDSPeak.DeviceManager", exc_info=1)

    def init_pixel_format_names(self):
        """ 
        If there's an in-built API method to convert from the name of a desired 
        format to a PixelFormat object or number, I didn't see one. (Okay, maybe
        using Node.Entries() and SymbolicValue().)

        @see IPL.PixelFormat class docs at https://en.ids-imaging.com/manuals/ids-peak/ids-peak-ipl-documentation/2.15.0/en/classpeak_1_1ipl_1_1_pixel_format.html
        """
        self.name_to_num = {}
        log.debug("All PixelFormat types:")
        for name in self.ALL_PIXEL_FORMAT_NAMES:
            num = getattr(IPL, f"PixelFormatName_{name}") # int
            fmt = IPL.PixelFormat(num) # num -> PixelFormat is easy
            log.debug(f"  {name:30} = 0x{num:08x} ({num})")
            assert name == fmt.Name(), f"name {name} != Name() {fmt.Name()}"
            assert num == fmt.PixelFormatName(), f"num {num} != PixelFormatName() {fmt.PixelFormatName()}" # poorly named
            self.name_to_num[name] = num

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
        self.node_map.FindNode("ExposureTime").SetValue(1_000_000) # value in µs
        
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
            log.debug(f"start: datastream initialized")
            self.reset()
        else:
            log.debug("start: datastream already initialized?")

        log.debug("start: queueing buffers")
        for buffer in self.buffers:
            self.datastream.QueueBuffer(buffer)

        try:
            # Lock parameters that should not be accessed during acquisition
            # MZ: what are these?
            log.debug("start: locking parameters")
            self.node_map.FindNode("TLParamsLocked").SetValue(1)

            # determine initial camera output format
            node = self.node_map.FindNode("PixelFormat")
            entry = node.CurrentEntry()
            num = entry.Value()
            input_fmt = IPL.PixelFormat(num)
            name = input_fmt.Name()

            log.debug(f"start: width {self.width}, height {self.height}, outputting {name} 0x{num:08x} ({num})")

            # Pre-allocate conversion buffers to speed up first image conversion
            # while the acquisition is running
            #
            # NOTE: Lazy-load the image converters
            if self.image_converter is None:
                self.image_converter = IPL.ImageConverter()

                # use this opportunity to report all the different conversion 
                # options from the sensor's DEFAULT format, which is either 
                # "Mono10g40IDS" or "Mono12g24IDS"
                log.debug(f"start: supported conversions from {name}:")
                for num in self.image_converter.SupportedOutputPixelFormatNames(input_fmt):
                    fmt = IPL.PixelFormat(num)
                    name = fmt.Name()
                    log.debug(f"  {name:30} = 0x{num:08x} ({num})")

                # don't pre-allocate for now; still playing with different formats
                # self.image_converter.PreAllocateConversion(input_fmt, self.FORMAT_VERTICAL_BINNING, self.width, self.height)
                #
                # it's possible that holding two pre-allocated ImageConverters was screwing something up? not sure
                # log.debug("start: pre-allocating image converter for area scan")
                # self.image_converter_area_scan = None
                # self.image_converter_area_scan = IPL.ImageConverter()
                # self.image_converter_area_scan.PreAllocateConversion(input_fmt, self.FORMAT_AREA_SCAN, self.width, self.height)
                
            self.datastream.StartAcquisition()
            self.node_map.FindNode("AcquisitionStart").Execute()
            self.node_map.FindNode("AcquisitionStart").WaitUntilDone()
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

        if True:
            # normal case, just vertically bin using the configured format
            spectrum, asi = self.vertically_bin_image(image)

            self.datastream.QueueBuffer(buffer)
        else:
            # characterization: test all supported image formats

            # this will take awhile, so make a deep-copy and release the buffer
            clone = image.Clone()
            self.datastream.QueueBuffer(buffer)

            # loop over all supported conversions
            for format_name in self.SUPPORTED_CONVERSIONS:
                try:
                    spectrum, asi = self.vertically_bin_image(clone, format_name)
                except Exception as ex:
                    log.error(f"caught exception during conversion to {format_name}: {ex}", exc_info=1)

        # for now, just use the PNG cached in the filesystem
        #
        # data = converted_area_scan.get_numpy_1D().copy()
        # log.debug(f"get_spectrum: area scan 1D len {len(data)}")
        # 
        # # ENLIGHTEN will convert to QtGui.QImage.Format_RGB32
        # self.last_area_scan_image = AreaScanImage(data, converted_area_scan.Width(), converted_area_scan.Height(), fmt="RGB32")

        self.last_area_scan_image = asi

        self.last_integration_time_ms = self.integration_time_ms
        log.debug("get_spectrum: done")
        return spectrum

    def vertically_bin_image(self, image, format_name=None):
        """ returns a single vertically-binned spectrum, and an AreaScanImage """

        if format_name is None:
            format_name = self.vertical_binning_format_name
        format_num = self.name_to_num[format_name]
        fmt = IPL.PixelFormat(format_num)

        converted = self.image_converter.Convert(image, fmt)
        if converted is None:
            log.error("vertically_bin_image: failed converting image to {format_name}")
            return None, None

        # attempt to save converted image to PNG for debugging
        pathname_png = f"idspeak/converted-{format_name}.png"
        pathlib.Path("idspeak").mkdir(exist_ok=True)
        try:
            IPL.ImageWriter.WriteAsPNG(pathname_png, converted)
            log.debug(f"saved {pathname_png}")
            asi = AreaScanImage(pathname_png=pathname_png)
        except IPL.ImageFormatNotSupportedException:
            log.error(f"vertically_bin_image: unable to save {format_name} as PNG", exc_info=1)
            asi = None

        # this will hold the sum of all channels
        spectrum = [0] * converted.Width()

        # individual per-channel spectra for characterization
        channel_count = fmt.NumChannels()
        channel_spectra = [ [0] * converted.Width() for i in range(channel_count) ]

        try:
            # iterate over each line of the 2D image
            for row in range(converted.Height()):
                pixel_row = IPL.PixelRow(converted, row)

                # iterate over each channel (R, G, B, a, etc)
                for channel_index, channel in enumerate(pixel_row.Channels()):
                    # iterate over each pixel in the line (for this channel)
                    values = channel.Values 
                    for pixel, intensity in enumerate(values):
                        spectrum[pixel] += intensity
                        channel_spectra[channel_index][pixel] += intensity
        except IPL.ImageFormatNotSupportedException:
            log.error(f"vertically_bin_image: unable to vertically bin {format_name}", exc_info=1)
            return None, None

        pathname_csv = f"idspeak/converted-{format_name}.csv"
        with open(pathname_csv, "w") as outfile:
            outfile.write("pixel, intensity, " + ", ".join(['chan_'+str(i) for i in range(channel_count)]) + "\n")
            for pixel, intensity in enumerate(spectrum):
                outfile.write(f"{pixel}, {spectrum[pixel]}")
                for i in range(channel_count):
                    outfile.write(f", {channel_spectra[i][pixel]}")
                outfile.write("\n")
            log.debug(f"  saved {pathname_csv} ({channel_count} channels)")

        return spectrum, asi

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
                # time.sleep(5)
                break

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
