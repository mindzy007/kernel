"""App catalog, winget helpers and a direct downloader.

Installers come straight from each publisher: the URL and SHA256 are read from the winget
manifest, the file is fetched from the publisher's own server and the hash is verified.
"""
import hashlib
import json
import os
import re
import shutil
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
from urllib.parse import unquote, urlparse

NOWIN = 0x08000000  # CREATE_NO_WINDOW


def _ssl_context():
    """Windows' certificate store plus certifi's bundle: a fresh PC or VM often lacks the root
    certificates GitHub and CDNs use until Windows fetches them, which breaks HTTPS downloads."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


_SSL = _ssl_context()


def _open(req, timeout=30):
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL)

# Apps with no winget package: a stable publisher link that redirects to the current installer
DIRECT = {"UniversalAudio.UAConnect": "https://www.uaudio.com/apps/uaconnect/win/installer"}

# Apps published only as GitHub releases: id -> (owner/repo, regex for the installer asset's file name)
GITHUB = {"krisgfx9.WirelessBattery": ("krisgfx9/Wireless-Device-Battery-Monitor",
                                       r"^WirelessBattery-Setup-.*\.exe$")}

# (category, display name, winget id, website used for the icon)
CATALOG = [
    ("Browsers", "Google Chrome", "Google.Chrome", "chrome.google.com"),
    ("Browsers", "Mozilla Firefox", "Mozilla.Firefox", "firefox.com"),
    ("Browsers", "Brave", "Brave.Brave", "brave.com"),
    ("Browsers", "Zen Browser", "Zen-Team.Zen-Browser", "zen-browser.app"),
    ("Browsers", "Vivaldi", "Vivaldi.Vivaldi", "vivaldi.com"),
    ("Communication", "Discord", "Discord.Discord", "discord.com"),
    ("Communication", "Zoom", "Zoom.Zoom", "zoom.us"),
    ("Communication", "Telegram", "Telegram.TelegramDesktop", "telegram.org"),
    ("Communication", "Slack", "SlackTechnologies.Slack", "slack.com"),
    ("Communication", "Vencord", "Vendicated.Vencord", "vencord.dev"),
    ("Gaming", "Steam", "Valve.Steam", "store.steampowered.com"),
    ("Gaming", "Epic Games Launcher", "EpicGames.EpicGamesLauncher", "epicgames.com"),
    ("Gaming", "GOG Galaxy", "GOG.Galaxy", "gog.com"),
    ("Gaming", "Ubisoft Connect", "Ubisoft.Connect", "ubisoft.com"),
    ("Gaming", "EA app", "ElectronicArts.EADesktop", "ea.com"),
    ("Gaming", "Battle.net", "Blizzard.BattleNet", "battle.net"),
    ("Hardware & firmware", "NVIDIA Profile Inspector", "Orbmu2k.nvidiaProfileInspector", "nvidia.com"),
    ("Hardware & firmware", "Display Driver Uninstaller", "Wagnardsoft.DisplayDriverUninstaller", "wagnardsoft.com"),
    ("Hardware & firmware", "Razer Synapse 4", "RazerInc.RazerInstaller.Synapse4", "razer.com"),
    ("Hardware & firmware", "NZXT CAM", "NZXT.CAM", "nzxt.com"),
    ("Hardware & firmware", "L-Connect 3", "LianLi.LConnect3", "lian-li.com"),
    ("Hardware & firmware", "Elgato Stream Deck", "Elgato.StreamDeck", "elgato.com"),
    ("Hardware & firmware", "UA Connect", "UniversalAudio.UAConnect", "uaudio.com"),
    ("Hardware & firmware", "Logitech G HUB", "Logitech.GHUB", "logitechg.com"),
    ("Hardware & firmware", "Corsair iCUE 5", "Corsair.iCUE.5", "corsair.com"),
    ("Hardware & firmware", "SteelSeries GG", "SteelSeries.GG", "steelseries.com"),
    ("Hardware & firmware", "Focusrite Control 2", "FocusriteAudioEngineeringLtd.FocusriteControl2", "focusrite.com"),
    ("Hardware & firmware", "Crucial Storage Executive", "Crucial.StorageExecutive", "crucial.com"),
    ("Hardware & firmware", "Kingston SSD Manager", "Kingston.SSDManager", "kingston.com"),
    ("Hardware & firmware", "DirectX", "Microsoft.DirectX", "microsoft.com"),
    ("Hardware & firmware", "MSI Center", "MSI.MSICenter", "msi.com"),
    ("Media", "OBS Studio", "OBSProject.OBSStudio", "obsproject.com"),
    ("Media", "VLC", "VideoLAN.VLC", "videolan.org"),
    ("Media", "Spotify", "Spotify.Spotify", "spotify.com"),
    ("Media", "Audacity", "Audacity.Audacity", "audacityteam.org"),
    ("Media", "HandBrake", "HandBrake.HandBrake", "handbrake.fr"),
    ("Utilities", "NanaZip", "M2Team.NanaZip", "github.com"),
    ("Utilities", "Notepad++", "Notepad++.Notepad++", "notepad-plus-plus.org"),
    ("Utilities", "Everything", "voidtools.Everything", "voidtools.com"),
    ("Utilities", "PowerToys", "Microsoft.PowerToys", "microsoft.com"),
    ("Utilities", "ShareX", "ShareX.ShareX", "getsharex.com"),
    ("Utilities", "qBittorrent", "qBittorrent.qBittorrent", "qbittorrent.org"),
    ("Utilities", "WinDirStat", "WinDirStat.WinDirStat", "windirstat.net"),
    ("Utilities", "Rufus", "Rufus.Rufus", "rufus.ie"),
    ("Utilities", "balenaEtcher", "Balena.Etcher", "etcher.balena.io"),
    ("Utilities", "AnyDesk", "AnyDesk.AnyDesk", "anydesk.com"),
    ("Utilities", "CPU-Z", "CPUID.CPU-Z", "cpuid.com"),
    ("Utilities", "HWMonitor", "CPUID.HWMonitor", "cpuid.com"),
    ("Utilities", "GPU-Z", "TechPowerUp.GPU-Z", "techpowerup.com"),
    ("Utilities", "Blur AutoClicker", "Blur009.BlurAutoClicker", "github.com"),
    ("Utilities", "IObit Unlocker", "IObit.IObitUnlocker", "iobit.com"),
    ("Utilities", "MacType", "MacType.MacType", "github.com"),
    ("Utilities", "ParkControl", "BitSum.ParkControl", "bitsum.com"),
    ("Utilities", "Wireless Battery", "krisgfx9.WirelessBattery", "github.com"),
    ("Utilities", "Cloudflare WARP", "Cloudflare.Warp", "cloudflare.com"),
    ("Utilities", "Malwarebytes", "Malwarebytes.Malwarebytes", "malwarebytes.com"),
    ("Developer", "Git", "Git.Git", "git-scm.com"),
    ("Developer", "Visual Studio Code", "Microsoft.VisualStudioCode", "code.visualstudio.com"),
    ("Developer", "Windows Terminal", "Microsoft.WindowsTerminal", "microsoft.com"),
    ("Developer", "Python 3", "Python.Python.3.13", "python.org"),
    ("Developer", "Node.js LTS", "OpenJS.NodeJS.LTS", "nodejs.org"),
    ("Developer", "GitHub Desktop", "GitHub.GitHubDesktop", "desktop.github.com"),
    ("Developer", "WinSCP", "WinSCP.WinSCP", "winscp.net"),
    ("Media", "Figma", "Figma.Figma", "figma.com"),
]

VCREDISTS = [
    "Microsoft.VCRedist.2005.x86", "Microsoft.VCRedist.2005.x64",
    "Microsoft.VCRedist.2008.x86", "Microsoft.VCRedist.2008.x64",
    "Microsoft.VCRedist.2010.x86", "Microsoft.VCRedist.2010.x64",
    "Microsoft.VCRedist.2012.x86", "Microsoft.VCRedist.2012.x64",
    "Microsoft.VCRedist.2013.x86", "Microsoft.VCRedist.2013.x64",
    "Microsoft.VCRedist.2015+.x86", "Microsoft.VCRedist.2015+.x64",
]

COMMON = ["--source", "winget", "--accept-source-agreements", "--disable-interactivity"]
OK_HINTS = ("no available upgrade", "newer version", "already installed", "no applicable upgrade")
PCT = re.compile(r"(\d{1,3})\s*%")
EXT = {"msi": "msi", "wix": "msi", "msix": "msix", "appx": "msix", "zip": "zip"}
ICON_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "kernel", "icons")


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def winget_path():
    return shutil.which("winget")


def _noise(s):
    if not s or set(s) <= set("-\\|/ "):
        return True
    return "█" in s or "▒" in s or bool(re.match(r"^\d+\s*%$", s)) or \
        bool(re.match(r"^[\d.]+\s*[KMG]?B\s*/\s*[\d.]+\s*[KMG]?B$", s))


def winget(args, job=None, track=True):
    """Run winget with stdin closed (a hidden-console app has no valid stdin, which can stall it)."""
    exe = winget_path()
    if not exe:
        if job:
            job.log("winget was not found. Install 'App Installer' from the Microsoft Store.")
        return 1, ""
    p = subprocess.Popen([exe] + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=NOWIN)
    if job:
        job.proc = p
    lines = []
    for raw in p.stdout:
        s = raw.strip()
        if job and job.cancelled:
            break
        if job and track:
            m = PCT.search(s)
            if m and ("█" in s or "▒" in s or s.endswith("%")):
                pct = min(int(m.group(1)), 100)
                if pct >= 100:
                    job.update(progress=-1, message="Installing…")
                else:
                    job.update(progress=pct / 100, message=f"Downloading · {pct}%")
            elif "starting package install" in s.lower():
                job.update(progress=-1, message="Installing…")
        if _noise(s):
            continue
        lines.append(s)
        if job:
            job.log("   " + s)
    p.wait()
    return p.returncode, "\n".join(lines)


def _ok(rc, text):
    return rc == 0 or any(h in text.lower() for h in OK_HINTS)


def _clean(v):
    """YAML values can come quoted ("160.0"); drop the quotes."""
    return (v or "").strip().strip("\"'")


def _version_key(v):
    return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+_]", v)]


def _github_manifest(pkg_id):
    """Installer URL / hash straight from the winget-pkgs repository when winget itself is
    missing or not initialised yet (common on a fresh install)."""
    api = "https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests/"
    parts = pkg_id.split(".")
    path = f"{parts[0][0].lower()}/" + "/".join(parts)
    for _ in range(4):
        data, _ = _get(api + path)
        if not data:
            return {}
        try:
            entries = json.loads(data)
        except ValueError:
            return {}
        installer = next((e for e in entries if e["name"].endswith(".installer.yaml")), None)
        if installer:
            text, _ = _get(installer["download_url"])
            break
        dirs = [e["name"] for e in entries if e["type"] == "dir"]
        if not dirs:
            return {}
        path += "/" + max(dirs, key=_version_key)
    else:
        return {}
    text = text.decode("utf-8", "replace") if text else ""
    version = re.search(r"^PackageVersion:\s*(\S+)", text, re.M)
    top_type = re.search(r"^InstallerType:\s*(\S+)", text, re.M)
    blocks = re.split(r"^\s*-\s+(?=Architecture:)", text, flags=re.M)[1:]
    chosen = next((b for b in blocks if re.search(r"Architecture:\s*x64", b)), blocks[0] if blocks else "")
    url = re.search(r"InstallerUrl:\s*(\S+)", chosen)
    if not url:
        return {}
    sha = re.search(r"InstallerSha256:\s*([0-9a-fA-F]{64})", chosen)
    kind = re.search(r"InstallerType:\s*(\S+)", chosen) or top_type
    return {"url": _clean(url.group(1)), "version": _clean(version.group(1)) if version else "",
            "sha": sha.group(1) if sha else None, "type": (kind.group(1) if kind else "").lower(),
            "home": None}


def _github_release(repo, pattern):
    """Newest release's installer straight from GitHub: URL, version (tag) and SHA256 when GitHub lists one.
    Looks at the newest releases (pre-releases included) and takes the first with a matching installer."""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=10",
                                     headers={"User-Agent": "kernel/1.0", "Accept": "application/vnd.github+json"})
        with _open(req, timeout=20) as r:
            releases = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return {}
    for rel in releases:
        if rel.get("draft"):
            continue
        asset = next((a for a in rel.get("assets", []) if re.search(pattern, a.get("name", ""), re.I)), None)
        if not asset:
            continue
        digest = asset.get("digest") or ""
        sha = digest.split(":", 1)[1] if digest.lower().startswith("sha256:") else None
        return {"url": asset["browser_download_url"], "version": _clean(rel.get("tag_name", "")).lstrip("vV"),
                "sha": sha, "type": "inno", "home": f"https://github.com/{repo}"}
    return {}


def show(pkg_id):
    """Read the publisher installer URL / hash / homepage from the winget manifest."""
    if pkg_id in DIRECT:
        return _direct(DIRECT[pkg_id])
    if pkg_id in GITHUB:
        return _github_release(*GITHUB[pkg_id])
    rc, text = winget(["show", "--id", pkg_id, "-e", "--source", "winget",
                       "--accept-source-agreements"]) if winget_path() else (1, "")
    if rc != 0:
        return _github_manifest(pkg_id)

    def grab(pattern):
        m = re.search(pattern, text, re.I | re.M)
        return m.group(1) if m else None
    return {
        "url": _clean(grab(r"Installer Url:\s*(\S+)")) or None,
        "version": _clean(grab(r"^Version:\s*(\S+)")) or None,
        "sha": grab(r"Installer SHA256:\s*([0-9a-f]{64})"),
        "type": (grab(r"Installer Type:\s*(\S+)") or "").lower(),
        "home": grab(r"Homepage:\s*(\S+)") or grab(r"Publisher Url:\s*(\S+)"),
    }


def _direct(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 kernel/1.0"})
        with _open(req, timeout=20) as r:
            final = r.geturl()
    except Exception:
        return {}
    m = re.search(r"(\d+(?:[_.]\d+)+)", unquote(os.path.basename(urlparse(final).path)))
    version = m.group(1).replace("_", ".") if m else ""
    return {"url": final, "version": version, "sha": None, "type": "exe", "home": None}


def _headers(url):
    # SourceForge only redirects to the real file for command-line style clients
    ua = "Wget/1.21" if "sourceforge.net" in (urlparse(url).hostname or "") else "Mozilla/5.0 kernel/1.0"
    return {"User-Agent": ua}


def remote_size(url):
    """Installer size in bytes without downloading it (HEAD, then a 1-byte ranged GET)."""
    for method, extra in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
        try:
            req = urllib.request.Request(url, method=method, headers={**_headers(url), **extra})
            with _open(req, timeout=15) as r:
                if "text/html" in r.headers.get("Content-Type", ""):
                    continue
                rng = r.headers.get("Content-Range", "")
                if "/" in rng and rng.rsplit("/", 1)[1].isdigit():
                    return int(rng.rsplit("/", 1)[1])
                n = int(r.headers.get("Content-Length") or 0)
                if n > 1:
                    return n
        except Exception:
            continue
    return 0


INFO_FILE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "kernel", "info.json")
INFO_TTL = 2 * 3600  # how long a looked-up version/size stays trusted
_info_lock = threading.Lock()
_sources_lock = threading.Lock()
_sources_done = False


def _update_sources():
    try:
        winget(["source", "update", "--name", "winget"], track=False)
    except Exception:
        pass


def refresh_sources():
    """Once per run, refresh winget's package index in the background. Lookups never wait for it: the first ones
    may still see the old index, later ones (and the next launch) see the fresh one."""
    global _sources_done
    with _sources_lock:
        if _sources_done or not winget_path():
            return
        _sources_done = True
    threading.Thread(target=_update_sources, daemon=True).start()


def _info_cache():
    try:
        with open(INFO_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def info(pkg_id, refresh=False):
    """(version, size_bytes, source_url) of the installer that Download would fetch.
    Cached for 2 hours, but an unknown size is retried after a few minutes."""
    with _info_lock:
        hit = _info_cache().get(pkg_id)
    if hit and not refresh and hit.get("url"):
        age = time.time() - hit["t"]
        if age < (INFO_TTL if hit["size"] else 300):
            return _clean(hit["version"]), hit["size"], hit["url"]
    refresh_sources()
    data = show(pkg_id) or show(pkg_id)
    version = _clean(data.get("version"))
    url = _clean(data.get("url"))
    size = remote_size(url) if url else 0
    if size < 100 * 1024:  # redirect/landing pages, not the real installer
        size = 0
    if version and url:
        with _info_lock:
            cache = _info_cache()
            cache[pkg_id] = {"version": version, "size": size, "url": url, "t": time.time()}
            os.makedirs(os.path.dirname(INFO_FILE), exist_ok=True)
            with open(INFO_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f)
    elif hit:
        return hit["version"], hit["size"], hit.get("url", "")
    return version, size, url


def fetch_file(url, dest_dir, sha, kind, job):
    os.makedirs(dest_dir, exist_ok=True)
    name = unquote(os.path.basename(urlparse(url).path)) or "installer"
    if "." not in name:
        name += "." + EXT.get(kind, "exe")
    final = os.path.join(dest_dir, name)
    part = final + ".part"
    req = urllib.request.Request(url, headers=_headers(url))
    ok = False
    try:
        with _open(req, timeout=30) as r, open(part, "wb") as f:
            if "text/html" in r.headers.get("Content-Type", ""):
                raise RuntimeError("The publisher returned a web page instead of the installer")
            total = int(r.headers.get("Content-Length") or 0)
            h = hashlib.sha256()
            done = last_done = 0
            last = time.monotonic()
            speed = 0.0
            while not job.cancelled:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last >= 0.25:
                    speed = (done - last_done) / (now - last)
                    last, last_done = now, done
                    size = f"{human(done)} of {human(total)}" if total else human(done)
                    pct = f"{done * 100 // total}% · " if total else ""
                    job.update(progress=done / total if total else -1,
                               message=f"{pct}{size} · {human(speed)}/s")
        if job.cancelled:
            return False
        stale = bool(sha) and h.hexdigest().lower() != sha.lower()
        if stale:
            job.log("   Note: the publisher's file is newer than the winget manifest (its hash is out of date). "
                    "It was still downloaded over HTTPS from the publisher's own server.")
        os.replace(part, final)
        job.path, job.size = final, done
        job.done_msg = f"Saved · {human(done)}" + (" · latest from publisher" if stale else "")
        ok = True
        return True
    finally:
        if not ok and os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass


def download(pkg_id, dest, job):
    job.update(progress=-1, message="Looking up publisher download…")
    info = show(pkg_id)
    if job.cancelled:
        return False
    if info.get("url"):
        job.log(f"Downloading {pkg_id} from {urlparse(info['url']).hostname}")
        return fetch_file(info["url"], dest, info.get("sha"), info.get("type"), job)
    job.log(f"No direct URL for {pkg_id}, using winget download")
    rc, text = winget(["download", "--id", pkg_id, "-e", "-d", dest,
                       "--accept-package-agreements"] + COMMON, job)
    if _ok(rc, text):
        job.path, job.done_msg = dest, "Saved"
        return True
    job.fail_msg = f"Failed (exit {rc})"
    return False


def install_github(pkg_id, job):
    """Download the release installer (hash-checked when GitHub gives one) and run it silently (Inno Setup)."""
    job.update(progress=-1, message="Looking up latest release…")
    info = show(pkg_id)
    if not info.get("url"):
        job.fail_msg = "Could not reach GitHub"
        return False
    dest = os.path.join(tempfile.gettempdir(), "kernel-installers")
    job.log(f"Downloading {pkg_id} {info.get('version', '')} from github.com")
    if not fetch_file(info["url"], dest, info.get("sha"), "exe", job):
        return False
    job.update(progress=-1, message="Installing…")
    try:
        rc = subprocess.run([job.path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                            creationflags=NOWIN).returncode
    except OSError as e:
        job.fail_msg = f"Could not run the installer ({e.strerror})"
        return False
    job.done_msg, job.fail_msg = "Installed", f"Failed (exit {rc})"
    return rc == 0


def install(pkg_id, job):
    if pkg_id in GITHUB:
        return install_github(pkg_id, job)
    if pkg_id in DIRECT:
        return install_direct(pkg_id, job)
    job.update(progress=-1, message="Starting…")
    job.log(f"Installing {pkg_id}")
    rc, text = winget(["install", "--id", pkg_id, "-e", "--silent",
                       "--accept-package-agreements"] + COMMON, job)
    if job.cancelled:
        return False
    if not _ok(rc, text) and "hash" in text.lower():
        # Publisher ships a newer file than the manifest hash (rolling installers like Chrome)
        job.log("   Manifest hash is out of date, retrying with the publisher's current installer")
        winget(["settings", "--enable", "InstallerHashOverride"], job, False)
        rc, text = winget(["install", "--id", pkg_id, "-e", "--silent", "--ignore-security-hash",
                           "--accept-package-agreements"] + COMMON, job)
        if job.cancelled:
            return False
    ok = _ok(rc, text)
    job.done_msg, job.fail_msg = "Installed", f"Failed (exit {rc})"
    return ok


def install_direct(pkg_id, job):
    job.update(progress=-1, message="Looking up publisher download?")
    info = show(pkg_id)
    if not info.get("url"):
        job.fail_msg = "Could not reach the publisher"
        return False
    dest = os.path.join(tempfile.gettempdir(), "kernel-installers")
    if not fetch_file(info["url"], dest, None, "exe", job):
        return False
    job.update(progress=-1, message="Launching installer?")
    os.startfile(job.path)
    job.done_msg = "Installer opened"
    return True


def vcredists(job):
    """Install missing C++ redistributables and repair the ones already present."""
    failed = 0
    for n, pkg in enumerate(VCREDISTS):
        if job.cancelled:
            return False
        job.update(progress=n / len(VCREDISTS), message=f"{pkg.split('.', 2)[2]} ({n + 1}/{len(VCREDISTS)})")
        rc, _ = winget(["list", "--id", pkg, "-e", "--accept-source-agreements"], job, False)
        if rc == 0:
            job.log(f"{pkg}: installed, repairing")
            rc, text = winget(["repair", "--id", pkg, "-e", "--accept-package-agreements"] + COMMON,
                              job, False)
            if not _ok(rc, text) and not job.cancelled:
                rc, text = winget(["install", "--id", pkg, "-e", "--force", "--silent",
                                   "--accept-package-agreements"] + COMMON, job, False)
        else:
            job.log(f"{pkg}: missing, installing")
            rc, text = winget(["install", "--id", pkg, "-e", "--silent",
                               "--accept-package-agreements"] + COMMON, job, False)
        if not _ok(rc, text) and not job.cancelled:
            failed += 1
            job.log(f"   failed (exit {rc})")
    if job.cancelled:
        return False
    job.done_msg = "All installed and repaired"
    job.fail_msg = f"{failed} package(s) failed"
    return failed == 0


def search(query, limit=25):
    """Return [(name, id, version)] from the winget source."""
    rc, text = winget(["search", query, "--source", "winget", "--accept-source-agreements"])
    lines = text.splitlines()
    head = next((i for i, l in enumerate(lines) if l.startswith("Name") and "Id" in l), None)
    if head is None:
        return []
    h = lines[head]
    i_id, i_ver = h.index("Id"), h.index("Version")
    out = []
    for l in lines[head + 1:]:
        if len(l) <= i_ver:
            continue
        ver = l[i_ver:].split()
        out.append((l[:i_id].strip(), l[i_id:i_ver].strip(), ver[0] if ver else ""))
        if len(out) >= limit:
            break
    return out


def _get(url, timeout=12, limit=2_000_000):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 kernel/1.0"})
        with _open(req, timeout=timeout) as r:
            return r.read(limit), r.headers.get("Content-Type", "")
    except Exception:
        return None, ""


def _image_px(data):
    """Largest side in pixels of a PNG / ICO / GIF / JPEG, read from the header (0 if unknown)."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return max(struct.unpack(">II", data[16:24]))
        if data[:4] == b"\x00\x00\x01\x00":
            count = struct.unpack("<H", data[4:6])[0]
            return max((data[6 + 16 * i] or 256) for i in range(count))
        if data[:3] == b"GIF":
            return max(struct.unpack("<HH", data[6:10]))
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return max(h, w)
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except Exception:
        pass
    return 0


