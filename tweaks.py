"""Tweak implementations. Each task takes a log function."""
import ctypes
import glob
import os
import shutil
import subprocess
import winreg

HKCU, HKLM = winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE
NOWIN = 0x08000000  # CREATE_NO_WINDOW


def set_reg(root, path, name, value):
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN, shell=False)
    return (r.stdout + r.stderr).strip()


def t_bing(log):
    set_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", 0)
    log("Bing web results disabled in Start search (sign out/in or restart Explorer to apply).")


def t_group_policy(log):
    pkgs = []
    for pat in ("ClientExtensions", "ClientTools"):
        pkgs += glob.glob(os.path.expandvars(
            rf"%SystemRoot%\servicing\Packages\Microsoft-Windows-GroupPolicy-{pat}-Package~3*.mum"))
    if not pkgs:
        log("No Group Policy packages found (already Pro/Enterprise?).")
        return
    for p in pkgs:
        log(f"Installing {os.path.basename(p)}")
        log(run(["dism", "/online", "/norestart", f"/add-package:{p}"])[-300:])


def _clean_dir(path, log):
    count = 0
    if not os.path.isdir(path):
        return
    for entry in os.scandir(path):
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.remove(entry.path)
            count += 1
        except Exception:
            pass
    log(f"{path}: removed {count} items")


def t_temp(log):
    _clean_dir(os.environ.get("TEMP", ""), log)
    _clean_dir(os.path.expandvars(r"%SystemRoot%\Temp"), log)
    _clean_dir(os.path.expandvars(r"%SystemRoot%\Prefetch"), log)
    run(["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"])
    log("Recycle Bin emptied.")


def t_ads(log):
    cdm = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
    for n in ("SubscribedContent-338388Enabled", "SubscribedContent-338389Enabled",
              "SubscribedContent-353694Enabled", "SubscribedContent-353696Enabled",
              "SystemPaneSuggestionsEnabled", "SoftLandingEnabled", "SilentInstalledAppsEnabled"):
        set_reg(HKCU, cdm, n, 0)
    set_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", 0)
    log("Suggestions, tips and advertising ID disabled.")


def t_telemetry(log):
    set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 0)
    log("Telemetry set to minimum (Security level; Home/Pro may treat as Required).")


def t_widgets(log):
    # TaskbarDa is blocked by Windows' UCPD driver on current builds, so use the policy first.
    adv = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    steps = [
        ("policy", lambda: set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests", 0)),
        ("TaskbarDa", lambda: set_reg(HKCU, adv, "TaskbarDa", 0)),
        ("TaskbarMn", lambda: set_reg(HKCU, adv, "TaskbarMn", 0)),
    ]
    ok = False
    for name, fn in steps:
        try:
            fn()
            ok = True
            log(f"{name}: set")
        except OSError as e:
            log(f"{name}: blocked ({e.strerror})")
    out = run(["powershell", "-NoProfile", "-Command",
               "Get-AppxPackage -AllUsers *WebExperience* | Remove-AppxPackage -AllUsers"])
    log("Widgets package removed." if not out else f"Widgets package: {out[-200:]}")
    run(["taskkill", "/f", "/im", "explorer.exe"])
    subprocess.Popen(["explorer.exe"], creationflags=NOWIN)
    log("Explorer restarted.")


def t_power(log):
    log(run(["powercfg", "/setactive", "SCHEME_MIN"]) or "High performance power plan activated.")


def t_time_format(log):
    with winreg.CreateKeyEx(HKCU, r"Control Panel\International", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "sShortDate", 0, winreg.REG_SZ, "MMM d, yyyy")
    ctypes.windll.user32.SendMessageTimeoutW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "intl", 2, 2000, None)
    log("Taskbar date now shows like 'Oct 27, 2026'.")


def set_str(root, path, name, value):
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def broadcast_setting(area):
    ctypes.windll.user32.SendMessageTimeoutW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p,
        ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, area, 2, 2000, None)


def t_end_task(log):
    set_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced\TaskbarDeveloperSettings", "TaskbarEndTask", 1)
    run(["taskkill", "/f", "/im", "explorer.exe"])
    subprocess.Popen(["explorer.exe"], creationflags=NOWIN)
    log("'End task' enabled in the taskbar right-click menu (Explorer restarted).")


def t_mouse_accel(log):
    key = r"Control Panel\Mouse"
    for n in ("MouseSpeed", "MouseThreshold1", "MouseThreshold2"):
        set_str(HKCU, key, n, "0")
    params = (ctypes.c_int * 3)(0, 0, 0)
    ctypes.windll.user32.SystemParametersInfoW(0x0004, 0, params, 3)
    log("Enhance pointer precision turned off.")


