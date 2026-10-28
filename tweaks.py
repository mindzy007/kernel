"""Tweak implementations. Each task takes a log function."""
import ctypes
import glob
import os
import re
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


WIDGETS_UI_SCRIPT = r'''
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$A=[Windows.Automation.AutomationElement]
$want=%s
Start-Process "ms-settings:taskbar"
$win=$null; $sw=$null
for($i=0;$i -lt 40 -and -not $sw;$i++){
  Start-Sleep -Milliseconds 400
  $win=$A::RootElement.FindFirst('Children',(New-Object Windows.Automation.PropertyCondition($A::NameProperty,'Settings')))
  if($win){$sw=$win.FindFirst('Descendants',(New-Object Windows.Automation.PropertyCondition($A::AutomationIdProperty,'SystemSettings_DesktopTaskbar_Da_ToggleSwitch')))}
}
if(-not $sw){'NOTFOUND';exit}
$p=$sw.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
$cur=[int]$p.Current.ToggleState
if($cur -ne $want){$p.Toggle(); Start-Sleep -Milliseconds 800}
"STATE=" + [int]$p.Current.ToggleState
try{$win.GetCurrentPattern([Windows.Automation.WindowPattern]::Pattern).Close()}catch{}
'''


def _set_widgets_toggle(on, log):
    out = run(["powershell", "-NoProfile", "-Command", WIDGETS_UI_SCRIPT % (1 if on else 0)])
    if f"STATE={1 if on else 0}" not in out:
        raise RuntimeError("Could not change the Widgets toggle in Settings > Taskbar.")
    log(f"Widgets toggle turned {'on' if on else 'off'}.")


def t_widgets(log):
    _set_widgets_toggle(False, log)


def t_power(log):
    ultimate = "e9a42b02-d5df-448d-aa00-03f14749eb61"
    result = subprocess.run(["powercfg", "/setactive", ultimate], capture_output=True, text=True,
                            creationflags=NOWIN, shell=False)
    if result.returncode == 0:
        log("Ultimate Performance power plan activated.")
        return
    log("Ultimate Performance plan is not installed; creating it...")
    duplicate = subprocess.run(["powercfg", "/duplicatescheme", ultimate],
                               capture_output=True, text=True, creationflags=NOWIN, shell=False)
    output = (duplicate.stdout + duplicate.stderr).strip()
    match = re.search(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", output)
    if duplicate.returncode != 0 or not match:
        raise RuntimeError(f"Could not create Ultimate Performance plan: {output[-300:]}")
    activated = subprocess.run(["powercfg", "/setactive", match.group(1)],
                               capture_output=True, text=True, creationflags=NOWIN, shell=False)
    if activated.returncode != 0:
        output = (activated.stdout + activated.stderr).strip()
        raise RuntimeError(f"Could not activate Ultimate Performance plan: {output[-300:]}")
    log("Ultimate Performance power plan created and activated.")


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
    set_reg(HKCU, cdp, "NearShareChannelUserAuthzPolicy", 0)
    set_reg(HKCU, cdp, "NearShareChannelUxMode", 0)
    set_reg(HKCU, cdp, "RomeSdkChannelUserAuthzPolicy", 0)
    set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\Bluetooth", "AllowDiscoverableMode", 0)
    set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\System", "EnableCdp", 0)
    broadcast_setting("CDP")
    log("Nearby sharing disabled for this user.")


def t_core_isolation(log):
    set_reg(HKLM, r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
            "Enabled", 0)
    log("Core isolation > Memory integrity turned off (restart required).")


def t_recall(log):
    log("Recall: applying policy settings...")
    set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\WindowsAI", "DisableAIDataAnalysis", 1)
    log("Recall: starting Windows feature removal (this may take a few minutes)...")
    out = run(["dism", "/online", "/disable-feature", "/featurename:Recall", "/norestart"])
    log("Recall policy set.")
    log(out[-300:] if out else "DISM returned no output.")


def t_game_mode(log):
    gb = r"Software\Microsoft\GameBar"
    set_reg(HKCU, gb, "AutoGameModeEnabled", 1)
    set_reg(HKCU, gb, "AllowAutoGameMode", 1)
    log("Game Mode turned on.")


HAGS_KEY = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"


def t_hags(log):
    set_reg(HKLM, HAGS_KEY, "HwSchMode", 2)
    log("Hardware-accelerated GPU scheduling turned on. Restart Windows for it to take effect.")


def _reg(root, path, name, default=None):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def _all_reg(root, path, names, expected):
    return all(_reg(root, path, name) == expected for name in names)


def _active_power():
    return "e9a42b02-d5df-448d-aa00-03f14749eb61" in run(["powercfg", "/getactivescheme"]).lower()


def _active_group_policy():
    return not glob.glob(os.path.expandvars(
        r"%SystemRoot%\servicing\Packages\Microsoft-Windows-GroupPolicy-*.mum"))


def _active_widgets():
    return _reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "TaskbarDa") == 0