def _site_icons(domain):
    """Icon URLs the site offers (page links, web app manifest, apple-touch defaults), biggest first."""
    from urllib.parse import urljoin
    base = f"https://{domain}/"
    data, _ = _get(base, limit=400_000)
    html = data.decode("utf-8", "replace") if data else ""
    found = {}

    def add(url, score):
        found[url] = max(score, found.get(url, 0))

    manifest = None
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        rel = re.search(r'rel=["\']([^"\']+)["\']', tag, re.I)
        href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        if not rel or not href:
            continue
        kind = rel.group(1).lower()
        if "manifest" in kind:
            manifest = urljoin(base, href.group(1))
            continue
        if "icon" not in kind or href.group(1).lower().split("?")[0].endswith(".svg"):
            continue
        sz = re.search(r'sizes=["\'](\d+)x', tag, re.I)
        add(urljoin(base, href.group(1)), (int(sz.group(1)) if sz else 0) + (1000 if "apple-touch" in kind else 0))
    if manifest:  # installable-web-app icons are usually 192px or 512px
        mdata, _ = _get(manifest, limit=200_000)
        try:
            for icon in json.loads(mdata.decode("utf-8", "replace")).get("icons", []):
                src = icon.get("src", "")
                if src.lower().split("?")[0].endswith(".svg"):
                    continue
                biggest = max([int(n) for n in re.findall(r"(\d+)x\d+", icon.get("sizes", ""))] or [0])
                add(urljoin(manifest, src), biggest + 1500)
        except Exception:
            pass
    add(urljoin(base, "/apple-touch-icon.png"), 1000)
    add(urljoin(base, "/apple-touch-icon-precomposed.png"), 900)
    return [u for u, _ in sorted(found.items(), key=lambda x: -x[1])]


