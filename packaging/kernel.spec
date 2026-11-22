# -*- mode: python ; coding: utf-8 -*-
# Lives in <root>\packaging; the source is in <root>\kernel. Paths below are resolved from this file's folder.
import os

K = os.path.abspath(os.path.join(SPECPATH, '..', 'kernel'))


a = Analysis(
    [os.path.join(K, 'kernel.py')],
    pathex=[K],
    binaries=[],
    datas=[(os.path.join(K, 'icon.ico'), '.'), (os.path.join(K, 'icon.png'), '.'),
           (os.path.join(K, 'fonts'), 'fonts'), (os.path.join(K, 'assets'), 'assets'),
           (os.path.join(K, 'monitor-helper', 'out', 'kernel-monitor.exe'), 'monitor-helper')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Kernel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    uac_admin=True,
    icon=[os.path.join(K, 'icon.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Kernel',
)