ACTIVE_CHECKS = {
    "t_bing": lambda: _reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Search",
                           "BingSearchEnabled") == 0,
    "t_ads": lambda: _all_reg(
        HKCU, r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
        ("SubscribedContent-338388Enabled", "SubscribedContent-338389Enabled",
         "SubscribedContent-353694Enabled", "SubscribedContent-353696Enabled",
         "SystemPaneSuggestionsEnabled", "SoftLandingEnabled", "SilentInstalledAppsEnabled"), 0)
        and _reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled") == 0,
    "t_telemetry": lambda: _reg(
        HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry") == 0,
    "t_group_policy": _active_group_policy,
    "t_power": _active_power,
    "t_widgets": _active_widgets,
    "t_time_format": lambda: _reg(HKCU, r"Control Panel\International", "sShortDate") == "MMM d, yyyy",
    "t_end_task": lambda: _reg(
        HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced\TaskbarDeveloperSettings",
        "TaskbarEndTask") == 1,
    "t_sounds": lambda: _reg(HKCU, r"AppEvents\Schemes", "") == ".None",
    "t_mactype": lambda: bool(_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Run", "MacType")),
    "t_cursor": lambda: _reg(HKCU, r"Control Panel\Cursors", "") == "Big Sur",
    "t_notepad": lambda: _active_notepad(),
    "t_nvidia_profile": lambda: os.path.isfile(NVIDIA_MARKER),
    "t_mouse_accel": lambda: _all_reg(HKCU, r"Control Panel\Mouse",
                                       ("MouseSpeed", "MouseThreshold1", "MouseThreshold2"), "0"),
    "t_sticky_keys": lambda: _reg(HKCU, r"Control Panel\Accessibility\StickyKeys", "Flags") == "506",
    "t_hags": lambda: _reg(HKLM, HAGS_KEY, "HwSchMode") == 2,
    "t_game_mode": lambda: _all_reg(HKCU, r"Software\Microsoft\GameBar",
                                     ("AutoGameModeEnabled", "AllowAutoGameMode"), 1),
    "t_smart_app_control": lambda: _reg(
        HKLM, r"SYSTEM\CurrentControlSet\Control\CI\Policy",
        "VerifiedAndReputablePolicyState") == 0,
    "t_core_isolation": lambda: _reg(
        HKLM, r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled") == 0,
    "t_nearby": lambda: _all_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\CDP",
                                 ("CdpSessionUserAuthzPolicy", "NearShareChannelUserAuthzPolicy",
                                  "NearShareChannelUxMode", "RomeSdkChannelUserAuthzPolicy"), 0)
        and _reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\Bluetooth",
                 "AllowDiscoverableMode") == 0
        and _reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\System", "EnableCdp") == 0,
    "t_recall": lambda: _reg(
        HKLM, r"SOFTWARE\Policies\Microsoft\Windows\WindowsAI", "DisableAIDataAnalysis") == 1,
}


def is_active(fn):
    checker = ACTIVE_CHECKS.get(fn.__name__)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return False


