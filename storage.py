import ctypes
import json
import os
import re
import shutil
import string
import subprocess
import time
import winreg
from concurrent.futures import ThreadPoolExecutor

NOWIN = 0x08000000  # CREATE_NO_WINDOW
BELOW_NORMAL = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
MIN_APP_BYTES = 50 * 1024 ** 2
GAME_DIRS = ("\\steamapps\\", "\\epic games\\", "\\gog galaxy\\games\\", "\\gog games\\", "\\riot games\\",
             "\\xboxgames\\", "\\ubisoft game launcher\\games\\", "\\ea games\\", "\\origin games\\",
             "\\rockstar games\\")
UNINSTALL_KEYS = (
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
)
APPX_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$start = @{}
Get-StartApps | ForEach-Object { $f = ($_.AppID -split '!')[0]; if ($f -and -not $start.ContainsKey($f)) { $start[$f] = $_.Name } }
@(Get-AppxPackage | Where-Object { -not $_.IsFramework -and -not $_.IsResourcePackage -and $_.SignatureKind -ne 'System' -and $_.InstallLocation -and $start[$_.PackageFamilyName] } | ForEach-Object {
  [pscustomobject]@{ name = $start[$_.PackageFamilyName]; location = $_.InstallLocation; publisher = $_.Publisher }
}) | ConvertTo-Json -Compress
"""


def drives():
    """[(letter path, label, free, total)] for fixed and removable drives."""
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) not in (2, 3):
            continue
        try:
            total, _, free = shutil.disk_usage(root)
        except OSError:
            continue
        buf = ctypes.create_unicode_buffer(261)
        ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
        out.append((root, buf.value or "Local Disk", free, total))
    return out


MODEL_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
@(Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object {
  $d = Get-Disk -Number $_.DiskNumber
  [pscustomobject]@{ letter = [string]$_.DriveLetter; model = (($d.FriendlyName -replace '\s+', ' ')).Trim() }
}) | ConvertTo-Json -Compress
"""


def drive_models():
    """{'C:\\': 'Samsung SSD 980 PRO 1TB', ...}: the model name Windows reports for the physical disk
    behind each drive letter. Empty if it can't be read."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", MODEL_SCRIPT],
                           capture_output=True, text=True, creationflags=NOWIN | BELOW_NORMAL, timeout=30)
        data = json.loads(r.stdout) if r.stdout.strip() else []
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    return {f"{d['letter'].upper()}:\\": d["model"] for d in data if d.get("letter") and d.get("model")}


def lower_priority():
    """Background mode for the calling thread: low CPU and low disk priority, so the UI stays smooth."""
    try:
        k = ctypes.windll.kernel32
        k.SetThreadPriority(k.GetCurrentThread(), 0x00010000)  # THREAD_MODE_BACKGROUND_BEGIN
    except Exception:
        pass


def is_removable(root):
    """True for USB sticks and other removable drives (not worth a deep scan)."""
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(root) == 2
    except Exception:
        return False


def load_cache(path, roots, max_age=3600):
    """Saved scan results {root: result} if they are fresh and cover exactly these drives, else None."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data["time"] > max_age or sorted(data["drives"]) != sorted(roots):
            return None
        return data["drives"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_cache(path, drives):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"time": time.time(), "drives": drives}, f)
    except (OSError, TypeError):
        pass


def size_of(path):
    """Total bytes under path (files only, symlinks not followed)."""
    total = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _entry_size(entry):
    try:
        if entry.is_symlink():
            return None
        if entry.is_dir(follow_symlinks=False):
            return entry.path, size_of(entry.path), True
        return entry.path, entry.stat(follow_symlinks=False).st_size, False
    except OSError:
        return None