def t_smart_app_control(log):
    set_reg(HKLM, r"SYSTEM\CurrentControlSet\Control\CI\Policy", "VerifiedAndReputablePolicyState", 0)
    log("Smart App Control turned off (restart required; Windows won't let you turn it back on without a reset).")


def t_sounds(log):
    base = r"AppEvents\Schemes"
    set_str(HKCU, base, "", ".None")
    n = 0
    with winreg.OpenKey(HKCU, base + r"\Apps") as apps:
        for i in range(winreg.QueryInfoKey(apps)[0]):
            app = winreg.EnumKey(apps, i)
            with winreg.OpenKey(apps, app) as a:
                for j in range(winreg.QueryInfoKey(a)[0]):
                    ev = winreg.EnumKey(a, j)
                    try:
                        with winreg.OpenKey(a, ev + r"\.Current", 0, winreg.KEY_SET_VALUE) as c:
                            winreg.SetValueEx(c, "", 0, winreg.REG_SZ, "")
                            n += 1
                    except OSError:
                        pass
    try:
        set_reg(HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI\BootAnimation",
                "DisableStartupSound", 1)
        set_reg(HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\EditionOverrides",
                "UserSetting_DisableStartupSound", 1)
    except OSError as e:
        log(f"Startup sound: blocked ({e.strerror})")
    log(f"Sound scheme set to 'No Sounds' ({n} events cleared) and startup sound disabled.")


def t_sticky_keys(log):
    key = r"Control Panel\Accessibility"
    set_str(HKCU, key + r"\StickyKeys", "Flags", "506")
    set_str(HKCU, key + r"\ToggleKeys", "Flags", "58")
    set_str(HKCU, key + r"\Keyboard Response", "Flags", "122")
    sk = (ctypes.c_uint * 2)(8, 506)
    ctypes.windll.user32.SystemParametersInfoW(0x003B, 8, sk, 3)
    log("Sticky/Toggle/Filter Keys shortcut prompts disabled.")


def t_nearby(log):
    cdp = r"Software\Microsoft\Windows\CurrentVersion\CDP"
    set_reg(HKCU, cdp, "CdpSessionUserAuthzPolicy", 0)
    set_reg(HKCU, cdp, "NearShareChannelUxMode", 0)
    set_reg(HKCU, cdp, "RomeSdkChannelUserAuthzPolicy", 0)
    log("Nearby sharing turned off.")


def t_core_isolation(log):
    set_reg(HKLM, r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
            "Enabled", 0)
    log("Core isolation > Memory integrity turned off (restart required).")


def t_recall(log):
    set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\WindowsAI", "DisableAIDataAnalysis", 1)
    out = run(["dism", "/online", "/disable-feature", "/featurename:Recall", "/norestart"])
    log("Recall policy set.")
    log(out[-300:] if out else "DISM returned no output.")


def t_game_mode(log):
    gb = r"Software\Microsoft\GameBar"
    set_reg(HKCU, gb, "AutoGameModeEnabled", 1)
    set_reg(HKCU, gb, "AllowAutoGameMode", 1)
    log("Game Mode turned on.")


MACTYPE_PROFILE = "XMac.LCD.HotShift@XHei(iOS^MacOS)"