def _delete_reg(root, path, name):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def revert_tweak(fn, log):
    name = fn.__name__
    if name == "t_bing":
        set_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", 1)
    elif name == "t_ads":
        path = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
        for value in ("SubscribedContent-338388Enabled", "SubscribedContent-338389Enabled",
                      "SubscribedContent-353694Enabled", "SubscribedContent-353696Enabled",
                      "SystemPaneSuggestionsEnabled", "SoftLandingEnabled",
                      "SilentInstalledAppsEnabled"):
            set_reg(HKCU, path, value, 1)
        set_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", 1)
    elif name == "t_telemetry":
        set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 1)
    elif name == "t_power":
        out = run(["powercfg", "/setactive", "SCHEME_BALANCED"])
        if out and "error" in out.lower():
            raise RuntimeError(out[-300:])
    elif name == "t_widgets":
        _set_widgets_toggle(True, log)
    elif name == "t_time_format":
        set_str(HKCU, r"Control Panel\International", "sShortDate", "M/d/yyyy")
        broadcast_setting("intl")
    elif name == "t_end_task":
        set_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
                r"\TaskbarDeveloperSettings", "TaskbarEndTask", 0)
    elif name == "t_sounds":
        set_str(HKCU, r"AppEvents\Schemes", "", ".Default")
    elif name == "t_mactype":
        _delete_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Run", "MacType")
        run(["taskkill", "/f", "/im", "MacTray.exe"])
    elif name == "t_cursor":
        set_str(HKCU, r"Control Panel\Cursors", "", "Windows Default")
        set_reg(HKCU, r"Control Panel\Cursors", "Scheme Source", 2)
        ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 3)
    elif name == "t_notepad":
        if not _notepad_restore():
            log("No saved Notepad settings backup; Notepad will recreate its defaults.")
        _delete_reg(HKLM, r"SOFTWARE\Policies\WindowsNotepad", "DisableAIFeatures")  # left by older versions
    elif name == "t_mouse_accel":
        for value, data in (("MouseSpeed", "1"), ("MouseThreshold1", "6"), ("MouseThreshold2", "10")):
            set_str(HKCU, r"Control Panel\Mouse", value, data)
        params = (ctypes.c_int * 3)(1, 6, 10)
        ctypes.windll.user32.SystemParametersInfoW(0x0004, 0, params, 3)
    elif name == "t_sticky_keys":
        key = r"Control Panel\Accessibility"
        set_str(HKCU, key + r"\StickyKeys", "Flags", "510")
        set_str(HKCU, key + r"\ToggleKeys", "Flags", "58")
        set_str(HKCU, key + r"\Keyboard Response", "Flags", "126")
    elif name == "t_hags":
        set_reg(HKLM, HAGS_KEY, "HwSchMode", 1)
        log("Restart Windows for the change to take effect.")
    elif name == "t_game_mode":
        set_reg(HKCU, r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 0)
        set_reg(HKCU, r"Software\Microsoft\GameBar", "AllowAutoGameMode", 0)
    elif name == "t_smart_app_control":
        _delete_reg(HKLM, r"SYSTEM\CurrentControlSet\Control\CI\Policy",
                    "VerifiedAndReputablePolicyState")
    elif name == "t_core_isolation":
        set_reg(HKLM, r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios"
                r"\HypervisorEnforcedCodeIntegrity", "Enabled", 1)
    elif name == "t_nearby":
        cdp = r"Software\Microsoft\Windows\CurrentVersion\CDP"
        for value in ("CdpSessionUserAuthzPolicy", "NearShareChannelUserAuthzPolicy",
                      "NearShareChannelUxMode", "RomeSdkChannelUserAuthzPolicy"):
            _delete_reg(HKCU, cdp, value)
        _delete_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\Bluetooth", "AllowDiscoverableMode")
        _delete_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\System", "EnableCdp")
        broadcast_setting("CDP")
    elif name == "t_recall":
        _delete_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\WindowsAI",
                    "DisableAIDataAnalysis")
        out = run(["dism", "/online", "/enable-feature", "/featurename:Recall", "/norestart"])
        if out and "error" in out.lower():
            raise RuntimeError(out[-300:])
    elif name == "t_nvidia_profile":
        _revert_nvidia_profile(log)
    elif name in {"t_temp", "t_group_policy"}:
        raise RuntimeError("This action does not have a safe automatic Windows default to restore.")
    else:
        raise RuntimeError(f"No revert handler is defined for {name}.")
    log(f"{fn.__name__} reverted to its Windows default.")


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


