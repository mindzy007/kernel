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


def _gpedit_packages():
    pkgs = []
    for pat in ("ClientExtensions", "ClientTools"):
        pkgs += glob.glob(os.path.expandvars(
            rf"%SystemRoot%\servicing\Packages\Microsoft-Windows-GroupPolicy-{pat}-Package~3*.mum"))
    return pkgs


def t_group_policy(log):
    if _active_group_policy():
        log("Group Policy Editor is already available.")
        return
    pkgs = _gpedit_packages()
    if not pkgs:
        log("No Group Policy packages found on this system.")
        return
    for p in pkgs:
        log(f"Installing {os.path.basename(p)}")
        log(run(["dism", "/online", "/norestart", f"/add-package:{p}"])[-300:])


def t_dark_mode(log):
    key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    set_reg(HKCU, key, "AppsUseLightTheme", 0)
    set_reg(HKCU, key, "SystemUsesLightTheme", 0)
    broadcast_setting("ImmersiveColorSet")
    run(["taskkill", "/f", "/im", "explorer.exe"])
    subprocess.Popen(["explorer.exe"], creationflags=NOWIN)
    log("Dark mode forced through the registry; Explorer restarted.")


def _xbox_values(enabled):
    d = 1 if enabled else 0
    set_reg(HKCU, r"Software\Microsoft\GameBar", "UseNexusForGameBarEnabled", d)
    set_reg(HKCU, r"Software\Microsoft\GameBar", "ShowStartupPanel", d)
    set_reg(HKCU, r"System\GameConfigStore", "GameDVR_Enabled", d)
    dvr = r"Software\Microsoft\Windows\CurrentVersion\GameDVR"
    set_reg(HKCU, dvr, "AppCaptureEnabled", d)
    set_reg(HKCU, dvr, "AudioCaptureEnabled", d)
    set_reg(HKCU, dvr, "CursorCaptureEnabled", d)
    if enabled:
        _delete_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR")
    else:
        set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", 0)


def t_xbox(log):
    _xbox_values(False)
    log("Xbox Game Bar overlay, controller hints and captures disabled.")


ONEDRIVE_DIRS = (r"%LOCALAPPDATA%\Microsoft\OneDrive", r"%ProgramData%\Microsoft OneDrive",
                 r"%ProgramFiles%\Microsoft OneDrive", r"%ProgramFiles(x86)%\Microsoft OneDrive",
                 r"%SystemDrive%\OneDriveTemp")


def t_onedrive(log):
    run(["taskkill", "/f", "/im", "OneDrive.exe"])
    for sub in ("SysWOW64", "System32"):
        setup = os.path.expandvars(rf"%SystemRoot%\{sub}\OneDriveSetup.exe")
        if os.path.isfile(setup):
            log(f"Uninstalling OneDrive with {sub}\\OneDriveSetup.exe...")
            run([setup, "/uninstall"])
    local = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\OneDrive")
    setup = os.path.join(local, "OneDriveSetup.exe")
    for path in glob.glob(os.path.join(local, "*", "OneDriveSetup.exe")) + [setup]:
        if os.path.isfile(path):
            run([path, "/uninstall"])
    for d in ONEDRIVE_DIRS:
        shutil.rmtree(os.path.expandvars(d), ignore_errors=True)
    home = os.path.expandvars(r"%USERPROFILE%\OneDrive")
    try:
        os.rmdir(home)  # only succeeds when empty, so user files are never deleted
    except OSError:
        if os.path.isdir(home):
            log("Kept your OneDrive folder because it still contains files.")
    _delete_reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Run", "OneDrive")
    set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\OneDrive", "DisableFileSyncNGSC", 1)
    for clsid in ("{018D5C66-4533-4307-9B53-224DE2ED1FE6}", "{04271989-C4D2-4E5C-8E3F-9B7D7BBE1D5B}"):
        for path in (rf"Software\Classes\CLSID\{clsid}", rf"Software\Classes\WOW6432Node\CLSID\{clsid}"):
            try:
                with winreg.OpenKey(HKCU, path, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, "System.IsPinnedToNameSpaceTree", 0, winreg.REG_DWORD, 0)
            except OSError:
                pass
    log("OneDrive removed. This cannot be undone.")


