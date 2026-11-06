# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/Users/alex/Documents/GitHub/kernel/kernel.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/alex/Documents/GitHub/kernel/icon.ico', '.'), ('C:/Users/alex/Documents/GitHub/kernel/icon.png', '.'), ('C:/Users/alex/Documents/GitHub/kernel/fonts', 'fonts'), ('C:/Users/alex/Documents/GitHub/kernel/assets', 'assets'), ('C:/Users/alex/Documents/GitHub/kernel/monitor-helper/out/kernel-monitor.exe', 'monitor-helper')],
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
    a.binaries,
    a.datas,
    [],
    name='Kernel-Portable-26.11.4.4',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='C:/Users/alex/Documents/GitHub/kernel/version_info.txt',
    uac_admin=True,
    icon=['C:/Users/alex/Documents/GitHub/kernel/icon.ico'],
)