def t_mactype(log):
    base = next((d for d in (os.path.expandvars(r"%ProgramFiles%\MacType"),
                             os.path.expandvars(r"%ProgramFiles(x86)%\MacType"))
                 if os.path.isfile(os.path.join(d, "MacTray.exe"))), None)
    if not base:
        raise RuntimeError("MacType is not installed. Install it from the Applications tab first.")
    ini_dir = os.path.join(base, "ini")
    prof = None
    for root, _, files in os.walk(ini_dir):
        if MACTYPE_PROFILE + ".ini" in files:
            prof = os.path.relpath(os.path.join(root, MACTYPE_PROFILE + ".ini"), base)
            break
    if not prof:
        raise RuntimeError(f"MacType profile '{MACTYPE_PROFILE}' was not found in {ini_dir}.")
    path = os.path.join(base, "MacType.ini")
    with open(path, "rb") as f:
        raw = f.read()
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    lines = raw.decode(enc).splitlines()
    sect, seen = "", set()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("["):
            sect = s.lower()
        for key, val in (("AlternativeFile", prof), ("LoadType", "1"), ("AutoRun", "1")):
            want = "[general]" if key == "AlternativeFile" else "[mactype]"
            if sect == want and s.lower().startswith(key.lower() + "="):
                lines[i] = f"{key}={val}"
                seen.add(key)
    if "AlternativeFile" not in seen:
        lines.insert(lines.index("[General]") + 1 if "[General]" in lines else 0, f"AlternativeFile={prof}")
    with open(path, "wb") as f:
        f.write(("\r\n".join(lines) + "\r\n").encode("utf-16" if enc == "utf-16" else "utf-8"))
    set_str(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Run", "MacType", f'"{os.path.join(base, "MacTray.exe")}"')
    run(["taskkill", "/f", "/im", "MacTray.exe"])
    subprocess.Popen([os.path.join(base, "MacTray.exe")], cwd=base, creationflags=NOWIN)
    log(f"MacType profile set to {MACTYPE_PROFILE}; tray app started and added to startup.")


def t_notepad(log):
    set_reg(HKLM, r"SOFTWARE\Policies\WindowsNotepad", "DisableAIFeatures", 1)
    log("Notepad AI/Copilot features disabled by policy.")
    pkg = os.path.expandvars(r"%LOCALAPPDATA%\Packages\Microsoft.WindowsNotepad_8wekyb3d8bbwe\Settings\settings.dat")
    if not os.path.isfile(pkg):
        raise RuntimeError("Notepad settings not found. Open Notepad once, then try again.")
    run(["taskkill", "/f", "/im", "notepad.exe"])
    hive = "kernel_notepad"
    out = run(["reg", "load", f"HKLM\\{hive}", pkg])
    if "success" not in out.lower():
        raise RuntimeError(f"Could not open Notepad settings: {out}")
    try:
        import struct
        import time
        stamp = struct.pack("<Q", int(time.time() * 1e7) + 116444736000000000)
        wanted = {"FontFamily": "Consolas", "FontSize": 14, "FontStyle": "Regular"}

        def apply(key):
            for name, val in wanted.items():
                try:
                    old, typ = winreg.QueryValueEx(key, name)
                except OSError:
                    old, typ = None, None
                tail = old[-8:] if isinstance(old, bytes) and len(old) >= 8 else stamp
                if name == "FontSize":
                    winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, struct.pack("<i", val) + tail)
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, val.encode("utf-16-le") + b"\0\0" + tail)

        found = []

        def walk(path):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
                names = [winreg.EnumValue(k, i)[0] for i in range(winreg.QueryInfoKey(k)[1])]
                if "FontSize" in names or "FontFamily" in names:
                    apply(k)
                    found.append(path)
                for i in range(winreg.QueryInfoKey(k)[0]):
                    walk(path + "\\" + winreg.EnumKey(k, i))

        walk(hive)
        if not found:
            with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, hive + r"\LocalState", 0,
                                    winreg.KEY_SET_VALUE) as k:
                apply(k)
        log("Notepad font set to Consolas, Regular, 14.")
    finally:
        import gc
        gc.collect()
        run(["reg", "unload", f"HKLM\\{hive}"])

CURSOR_SLOTS = [
    ("Arrow", "arrow.cur"), ("Help", "helpsel.cur"), ("AppStarting", "working.ani"), ("Wait", "busy.ani"),
    ("Crosshair", "cross.cur"), ("IBeam", "text.cur"), ("NWPen", "pen.cur"), ("No", "unavail.cur"),
    ("SizeNS", "ns.cur"), ("SizeWE", "ew.cur"), ("SizeNWSE", "nwse.cur"), ("SizeNESW", "nesw.cur"),
    ("SizeAll", "move.cur"), ("UpArrow", "arrow_i.cur"), ("Hand", "link.cur"),
    ("Person", "person.cur"), ("Pin", "pin.cur"),
]


def t_cursor(log):
    import sys
    import zipfile
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    z = os.path.join(base, "assets", "bigsur.zip")
    if not os.path.isfile(z):
        raise RuntimeError("Cursor pack is missing from the app files.")
    dest = os.path.expandvars(r"%SystemRoot%\Cursors\Big Sur")
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(dest)
    log(f"Cursor files copied to {dest}")
    key = r"Control Panel\Cursors"
    paths = [os.path.join(dest, f) for _, f in CURSOR_SLOTS]
    with winreg.CreateKeyEx(HKCU, key, 0, winreg.KEY_SET_VALUE) as k:
        for (name, _), p in zip(CURSOR_SLOTS, paths):
            winreg.SetValueEx(k, name, 0, winreg.REG_EXPAND_SZ, p)
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Big Sur")
        winreg.SetValueEx(k, "Scheme Source", 0, winreg.REG_DWORD, 1)
    with winreg.CreateKeyEx(HKCU, key + r"\Schemes", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Big Sur", 0, winreg.REG_SZ, ",".join(paths))
    ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 3)
    log("Big Sur cursor scheme installed and applied.")