# (display name, package name)
BLOAT_APPS = [
    ("Sticky Notes", "Microsoft.MicrosoftStickyNotes"),
    ("Sound Recorder", "Microsoft.WindowsSoundRecorder"),
    ("Weather", "Microsoft.BingWeather"),
    ("News", "Microsoft.BingNews"),
    ("Microsoft Family", "MicrosoftCorporationII.MicrosoftFamily"),
    ("Clock", "Microsoft.WindowsAlarms"),
    ("Clipchamp", "Clipchamp.Clipchamp"),
    ("Feedback Hub", "Microsoft.WindowsFeedbackHub"),
    ("Get Help", "Microsoft.GetHelp"),
    ("Tips", "Microsoft.Getstarted"),
    ("Solitaire Collection", "Microsoft.MicrosoftSolitaireCollection"),
    ("Movies & TV", "Microsoft.ZuneVideo"),
    ("Maps", "Microsoft.WindowsMaps"),
    ("Power Automate", "Microsoft.PowerAutomateDesktop"),
    ("Cortana", "Microsoft.549981C3F5F10"),
    ("Microsoft Bing", "Microsoft.BingSearch"),
    ("Microsoft Bing", "Microsoft.Bing"),
    ("Microsoft To Do", "Microsoft.Todos"),
    ("Outlook", "Microsoft.OutlookForWindows"),
    ("Microsoft Teams", "MSTeams"),
    ("Microsoft Teams", "MicrosoftTeams"),
    ("Copilot", "Microsoft.Copilot"),
    ("Copilot", "Microsoft.Windows.Ai.Copilot.Provider"),
]
BLOAT_NAMES = ", ".join(dict.fromkeys(n for n, _ in BLOAT_APPS))
APPX_STORE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore\Applications"


def _installed_appx():
    names = []
    try:
        with winreg.OpenKey(HKLM, APPX_STORE) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                names.append(winreg.EnumKey(k, i).lower())
    except OSError:
        pass
    return names


def t_remove_apps(log):
    last = None
    for display, pkg in BLOAT_APPS:
        if display != last:
            log(f"Removing {display}...")
            last = display
        script = (f"Get-AppxPackage -AllUsers -Name '{pkg}' | Remove-AppxPackage -AllUsers "
                  f"-ErrorAction SilentlyContinue; "
                  f"Get-AppxProvisionedPackage -Online | Where-Object DisplayName -eq '{pkg}' | "
                  f"Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue")
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    log("Selected apps removed. This cannot be undone.")


START_STATE_DIR = os.path.expandvars(
    r"%LOCALAPPDATA%\Packages\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\LocalState")


def t_clear_start(log):
    import sys
    import time
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    template = os.path.join(base, "assets", "start2.bin")
    if sys.getwindowsversion().build < 22621 or not os.path.isdir(START_STATE_DIR):
        raise RuntimeError("Clearing Start pins needs Windows 11 22H2 or newer.")
    if not os.path.isfile(template):
        raise RuntimeError("assets\\start2.bin is missing from the app files.")
    target = os.path.join(START_STATE_DIR, "start2.bin")
    if os.path.isfile(target) and not os.path.isfile(target + ".bak"):
        shutil.copy2(target, target + ".bak")  # keeps the original layout so it can be restored by hand
    run(["taskkill", "/F", "/IM", "StartMenuExperienceHost.exe"])
    time.sleep(1)
    shutil.copyfile(template, target)
    run(["taskkill", "/F", "/IM", "StartMenuExperienceHost.exe"])
    log("All pinned Start shortcuts removed. Open Start to see the empty layout.")


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


def t_lock_screen_widgets(log):
    set_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Dsh", "DisableWidgetsOnLockScreen", 1)
    log("Widgets disabled on the Windows lock screen.")


def _get_all_ultimate_performance_plans():
    """Extract all Ultimate Performance plan GUIDs from powercfg list."""
    try:
        output = run(["powercfg", "/list"])
    except Exception:
        return []

    plans = []
    lines = output.split('\n')

    for i, line in enumerate(lines):
        # Look for lines containing the plan name in parentheses
        if '(Ultimate Performance)' in line:
            # Extract GUID which should be in this line or nearby
            # Format: "Power Scheme GUID: {GUID}  (Plan Name)"
            match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", line, re.IGNORECASE)
            if match:
                guid = match.group(1)
                if guid not in plans:  # Avoid duplicates
                    plans.append(guid)

    return plans


