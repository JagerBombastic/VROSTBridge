# PyInstaller spec for the frozen Linux companion of VROSTBridge Client (VROSTBridge-Client).
# From the source root:
#
#   python3.12 -m PyInstaller --noconfirm --distpath /tmp/vrostbridge-frozen --workpath /tmp/pyi-work \
#       packaging/VROSTBridge-Client.spec
#
# The same options as the command line in packaging/README.md, plus one rule: launcher/x11_window.py
# (RC4.6) loads libX11 with ctypes at run time, and PyInstaller would bundle the build machine's
# libX11 and its dependencies for it. The companion must use the desktop's own X11 libraries (they
# come with every Bazzite desktop and Game Mode session), so they are left out of the bundle.
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is defined by PyInstaller)
SYSTEM_ONLY = ('libX11.so', 'libX11-xcb.so', 'libXau.so', 'libXdmcp.so', 'libxcb.so', 'libbsd.so', 'libmd.so')

a = Analysis(  # noqa: F821
    [str(ROOT / 'launcher/bootstrap.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / 'launcher/desktop.html'), 'launcher'),
           (str(ROOT / 'launcher/brand'), 'launcher/brand'),
           (str(ROOT / 'launcher/game_ui_assets'), 'launcher/game_ui_assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if not os.path.basename(entry[0]).startswith(SYSTEM_ONLY)]
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VROSTBridge-Client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VROSTBridge-Client',
)