def _valid_image(data, ctype):
    return bool(data) and len(data) > 120 and ("image" in ctype or data[:4] in (b"\x89PNG", b"\x00\x00\x01\x00")
                                               or data[:3] == b"GIF" or data[:2] == b"\xff\xd8")


def icon_bytes(key, domain=None, google_only=False):
    """Official icon for an app, cached on disk: the site's own icon first, then Google's favicon cache."""
    os.makedirs(ICON_DIR, exist_ok=True)
    path = os.path.join(ICON_DIR, re.sub(r"[^\w.+-]", "_", key) + ".v4.img")
    if google_only and os.path.exists(path):
        os.remove(path)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    if not domain:
        home = show(key).get("home")
        domain = urlparse(home).hostname if home else None
    guess = key.split(".")[0].lower() + ".com" if "." in key else None
    if not domain:
        domain = guess
    if not domain:
        return None
    data, best = None, 0
    for url in ([] if google_only else _site_icons(domain)[:6]):
        d, ct = _get(url)
        if _valid_image(d, ct):
            px = _image_px(d)
            if px > best:  # keep the sharpest one, not just the first that loads
                data, best = d, px
            if best >= 128:
                break
    if best < 96:  # nothing sharp on the site itself: ask Google's favicon cache for its biggest copy
        d, _ = _get("https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL"
                    f"&url=https://{domain}&size=256")
        if d and len(d) > 120 and _image_px(d) > best:
            data = d
    if not data:
        return None
    with open(path, "wb") as f:
        f.write(data)
    return data