def t_power(log):
    """Activate Ultimate Performance power plan without creating duplicates."""
    # First, get list of ALL existing Ultimate Performance plans
    existing_plans = _get_all_ultimate_performance_plans()

    if existing_plans:
        # Try each existing plan until one works
        for plan_guid in existing_plans:
            result = subprocess.run(["powercfg", "/setactive", plan_guid],
                                  capture_output=True, text=True, creationflags=NOWIN, shell=False)
            if result.returncode == 0:
                log("Ultimate Performance power plan activated.")
                return

    # No existing plans or all failed - try standard GUID
    ultimate = "e9a42b02-d5df-448d-aa00-03f14749eb61"
    result = subprocess.run(["powercfg", "/setactive", ultimate],
                          capture_output=True, text=True, creationflags=NOWIN, shell=False)
    if result.returncode == 0:
        log("Ultimate Performance power plan activated.")
        return

    # If standard GUID doesn't exist, create it
    log("Ultimate Performance plan not found, creating...")
    result = subprocess.run(["powercfg", "/duplicatescheme", ultimate],
                          capture_output=True, text=True, creationflags=NOWIN, shell=False)

    if result.returncode != 0:
        raise RuntimeError(f"Could not create Ultimate Performance plan: {(result.stdout + result.stderr).strip()[-300:]}")

    output = (result.stdout + result.stderr).strip()
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", output, re.IGNORECASE)
    if not match:
        raise RuntimeError(f"Could not extract GUID from duplicated scheme: {output[-300:]}")

    new_guid = match.group(1)
    result = subprocess.run(["powercfg", "/setactive", new_guid],
                          capture_output=True, text=True, creationflags=NOWIN, shell=False)

    if result.returncode != 0:
        raise RuntimeError(f"Could not activate new Ultimate Performance plan: {(result.stdout + result.stderr).strip()[-300:]}")

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


SYSTEM_REPAIR_STEPS = (
    ("DISM CheckHealth", ["DISM", "/Online", "/Cleanup-Image", "/CheckHealth"]),
    ("DISM RestoreHealth", ["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"]),
    ("SFC /scannow", ["sfc", "/scannow"]),
    ("DISM StartComponentCleanup", ["DISM", "/Online", "/Cleanup-Image", "/StartComponentCleanup"]),
)
ERROR_SUCCESS_REBOOT_REQUIRED = 3010


def _run_tool(cmd):
    """Run a console tool; returns (exit code, text). SFC writes UTF-16, DISM the OEM code page."""
    r = subprocess.run(cmd, capture_output=True, creationflags=NOWIN, shell=False)
    raw = r.stdout + r.stderr
    text = raw.decode("utf-16-le", "replace") if b"\x00" in raw else raw.decode("oem", "replace")
    return r.returncode, text.replace("\x00", "").replace("\r", "\n")


def _tool_result_lines(text):
    """The meaningful lines of DISM/SFC output, without banners and progress bars."""
    skip = ("deployment image servicing", "version:", "image version:", "beginning system scan",
            "beginning verification")
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s and "%" not in s and not s.startswith(("[", "=")) and not s.lower().startswith(skip):
            lines.append(s)
    return lines


def t_system_repair(log):
    total = len(SYSTEM_REPAIR_STEPS)
    results = []
    restart_needed = False
    for number, (label, cmd) in enumerate(SYSTEM_REPAIR_STEPS, 1):
        if restart_needed:
            results.append((label, "SKIPPED (restart first)"))
            continue
        log(f"[{number}/{total}] {label}: running (this can take several minutes)...")
        code, text = _run_tool(cmd)
        low = text.lower()
        if code == ERROR_SUCCESS_REBOOT_REQUIRED or re.search(r"restart|reboot", low):
            state, restart_needed = "RESTART REQUIRED", True
        elif "repairable" in low or "unable to fix" in low:
            state = "WARNING"
        elif code != 0:
            state = "FAILED"
        else:
            state = "OK"
        log(f"[{number}/{total}] {label}: {state}")
        lines = _tool_result_lines(text)
        for line in lines[-3:] or ([f"Exit code {code}."] if state == "FAILED" else []):
            log(f"    {line}")
        results.append((label, state))
    log("== Results")
    for label, state in results:
        log(f"  {label}: {state}")
    if restart_needed:
        log("A repair needs a restart to finish. Restart Windows before running any more "
            "maintenance operations; the remaining steps were skipped.")
    log("Restart Windows now to finish applying repairs and cleanup.")


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
    active_scheme = run(["powercfg", "/getactivescheme"]).lower()
    # Check for standard GUID or any Ultimate Performance plan name
    return "e9a42b02-d5df-448d-aa00-03f14749eb61" in active_scheme or "ultimate performance" in active_scheme