WINRT_INT32, WINRT_STRING = 100000004, 100000012  # registry types used by packaged-app settings.dat
NOTEPAD_FONT = {"FontFamily": "Consolas", "FontSize": 14, "FontStyle": "Regular"}
NOTEPAD_SETTINGS_DIR = r"%LOCALAPPDATA%\Packages\Microsoft.WindowsNotepad_8wekyb3d8bbwe\Settings"
NOTEPAD_BACKUP_DIR = r"%LOCALAPPDATA%\kernel\notepad-settings-backup"


def _notepad_files(directory):
    return glob.glob(os.path.join(os.path.expandvars(directory), "settings.dat*"))


def _retry(fn, *args):
    import time
    for attempt in range(20):
        try:
            return fn(*args)
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def _notepad_backup():
    backup = os.path.expandvars(NOTEPAD_BACKUP_DIR)
    if _notepad_files(NOTEPAD_BACKUP_DIR):
        return
    staging = backup + ".tmp"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    for f in _notepad_files(NOTEPAD_SETTINGS_DIR):
        _retry(shutil.copy2, f, staging)
    shutil.rmtree(backup, ignore_errors=True)
    os.replace(staging, backup)


def _notepad_restore():
    run(["taskkill", "/f", "/im", "notepad.exe"])
    for f in _notepad_files(NOTEPAD_SETTINGS_DIR):
        _retry(os.remove, f)
    backup = _notepad_files(NOTEPAD_BACKUP_DIR)
    for f in backup:
        _retry(shutil.copy2, f, os.path.expandvars(NOTEPAD_SETTINGS_DIR))
    return bool(backup)


def _appkey_open(path, write=False):
    """Open a packaged app's settings.dat as a private registry root (no admin or hive load needed)."""
    import ctypes.wintypes as wt
    adv = ctypes.WinDLL("advapi32")
    handle = ctypes.c_void_p
    adv.RegLoadAppKeyW.argtypes = [wt.LPCWSTR, ctypes.POINTER(handle), wt.DWORD, wt.DWORD, wt.DWORD]
    adv.RegCloseKey.argtypes = [handle]
    root = handle()
    err = adv.RegLoadAppKeyW(path, ctypes.byref(root), 0x20006 if write else 0x20019, 0, 0)
    if err:
        raise OSError(err, f"Could not open Notepad settings (error {err}).")
    return adv, root


