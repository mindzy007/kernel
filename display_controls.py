"""NVIDIA NVAPI display controls and primary-display refresh-rate helpers."""
import ctypes
import math
from ctypes import wintypes


class DisplayError(RuntimeError):
    pass


class NVIDIAUnavailable(DisplayError):
    pass


class UnsupportedDisplay(DisplayError):
    pass


class DIGITAL_VIBRANCE_INFO(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("current_level", ctypes.c_int32),
        ("minimum_level", ctypes.c_int32),
        ("maximum_level", ctypes.c_int32),
        ("default_level", ctypes.c_int32),
    ]


class GAMMA_CORRECTION_EX(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("gamma_ramp", ctypes.c_float * (1024 * 3)),
        ("unknown", ctypes.c_uint32),
    ]


class NVIDIAControl:
    def __init__(self):
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise NVIDIAUnavailable("NVIDIA display controls require a 64-bit build.")
        try:
            self._dll = ctypes.WinDLL("nvapi64.dll")
            query = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint32)(
                ("nvapi_QueryInterface", self._dll))
        except (AttributeError, OSError) as ex:
            raise NVIDIAUnavailable("The NVIDIA display driver NVAPI is unavailable.") from ex

        self._initialize = self._function(query, 0x0150E828, ctypes.c_int)
        status = self._initialize()
        if status != 0:
            raise NVIDIAUnavailable(f"NVIDIA NVAPI initialization failed (status {status}).")

        self._enum_display = self._function(
            query, 0x9ABDD40D, ctypes.c_int, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p))
        self._get_display_name = self._function(
            query, 0x22A78B05, ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p)
        self._get_display_id = self._function(
            query, 0xAE457190, ctypes.c_int, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32))
        self._get_vibrance = self._function(
            query, 0x0E45002D, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(DIGITAL_VIBRANCE_INFO), optional=True)
        self._set_vibrance = self._function(
            query, 0x4A82C2B1, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(DIGITAL_VIBRANCE_INFO), optional=True)
        self._set_gamma = self._function(
            query, 0x7082A053, ctypes.c_int, ctypes.c_uint32,
            ctypes.POINTER(GAMMA_CORRECTION_EX), optional=True)

        try:
            primary = _primary_device_name()
        except OSError as ex:
            raise NVIDIAUnavailable("Windows could not identify the primary display.") from ex
        if not primary:
            raise NVIDIAUnavailable("Windows did not report a primary display.")
        self.display_name = primary
        self.display_handle = None
        for index in range(32):
            handle = ctypes.c_void_p()
            status = self._enum_display(index, ctypes.byref(handle))
            if status != 0:
                break
            name = ctypes.create_string_buffer(64)
            status = self._get_display_name(handle, name)
            if status == 0 and name.value.decode("ascii", errors="replace").casefold() == primary.casefold():
                self.display_handle = handle
                break
        if self.display_handle is None:
            raise NVIDIAUnavailable(
                "The primary display is not connected to an active NVIDIA display output.")

        self.display_id = ctypes.c_uint32()
        status = self._get_display_id(
            primary.encode("ascii"), ctypes.byref(self.display_id))
        if status != 0:
            raise NVIDIAUnavailable(
                f"NVIDIA could not identify the primary display (status {status}).")

    @property
    def supports_gamma_contrast(self):
        return self._set_gamma is not None

    @property
    def supports_vibrance(self):
        return self._get_vibrance is not None and self._set_vibrance is not None

    @staticmethod
    def _function(query, identifier, restype, *argtypes, optional=False):
        address = query(identifier)
        if not address:
            if optional:
                return None
            raise NVIDIAUnavailable(f"An NVIDIA NVAPI function is unavailable ({identifier:#010x}).")
        return ctypes.CFUNCTYPE(restype, *argtypes)(address)

    def vibrance_info(self):
        info = self._read_vibrance_info()
        return info.minimum_level, info.maximum_level, info.current_level

    def _read_vibrance_info(self):
        if self._get_vibrance is None:
            raise UnsupportedDisplay("NVIDIA digital-vibrance control is unavailable.")
        info = DIGITAL_VIBRANCE_INFO()
        info.version = ctypes.sizeof(info) | (1 << 16)
        status = self._get_vibrance(
            self.display_handle, 0, ctypes.byref(info))
        if status != 0:
            raise UnsupportedDisplay(
                f"NVIDIA could not read digital vibrance (status {status}).")
        return info

    def set_vibrance(self, level):
        if not self.supports_vibrance:
            raise UnsupportedDisplay("NVIDIA digital-vibrance control is unavailable.")
        info = self._read_vibrance_info()
        if level < info.minimum_level or level > info.maximum_level:
            raise DisplayError("The digital-vibrance value is outside the NVIDIA-supported range.")
        info.current_level = level
        status = self._set_vibrance(
            self.display_handle, 0, ctypes.byref(info))
        if status != 0:
            raise DisplayError(f"NVIDIA could not set digital vibrance (status {status}).")

    def set_gamma_contrast(self, gamma, contrast):
        if self._set_gamma is None:
            raise UnsupportedDisplay("NVIDIA gamma-correction control is unavailable.")
        if not math.isfinite(gamma) or not 0.4 <= gamma <= 2.8:
            raise DisplayError("Gamma must be between 0.4 and 2.8.")
        if not 0 <= contrast <= 100:
            raise DisplayError("Contrast must be between 0 and 100.")

        data = GAMMA_CORRECTION_EX()
        data.version = ctypes.sizeof(data) | (1 << 16)
        data.unknown = 1
        contrast_curve = (80.0 + contrast / 100.0 * 40.0 - 100.0) / 100.0
        gamma_exponent = 1.0 / gamma
        for index in range(1024):
            x = index / 1023.0
            value = ((contrast_curve + 1.0) * (x - 0.5)
                     if contrast_curve <= 0.0
                     else (x - 0.5) / (1.0 - contrast_curve))
            value = min(1.0, max(0.0, value + 0.5))
            value = math.pow(value, gamma_exponent)
            channel_value = ctypes.c_float(value).value
            for channel in range(3):
                data.gamma_ramp[index * 3 + channel] = channel_value
        status = self._set_gamma(self.display_id, ctypes.byref(data))
        if status != 0:
            raise DisplayError(f"NVIDIA could not apply gamma or contrast (status {status}).")


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("device_name", wintypes.WCHAR * 32),
        ("spec_version", wintypes.WORD),
        ("driver_version", wintypes.WORD),
        ("size", wintypes.WORD),
        ("driver_extra", wintypes.WORD),
        ("fields", wintypes.DWORD),
        ("orientation", wintypes.SHORT),
        ("paper_size", wintypes.SHORT),
        ("paper_length", wintypes.SHORT),
        ("paper_width", wintypes.SHORT),
        ("scale", wintypes.SHORT),
        ("copies", wintypes.SHORT),
        ("default_source", wintypes.SHORT),
        ("print_quality", wintypes.SHORT),
        ("color", wintypes.SHORT),
        ("duplex", wintypes.SHORT),
        ("y_resolution", wintypes.SHORT),
        ("tt_option", wintypes.SHORT),
        ("collate", wintypes.SHORT),
        ("form_name", wintypes.WCHAR * 32),
        ("log_pixels", wintypes.WORD),
        ("bits_per_pixel", wintypes.DWORD),
        ("width", wintypes.DWORD),
        ("height", wintypes.DWORD),
        ("display_flags", wintypes.DWORD),
        ("frequency", wintypes.DWORD),
        ("icm_method", wintypes.DWORD),
        ("icm_intent", wintypes.DWORD),
        ("media_type", wintypes.DWORD),
        ("dither_type", wintypes.DWORD),
        ("reserved1", wintypes.DWORD),
        ("reserved2", wintypes.DWORD),
        ("panning_width", wintypes.DWORD),
        ("panning_height", wintypes.DWORD),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("device_name", wintypes.WCHAR * 32),
        ("device_string", wintypes.WCHAR * 128),
        ("state_flags", wintypes.DWORD),
        ("device_id", wintypes.WCHAR * 128),
        ("device_key", wintypes.WCHAR * 128),
    ]


ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
CDS_TEST = 0x2
CDS_UPDATEREGISTRY = 0x1
DM_DISPLAYFREQUENCY = 0x400000
DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DISP_CHANGE_SUCCESSFUL = 0