def _active_group_policy():
    return os.path.isfile(os.path.expandvars(r"%SystemRoot%\System32\gpedit.msc"))


def _active_onedrive():
    exes = [os.path.expandvars(p) for p in (
        r"%LOCALAPPDATA%\Microsoft\OneDrive\OneDrive.exe",
        r"%ProgramFiles%\Microsoft OneDrive\OneDrive.exe",
        r"%ProgramFiles(x86)%\Microsoft OneDrive\OneDrive.exe")]
    return not any(os.path.isfile(p) for p in exes)


def _active_remove_apps():
    installed = _installed_appx()
    return not any(n.startswith(pkg.lower() + "_") for _, pkg in BLOAT_APPS for n in installed)


def _active_xbox():
    return (_reg(HKCU, r"Software\Microsoft\GameBar", "UseNexusForGameBarEnabled") == 0
            and _reg(HKCU, r"System\GameConfigStore", "GameDVR_Enabled") == 0
            and _reg(HKCU, r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                     "AppCaptureEnabled") == 0)


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
    "t_onedrive": _active_onedrive,
    "t_remove_apps": _active_remove_apps,
    "t_xbox": _active_xbox,
    "t_dark_mode": lambda: _all_reg(
        HKCU, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ("AppsUseLightTheme", "SystemUsesLightTheme"), 0),
    "t_power": _active_power,
    "t_widgets": _active_widgets,
    "t_lock_screen_widgets": lambda: _reg(
        HKLM, r"SOFTWARE\Policies\Microsoft\Dsh", "DisableWidgetsOnLockScreen") == 1,
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
        # Switch to High Performance first
        out = run(["powercfg", "/setactive", "SCHEME_HIGH_PERFORMANCE"])
        if out and "error" in out.lower():
            log(f"Warning switching to High Performance: {out[-100:]}")

        # Delete ALL Ultimate Performance plans
        plans = _get_all_ultimate_performance_plans()
        for plan in plans:
            run(["powercfg", "/delete", plan])

        log("Switched to High Performance and deleted all Ultimate Performance plans.")
    elif name == "t_widgets":
        _set_widgets_toggle(True, log)
    elif name == "t_lock_screen_widgets":
        _delete_reg(HKLM, r"SOFTWARE\Policies\Microsoft\Dsh", "DisableWidgetsOnLockScreen")
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
        if _notepad_running():
            raise RuntimeError("Notepad is open. Close Notepad completely, then revert this tweak again.")
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
    elif name == "t_group_policy":
        for p in glob.glob(os.path.expandvars(
                r"%SystemRoot%\servicing\Packages\Microsoft-Windows-GroupPolicy-Client*-Package~3*.mum")):
            out = run(["dism", "/online", "/norestart", f"/remove-package:{os.path.basename(p)[:-4]}"])
            if out and "error" in out.lower():
                raise RuntimeError(out[-300:])
    elif name == "t_dark_mode":
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        set_reg(HKCU, key, "AppsUseLightTheme", 1)
        set_reg(HKCU, key, "SystemUsesLightTheme", 1)
        broadcast_setting("ImmersiveColorSet")
        run(["taskkill", "/f", "/im", "explorer.exe"])
        subprocess.Popen(["explorer.exe"], creationflags=NOWIN)
    elif name == "t_xbox":
        _xbox_values(True)
    elif name in IRREVERSIBLE or name in ("t_temp", "t_clear_start"):
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


def _notepad_running():
    return "notepad.exe" in run(["tasklist", "/FI", "IMAGENAME eq Notepad.exe", "/NH"]).lower()


def t_notepad(log):
    pkg = os.path.join(os.path.expandvars(NOTEPAD_SETTINGS_DIR), "settings.dat")
    if not os.path.isfile(pkg):
        raise RuntimeError("Notepad settings not found. Open Notepad once, then try again.")
    if _notepad_running():
        raise RuntimeError("Notepad is open. Close Notepad completely, then apply this tweak again.")
    _notepad_backup()
    try:
        _write_notepad_font(pkg)
        if not _active_notepad():
            raise RuntimeError("Notepad font settings did not save correctly. "
                               "Make sure Notepad is fully closed, then try again.")
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
ONE_SHOT = {"t_temp", "t_system_repair"}