def sized_children(folder):
    """Every direct child of folder: [(path, bytes, is_dir)]."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    with ThreadPoolExecutor(4, initializer=lower_priority) as pool:
        return [r for r in pool.map(_entry_size, entries) if r]


def largest(folder, limit=10):
    """Biggest direct children of folder: [(path, bytes, is_dir)]."""
    sized = sized_children(folder)
    sized.sort(key=lambda r: r[1], reverse=True)
    return sized[:limit]


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


def _protected_dirs():
    keys = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
            "USERPROFILE", "LOCALAPPDATA", "APPDATA")
    return {_norm(os.environ[k]) for k in keys if os.environ.get(k)}


def _usable_location(path):
    n = _norm(path)
    if os.path.splitdrive(n)[1] in ("", "\\") or n in _protected_dirs():
        return False
    win = _norm(os.environ.get("SystemRoot", "C:\\Windows"))
    return not (n == win or n.startswith(win + "\\")) and os.path.isdir(path)


def _unquote_path(text):
    if not text:
        return None
    text = str(text).strip()
    if text.startswith('"'):
        return text[1:].split('"')[0] or None
    return text.split(",")[0].strip() or None


def _reg_value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


# Publishers whose name gives a game away (matched as plain text inside the publisher field).
GAME_PUBLISHERS = ("psyonix", "games", "gaming", "interactive", "entertainment", "studios", "valve", "electronic arts",
                   "activision", "blizzard", "ubisoft", "rockstar", "bethesda", "cd projekt", "bandai", "square enix",
                   "capcom", "sega", "konami", "take-two", "bungie", "mojang", "wargaming", "gaijin", "nexon",
                   "hoyoverse", "mihoyo", "supercell")
# Game stores, launchers and helper software: never shown as a game, whoever published them.
NOT_GAMES = re.compile(r"\b(launcher|battle\.net|ea app|gog galaxy|ubisoft connect|origin|xbox|client|steam|"
                       r"redistributable|runtime|easyanticheat|battleye)\b", re.I)


def _is_game(name, location, publisher=""):
    """A game if it lives in a known game-library folder, or its publisher is a game company."""
    if NOT_GAMES.search(name):
        return False
    low = f"{location or ''}\\".lower()
    if any(part in low for part in GAME_DIRS):
        return True
    pub = (publisher or "").lower()
    return any(word in pub for word in GAME_PUBLISHERS)


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and not w.isdigit()}


def _install_root(folder, name):
    """The icon path usually points deep inside an app (...\\After Effects 2025\\Support Files). Climb to the
    folder that is really the app's: the highest ancestor, still below Program Files and friends, whose name
    covers most of the app's name. Without this such apps measured tiny and never made the list."""
    words = _words(name)
    if not words:
        return folder
    protected, best, cur = _protected_dirs(), folder, folder
    while True:
        parent = os.path.dirname(cur)
        if not parent or parent == cur or _norm(parent) in protected or os.path.splitdrive(_norm(parent))[1] in ("", "\\"):
            return best
        cur = parent
        if len(_words(os.path.basename(cur)) & words) / len(words) >= 0.6:
            best = cur


def _json_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _subkey_values(hive, path, names):
    """First non-empty value (from `names`) of every subkey under hive\\path."""
    out = []
    try:
        with winreg.OpenKey(hive, path) as base:
            for i in range(winreg.QueryInfoKey(base)[0]):
                try:
                    with winreg.OpenKey(base, winreg.EnumKey(base, i)) as key:
                        for n in names:
                            v = _reg_value(key, n)
                            if v:
                                out.append(str(v))
                                break
                except OSError:
                    continue
    except OSError:
        pass
    return out


