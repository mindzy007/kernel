import ctypes
import os
import shutil
import string
from concurrent.futures import ThreadPoolExecutor


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


def largest(folder, limit=10):
    """Biggest direct children of folder: [(path, bytes, is_dir)]."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    with ThreadPoolExecutor(8) as pool:
        sized = [r for r in pool.map(_entry_size, entries) if r]
    sized.sort(key=lambda r: r[1], reverse=True)
    return sized[:limit]


def analyze(root):
    """Top-level hogs for a drive plus the biggest items inside the user profile (if on that drive)."""
    result = {"root": largest(root, 10), "user": []}
    profile = os.path.expanduser("~")
    if os.path.splitdrive(profile)[0].upper() == os.path.splitdrive(root)[0].upper():
        result["user"] = largest(profile, 10)
    return result
