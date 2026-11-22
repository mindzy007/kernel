"""Detect this PC's hardware and OS details as (label, value) pairs."""
import ctypes
import json
import subprocess
from ctypes import wintypes

NOWIN = 0x08000000  # CREATE_NO_WINDOW

SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$bb = Get-CimInstance Win32_BaseBoard
$bios = Get-CimInstance Win32_BIOS
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[pscustomobject]@{
  os = $os | Select-Object Caption, Version, BuildNumber, OSArchitecture
  ver = $cv | Select-Object DisplayVersion, UBR
  cs = $cs | Select-Object Manufacturer, Model, TotalPhysicalMemory
  bb = $bb | Select-Object Manufacturer, Product
  bios = $bios | Select-Object SMBIOSBIOSVersion, Manufacturer
  cpu = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed)
  gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name, AdapterCompatibility, DriverVersion, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate)
  ram = @(Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity, Speed, ConfiguredClockSpeed, Manufacturer, PartNumber, DeviceLocator, SMBIOSMemoryType)
  disk = @(Get-CimInstance Win32_DiskDrive | Select-Object Model, Size)
  mon = @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID | ForEach-Object {
    [pscustomobject]@{ id = $_.InstanceName; name = (-join ($_.UserFriendlyName | Where-Object { $_ -ne 0 } | ForEach-Object { [char]$_ })) } })
} | ConvertTo-Json -Depth 4 -Compress
"""

UPDATE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$result = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
$required = @()
$optional = 0
foreach ($u in $result.Updates) {
  $cats = @($u.Categories | ForEach-Object { $_.Name })
  if ($cats -contains 'Definition Updates') { continue }
  if ($u.Type -eq 2 -or (-not $u.AutoSelectOnWebSites -and $cats -notcontains 'Upgrades')) { $optional++; continue }
  $required += $u.Title
}
[pscustomobject]@{
  count = $required.Count
  titles = @($required)
  optional = $optional
} | ConvertTo-Json -Compress
"""

MEMORY_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}


def _list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _gb(n):
    return f"{n / 1024 ** 3:.0f} GB"


def _clean(s):
    return " ".join(str(s or "").split())


