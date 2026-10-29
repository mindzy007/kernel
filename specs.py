"""Detect this PC's hardware and OS details as (label, value) pairs."""
import json
import subprocess

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
} | ConvertTo-Json -Depth 4 -Compress
"""

UPDATE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$result = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
[pscustomobject]@{
  count = $result.Updates.Count
  titles = @($result.Updates | ForEach-Object { $_.Title })
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
    add("Motherboard", f"{_clean(bb.get('Manufacturer'))} {_clean(bb.get('Product'))}")
    add("BIOS", _clean(bios.get("SMBIOSBIOSVersion")))

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
    for i, g in enumerate(gpus, 1):
        tag = "GPU" if len(gpus) == 1 else f"GPU {i}"
        add(tag, g.get("Name"))
        add(f"{tag} driver", _gpu_driver(g))
        if g.get("CurrentHorizontalResolution"):
            add(f"{tag} display",
                f"{g['CurrentHorizontalResolution']}x{g['CurrentVerticalResolution']} @ {g.get('CurrentRefreshRate')} Hz")

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
    """Return (count, titles) of pending Windows updates, or None if the check failed."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", UPDATE_SCRIPT],
                           capture_output=True, text=True, creationflags=NOWIN, timeout=180)
        data = json.loads(r.stdout)
        return int(data.get("count") or 0), [_clean(t) for t in _list(data.get("titles"))]
    except Exception:
        return None