# Tweaks with no way back; the UI warns before applying and disables Revert
IRREVERSIBLE = {"t_onedrive", "t_remove_apps", "t_clear_start"}

# (section, title, description, function)
TASKS = [
    ("Privacy", "Remove Bing from search", "Removes Bing results when searching for items on your PC via start menu and search", t_bing),
    ("Privacy", "Disable ads & suggestions", "Tips, suggested apps and advertising ID", t_ads),
    ("Privacy", "Minimize telemetry", "Reduce diagnostic data collection", t_telemetry),
    ("Storage", "System Cleanup", "Removes temporary system files, clears Windows prefetch data, and empties the Recycle Bin to free up disk space.", t_temp),
    ("System", "Enable Group Policy Editor", "Installs pro version of gpeditor on to your system", t_group_policy),
    ("Performance", "Ultimate performance", "Activate the maximum performance power plan", t_power),
    ("Appearance", "Hide Widgets & Chat", "Remove the taskbar buttons", t_widgets),
    ("Appearance", "Disable lock screen widgets", "Remove widgets from the Windows sign-in screen", t_lock_screen_widgets),
    ("Appearance", "Taskbar date format", "Show the date as MMM d, yyyy (e.g. Dec 31, 2030)", t_time_format),
    ("Appearance", "Enable End Task in taskbar", "Adds 'End task' to the right-click menu of taskbar apps", t_end_task),
    ("Appearance", "Disable Windows sounds", "Sound scheme to 'No Sounds' and no startup chime", t_sounds),
    ("Appearance", "Apply MacType in system tray", "Use the XMac LCD HotShift profile (MacType must be installed)", t_mactype),
    ("Appearance", "Apply Big Sur cursor", "Install the Big Sur cursor pack and apply it as your cursor scheme", t_cursor),
    ("Performance", "NVIDIA Profile", "Applies a minimal and optimized NVIDIA Profile Inspector config.", t_nvidia_profile),
    ("Appearance", "Notepad settings", "Set Notepad's font to Consolas Regular 14", t_notepad),
    ("Input", "Disable enhanced mouse precision", "Turns off pointer acceleration for consistent aim", t_mouse_accel),
    ("Input", "Disable Sticky Keys", "Stops the popup when Shift is pressed five times", t_sticky_keys),
    ("Performance", "Enable Game Mode", "Prioritizes games and limits background activity", t_game_mode),
    ("Performance", "Hardware-accelerated GPU scheduling", "Let the GPU manage its own memory, lowering CPU overhead (restart required)", t_hags),
    ("Security", "Disable Smart App Control", "Disables Smart App Control to prevent it from blocking untrusted, unsigned, or unknown applications, reduces Windows application security.", t_smart_app_control),
    ("Performance", "Disable Core isolation", "Disables Memory Integrity (HVCI) to improve performance and resolve compatibility issues with applications that don't support it. Note: This reduces a Windows security protection.", t_core_isolation),
    ("Privacy", "Disable Nearby sharing", "Stops sharing with nearby PCs", t_nearby),
    ("Performance", "Disable Windows Recall (Hidden from users)", "Disables Windows 11 AI-powered feature that acts like a photographic memory for your computer", t_recall),
    ("Apps", "Remove OneDrive", "Completely uninstalls OneDrive, deletes its leftovers and blocks it from coming back. This cannot be reverted.", t_onedrive),
    ("Apps", "Remove useless Windows apps", f"Uninstalls: {BLOAT_NAMES}. This cannot be reverted.", t_remove_apps),
    ("System", "Clear Start Menu pins", "Removes every pinned shortcut from the Start Menu, requires Windows 11 22H2 or newer. This cannot be reverted.", t_clear_start),
    ("Appearance", "Force dark mode", "Turns on dark mode through the registry, even on Windows that isn't activated", t_dark_mode),
    ("Performance", "Disable Xbox Game Bar", "Disables Xbox Game Bar Overlay, Xbox accessibility control hints and Xbox capture", t_xbox),
    ("Performance", "System file repair & maintenance", "Automatically checks, repairs, and maintains Windows system files and components to help resolve corruption, improve system integrity, and remove unnecessary old component files.", t_system_repair),
]