def launcher_game_dirs():
    """Install folders that the game launchers themselves list as games (normalised). A game can live in any
    folder, so this is far more reliable than guessing from the path or the publisher's name."""
    dirs = set()
    epic = os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"), "Epic")
    manifests = os.path.join(epic, "EpicGamesLauncher", "Data", "Manifests")
    try:
        for fn in os.listdir(manifests):
            data = _json_file(os.path.join(manifests, fn)) if fn.endswith(".item") else None
            if isinstance(data, dict) and data.get("InstallLocation"):
                cats = [str(c).lower() for c in data.get("AppCategories") or []]
                if not cats or "games" in cats:
                    dirs.add(_norm(data["InstallLocation"]))
    except OSError:
        pass
    data = _json_file(os.path.join(epic, "UnrealEngineLauncher", "LauncherInstalled.dat"))
    if isinstance(data, dict):
        for item in data.get("InstallationList") or []:
            if isinstance(item, dict) and item.get("InstallLocation") and not str(item.get("AppName", "")).startswith("UE_"):
                dirs.add(_norm(item["InstallLocation"]))
    hklm = winreg.HKEY_LOCAL_MACHINE
    for path, names in ((r"SOFTWARE\WOW6432Node\GOG.com\Games", ("path",)),
                        (r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs", ("InstallDir",)),
                        (r"SOFTWARE\WOW6432Node\EA Games", ("Install Dir",)),
                        (r"SOFTWARE\WOW6432Node\Rockstar Games", ("InstallFolder",))):
        dirs.update(_norm(v) for v in _subkey_values(hklm, path, names))
    return dirs


def _in_dirs(location, dirs):
    n = _norm(location) if location else None
    return bool(n) and any(n == d or n.startswith(d + "\\") for d in dirs)


def _registry_apps():
    wanted = ("DisplayName", "UninstallString", "QuietUninstallString", "InstallLocation", "DisplayIcon",
              "EstimatedSize", "Publisher", "SystemComponent", "ParentKeyName", "ReleaseType")
    seen, out = set(), []
    for hive, path in UNINSTALL_KEYS:
        try:
            base = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with base:
            for i in range(winreg.QueryInfoKey(base)[0]):
                try:
                    with winreg.OpenKey(base, winreg.EnumKey(base, i)) as key:
                        v = {n: _reg_value(key, n) for n in wanted}
                except OSError:
                    continue
                name = " ".join(str(v["DisplayName"] or "").split())
                command = v["UninstallString"] or v["QuietUninstallString"]
                if (not name or not command or v["SystemComponent"] == 1 or v["ParentKeyName"]
                        or v["ReleaseType"] in ("Update", "Hotfix", "Security Update")
                        or re.match(r"(KB\d+|Update for|Security Update)", name) or name.lower() in seen):
                    continue
                seen.add(name.lower())
                location = _unquote_path(v["InstallLocation"])
                if not location:
                    icon = _unquote_path(v["DisplayIcon"])
                    if icon:
                        location = os.path.dirname(icon) if os.path.splitext(icon)[1] else icon
                        if location and _usable_location(location):
                            location = _install_root(location, name)
                if location and not _usable_location(location):
                    location = None
                estimate = v["EstimatedSize"] * 1024 if isinstance(v["EstimatedSize"], int) else 0
                out.append({"name": name, "publisher": str(v["Publisher"] or ""), "location": location,
                            "estimate": estimate, "command": re.sub(r"(?i)msiexec(\.exe)?\s+/i", "MsiExec.exe /X",
                                                                    str(command)),
                            "uri": None, "uwp": False})
    return out


def _appx_publisher(location, subject):
    """Store apps carry their publisher in two places: a friendly name in the app's manifest (sometimes only a
    resource reference, which can't be read), and the signing certificate's subject, e.g.
    'CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond'. Use the first one that gives a real name."""
    try:
        with open(os.path.join(location, "AppxManifest.xml"), encoding="utf-8", errors="replace") as f:
            m = re.search(r"<PublisherDisplayName>\s*([^<]+?)\s*</PublisherDisplayName>", f.read())
        if m and not m.group(1).lower().startswith("ms-resource"):
            return m.group(1)
    except OSError:
        pass
    for field in ("O", "CN"):
        m = re.search(r'(?:^|,\s*)' + field + r'=("([^"]+)"|[^,]+)', subject or "")
        if m:
            return (m.group(2) or m.group(1)).strip()
    return ""


def _appx_apps():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", APPX_SCRIPT],
                           capture_output=True, text=True, creationflags=NOWIN | BELOW_NORMAL, timeout=60)
        data = json.loads(r.stdout) if r.stdout.strip() else []
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    return [{"name": d["name"], "publisher": _appx_publisher(d["location"], d.get("publisher")),
             "location": d["location"], "estimate": 0,
             "command": None, "uri": "ms-settings:appsfeatures", "uwp": True} for d in data if d.get("name")]