def _notepad_values(path):
    """Return {name: (registry type, bytes)} for the Notepad font values in a settings.dat."""
    import ctypes.wintypes as wt
    adv, root = _appkey_open(path)
    handle = ctypes.c_void_p
    adv.RegOpenKeyExW.argtypes = [handle, wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(handle)]
    adv.RegQueryValueExW.argtypes = [handle, wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(wt.DWORD),
                                     ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
    out = {}
    try:
        key = handle()
        if adv.RegOpenKeyExW(root, "LocalState", 0, 0x20019, ctypes.byref(key)):
            return out
        for name in NOTEPAD_FONT:
            buf = (ctypes.c_ubyte * 512)()
            size = wt.DWORD(512)
            typ = wt.DWORD()
            if adv.RegQueryValueExW(key, name, None, ctypes.byref(typ), buf, ctypes.byref(size)) == 0:
                out[name] = (typ.value, bytes(buf[:size.value]))
        adv.RegCloseKey(key)
    finally:
        adv.RegCloseKey(root)
    return out


def _notepad_font_payload(name, value):
    import struct
    if name == "FontSize":
        return WINRT_INT32, struct.pack("<i", value)
    return WINRT_STRING, value.encode("utf-16-le") + b"\0\0"


_notepad_state = [False]


def _active_notepad():
    import tempfile
    src = _notepad_files(NOTEPAD_SETTINGS_DIR)
    if not src:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        try:
            for f in src:
                with open(f, "rb") as a, open(os.path.join(tmp, os.path.basename(f)), "wb") as b:
                    shutil.copyfileobj(a, b)
            current = _notepad_values(os.path.join(tmp, "settings.dat"))
        except PermissionError:
            return _notepad_state[0]  # Windows locks the file while Notepad is open
        except OSError:
            return False
    def matches(name, value):
        typ, want = _notepad_font_payload(name, value)
        return name in current and current[name][0] == typ and current[name][1][:len(want)] == want

    _notepad_state[0] = all(matches(n, v) for n, v in NOTEPAD_FONT.items())
    return _notepad_state[0]


def t_notepad(log):
    pkg = os.path.join(os.path.expandvars(NOTEPAD_SETTINGS_DIR), "settings.dat")
    if not os.path.isfile(pkg):
        raise RuntimeError("Notepad settings not found. Open Notepad once, then try again.")
    run(["taskkill", "/f", "/im", "notepad.exe"])
    _notepad_backup()
    try:
        _write_notepad_font(pkg)
        if not _active_notepad():
            raise RuntimeError("Notepad font settings did not save correctly.")
        log("Notepad font set to Consolas, Regular, 14.")
    except Exception:
        _notepad_restore()
        log("Notepad settings rolled back to the original.")
        raise


def _write_notepad_font(pkg):
    import ctypes.wintypes as wt
    import struct
    import time
    adv, root = _appkey_open(pkg, write=True)
    handle = ctypes.c_void_p
    adv.RegCreateKeyExW.argtypes = [handle, wt.LPCWSTR, wt.DWORD, wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                    ctypes.c_void_p, ctypes.POINTER(handle), ctypes.c_void_p]
    adv.RegSetValueExW.argtypes = [handle, wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_char_p, wt.DWORD]
    stamp = struct.pack("<Q", int(time.time() * 1e7) + 116444736000000000)
    try:
        key = handle()
        err = adv.RegCreateKeyExW(root, "LocalState", 0, None, 0, 0x20006, None, ctypes.byref(key), None)
        if err:
            raise OSError(err, f"Could not open Notepad settings (error {err}).")
        for name, value in NOTEPAD_FONT.items():
            typ, data = _notepad_font_payload(name, value)
            data += stamp
            err = adv.RegSetValueExW(key, name, 0, typ, data, len(data))
            if err:
                raise OSError(err, f"Could not write {name} (error {err}).")
        adv.RegCloseKey(key)
    finally:
        adv.RegCloseKey(root)

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

NVIDIA_MARKER = os.path.join(os.environ.get("APPDATA", ""), "kernel", "nvidia_profile_applied")


def _npi_exe(log):
    import urllib.request
    import zipfile
    import apps
    if not glob.glob(os.path.expandvars(r"%SystemRoot%\System32\nvapi64.dll")):
        raise RuntimeError("No NVIDIA driver found. Install the NVIDIA driver first.")
    tool_dir = os.path.join(os.environ.get("APPDATA", ""), "kernel", "npi")
    find = lambda: next(iter(glob.glob(os.path.join(tool_dir, "**", "nvidiaProfileInspector.exe"), recursive=True)), None)
    exe = find()
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
        exe = find()
        if not exe:
            raise RuntimeError("nvidiaProfileInspector.exe was not found in the download.")
    return exe


def _npi_import(exe, nip):
    r = subprocess.run([exe, "-silent", nip], capture_output=True, text=True,
                       creationflags=NOWIN, timeout=120, cwd=os.path.dirname(exe))
    if r.returncode not in (0, None):
        raise RuntimeError(f"Profile Inspector exited with code {r.returncode}. {(r.stdout + r.stderr).strip()[-200:]}")


def _nvidia_defaults_nip():
    # Same settings as profile.nip, with every value set back to the driver default (0)
    import re
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "assets", "profile.nip"), "rb") as f:
        raw = f.read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    text = re.sub(r"<SettingValue>[^<]*</SettingValue>", "<SettingValue>0</SettingValue>", text)
    path = os.path.join(os.environ.get("TEMP", ""), "kernel_nvidia_defaults.nip")
    with open(path, "w", encoding="utf-16") as f:
        f.write(text)
    return path


