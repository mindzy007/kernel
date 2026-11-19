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

        self._displays = {}
        for index in range(32):
            handle = ctypes.c_void_p()
            if self._enum_display(index, ctypes.byref(handle)) != 0:
                break
            name = ctypes.create_string_buffer(64)
            if self._get_display_name(handle, name) != 0:
                continue
            display_id = ctypes.c_uint32()
            if self._get_display_id(name.value, ctypes.byref(display_id)) != 0:
                continue
            text = name.value.decode("ascii", errors="replace")
            self._displays[text.casefold()] = (text, handle, display_id)
        if not self._displays:
            raise NVIDIAUnavailable("No display is connected to an active NVIDIA display output.")

        try:
            primary = _primary_device_name()
        except OSError:
            primary = None
        if not self.select(primary):
            self.select(next(iter(self._displays.values()))[0])

    @property
    def display_names(self):
        return [entry[0] for entry in self._displays.values()]

    def select(self, name):
        """Make `name` the display that gamma/contrast/vibrance calls act on. False if it is not on an NVIDIA output."""
        entry = self._displays.get((name or "").casefold())
        if entry is None:
            return False
        self.display_name, self.display_handle, self.display_id = entry
        return True

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
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
DISPLAY_DEVICE_MIRRORING_DRIVER = 0x8
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


def _enum_display_devices(parent, flags=0):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumDisplayDevicesW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    index = 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(device)
        if not user32.EnumDisplayDevicesW(parent, index, ctypes.byref(device), flags):
            return
        yield device
        index += 1


def _edid_model(edid):
    """Monitor model name from the descriptor block (tag 0xFC) of an EDID."""
    for offset in (54, 72, 90, 108):
        block = bytes(edid[offset:offset + 18])
        if len(block) == 18 and block[:4] == b"\x00\x00\x00\xfc":
            return block[5:].split(b"\n")[0].decode("ascii", errors="ignore").strip() or None
    return None


def _monitor_model(adapter_name):
    """Model name (e.g. 'LG ULTRAGEAR') of the monitor on a display adapter, read from its EDID."""
    import winreg
    for monitor in _enum_display_devices(adapter_name, 1):  # 1 = EDD_GET_DEVICE_INTERFACE_NAME
        parts = monitor.device_id.split("#")  # \?\DISPLAY#<hardware id>#<instance>#{guid}
        if len(parts) >= 3:
            path = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{parts[1]}\{parts[2]}\Device Parameters"
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                    model = _edid_model(winreg.QueryValueEx(key, "EDID")[0])
            except OSError:
                model = None
            if model:
                return model
        generic = monitor.device_string.strip()
        if generic and "generic" not in generic.casefold():
            return generic
        break
    return None


def get_all_displays():
    """[(Windows device name, readable label)] for every display attached to the desktop."""
    found = []
    for adapter in _enum_display_devices(None):
        if not adapter.state_flags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP or adapter.state_flags & DISPLAY_DEVICE_MIRRORING_DRIVER:
            continue
        number = "".join(ch for ch in adapter.device_name if ch.isdigit()) or "?"
        try:
            model = _monitor_model(adapter.device_name)
        except OSError:
            model = None
        found.append((adapter.device_name, model or f"Display {number}", number))
    labels = [label for _, label, _ in found]
    return [(name, f"{label} ({number})" if labels.count(label) > 1 else label)
            for name, label, number in found]


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


def refresh_info(device=None):
    device = device or _primary_device_name()
    if not device:
        raise DisplayError("Windows did not report a display.")
    current = _mode(device, ENUM_CURRENT_SETTINGS)
    if current is None:
        raise DisplayError("Windows could not read the display's current mode.")
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


def apply_highest_refresh(device=None):
    info = refresh_info(device)
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
    return refresh_info(info["device"])