def _primary_device_name():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplayDevicesW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    device = DISPLAY_DEVICEW()
    device.cb = ctypes.sizeof(device)
    index = 0
    while user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
        if device.state_flags & DISPLAY_DEVICE_PRIMARY_DEVICE:
            return device.device_name
        index += 1
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(device)
    return None


def _mode(device, index):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplaySettingsW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
    user32.EnumDisplaySettingsW.restype = wintypes.BOOL
    mode = DEVMODEW()
    mode.size = ctypes.sizeof(mode)
    if not user32.EnumDisplaySettingsW(device, index, ctypes.byref(mode)):
        return None
    return mode


def refresh_info():
    device = _primary_device_name()
    current = _mode(device, ENUM_CURRENT_SETTINGS)
    if current is None:
        raise DisplayError("Windows could not read the primary display's current mode.")
    modes = []
    index = 0
    while True:
        mode = _mode(device, index)
        if mode is None:
            break
        if (mode.width == current.width and mode.height == current.height
                and mode.bits_per_pixel == current.bits_per_pixel
                and mode.frequency > 0):
            modes.append(mode)
        index += 1
    if not modes:
        raise DisplayError("Windows did not report any refresh rates for the current resolution.")
    highest = max(modes, key=lambda mode: mode.frequency)
    return {
        "device": device,
        "width": current.width,
        "height": current.height,
        "current": current.frequency,
        "highest": highest.frequency,
        "mode": highest,
    }


def apply_highest_refresh(expected_device=None):
    info = refresh_info()
    if expected_device and info["device"].casefold() != expected_device.casefold():
        raise DisplayError("The primary display is no longer connected to the active NVIDIA output.")
    if info["highest"] <= info["current"]:
        return info
    mode = info["mode"]
    mode.fields |= DM_DISPLAYFREQUENCY | DM_PELSWIDTH | DM_PELSHEIGHT
    change = ctypes.WinDLL("user32", use_last_error=True).ChangeDisplaySettingsExW
    change.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(DEVMODEW), wintypes.HWND, wintypes.DWORD, wintypes.LPVOID]
    change.restype = wintypes.LONG
    tested = change(info["device"], ctypes.byref(mode), None, CDS_TEST, None)
    if tested != DISP_CHANGE_SUCCESSFUL:
        raise DisplayError(f"Windows rejected the highest refresh rate (display status {tested}).")
    applied = change(info["device"], ctypes.byref(mode), None, CDS_UPDATEREGISTRY, None)
    if applied != DISP_CHANGE_SUCCESSFUL:
        raise DisplayError(f"Windows could not apply the highest refresh rate (display status {applied}).")
    return refresh_info()