def _gpu_driver(gpu):
    """Driver line labelled by vendor; only NVIDIA and AMD are reported."""
    text = f"{gpu.get('Name', '')} {gpu.get('AdapterCompatibility', '')}".lower()
    version = _clean(gpu.get("DriverVersion"))
    if "nvidia" in text or "geforce" in text:
        parts = version.split(".")
        if len(parts) == 4 and (parts[2] + parts[3]).isdigit():
            digits = (parts[2] + parts[3])[-5:]
            return f"NVIDIA {digits[:-2]}.{digits[-2:]} ({version})"
        return f"NVIDIA {version}"
    if "amd" in text or "radeon" in text or "advanced micro devices" in text:
        return f"AMD {version}"
    return "Not applicable"


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class _DEVMODE(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


def _displays(models=None):
    """Every monitor currently on the desktop as (is main, width, height, refresh Hz, name), main first.

    models maps a monitor's PnP id (like AUS27A1) to the model name stored in its EDID. Windows reports
    "Generic PnP Monitor" for monitors that have no specific driver, so that name replaces the generic one."""
    models = models or {}
    found = []
    try:
        user32 = ctypes.windll.user32
        i = 0
        while True:
            dev = _DISPLAY_DEVICE()
            dev.cb = ctypes.sizeof(dev)
            if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
                break
            i += 1
            if not dev.StateFlags & 0x1:  # not attached to the desktop
                continue
            mode = _DEVMODE()
            mode.dmSize = ctypes.sizeof(mode)
            if not user32.EnumDisplaySettingsW(dev.DeviceName, -1, ctypes.byref(mode)):  # -1 = current mode
                continue
            mon = _DISPLAY_DEVICE()
            mon.cb = ctypes.sizeof(mon)
            name = ""
            if user32.EnumDisplayDevicesW(dev.DeviceName, 0, ctypes.byref(mon), 0):
                name = _clean(mon.DeviceString)
                parts = mon.DeviceID.split("\\")  # MONITOR\AUS27A1\{guid}\0001
                edid_name = models.get(parts[1].upper()) if len(parts) > 1 else None
                if edid_name and (not name or name.lower().startswith("generic")):
                    name = edid_name
            found.append((bool(dev.StateFlags & 0x4), mode.dmPelsWidth, mode.dmPelsHeight,
                          mode.dmDisplayFrequency, name))
    except Exception:
        return []
    return sorted(found, key=lambda d: not d[0])


def collect():
    """Return (hardware (label, value) pairs, Windows version text)."""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
                       capture_output=True, text=True, creationflags=NOWIN, timeout=60)
    data = json.loads(r.stdout)
    out = []

    def add(label, value):
        value = "\n".join(_clean(line) for line in str(value or "").split("\n"))
        if value.strip():
            out.append((label, value))

    os_ = data.get("os") or {}
    ver = data.get("ver") or {}
    cs = data.get("cs") or {}
    bb = data.get("bb") or {}
    bios = data.get("bios") or {}
    add("PC", f"{_clean(cs.get('Manufacturer'))} {_clean(cs.get('Model'))}")
    board = f"{_clean(bb.get('Manufacturer'))} {_clean(bb.get('Product'))}".strip()
    bios_version = _clean(bios.get("SMBIOSBIOSVersion"))
    add("Motherboard", f"{board} · BIOS {bios_version}" if board and bios_version else board or bios_version)

    cpus = _list(data.get("cpu"))
    for i, c in enumerate(cpus, 1):
        add("CPU" if len(cpus) == 1 else f"CPU {i}", c.get("Name"))
        add("Cores" if len(cpus) == 1 else f"CPU {i} cores",
            f"{c.get('NumberOfCores')} cores, {c.get('NumberOfLogicalProcessors')} threads")

    lines = []
    total = cs.get("TotalPhysicalMemory")
    if total:
        lines.append(f"{_gb(total)} total")
    for i, m in enumerate(_list(data.get("ram")), 1):
        cap = m.get("Capacity")
        kind = MEMORY_TYPES.get(m.get("SMBIOSMemoryType"), "")
        speed = m.get("ConfiguredClockSpeed") or m.get("Speed")
        parts = [f"{_gb(cap)}" if cap else "", kind, f"@ {speed} MHz" if speed else ""]
        detail = " ".join(p for p in parts if p)
        maker = f"{_clean(m.get('Manufacturer'))} {_clean(m.get('PartNumber'))}".strip()
        slot = _clean(m.get("DeviceLocator"))
        lines.append(f"Module {i}: {detail}" + (f" · {maker}" if maker else "") + (f" ({slot})" if slot else ""))
    add("Memory", "\n".join(lines))

    gpus = _list(data.get("gpu"))
    models = {}
    for m in _list(data.get("mon")):
        parts = str(m.get("id") or "").split("\\")  # DISPLAY\AUS27A1\5&...
        if len(parts) > 1 and _clean(m.get("name")):
            models[parts[1].upper()] = _clean(m.get("name"))
    displays = _displays(models)
    for i, g in enumerate(gpus, 1):
        tag = "GPU" if len(gpus) == 1 else f"GPU {i}"
        add(tag, g.get("Name"))
        add(f"{tag} driver", _gpu_driver(g))
        if not displays and g.get("CurrentHorizontalResolution"):
            add(f"{tag} display",
                f"{g['CurrentHorizontalResolution']}x{g['CurrentVerticalResolution']} @ {g.get('CurrentRefreshRate')} Hz")
    for i, (main, width, height, hz, name) in enumerate(displays, 1):
        detail = f"{width}x{height} @ {hz} Hz"
        extras = [p for p in (name, "main" if main and len(displays) > 1 else "") if p]
        add("Display" if len(displays) == 1 else f"Display {i}", detail + (f" ({', '.join(extras)})" if extras else ""))

    for i, d in enumerate(_list(data.get("disk")), 1):
        size = d.get("Size")
        add(f"Disk {i}", f"{_clean(d.get('Model'))} ({_gb(int(size))})" if size else d.get("Model"))

    name = _clean(os_.get("Caption")).replace("Microsoft ", "", 1)
    build = os_.get("BuildNumber") or ""
    if build and ver.get("UBR") is not None:
        build = f"{build}.{ver['UBR']}"
    windows = " · ".join(p for p in (
        name, _clean(ver.get("DisplayVersion")), f"build {build}" if build else "",
        _clean(os_.get("OSArchitecture"))) if p)
    return out, windows


def check_updates():
    """Return (required count, required titles, optional count) like the Settings page, or None if the check failed.

    Settings only lists required updates on its main page; drivers and other optional updates sit under
    Advanced options > Optional updates, and Defender definition updates are never listed.
    """
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", UPDATE_SCRIPT],
                           capture_output=True, text=True, creationflags=NOWIN, timeout=180)
        data = json.loads(r.stdout)
        return (int(data.get("count") or 0), [_clean(t) for t in _list(data.get("titles"))],
                int(data.get("optional") or 0))
    except Exception:
        return None