def t_nvidia_profile(log):
    import sys
    import urllib.request
    import zipfile
    import apps
    if not glob.glob(os.path.expandvars(r"%SystemRoot%\System32\nvapi64.dll")):
        raise RuntimeError("No NVIDIA driver found. Install the NVIDIA driver first.")
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    nip = os.path.join(base, "assets", "profile.nip")
    tool_dir = os.path.join(os.environ.get("APPDATA", ""), "kernel", "npi")
    exe = next(iter(glob.glob(os.path.join(tool_dir, "**", "nvidiaProfileInspector.exe"), recursive=True)), None)
    if not exe:
        log("Downloading NVIDIA Profile Inspector...")
        info = apps.show("Orbmu2k.nvidiaProfileInspector")
        if not info.get("url"):
            raise RuntimeError("Could not look up NVIDIA Profile Inspector (is winget working?).")
        os.makedirs(tool_dir, exist_ok=True)
        zpath = os.path.join(tool_dir, "npi.zip")
        req = urllib.request.Request(info["url"], headers={"User-Agent": "kernel/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            shutil.copyfileobj(r, f)
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tool_dir)
        os.remove(zpath)
        exe = next(iter(glob.glob(os.path.join(tool_dir, "**", "nvidiaProfileInspector.exe"), recursive=True)), None)
        if not exe:
            raise RuntimeError("nvidiaProfileInspector.exe was not found in the download.")
    log("Importing your NVIDIA profile...")
    r = subprocess.run([exe, "-silent", nip], capture_output=True, text=True,
                       creationflags=NOWIN, timeout=120, cwd=os.path.dirname(exe))
    if r.returncode not in (0, None):
        raise RuntimeError(f"Profile Inspector exited with code {r.returncode}. {(r.stdout + r.stderr).strip()[-200:]}")
    log("NVIDIA profile imported.")

# Run-once actions shown as buttons instead of toggles
ONE_SHOT = {"t_temp", "t_group_policy", "t_mactype", "t_notepad", "t_cursor", "t_nvidia_profile"}

# (section, title, description, function)
TASKS = [
    ("Privacy", "Remove Bing from search", "Stop web results in Start menu search", t_bing),
    ("Privacy", "Disable ads & suggestions", "Tips, suggested apps and advertising ID", t_ads),
    ("Privacy", "Minimize telemetry", "Reduce diagnostic data collection", t_telemetry),
    ("System", "Clean temp files", "Temp, Prefetch and Recycle Bin", t_temp),
    ("System", "Group Policy Editor", "Install gpedit on Windows Home", t_group_policy),
    ("System", "High performance", "Activate the maximum performance power plan", t_power),
    ("Appearance", "Hide Widgets & Chat", "Remove the taskbar buttons", t_widgets),
    ("Appearance", "Taskbar date format", "Show the date as MMM d, yyyy (e.g. Dec 31, 2030)", t_time_format),
    ("Appearance", "Enable End Task in taskbar", "Adds 'End task' to the right-click menu of taskbar apps", t_end_task),
    ("Appearance", "Disable Windows sounds", "Sound scheme to 'No Sounds' and no startup chime", t_sounds),
    ("Appearance", "Apply MacType in system tray", "Use the XMac LCD HotShift profile (MacType must be installed)", t_mactype),
    ("Appearance", "Big Sur cursors", "Install the Big Sur cursor pack and apply it as your cursor scheme", t_cursor),
    ("Gaming", "NVIDIA profile", "Import your saved NVIDIA Profile Inspector settings", t_nvidia_profile),
    ("Apps", "Notepad settings", "Consolas Regular 14 and Notepad's Copilot/AI turned off", t_notepad),
    ("Input", "Disable enhanced mouse precision", "Turns off pointer acceleration for consistent aim", t_mouse_accel),
    ("Input", "Disable Sticky Keys", "Stops the popup when Shift is pressed five times", t_sticky_keys),
    ("Gaming", "Enable Game Mode", "Prioritizes games and limits background activity", t_game_mode),
    ("Security", "Disable Smart App Control", "Stops blocking unsigned or unknown apps (permanent until Windows reset)", t_smart_app_control),
    ("Security", "Disable Core isolation", "Turn off Memory integrity (HVCI) for better performance", t_core_isolation),
    ("Privacy", "Disable Nearby sharing", "Stops sharing with nearby PCs", t_nearby),
    ("Privacy", "Disable Recall", "Policy plus DISM /Disable-Feature Recall", t_recall),
]