def _steam_apps():
    """Steam games from each library's appmanifest files (Steam does not register uninstallers)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = _reg_value(key, "SteamPath")
    except OSError:
        return []
    if not steam:
        return []
    libraries = {_norm(steam): steam}
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            for raw in re.findall(r'"path"\s+"([^"]+)"', f.read()):
                path = raw.replace("\\\\", "\\")
                libraries.setdefault(_norm(path), path)
    except OSError:
        pass
    out = []
    for library in libraries.values():
        folder = os.path.join(library, "steamapps")
        try:
            names = [n for n in os.listdir(folder) if n.startswith("appmanifest_") and n.endswith(".acf")]
        except OSError:
            continue
        for n in names:
            try:
                with open(os.path.join(folder, n), encoding="utf-8", errors="replace") as f:
                    text = f.read()
                app_id = re.search(r'"appid"\s+"(\d+)"', text).group(1)
                name = re.search(r'"name"\s+"([^"]+)"', text).group(1)
                install = re.search(r'"installdir"\s+"([^"]+)"', text).group(1)
            except (OSError, AttributeError):
                continue
            size = re.search(r'"SizeOnDisk"\s+"(\d+)"', text)
            out.append({"name": name.replace("\\\\", "\\"), "publisher": "Steam",
                        "location": os.path.join(folder, "common", install), "estimate": int(size.group(1)) if size else 0,
                        "command": None, "uri": f"steam://uninstall/{app_id}", "uwp": False})
    return out


def installed_apps():
    """Installed desktop apps, Steam games and Store apps with sizes: list of dicts."""
    items = _registry_apps() + _steam_apps() + _appx_apps()
    with ThreadPoolExecutor(4, initializer=lower_priority) as pool:
        sizes = list(pool.map(lambda it: size_of(it["location"]) if it["location"] else it["estimate"], items))
    launch = launcher_game_dirs()
    kept, seen = [], set()
    for it, size in sorted(zip(items, sizes), key=lambda p: p[1], reverse=True):
        key = _norm(it["location"]) if it["location"] else None
        if size < MIN_APP_BYTES or (key and key in seen):  # apps with no known folder never count as duplicates
            continue
        seen.add(key)
        it["size"] = size
        it["game"] = _in_dirs(it["location"], launch) or _is_game(it["name"], it["location"], it["publisher"]) or "\\xboxgames\\" in f"{it['location']}\\".lower()
        kept.append(it)
    # Drop apps that merely contain other listed apps (a launcher holding its games, for example).
    inside = {_norm(it["location"]) + "\\": it["size"] for it in kept if it["location"]}
    result = []
    for it in kept:
        mine = _norm(it["location"]) + "\\" if it["location"] else None
        if mine:
            inner = sum(sz for p, sz in inside.items() if p != mine and p.startswith(mine))
            if inner and inner >= 0.6 * it["size"]:  # mostly other listed apps: a launcher and its games
                continue
        result.append(it)
    return result


CATEGORIES = ("Apps", "Games", "Documents & files", "System", "Other")
SYSTEM_ENTRIES = {"windows", "$recycle.bin", "system volume information", "recovery", "$windows.~bt",
                  "$winreagent", "windows.old", "pagefile.sys", "hiberfil.sys", "swapfile.sys", "config.msi",
                  "msocache", "$sysreset", "boot", "bootmgr", "perflogs", "$sysreset", "documents and settings"}
APP_ENTRIES = {"program files", "program files (x86)", "programdata", "windowsapps"}


def _top_category(name):
    name = name.lower()
    if name in SYSTEM_ENTRIES:
        return "System"
    if name in APP_ENTRIES:
        return "Apps"
    if name == "users":
        return "Documents & files"
    if any(w in name for w in ("game", "steam", "epic", "gog", "riot")):
        return "Games"
    return "Other"


def categorize(root, entries, apps, used):
    """Bytes used per category. Apps found in other folders (a user profile, a library) are moved
    out of the folder bucket they live in, so the total never grows."""
    cats = dict.fromkeys(CATEGORIES, 0)
    bucket = {}
    for path, size, _ in entries:
        cat = _top_category(os.path.basename(path))
        cats[cat] += size
        bucket[_norm(path)] = cat
    for app in apps:
        if not app["location"]:
            continue
        rel = os.path.relpath(app["location"], root)
        source = bucket.get(_norm(os.path.join(root, rel.split("\\")[0])))
        target = "Games" if app["game"] else "Apps"
        if source and source != target:
            moved = min(app["size"], cats[source])
            cats[source] -= moved
            cats[target] += moved
    # Locked files and protected folders cannot be read; they are system reserved space. The reverse also
    # happens: a folder walk counts every hard link (the Windows component store, WinSxS, is full of them)
    # as a separate file, so Windows reads bigger than it is. Trim that surplus from System so the
    # categories never add up to more than the drive actually uses.
    cats["System"] = max(0, cats["System"] + used - sum(cats.values()))
    return [(name, cats[name]) for name in CATEGORIES if cats[name] > 0]


def analyze(root, apps=None):
    """Category breakdown, top-level hogs, the biggest items in the user profile (if on that drive)
    and the largest installed apps and games on the drive."""
    entries = sized_children(root)
    biggest = sorted(entries, key=lambda r: r[1], reverse=True)[:10]
    result = {"root": biggest, "user": [], "categories": [], "apps": []}
    drive = os.path.splitdrive(root)[0].upper()
    profile = os.path.expanduser("~")
    if os.path.splitdrive(profile)[0].upper() == drive:
        result["user"] = largest(profile, 10)
    try:
        total, used, _ = shutil.disk_usage(root)
        if callable(apps):  # lazy: lets the folder scan overlap with the installed-apps lookup
            apps = apps()
        elif apps is None:
            apps = installed_apps()
    except OSError:
        total, used, apps = 0, sum(r[1] for r in entries), []
    system_drive = os.path.splitdrive(os.environ.get("SystemRoot", "C:\\Windows"))[0].upper()
    here = [a for a in apps if (os.path.splitdrive(a["location"])[0].upper() if a["location"] else system_drive) == drive]
    result["categories"] = categorize(root, entries, [a for a in here if a["location"]], used)
    result["apps"] = here[:15]
    return result