def t_nvidia_profile(log):
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    exe = _npi_exe(log)
    log("Importing your NVIDIA profile...")
    _npi_import(exe, os.path.join(base, "assets", "profile.nip"))
    log("NVIDIA profile imported.")
    os.makedirs(os.path.dirname(NVIDIA_MARKER), exist_ok=True)
    with open(NVIDIA_MARKER, "w") as f:
        f.write("applied")


def _revert_nvidia_profile(log):
    exe = _npi_exe(log)
    nip = _nvidia_defaults_nip()
    try:
        log("Restoring NVIDIA driver defaults for the profile settings...")
        _npi_import(exe, nip)
    finally:
        if os.path.isfile(nip):
            os.remove(nip)
    if os.path.isfile(NVIDIA_MARKER):
        os.remove(NVIDIA_MARKER)


# Run-once actions shown as buttons instead of toggles
ONE_SHOT = {"t_temp", "t_group_policy"}

# (section, title, description, function)
TASKS = [
    ("Privacy", "Remove Bing from search", "Stop web results in Start menu search", t_bing),
    ("Privacy", "Disable ads & suggestions", "Tips, suggested apps and advertising ID", t_ads),
    ("Privacy", "Minimize telemetry", "Reduce diagnostic data collection", t_telemetry),
    ("Storage", "Clean temp files", "Temp, Prefetch and Recycle Bin", t_temp),
    ("System", "Group Policy Editor", "Install gpedit on Windows Home", t_group_policy),
    ("Performance", "Ultimate performance", "Activate the maximum performance power plan", t_power),
    ("Appearance", "Hide Widgets & Chat", "Remove the taskbar buttons", t_widgets),
    ("Appearance", "Taskbar date format", "Show the date as MMM d, yyyy (e.g. Dec 31, 2030)", t_time_format),
    ("Appearance", "Enable End Task in taskbar", "Adds 'End task' to the right-click menu of taskbar apps", t_end_task),
    ("Appearance", "Disable Windows sounds", "Sound scheme to 'No Sounds' and no startup chime", t_sounds),
    ("Appearance", "Apply MacType in system tray", "Use the XMac LCD HotShift profile (MacType must be installed)", t_mactype),
    ("Appearance", "Apply Big Sur cursor", "Install the Big Sur cursor pack and apply it as your cursor scheme", t_cursor),
    ("Performance", "NVIDIA profile", "Import your saved NVIDIA Profile Inspector settings", t_nvidia_profile),
    ("Appearance", "Notepad settings", "Set Notepad's font to Consolas Regular 14", t_notepad),
    ("Input", "Disable enhanced mouse precision", "Turns off pointer acceleration for consistent aim", t_mouse_accel),
    ("Input", "Disable Sticky Keys", "Stops the popup when Shift is pressed five times", t_sticky_keys),
    ("Performance", "Enable Game Mode", "Prioritizes games and limits background activity", t_game_mode),
    ("Performance", "Hardware-accelerated GPU scheduling", "Let the GPU manage its own memory, lowering CPU overhead (restart required)", t_hags),
    ("Security", "Disable Smart App Control", "Stops blocking unsigned or unknown apps (permanent until Windows reset)", t_smart_app_control),
    ("Security", "Disable Core isolation", "Turn off Memory integrity (HVCI) for better performance", t_core_isolation),
    ("Privacy", "Disable Nearby sharing", "Stops sharing with nearby PCs", t_nearby),
    ("Privacy", "Disable Recall", "Policy plus DISM /Disable-Feature Recall", t_recall),
]
