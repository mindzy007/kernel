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
[pscustomobject]@{
  os = $os | Select-Object Caption, Version, BuildNumber, OSArchitecture
  cs = $cs | Select-Object Manufacturer, Model, TotalPhysicalMemory
  bb = $bb | Select-Object Manufacturer, Product
  bios = $bios | Select-Object SMBIOSBIOSVersion, Manufacturer
  cpu = @(Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed)
  gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate)
  ram = @(Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity, Speed, Manufacturer, PartNumber)
  disk = @(Get-CimInstance Win32_DiskDrive | Select-Object Model, Size)
} | ConvertTo-Json -Depth 4 -Compress
"""


def _list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _gb(n):
    return f"{n / 1024 ** 3:.0f} GB"


def _clean(s):
    return " ".join(str(s or "").split())


def collect():
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
                       capture_output=True, text=True, creationflags=NOWIN, timeout=60)
    data = json.loads(r.stdout)
    out = []

    def add(label, value):
        value = _clean(value)
        if value:
            out.append((label, value))

    os_ = data.get("os") or {}
    cs = data.get("cs") or {}
    bb = data.get("bb") or {}
    bios = data.get("bios") or {}
    add("OS", f"{_clean(os_.get('Caption'))} ({os_.get('OSArchitecture', '')}, build {os_.get('BuildNumber', '')})")
    add("PC", f"{_clean(cs.get('Manufacturer'))} {_clean(cs.get('Model'))}")
    add("Motherboard", f"{_clean(bb.get('Manufacturer'))} {_clean(bb.get('Product'))}")
    add("BIOS", _clean(bios.get("SMBIOSBIOSVersion")))

    cpus = _list(data.get("cpu"))
    for i, c in enumerate(cpus, 1):
        add("CPU" if len(cpus) == 1 else f"CPU {i}", c.get("Name"))
        add("Cores", f"{c.get('NumberOfCores')} cores, {c.get('NumberOfLogicalProcessors')} threads"
            if len(cpus) == 1 else f"CPU {i} cores: {c.get('NumberOfCores')} cores, {c.get('NumberOfLogicalProcessors')} threads")

    total = cs.get("TotalPhysicalMemory")
    if total:
        add("Memory", _gb(total))
    for i, m in enumerate(_list(data.get("ram")), 1):
        cap = m.get("Capacity")
        speed = f" @ {m['Speed']} MHz" if m.get("Speed") else ""
        add(f"RAM stick {i}", f"{_gb(cap) if cap else ''}{speed} {_clean(m.get('Manufacturer'))} {_clean(m.get('PartNumber'))}")

    gpus = _list(data.get("gpu"))
    for i, g in enumerate(gpus, 1):
        tag = "GPU" if len(gpus) == 1 else f"GPU {i}"
        add(tag, g.get("Name"))
        add(f"{tag} driver", g.get("DriverVersion"))
        if g.get("CurrentHorizontalResolution"):
            add(f"{tag} display",
                f"{g['CurrentHorizontalResolution']}x{g['CurrentVerticalResolution']} @ {g.get('CurrentRefreshRate')} Hz")

    for i, d in enumerate(_list(data.get("disk")), 1):
        size = d.get("Size")
        add(f"Disk {i}", f"{_clean(d.get('Model'))} ({_gb(int(size))})" if size else d.get("Model"))
    return out
