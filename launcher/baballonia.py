"""Baballonia, set up and run by VROSTBridge for Beyond 2e eye tracking (RC4.5).

Baballonia (Project Babble) turns the Beyond 2e eye-camera images into where each eye looks;
VROSTBridge's camera helper (go-bsb-cams) serves those images on 127.0.0.1:8880 and its gaze
relay receives Baballonia's VRChat-style OSC output on 127.0.0.1:9005. Baballonia is not part of
VROSTBridge and is not shipped with it: on request it is downloaded from Project Babble's own
GitHub release, checked against the pinned size and SHA-256, and kept under VROSTBridge's data
folder. Its licence is the Babble Software Distribution License 1.0 (non-commercial).

What VROSTBridge changes in Baballonia's own settings (~/.config/ProjectBabble), and only these:
- the two eye cameras: the helper's stream, read with Baballonia's own MJPEG reader (its OpenCV
  and GStreamer path waited 13 s and crashed in the sandbox runs), started automatically;
- the left/right halves of the Beyond 2e's side-by-side image, once (later crops are yours);
- the output: VRChat native eye tracking to 127.0.0.1:9005, never redirected by OSCQuery (it
  would follow VRChat on the host PC and skip VROSTBridge);
- no SteamVR auto-start, update check or GPU attempt (the Linux release runs on the CPU).

It runs as its own user service (systemd-run), outside Steam's app scope: in Game Mode gamescope
then never gives its window the focus over VROSTBridge's screen. It ignores SIGTERM (only its
background services stop, while inference goes on), so stopping uses SIGKILL after 3 s. Its OSC
sender also stops for good once a send is refused, so the relay must listen for as long as it
runs. A small shell watcher (WATCH_SCRIPT) runs it and ends it when the companion that started it
is gone, so a companion that crashed or was killed never leaves it running on battery. Calibration
stays Baballonia's: it trains a model from its own calibration in the headset.

Windows (1.0.0.7): Project Babble publishes Baballonia for Windows as an NSIS installer (WINDOWS_RELEASE), checked
like the Linux archive (size and SHA-256, after the licence was accepted) and run silently for this user into
VROSTBridge's own folder (%LOCALAPPDATA%\\VROSTBridge\\Baballonia\\<version>, /S /D=...). It may not start any
program (a job object allowing one process: its installer would start Baballonia when it finishes), and the uninstall
entry and shortcuts it writes are put back as they were, so VROSTBridge's copy stays its own (as on Linux) and a
Baballonia the person installed keeps its entry. A Baballonia the person installed of the pinned release (the same
files) is used instead of a download. Windows names the Beyond 2e's eye cameras (USB 35bd:0202, one 800 x 400
side-by-side picture) BigEye; Baballonia reads them itself through DirectShow (OpenCvCapture), with no camera helper:
both eyes read that one camera. Baballonia's settings are in %APPDATA%\\ProjectBabble. It runs one step down in
priority (BELOW_NORMAL_PRIORITY_CLASS), minimised without taking the focus, in a job object that ends it with the
companion; stopping closes its window (it saves its settings), and ends it after STOP_GRACE_WINDOWS_S. The
calibration launcher and Vulkan layer are Linux's: on Windows Baballonia shows its calibration as a SteamVR overlay
itself.
"""
import hashlib
import json
try:
    import lzma  # noqa: F401 - tarfile opens .tar.xz through it; imported so frozen builds include it
except ImportError:  # a Python without it cannot set up Baballonia, but imports this module (the Windows host)
    lzma = None
import ntpath
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

RELEASE = {
    'version': '1.1.1.0rc6',
    'url': 'https://github.com/Project-Babble/Baballonia/releases/download/v1.1.1.0rc6/'
           'Baballonia.x64.v1.1.1.0rc6.tar.xz',
    'size': 517439412,
    'sha256': '06c9ce7ecca19a0fee3ead40ef0c705ca173916e11a3315b99df63d348ea906a',
    'extracted_mb': 841,  # bytes of files once unpacked, in MB (803 MiB)
    'capture': 'IpCameraCapture',  # this release's name for its own MJPEG reader
}
# 1.0.0.7: the same release for Windows: Project Babble's NSIS installer (there is no Windows archive). Size and
# SHA-256 of the download as published (checked by downloading it in full, 2026-10-08); the installed size is the
# installer's 565 files plus its uninstaller. 'files' are three of the program files it installs, by SHA-256: how a
# Baballonia the person installed is recognised as this release ('display_version' is its uninstall entry's version).
WINDOWS_RELEASE = {
    'version': '1.1.1.0rc6',
    'url': 'https://github.com/Project-Babble/Baballonia/releases/download/v1.1.1.0rc6/'
           'Baballonia.Setup.v1.1.1.0rc6.exe',
    'size': 423324292,
    'sha256': 'dec2e8fd1e5c844662f99b47680d772a393f6eb2fdf4e4dd3c62853928b2c470',
    'extracted_mb': 877,  # bytes of files once installed, in MB (836 MiB)
    'capture': 'OpenCvCapture',  # this release's DirectShow reader on Windows
    'display_version': 'v1.1.1.0rc6',
    'files': {'Baballonia.Desktop.exe': '1160b576b5f96c9bba349fd2fcf2855c2bcd113ab0019870e8c99c97b025941d',
              'Baballonia.Desktop.dll': 'b020b0e2a2e41256e062a7b71c88082e95d6165b4430655e450a91f4bd9230bf',
              'Baballonia.dll': '0e9b6d05af4075aaa0f270fa470cc8eff487f6a0853bd255019210451a6a412b'},
}
LICENCE = 'Babble Software Distribution License 1.0 (non-commercial)'
LICENCE_URL = 'https://github.com/Project-Babble/Baballonia/blob/v1.1.1.0rc6/LICENSE'
HOME_URL = 'https://github.com/Project-Babble/Baballonia'
CAMERA_URL = 'http://127.0.0.1:8880/stream'
OSC_PORT = 9005
UNIT = 'vrostbridge-baballonia'
EXECUTABLE = 'Baballonia.Desktop'
WINDOWS_EXECUTABLE = 'Baballonia.Desktop.exe'
# Windows: the program names of Baballonia, its calibration overlay and its trainer (Toolhelp32 lists names only;
# only these processes are opened to read their full path).
WINDOWS_PROGRAMS = (WINDOWS_EXECUTABLE, 'BabbleCalibration.x86_64.exe', 'BabbleCalibration.arm64.exe', 'BabbleTrainer.exe')
# The eye cameras on Windows: the name Windows gives them (Baballonia lists DirectShow cameras by that name), the
# generic one it may give them instead, and their one picture size (both eyes side by side; the Linux helper reports it).
WINDOWS_CAMERA_NAME = 'BigEye'
GENERIC_CAMERA_NAMES = ('USB Video Device',)
EYE_CAMERA_DEVICE = 'USB\\VID_35BD&PID_0202'
EYE_CAMERA_SIZE = (800, 400)
# WQL: a backslash is escaped in a string literal; PNPClass, Present and ConfigManagerErrorCode tell the camera
# function apart from the headset's other USB functions, and a disconnected (remembered) device from a present one.
EYE_CAMERA_QUERY = ('SELECT Name, PNPDeviceID, PNPClass, Present, ConfigManagerErrorCode FROM Win32_PnPEntity '
                    "WHERE PNPDeviceID LIKE 'USB\\\\VID_35BD&PID_0202%'")
EYE_CAMERA_QUERY_BASIC = "SELECT Name, PNPDeviceID FROM Win32_PnPEntity WHERE PNPDeviceID LIKE 'USB\\\\VID_35BD&PID_0202%'"
STOP_GRACE_WINDOWS_S = 5  # closing its window: it saves its settings and stops its cameras first
INSTALL_TIMEOUT_S = 1200
# What Baballonia's installer writes outside the folder it installs to (its main.nsi): put back as they were.
UNINSTALL_KEY = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Baballonia'
INSTALLER_SHORTCUTS = (('Programs', 'Baballonia.lnk'), ('Programs', 'Uninstall Baballonia.lnk'), ('Desktop', 'Baballonia.lnk'))
ADOPTED = 'vrostbridge-adopted.json'  # in the data folder: the person's own installation VROSTBridge uses
# Windows process and window constants.
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x00000001
SW_SHOWNORMAL, SW_SHOW, SW_SHOWMINNOACTIVE, SW_RESTORE = 1, 5, 7, 9


def _windows():
    """Decided at call time (the tests change sys.platform)."""
    return sys.platform == 'win32'


def default_release():
    return WINDOWS_RELEASE if _windows() else RELEASE


def program_name():
    return WINDOWS_EXECUTABLE if _windows() else EXECUTABLE


def _path_key(path):
    """A Windows path for comparing (case and separators as Windows treats them)."""
    return ntpath.normcase(ntpath.normpath(str(path)))


# Baballonia's eye filter (One Euro: the smoothing's lowest cut-off in Hz, and how fast it opens up with the speed
# of the eyes) as Baballonia sets it, and as VROSTBridge sets it once. Simulated with Baballonia's own filter (its
# derivative smoothing is fixed at 1 Hz) at 60 camera frames a second: 1.0.0.3's 1 Hz and 10 reached 90 % of a 5 or
# 10 degree eye movement 83 or 67 ms after it began; 1.0.0.5's 2 Hz and 60 takes 33 ms for both (two camera frames).
# While the eyes rest it passes a little more of the tracker's noise (0.6 instead of 0.35 of it); the host's sharp
# region now ignores small movements (its comfort zone), so the noise does not move it. The lag did.
BABALLONIA_EYE_FILTER = {'AppSettings_OneEuroEnabled': True, 'AppSettings_OneEuroMinFreqCutoff': 0.5,
                         'AppSettings_OneEuroSpeedCutoff': 3.0}
EYE_FILTER = {'AppSettings_OneEuroEnabled': True, 'AppSettings_OneEuroMinFreqCutoff': 2.0,
              'AppSettings_OneEuroSpeedCutoff': 60.0}
EYE_FILTER_VERSION = 2
# Filters earlier VROSTBridge versions set: replaced by EYE_FILTER while still in place, never someone's own choice.
EARLIER_EYE_FILTERS = ({'AppSettings_OneEuroEnabled': True, 'AppSettings_OneEuroMinFreqCutoff': 1.0,
                        'AppSettings_OneEuroSpeedCutoff': 10.0},)


def version_key(text):
    """A sortable key for Baballonia versions such as 1.1.1.0rc6 (a release sorts after its candidates)."""
    key = []
    for piece in re.split(r'[.\-]', str(text)):
        match = re.fullmatch(r'(\d+)(?:([A-Za-z]+)(\d*))?', piece)
        if not match:
            key.append((0, -2, piece.lower(), 0))
        elif match.group(2):
            key.append((int(match.group(1)), -1, match.group(2).lower(), int(match.group(3) or 0)))
        else:
            key.append((int(match.group(1)), 0, '', 0))
    return tuple(key)
STOP_GRACE_S = 3
# 1.0.0.4: Baballonia runs its eye model on the processor, for up to 66 pictures a second.
# On a handheld the processor and the GPU share one power limit, and in the 1.0.0.3 test the stream's
# own work (decoding, SteamVR's compositor, VROSTBridge's frame pass) stalled for 40-60 ms every few
# seconds while the processor was short of power. A slightly lower priority lets those win when they
# compete; with spare processor time Baballonia runs exactly as fast as before.
NICENESS = 5
NICE = '/usr/bin/nice'
# 1.0.0.9: as its own user service Baballonia sits in a cgroup of its own, where niceness only orders
# its own threads; between cgroups the CPU weight decides (systemd's default is 100). At 25 the
# stream's processes win when they compete with its inference bursts; with spare processor time
# Baballonia still runs at full speed.
CPU_WEIGHT = 25
KEEP_ENV = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE', 'LC_MESSAGES',
            'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
            'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP', 'XDG_DATA_DIRS',
            'DBUS_SESSION_BUS_ADDRESS')
# The download alone honours the desktop's proxy settings (as the urllib fallback would); Baballonia never
# gets them: its camera reader would send http://127.0.0.1:8880 through the proxy.
PROXY_ENV = ('http_proxy', 'https_proxy', 'no_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY')
# Runs a program for VROSTBridge and ends it when the companion that started it is gone ($1: its pid; a
# zombie counts as gone), or when asked to stop (SIGTERM): SIGTERM to the program, then SIGKILL after $2
# seconds. The program's own exit status is the watcher's when it ends by itself. No "${" and no "%"
# anywhere: systemd-run expands ${...} in a service's command line (systemd 254 and later), and a unit's
# % specifiers are its own.
WATCH_SCRIPT = r'''companion=$1; grace=$2; shift 2
child=
finish() {
  if [ -n "$child" ]; then
    kill -TERM "$child" 2>/dev/null
    i=0
    while [ "$i" -lt "$grace" ] && kill -0 "$child" 2>/dev/null; do sleep 1; i=$((i+1)); done
    kill -KILL "$child" 2>/dev/null
    wait "$child" 2>/dev/null
  fi
  exit 0
}
trap finish TERM INT HUP
alive() {
  kill -0 "$1" 2>/dev/null || return 1
  read -r stat < "/proc/$1/stat" 2>/dev/null || return 1
  case $stat in *") Z "*) return 1;; esac
  return 0
}
"$@" &
child=$!
while alive "$companion" && kill -0 "$child" 2>/dev/null; do
  sleep 1 & wait $!
done
if kill -0 "$child" 2>/dev/null; then finish; fi
wait "$child"
'''


def watched(command, grace, companion=None, shell='/bin/sh'):
    """command, run under WATCH_SCRIPT for this companion (see there)."""
    return [shell, '-c', WATCH_SCRIPT, 'vrostbridge-watch', str(companion or os.getpid()), str(int(grace)),
            *map(str, command)]
CA_BUNDLES = ('/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/certs/ca-certificates.crt', '/etc/ssl/cert.pem',
              '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem')


def data_root():
    """VROSTBridge's Baballonia folder: ~/.local/share/vrostbridge/baballonia; on Windows
    %LOCALAPPDATA%\\VROSTBridge\\Baballonia (beside VROSTBridge's own Client and Host folders)."""
    if _windows():
        return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')/'VROSTBridge'/'Baballonia'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')/'vrostbridge'/'baballonia'


def config_root():
    """Baballonia's own folder: .NET's ApplicationData on Linux is $XDG_CONFIG_HOME or ~/.config; on Windows
    %APPDATA% (its roaming application data)."""
    if _windows():
        return Path(os.environ.get('APPDATA') or Path.home()/'AppData'/'Roaming')/'ProjectBabble'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'ProjectBabble'


def launch_environment(base=None):
    """The desktop session's environment for Baballonia, the camera helper and system tools: no library
    path (the frozen companion's would replace the system's; Baballonia is self-contained), no
    LD_PRELOAD (Steam's overlay) and no Steam game ids (gamescope would treat its window as part of
    VROSTBridge). Windows: the whole environment (KEEP_ENV is Unix's; Windows programs need SystemRoot,
    APPDATA, LOCALAPPDATA, TEMP and more, and Baballonia reads its cameras through DirectShow, not a proxy)."""
    base = dict(os.environ if base is None else base)
    if _windows():
        return base
    environment = {key: base[key] for key in KEEP_ENV if base.get(key)}
    environment.setdefault('PATH', '/usr/local/bin:/usr/bin:/bin')
    return environment


def steamvr_openxr_runtime(roots=None):
    """SteamVR's OpenXR runtime manifest (steamxr_linux64.json in the SteamVR installation OpenVR uses), or None.

    RC4.6: on Linux Baballonia shows its calibration through OpenXR when SteamVR runs on this PC (it looks
    for vrserver when a calibration starts), and on this screen otherwise. Baballonia and its calibration get
    XR_RUNTIME_JSON pointing here, so the calibration reaches the headset whichever OpenXR runtime this PC has
    active: SteamVR's own button to make itself the OpenXR runtime often does nothing on Linux. Not on Windows
    (Baballonia's calibration is a SteamVR overlay there): None."""
    if _windows():
        return None
    if roots is None:
        from launcher.client_runtime import runtime_roots
        roots = runtime_roots()
    for root in roots[:1]:  # the runtime OpenVR uses, as for the native client
        manifest = Path(root)/'steamxr_linux64.json'
        if manifest.is_file():
            return manifest
    return None


XR_PROCESSES = ('vrserver', 'monado', 'wivrn-server')


def openxr_calibration():
    """VROSTBRIDGE_CALIBRATION=openxr in the companion's environment: Baballonia's own OpenXR calibration instead
    of the SteamVR overlay (see CALIBRATION_LAUNCHER)."""
    return os.environ.get('VROSTBRIDGE_CALIBRATION', '').strip().lower() == 'openxr'

# RC4.6: the file Baballonia starts for its calibration (Calibration/Linux/Overlay/BabbleCalibration.x86_64)
# becomes this launcher, and Baballonia's own program is kept beside it as BabbleCalibration.program (Godot
# finds BabbleCalibration.pck from that name). With SteamVR running, Baballonia starts its calibration on
# Linux with OpenXR (--xr-mode on), as a new SteamVR scene application: SteamVR then asks the one showing
# the stream to quit, which ended the VROSTBridge session in the first test (and with SteamVR started by
# hand the picture showed each eye the other eye's view). The launcher starts it as a SteamVR overlay
# instead (--use-openvr), as Baballonia does with SteamVR on Windows: over the stream, which keeps running.
# Without SteamVR (Monado, WiVRn, or no VR runtime) the arguments are passed on unchanged.
# VROSTBRIDGE_CALIBRATION=openxr in VROSTBridge's environment leaves Baballonia's choice as it is.
# 1.0.0.2: as a SteamVR overlay the program also gets VROSTBridge's Vulkan layer (VULKAN_LAYER_FOLDER beside the
# launcher; client/platform/linux/openvr_texture_layer.c). SteamVR takes an overlay's picture through extension
# commands on the program's own Vulkan device, which this Godot program does not enable; Mesa returns no command for
# them, and SteamVR crashed the program as soon as it showed its first picture (1.0.0.1, found in its first test on Bazzite).
# The layer enables them for this program only. VROSTBRIDGE_VULKAN_LAYER=off in VROSTBridge's environment leaves it out.
CALIBRATION_FOLDER = 'Calibration/Linux/Overlay'
CALIBRATION_LAUNCHER_NAME = 'BabbleCalibration.x86_64'
CALIBRATION_PROGRAM_NAME = 'BabbleCalibration.program'
VULKAN_LAYER_FOLDER = 'vrostbridge-vulkan'
VULKAN_LAYER_FILES = ('libVkLayer_vrostbridge_openvr.so', 'VkLayer_vrostbridge_openvr.json')
VULKAN_LAYER_NAME = 'VK_LAYER_VROSTBRIDGE_openvr_textures'
CALIBRATION_LAUNCHER = r'''#!/bin/sh
# VROSTBridge calibration launcher (part of VROSTBridge, MIT licence; see launcher/baballonia.py there).
# Not part of Baballonia and contains no Baballonia code: Baballonia's calibration program is unchanged
# beside it as BabbleCalibration.program, started with its own --use-openvr option.
# With SteamVR running, Baballonia's calibration program starts as a SteamVR overlay over the stream
# (--use-openvr) instead of an OpenXR scene application, which would make SteamVR end the stream.
# As an overlay it gets VROSTBridge's Vulkan layer from vrostbridge-vulkan beside this launcher: it enables
# the Vulkan extensions SteamVR uses to take the overlay's picture, which the program does not enable itself.
# Baballonia's own program is BabbleCalibration.program; Remove Baballonia in VROSTBridge deletes all three.
here="$(dirname -- "$0")"
program="$here/BabbleCalibration.program"
steamvr=0
if [ "$VROSTBRIDGE_CALIBRATION" != openxr ]; then
    for comm in /proc/[0-9]*/comm; do
        { read -r name < "$comm"; } 2>/dev/null || continue
        if [ "$name" = vrserver ]; then steamvr=1; break; fi
    done
fi
openxr=0
openvr=0
for argument in "$@"; do
    case $argument in --xr-mode|--xr-mode=*) openxr=1 ;; --use-openvr) openvr=1 ;; esac
done
if [ "$steamvr" = 1 ] && [ "$openxr" = 1 ]; then
    count=$#
    skip=0
    while [ "$count" -gt 0 ]; do
        argument=$1
        shift
        count=$((count - 1))
        if [ "$skip" = 1 ]; then skip=0; continue; fi
        case $argument in
            --xr-mode) skip=1; continue ;;
            --xr-mode=*|--use-openxr-overlay) continue ;;
        esac
        set -- "$@" "$argument"
    done
    set -- "$@" --use-openvr
    openvr=1
    echo "VROSTBridge: Baballonia's calibration starts as a SteamVR overlay." >&2
fi
layer="$here/vrostbridge-vulkan"
if [ "$openvr" = 1 ] && [ "$VROSTBRIDGE_VULKAN_LAYER" != off ] && [ -f "$layer/VkLayer_vrostbridge_openvr.json" ]; then
    VK_ADD_LAYER_PATH="$layer${VK_ADD_LAYER_PATH:+:$VK_ADD_LAYER_PATH}"
    VK_INSTANCE_LAYERS="VK_LAYER_VROSTBRIDGE_openvr_textures${VK_INSTANCE_LAYERS:+:$VK_INSTANCE_LAYERS}"
    export VK_ADD_LAYER_PATH VK_INSTANCE_LAYERS
    echo "VROSTBridge: with VROSTBridge's Vulkan layer for SteamVR's overlay textures." >&2
fi
exec "$program" "$@"
'''


def vulkan_layer_source():
    """The folder with VROSTBridge's Vulkan layer for the calibration (VULKAN_LAYER_FILES): vulkan/ in this installation,
    beside the frozen companion, or in a source tree's dist/client after cmake --install; None without one."""
    if getattr(sys, 'frozen', False):
        roots = [Path(sys.executable).resolve().parent]
    else:
        root = Path(__file__).resolve().parents[1]
        roots = [root, root/'dist/client']
    for root in roots:
        folder = root/'vulkan'
        if all((folder/name).is_file() for name in VULKAN_LAYER_FILES):
            return folder
    return None


def xr_runtime_running(proc=Path('/proc'), names=None):
    """Whether a VR runtime runs on this PC, judged as Baballonia judges it when a calibration starts: a
    process whose name contains vrserver (SteamVR), monado or wivrn-server. Without one it calibrates on
    this screen. names: the running programs' names (Windows: from Toolhelp32)."""
    if names is None and _windows():
        try:
            names = [name for _, name in windows_api().processes()]
        except OSError:
            return False
    if names is not None:
        return any(process in str(name).lower() for name in names for process in XR_PROCESSES)
    try:
        entries = [entry for entry in proc.iterdir() if entry.name.isdecimal()]
    except OSError:
        return False
    for entry in entries:
        try:
            name = (entry/'comm').read_text().strip().lower()
        except OSError:
            continue
        if any(process in name for process in XR_PROCESSES):
            return True
    return False


def jpeg_size(data):
    """(width, height) from a JPEG's frame header, or None."""
    start = data.find(b'\xff\xd8')
    if start < 0:
        return None
    i = start + 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], 'big')
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(data[i + 5:i + 7], 'big')
            width = int.from_bytes(data[i + 7:i + 9], 'big')
            return (width, height) if width and height else None
        if length < 2:
            return None
        i += 2 + length
    return None


def probe_frame_size(url=CAMERA_URL, timeout=6.0, limit=4 * 1024 * 1024):
    """The size of the camera helper's images, read from its stream's first JPEG; None if none came."""
    host_port, _, path = url.partition('://')[2].partition('/')
    host, _, port = host_port.partition(':')
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection((host, int(port or 80)), timeout=timeout) as connection:
            connection.sendall(('GET /' + path + ' HTTP/1.0\r\nHost: ' + host_port + '\r\n\r\n').encode())
            data = b''
            while len(data) < limit and time.monotonic() < deadline:
                connection.settimeout(max(.1, deadline - time.monotonic()))
                chunk = connection.recv(65536)
                if not chunk:
                    break
                data += chunk
                size = jpeg_size(data)
                if size:
                    return size
    except (OSError, ValueError):
        return None
    return None


def eye_crops(size):
    """Baballonia's left/right eye regions, as its own 'select the entire frame' chooses them: for a
    side-by-side stereo image twice as wide as high (the Beyond 2e) each half, one pixel short so it
    is not taken as the whole frame; for any other image the whole frame for both eyes."""
    if not size:
        return None
    width, height = size
    if width < 32 or height < 16:
        return None
    half = width // 2
    if half != height:
        whole = {'X': 0, 'Y': 0, 'Width': width, 'Height': height}
        return dict(whole), dict(whole)
    return ({'X': 0, 'Y': 0, 'Width': half - 1, 'Height': height - 1},
            {'X': half, 'Y': 0, 'Width': half - 1, 'Height': height - 1})


# ---------------------------------------------------------------------- Windows (1.0.0.7)
CAMERA_MESSAGES = {
    'absent': "The Beyond 2e's eye cameras were not found: check that the headset is connected and on.",
    'generic-name': ('Windows lists the Beyond 2e\'s eye cameras as "{name}" instead of "BigEye". Bigscreen\'s advice: '
                     'in Device Manager, uninstall that device (under Cameras), then unplug the headset and connect it '
                     'again so Windows names it BigEye. Eye tracking uses it as "{name}" meanwhile; another camera of '
                     'that name could be taken for it.'),
    'unknown': "Windows did not say whether the Beyond 2e's eye cameras are connected ({detail}); eye tracking tries "
               "them anyway.",
}


def parse_wmi(text):
    """Rows of _wmi.exec_query's answer: 'Name=value' fields separated by NUL, and rows by an empty field."""
    rows, row = [], {}
    for field in str(text or '').split('\0'):
        if not field:
            if row:
                rows.append(row)
                row = {}
            continue
        key, separator, value = field.partition('=')
        if separator:
            row[key] = value
    if row:
        rows.append(row)
    return rows


def _false(value):
    return str(value).strip().lower() in ('false', '0', 'no')


def eye_camera_from_rows(rows):
    """The Beyond 2e's eye cameras among Windows' devices (rows of EYE_CAMERA_QUERY): {'state': 'present',
    'generic-name' or 'absent', 'name', 'device_id', 'message'}. The headset's USB device has other functions
    beside the cameras (a composite parent, HID): the camera is the one in the Camera (or Image) class; without
    PNPClass (the basic query), an interface (MI_xx) that is not an input device. A device Windows remembers but
    that is not connected (Present FALSE, problem 45) does not count."""
    prefix = EYE_CAMERA_DEVICE.upper()
    devices = [row for row in rows if str(row.get('PNPDeviceID') or '').upper().startswith(prefix)]
    present = [row for row in devices if not _false(row.get('Present', 'TRUE')) and
               str(row.get('ConfigManagerErrorCode') or '').strip() != '45']

    def camera(row):
        kind = str(row.get('PNPClass') or '').strip().lower()
        if 'PNPClass' in row and kind:
            return kind in ('camera', 'image')
        name = str(row.get('Name') or '').strip().lower()
        interface = '&MI_' in str(row.get('PNPDeviceID') or '').upper()
        return interface and not any(word in name for word in ('input', 'hid', 'composite', 'audio'))
    cameras = sorted((row for row in present if camera(row)),
                     key=lambda row: (str(row.get('Name') or '').strip() in GENERIC_CAMERA_NAMES or
                                      not str(row.get('Name') or '').strip(), str(row.get('PNPDeviceID'))))
    if not cameras:
        return {'state': 'absent', 'name': None, 'device_id': None, 'message': CAMERA_MESSAGES['absent']}
    row = cameras[0]
    name = str(row.get('Name') or '').strip()
    if not name or name in GENERIC_CAMERA_NAMES:
        name = name or GENERIC_CAMERA_NAMES[0]
        return {'state': 'generic-name', 'name': name, 'device_id': str(row.get('PNPDeviceID')),
                'message': CAMERA_MESSAGES['generic-name'].format(name=name)}
    return {'state': 'present', 'name': name, 'device_id': str(row.get('PNPDeviceID')), 'message': ''}


# DirectShow lists a camera by its capture interface's FriendlyName (KSCATEGORY_CAPTURE), which Baballonia matches.
CAPTURE_INTERFACES = 'SYSTEM\\CurrentControlSet\\Control\\DeviceClasses\\{65e8773d-8f56-11d0-a3b9-00a0c9223196}'


def directshow_name(device_id, registry=None):
    """The name DirectShow, and so Baballonia's camera list, gives a camera (its instance id as WMI's PNPDeviceID):
    the FriendlyName of its capture interface, or None."""
    registry = registry or WindowsRegistry()
    prefix = ('##?#' + str(device_id).replace('\\', '#') + '#').lower()
    try:
        for name in registry.subkeys('HKLM', CAPTURE_INTERFACES):
            if not name.lower().startswith(prefix):
                continue
            values = registry.read_values('HKLM', CAPTURE_INTERFACES + '\\' + name + '\\#GLOBAL\\Device Parameters') or {}
            friendly = str((values.get('FriendlyName') or ('',))[0] or '').strip()
            if friendly:
                return friendly
    except OSError:
        return None
    return None


def windows_eye_camera(query=None, registry=None):
    """The Beyond 2e's eye cameras as Windows lists them (WMI, through CPython's _wmi: SELECT only, answers within
    about a second): see eye_camera_from_rows; 'unknown' when Windows did not answer. query: _wmi.exec_query or a
    stand-in. A query naming a property Windows lacks (or a NULL value) fails: the basic query is tried then. The
    name is DirectShow's when it has one (directshow_name): Baballonia finds the camera by it."""
    if query is None:
        try:
            import _wmi
        except ImportError:
            return {'state': 'unknown', 'name': None, 'device_id': None,
                    'message': CAMERA_MESSAGES['unknown'].format(detail='no WMI in this Python')}
        query = _wmi.exec_query
    detail = ''
    for text in (EYE_CAMERA_QUERY, EYE_CAMERA_QUERY_BASIC):
        try:
            camera = eye_camera_from_rows(parse_wmi(query(text)))
        except (OSError, ValueError) as error:
            detail = str(error)[:120] or type(error).__name__
            continue
        listed = directshow_name(camera['device_id'], registry) if camera['device_id'] else None
        if listed and listed != camera['name']:
            generic = listed in GENERIC_CAMERA_NAMES
            camera.update(state='generic-name' if generic else 'present', name=listed,
                          message=CAMERA_MESSAGES['generic-name'].format(name=listed) if generic else '')
        return camera
    return {'state': 'unknown', 'name': None, 'device_id': None,
            'message': CAMERA_MESSAGES['unknown'].format(detail=detail)}


_JOB_STRUCTURES = None


def job_structures():
    """(JOBOBJECT_EXTENDED_LIMIT_INFORMATION, PROCESSENTRY32W) for ctypes, built on first use. Fixed-size fields
    (a DWORD is 32-bit, ULONG_PTR and SIZE_T pointer-sized), so their sizes are Windows' own: 144 and 568 bytes
    on 64-bit Windows."""
    global _JOB_STRUCTURES
    if _JOB_STRUCTURES is None:
        import ctypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', ctypes.c_uint32), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', ctypes.c_uint32),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', ctypes.c_uint32),
                        ('SchedulingClass', ctypes.c_uint32)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount',
                'WriteTransferCount', 'OtherTransferCount')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        class ProcessEntry(ctypes.Structure):
            _fields_ = [('dwSize', ctypes.c_uint32), ('cntUsage', ctypes.c_uint32), ('th32ProcessID', ctypes.c_uint32),
                        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', ctypes.c_uint32),
                        ('cntThreads', ctypes.c_uint32), ('th32ParentProcessID', ctypes.c_uint32),
                        ('pcPriClassBase', ctypes.c_int32), ('dwFlags', ctypes.c_uint32),
                        ('szExeFile', ctypes.c_wchar * 260)]
        _JOB_STRUCTURES = (ExtendedLimits, ProcessEntry)
    return _JOB_STRUCTURES


JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x102
WM_CLOSE = 0x0010
GW_OWNER = 4


class WindowsApi:
    """The Win32 calls VROSTBridge makes for Baballonia on Windows, through ctypes loaded on first use (this module
    imports on Linux too). Each raises OSError when Windows refuses; tests pass a stand-in with the same methods."""

    def __init__(self):
        self._lock = threading.Lock()
        self._api = None

    def _load(self):
        with self._lock:
            if self._api is not None:
                return self._api
            import ctypes
            from ctypes import wintypes
            if not hasattr(ctypes, 'WinDLL'):
                raise OSError('Windows calls are only available on Windows.')
            extended, entry = job_structures()
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            user = ctypes.WinDLL('user32', use_last_error=True)
            HANDLE, DWORD, BOOL, HWND, UINT = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.HWND, wintypes.UINT
            for function, arguments, result in (
                    (kernel.CreateJobObjectW, [ctypes.c_void_p, wintypes.LPCWSTR], HANDLE),
                    (kernel.SetInformationJobObject, [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD], BOOL),
                    (kernel.AssignProcessToJobObject, [HANDLE, HANDLE], BOOL),
                    (kernel.TerminateJobObject, [HANDLE, UINT], BOOL),
                    (kernel.CloseHandle, [HANDLE], BOOL),
                    (kernel.OpenProcess, [DWORD, BOOL, DWORD], HANDLE),
                    (kernel.QueryFullProcessImageNameW, [HANDLE, DWORD, wintypes.LPWSTR, ctypes.POINTER(DWORD)], BOOL),
                    (kernel.TerminateProcess, [HANDLE, UINT], BOOL),
                    (kernel.WaitForSingleObject, [HANDLE, DWORD], DWORD),
                    (kernel.CreateToolhelp32Snapshot, [DWORD, DWORD], HANDLE),
                    (kernel.Process32FirstW, [HANDLE, ctypes.POINTER(entry)], BOOL),
                    (kernel.Process32NextW, [HANDLE, ctypes.POINTER(entry)], BOOL),
                    (kernel.GetCurrentThreadId, [], DWORD),
                    (user.GetWindowThreadProcessId, [HWND, ctypes.POINTER(DWORD)], DWORD),
                    (user.IsWindowVisible, [HWND], BOOL),
                    (user.GetWindow, [HWND, UINT], HWND),
                    (user.PostMessageW, [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM], BOOL),
                    (user.ShowWindow, [HWND, ctypes.c_int], BOOL),
                    (user.IsIconic, [HWND], BOOL),
                    (user.SetForegroundWindow, [HWND], BOOL),
                    (user.GetForegroundWindow, [], HWND),
                    (user.BringWindowToTop, [HWND], BOOL),
                    (user.AttachThreadInput, [DWORD, DWORD, BOOL], BOOL)):
                function.argtypes, function.restype = arguments, result
            enum_proc = ctypes.WINFUNCTYPE(BOOL, HWND, wintypes.LPARAM)
            user.EnumWindows.argtypes, user.EnumWindows.restype = [enum_proc, wintypes.LPARAM], BOOL
            self._api = (ctypes, wintypes, kernel, user, enum_proc, extended, entry)
            return self._api

    @staticmethod
    def _error(ctypes, what):
        code = ctypes.get_last_error()
        return OSError(code, what + ' failed (Windows error ' + str(code) + ')')

    # ---- job objects: a Baballonia (and what it starts) ends with the companion
    def create_job(self, single_process=False):
        """A job object whose processes end when its last handle closes (the companion's, also when it crashes).
        single_process: its one process may start no other (Baballonia's installer would start Baballonia)."""
        ctypes, wintypes, kernel, user, _, extended, _ = self._load()
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise self._error(ctypes, 'CreateJobObjectW')
        limits = extended()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | (
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS if single_process else 0)
        limits.BasicLimitInformation.ActiveProcessLimit = 1 if single_process else 0
        if not kernel.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                              ctypes.sizeof(limits)):
            error = self._error(ctypes, 'SetInformationJobObject')
            kernel.CloseHandle(job)
            raise error
        return job

    def assign(self, job, pid):
        ctypes, wintypes, kernel, *_ = self._load()
        process = kernel.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if not process:
            raise self._error(ctypes, 'OpenProcess')
        try:
            if not kernel.AssignProcessToJobObject(job, process):
                raise self._error(ctypes, 'AssignProcessToJobObject')
        finally:
            kernel.CloseHandle(process)

    def terminate_job(self, job, code=1):
        ctypes, wintypes, kernel, *_ = self._load()
        return bool(kernel.TerminateJobObject(job, code))

    def close_handle(self, handle):
        ctypes, wintypes, kernel, *_ = self._load()
        return bool(kernel.CloseHandle(handle))

    # ---- processes
    def processes(self):
        """(pid, program name) of every process (Toolhelp32)."""
        ctypes, wintypes, kernel, user, _, _, entry_type = self._load()
        snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            raise self._error(ctypes, 'CreateToolhelp32Snapshot')
        found = []
        try:
            entry = entry_type()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                found.append((int(entry.th32ProcessID), entry.szExeFile))
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        return found

    def _path(self, handle):
        ctypes, wintypes, kernel, *_ = self._load()
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value

    def image_path(self, pid):
        """The full path of a running process's program, or None (ended, or not this user's to open)."""
        ctypes, wintypes, kernel, *_ = self._load()
        handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid))
        if not handle:
            return None
        try:
            if kernel.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT:
                return None  # it has ended (a handle elsewhere keeps its record)
            return self._path(handle)
        finally:
            kernel.CloseHandle(handle)

    def terminate(self, pid, path=None, wait_ms=3000):
        """End a process, only if it still runs path (checked on the handle that ends it: never a reused id)."""
        ctypes, wintypes, kernel, *_ = self._load()
        handle = kernel.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            if path is not None and _path_key(self._path(handle) or '') != _path_key(path):
                return False
            if not kernel.TerminateProcess(handle, 1):
                return False
            kernel.WaitForSingleObject(handle, int(wait_ms))
            return True
        finally:
            kernel.CloseHandle(handle)

    # ---- windows
    def windows(self, pids):
        """The visible top-level windows (not owned by another, so not dialogs) of these processes, front first."""
        ctypes, wintypes, kernel, user, enum_proc, *_ = self._load()
        wanted, found = {int(pid) for pid in pids}, []

        def visit(hwnd, _):
            owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in wanted and user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, GW_OWNER):
                found.append(hwnd)
            return True
        callback = enum_proc(visit)  # kept referenced for the call
        user.EnumWindows(callback, 0)
        return found

    def post_close(self, hwnd):
        ctypes, wintypes, kernel, user, *_ = self._load()
        return bool(user.PostMessageW(hwnd, WM_CLOSE, 0, 0))

    def show(self, hwnd, command):
        ctypes, wintypes, kernel, user, *_ = self._load()
        return bool(user.ShowWindow(hwnd, int(command)))

    def is_minimised(self, hwnd):
        ctypes, wintypes, kernel, user, *_ = self._load()
        return bool(user.IsIconic(hwnd))

    def bring_to_front(self, hwnd):
        """In front with the focus, as its taskbar button would. Windows lets only the program in front give the
        focus away; the companion's page is in a browser, so this thread borrows the front window's input state."""
        ctypes, wintypes, kernel, user, *_ = self._load()
        if user.SetForegroundWindow(hwnd):
            return True
        front = user.GetForegroundWindow()
        theirs = user.GetWindowThreadProcessId(front, None) if front else 0
        ours = kernel.GetCurrentThreadId()
        if not theirs or theirs == ours or not user.AttachThreadInput(ours, theirs, True):
            return False
        try:
            user.BringWindowToTop(hwnd)
            return bool(user.SetForegroundWindow(hwnd))
        finally:
            user.AttachThreadInput(ours, theirs, False)

    # ---- devices
    def device_present(self, instance_id):
        """Whether a device (its instance id, as WMI's PNPDeviceID) is connected now: CM_Locate_DevNodeW finds only
        present devices. None when Windows could not say."""
        try:
            ctypes, wintypes, *_ = self._load()
            cfgmgr = ctypes.WinDLL('cfgmgr32')
            locate = cfgmgr.CM_Locate_DevNodeW
            locate.argtypes, locate.restype = [ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, wintypes.ULONG], wintypes.DWORD
            node = wintypes.DWORD()
            result = locate(ctypes.byref(node), str(instance_id), 0)  # CM_LOCATE_DEVNODE_NORMAL
        except (OSError, AttributeError):
            return None
        if result == 0:  # CR_SUCCESS
            return True
        return False if result == 0x0D else None  # CR_NO_SUCH_DEVNODE


_WINDOWS_API = None


def windows_api():
    """The one WindowsApi of this companion."""
    global _WINDOWS_API
    if _WINDOWS_API is None:
        _WINDOWS_API = WindowsApi()
    return _WINDOWS_API


UNINSTALL_ROOT = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall'
SHELL_FOLDERS = 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders'
ENTRY_VALUES = ('DisplayName', 'DisplayVersion', 'InstallLocation', 'UninstallString', 'DisplayIcon')


class WindowsRegistry:
    """The registry reads and writes for Baballonia's installer on Windows (winreg, imported on first use)."""

    @staticmethod
    def _winreg():
        try:
            import winreg
        except ImportError:
            raise OSError('The registry is only on Windows.') from None
        return winreg

    def _hive(self, name):
        winreg = self._winreg()
        return {'HKCU': winreg.HKEY_CURRENT_USER, 'HKLM': winreg.HKEY_LOCAL_MACHINE}[name]

    def read_values(self, hive, path):
        """{name: (data, type)} of a key, or None when it does not exist."""
        winreg = self._winreg()
        try:
            key = winreg.OpenKey(self._hive(hive), path, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return None
        values = {}
        with key:
            index = 0
            while True:
                try:
                    name, data, kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                values[name] = (data, kind)
                index += 1
        return values

    def restore_values(self, hive, path, values):
        """Make a key what read_values gave (None: no key)."""
        winreg = self._winreg()
        try:
            winreg.DeleteKey(self._hive(hive), path)
        except FileNotFoundError:
            pass
        if values is None:
            return
        with winreg.CreateKeyEx(self._hive(hive), path, 0, winreg.KEY_WRITE) as key:
            for name, (data, kind) in values.items():
                winreg.SetValueEx(key, name, 0, kind, data)

    def delete_key(self, hive, path):
        winreg = self._winreg()
        try:
            winreg.DeleteKey(self._hive(hive), path)
        except FileNotFoundError:
            pass

    def subkeys(self, hive, path):
        """The names of a key's subkeys ([] when it does not exist)."""
        winreg = self._winreg()
        try:
            key = winreg.OpenKey(self._hive(hive), path, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return []
        names = []
        with key:
            while True:
                try:
                    names.append(winreg.EnumKey(key, len(names)))
                except OSError:
                    return names

    def uninstall_entries(self):
        """The programs Windows lists as installed whose name begins with Baballonia: dicts of ENTRY_VALUES, with
        'hive' and 'key' (this user's first, then the PC's 64- and 32-bit lists)."""
        winreg = self._winreg()
        found = []
        for hive, views in (('HKCU', (0,)), ('HKLM', (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY))):
            for view in views:
                try:
                    root = winreg.OpenKey(self._hive(hive), UNINSTALL_ROOT, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with root:
                    index = 0
                    while True:
                        try:
                            name = winreg.EnumKey(root, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root, name, 0, winreg.KEY_READ | view) as key:
                                display = winreg.QueryValueEx(key, 'DisplayName')[0]
                                if not str(display).lower().startswith('baballonia'):
                                    continue
                                entry = {'hive': hive, 'key': name}
                                for value in ENTRY_VALUES:
                                    try:
                                        entry[value] = str(winreg.QueryValueEx(key, value)[0])
                                    except OSError:
                                        pass
                        except OSError:
                            continue
                        if entry not in found:
                            found.append(entry)
        return found

    def user_folder(self, name):
        """A folder of this user's shell (Desktop, Programs (the Start menu's), Personal (Documents)), expanded."""
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS) as key:
                value = winreg.QueryValueEx(key, name)[0]
            return winreg.ExpandEnvironmentStrings(str(value)) or None
        except OSError:
            pass
        profile, appdata = os.environ.get('USERPROFILE'), os.environ.get('APPDATA')
        defaults = {'Desktop': profile and Path(profile)/'Desktop', 'Personal': profile and Path(profile)/'Documents',
                    'Programs': appdata and Path(appdata)/'Microsoft'/'Windows'/'Start Menu'/'Programs'}
        folder = defaults.get(name)
        return str(folder) if folder else None


def _rmtree(path):
    """Remove a folder; on Windows also files marked read-only."""
    def retry(function, name, _):
        try:
            os.chmod(name, 0o700)
            function(name)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=retry)


class _StartupInfo:
    """subprocess.STARTUPINFO's fields, where Python has none (the tests run on Linux)."""
    def __init__(self):
        self.dwFlags, self.wShowWindow = 0, 0


def _replace(source, target):
    """os.replace; on Windows tried again for a moment when another program (a virus scanner) has the file open."""
    for attempt in range(10 if _windows() else 1):
        try:
            return os.replace(source, target)
        except PermissionError:
            if attempt == (9 if _windows() else 0):
                raise
            time.sleep(.2)


def _folder_size(folder):
    """Bytes of the files in a folder so far (an installer's progress)."""
    total = 0
    for directory, _, files in os.walk(folder):
        for name in files:
            try:
                total += os.stat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class BaballoniaManager:
    def __init__(self, log=print, root=None, config=None, state=None, runner=subprocess.run, popen=subprocess.Popen,
                 systemd_run='/usr/bin/systemd-run', systemctl='/usr/bin/systemctl', curl='/usr/bin/curl',
                 release=None, vulkan_layer=..., win32=None, registry=None, camera_check=None):
        self.log = log
        self.root = Path(root) if root else data_root()
        self.config = Path(config) if config else config_root()
        # What VROSTBridge set up once (crops, onboarding), kept outside the folder Remove deletes.
        self.state_path = Path(state) if state else self.root.parent/'baballonia-setup.json'
        self.runner, self.popen = runner, popen
        self.systemd_run, self.systemctl, self.curl = systemd_run, systemctl, curl
        # Windows (1.0.0.7): the Win32 calls, the registry and the eye-camera check (stand-ins in tests).
        self._win32, self._registry = win32, registry
        self.camera_check = camera_check or windows_eye_camera
        self.process_job = None  # Windows: the job object of the Baballonia this companion started
        self._found = (None, None)  # Windows: (when, installations the person has) for the pages
        self._installed_note = None
        # The release this VROSTBridge sets up. self.release is the one in use: this one once it is set up,
        # until then one an earlier VROSTBridge set up here (1.0.0.3, see earlier()).
        self.pinned = dict(release or default_release())
        # 1.0.0.2: where VROSTBridge's Vulkan layer for the calibration comes from (None: there is none to put in place).
        self.vulkan_layer = vulkan_layer_source() if vulkan_layer is ... else (Path(vulkan_layer) if vulkan_layer else None)
        self.lock = threading.RLock()
        self.run_lock = threading.RLock()  # one start or stop at a time (worker, start-up check, Remove)
        self.cancel = threading.Event()
        self.job = None
        self.process = None  # the watcher, when started without systemd
        self.unit_started = False
        self._value = {'state': 'missing', 'message': '', 'progress': None}
        self._running = (0.0, False)  # (when, value): the pages ask every second, systemctl is not free
        self._openxr = (None, False)  # (when, value): whether SteamVR's OpenXR runtime was found
        self.on_installed = None  # called after a successful set-up (eye tracking may be waiting for it)

    # ------------------------------------------------------------------ paths and state
    @property
    def win32(self):
        if self._win32 is None:
            self._win32 = windows_api()
        return self._win32

    @property
    def registry(self):
        if self._registry is None:
            self._registry = WindowsRegistry()
        return self._registry

    @property
    def release(self):
        """The Baballonia in use: the pinned release once it is set up; until then one that an earlier VROSTBridge
        set up here (1.0.0.3), so an update of VROSTBridge that pins a newer Baballonia keeps eye tracking and its
        calibration working until Update Baballonia; otherwise the pinned release (not set up yet). Windows: the
        person's own installation of the pinned release, once Set up eye tracking chose it (adopt_installation)."""
        if self._receipt_matches(self.root/self.pinned['version'], self.pinned['sha256']):
            return self.pinned
        if self.adopted_dir() is not None:
            return self.pinned
        earlier = self.earlier()
        if earlier:
            return dict(self.pinned, version=earlier['version'], url=str(earlier.get('url') or ''),
                        sha256=earlier['sha256'], capture=str(earlier.get('capture') or self.pinned['capture']))
        return self.pinned

    @property
    def install_dir(self):
        if _windows() and not self._receipt_matches(self.root/self.pinned['version'], self.pinned['sha256']):
            adopted = self.adopted_dir()
            if adopted is not None:
                return adopted
        return self.root/self.release['version']

    @property
    def executable(self):
        return self.install_dir/program_name()

    @property
    def settings_path(self):
        return self.config/'ApplicationData'/'LocalSettings.json'

    @staticmethod
    def _receipt(folder):
        """The receipt VROSTBridge wrote when it set up the Baballonia in folder, if its program is there."""
        try:
            data = json.loads((folder/'vrostbridge-receipt.json').read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and (folder/program_name()).is_file() else None

    def adopted_dir(self):
        """Windows: the folder of the person's own Baballonia that VROSTBridge uses (adopt_installation), while it
        is still that installation (its main library unchanged since), else None. Never on Linux."""
        if not _windows():
            return None
        try:
            data = json.loads((self.root/ADOPTED).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get('version') != self.pinned['version'] or \
                data.get('sha256') != self.pinned['sha256']:
            return None
        folder = Path(str(data.get('path') or ''))
        if not str(data.get('path') or '') or not folder.is_absolute() or not (folder/program_name()).is_file():
            return None
        return folder if self._stamp(folder) == data.get('stamp') else None

    @staticmethod
    def _stamp(folder):
        """Size and modification time of an installation's main library: it changes with every release."""
        try:
            stat = (Path(folder)/'Baballonia.dll').stat()
        except OSError:
            return None
        return [stat.st_size, stat.st_mtime_ns]

    def _receipt_matches(self, folder, sha256):
        data = self._receipt(folder)
        return bool(data) and data.get('sha256') == sha256

    def earlier(self):
        """1.0.0.3: the newest Baballonia an earlier VROSTBridge set up here (another version than the pinned one,
        with its receipt and program), or None."""
        try:
            folders = [p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith('.') and
                       p.name != self.pinned['version']]
        except OSError:
            return None
        found = []
        for folder in folders:
            data = self._receipt(folder)
            if data and data.get('version') == folder.name and isinstance(data.get('sha256'), str) and \
                    len(data['sha256']) == 64:
                found.append(data)
        return max(found, key=lambda data: version_key(data['version']), default=None)

    def installed(self):
        """A Baballonia is set up (the pinned release, or one an earlier VROSTBridge set up; on Windows also the
        person's own installation of the pinned release, once chosen)."""
        if self._receipt_matches(self.install_dir, self.release['sha256']):
            return True
        adopted = self.adopted_dir()
        return adopted is not None and self.install_dir == adopted

    def installation(self):
        """Windows: 'vrostbridge' (its own copy), 'yours' (the person's installation it uses) or None (not set up)."""
        if not self.installed():
            return None
        return 'yours' if self.adopted_dir() is not None and self.install_dir == self.adopted_dir() else 'vrostbridge'

    def update_available(self):
        """The Baballonia in use is an earlier VROSTBridge's and this one pins another (1.0.0.3)."""
        return self.installed() and self.release['version'] != self.pinned['version']

    def calibration(self):
        """Baballonia keeps the model its calibration trained as EyeHome_EyeModel; the bundled
        eyeModel.onnx means it has not been calibrated. (Its settings file is read again only when it
        changes: the pages ask every second.)"""
        try:
            stat = self.settings_path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        cached = getattr(self, '_model', None)
        if key is not None and cached and cached[0] == key:
            model = cached[1]
        else:
            try:
                model = str(json.loads(self.settings_path.read_text(encoding='utf-8-sig')).get('EyeHome_EyeModel') or '')
            except (OSError, ValueError, AttributeError):
                model = ''
            self._model = (key, model)
        name = Path(model).name
        if name.startswith('tuned_temporal_eye_tracking') and Path(model).is_absolute() and Path(model).is_file():
            stamp = name[len('tuned_temporal_eye_tracking_'):-len('.onnx')]
            when = (stamp[:4] + '-' + stamp[4:6] + '-' + stamp[6:8]) if len(stamp) >= 8 and stamp[:8].isdigit() else None
            return {'calibrated': True, 'date': when}
        return {'calibrated': False, 'date': None}

    def model_path(self):
        """Baballonia's eye model setting (a new path after each calibration; never shown or reported)."""
        self.calibration()
        return (getattr(self, '_model', None) or (None, ''))[1]

    MODEL_PATTERN = re.compile(r'tuned_temporal_eye_tracking_(\d{8})_(\d{6})\.onnx')

    def calibrations(self):
        """1.0.0.8: the eye models Baballonia's calibrations trained (in its Models folder), newest first:
        [{'name', 'date', 'current'}] (file names and dates only)."""
        current = Path(self.model_path() or '').name
        rows = []
        try:
            paths = list((self.config / 'Models').glob('tuned_temporal_eye_tracking_*.onnx'))
        except OSError:
            paths = []
        for path in paths:
            match = self.MODEL_PATTERN.fullmatch(path.name)
            if not match or not path.is_file():
                continue
            day, clock = match.groups()
            rows.append({'name': path.name, 'stamp': day + clock, 'current': path.name == current,
                         'date': day[:4] + '-' + day[4:6] + '-' + day[6:] + ' ' + clock[:2] + ':' + clock[2:4]})
        rows.sort(key=lambda row: row['stamp'], reverse=True)
        return [{key: row[key] for key in ('name', 'date', 'current')} for row in rows[:12]]

    def previous_calibration(self):
        """The newest calibration older than the one in use, or None."""
        rows = self.calibrations()
        index = next((number for number, row in enumerate(rows) if row['current']), None)
        if index is None:
            return None
        return rows[index + 1] if index + 1 < len(rows) else None

    def use_calibration(self, name):
        """Point Baballonia at one of the eye models its calibrations trained (1.0.0.8: Use previous calibration).
        Only while it is closed: it keeps its settings in memory and writes them back."""
        if not isinstance(name, str) or not self.MODEL_PATTERN.fullmatch(name):
            raise ValueError('Choose one of the calibrations listed.')
        path = self.config / 'Models' / name
        if not path.is_file():
            raise ValueError('That calibration is no longer on this PC.')
        if self.running():
            raise ValueError('Stop the session and close Baballonia first: it keeps its settings while it runs.')
        settings_path = self.settings_path
        try:
            settings = json.loads(settings_path.read_text(encoding='utf-8-sig'))
            if not isinstance(settings, dict):
                raise ValueError('not an object')
        except (OSError, ValueError) as error:
            raise ValueError("Baballonia's settings could not be read (" + str(error)[:120] + ').') from error
        settings['EyeHome_EyeModel'] = str(path)
        temporary = settings_path.with_name('.LocalSettings.json.vrostbridge')
        temporary.write_text(json.dumps(settings, indent=2) + '\n')
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        _replace(temporary, settings_path)
        self._model = None  # read again
        self.log('Eye tracking: Baballonia now uses the calibration from ' +
                 next((row['date'] for row in self.calibrations() if row['name'] == name), name) + '.')
        return name

    def status(self, running=None):
        """Set-up state for the pages: 'missing', 'downloading', 'installing', 'installed' or 'error'."""
        with self.lock:
            value = dict(self._value)
            busy = self.job is not None and self.job.is_alive()
        if not busy:
            value['state'] = 'installed' if self.installed() else ('error' if value['state'] == 'error' else 'missing')
            if value['state'] != 'error':
                value['progress'] = None
        value.update(version=self.release['version'], size_mb=round(self.release['size'] / 1e6),
                     disk_mb=self.release['extracted_mb'],  # once unpacked (the download is deleted)
                     licence=LICENCE, licence_url=LICENCE_URL, home_url=HOME_URL,
                     running=self.running() if running is None else bool(running),
                     steamvr_openxr=self.openxr_found(), **self.calibration())
        update = value['state'] == 'installed' and self.update_available()
        value['update'] = self.pinned['version'] if update else None
        if update:  # the download and its size are the newer release's
            value.update(size_mb=round(self.pinned['size'] / 1e6), disk_mb=self.pinned['extracted_mb'])
        found = None
        if _windows():
            # 1.0.0.7: whose Baballonia is used, and one the person installed (offered instead of a download when it
            # is the pinned release; another version stays as it is beside VROSTBridge's own copy).
            value['installation'] = self.installation() if value['state'] == 'installed' else None
            found = self.installed_here() if value['state'] in ('missing', 'error') else None
            value['installed_here'] = found
        if value['state'] == 'missing' and not value.get('message'):
            value['message'] = ('Not set up. Set up eye tracking downloads Baballonia ' + self.release['version'] +
                                ' (about ' + str(value['size_mb']) + ' MB) from Project Babble.')
            if found and found.get('usable'):
                value['message'] = ('Not set up. Baballonia ' + self.pinned['version'] + ' is installed on this PC: Set '
                                    'up eye tracking uses it, with nothing to download.')
            elif found:
                value['message'] += (' The Baballonia installed on this PC (' + (found.get('version') or 'another '
                                     'version') + ') stays as it is; VROSTBridge keeps its own copy.')
        elif value['state'] == 'installed':
            value['message'] = ('Baballonia ' + self.release['version'] + ' is set up' +
                                (' (the one installed on this PC)' if value.get('installation') == 'yours' else '') +
                                ('; calibrated ' + value['date'] if value.get('calibrated') and value.get('date') else
                                 '; calibrated' if value.get('calibrated') else
                                 '. Calibrate it once in the headset') + '.' +
                                (' Baballonia ' + self.pinned['version'] + ' is available: Update Baballonia downloads it '
                                 '(about ' + str(value['size_mb']) + ' MB); your calibration and settings stay.' if update else ''))
        return value

    def prepare_calibration(self):
        """RC4.6: put CALIBRATION_LAUNCHER in place of the program Baballonia starts for its calibration,
        keeping Baballonia's own program beside it (renamed once). 1.0.0.2: and VROSTBridge's Vulkan layer in
        VULKAN_LAYER_FOLDER beside them (see prepare_vulkan_layer). Returns 'installed' (something was written),
        'ready' (all in place) or None when the release has no calibration program; raises OSError when the
        launcher cannot be put in place. Windows: None (Baballonia shows its calibration as a SteamVR overlay itself)."""
        if _windows():
            return None
        folder = self.install_dir/CALIBRATION_FOLDER
        launcher = folder/CALIBRATION_LAUNCHER_NAME
        program = folder/CALIBRATION_PROGRAM_NAME
        try:
            with launcher.open('rb') as handle:
                head = handle.read(4)
        except FileNotFoundError:
            head = b''
        if head == b'\x7fELF':  # Baballonia's own program (a fresh set-up): kept under the new name
            os.replace(launcher, program)
        if not program.is_file():
            return None
        layer = self.prepare_vulkan_layer(folder)  # first: a launcher that finds the layer always finds all of it
        try:
            if launcher.read_text(encoding='utf-8') == CALIBRATION_LAUNCHER:
                return 'installed' if layer == 'installed' else 'ready'
        except (OSError, UnicodeDecodeError):
            pass
        temporary = folder/('.' + CALIBRATION_LAUNCHER_NAME + '.vrostbridge')
        try:
            temporary.write_text(CALIBRATION_LAUNCHER, encoding='utf-8')
            temporary.chmod(0o755)
            os.replace(temporary, launcher)
        except OSError:
            temporary.unlink(missing_ok=True)
            if not launcher.exists():  # never leave Baballonia without the program it starts
                os.replace(program, launcher)
            raise
        return 'installed'

    def prepare_vulkan_layer(self, folder):
        """1.0.0.2: copy VROSTBridge's Vulkan layer (VULKAN_LAYER_FILES) into VULKAN_LAYER_FOLDER in the calibration
        folder, where the launcher looks for it; again whenever this VROSTBridge's copy differs (an update). The
        manifest goes last, as the launcher enables the layer only when it finds the manifest. Returns 'installed',
        'ready' or None (this VROSTBridge has no layer, or it could not be copied: the calibration then starts
        without it, as in 1.0.0.1). Windows: None (Linux's Mesa needed it)."""
        if self.vulkan_layer is None or _windows():
            return None
        target = folder/VULKAN_LAYER_FOLDER
        written = False
        try:
            for name in VULKAN_LAYER_FILES:
                data = (self.vulkan_layer/name).read_bytes()
                try:
                    if (target/name).read_bytes() == data:
                        continue
                except OSError:
                    pass
                target.mkdir(exist_ok=True)
                temporary = target/('.' + name + '.vrostbridge')
                temporary.write_bytes(data)
                temporary.chmod(0o755 if name.endswith('.so') else 0o644)
                os.replace(temporary, target/name)
                written = True
        except OSError as error:
            for name in VULKAN_LAYER_FILES:
                (target/('.' + name + '.vrostbridge')).unlink(missing_ok=True)
            self.log('Eye tracking: VROSTBridge\'s Vulkan layer for the calibration could not be put in place (' +
                     str(error)[:160] + '); the calibration starts without it.')
            return None
        return 'installed' if written else 'ready'

    def openxr_found(self, max_age=10.0):
        """Whether SteamVR's OpenXR runtime is there for Baballonia's OpenXR calibration (read again every 10 s:
        the pages ask every second). None unless VROSTBRIDGE_CALIBRATION=openxr: the default calibration is a
        SteamVR overlay, which does not need it. Windows: None (its calibration is a SteamVR overlay there)."""
        if not openxr_calibration() or _windows():
            return None
        now = time.monotonic()
        with self.lock:
            when, value = self._openxr
            if when is not None and now - when < max_age:
                return value
        value = steamvr_openxr_runtime() is not None
        with self.lock:
            self._openxr = (now, value)
        return value

    def _set(self, **changes):
        with self.lock:
            self._value.update(changes)

    # ------------------------------------------------------------------ download and install
    def install_async(self):
        with self.lock:
            if self.job is not None and self.job.is_alive():
                return False
            if self.installed() and not self.update_available():
                return False
            if self.update_available() and self.running():
                raise ValueError('Close Baballonia (or stop the session) before updating it.')
            self.cancel.clear()
            self._set(state='downloading', message='Downloading Baballonia ' + self.pinned['version'] + '…', progress=0.0)
            self.job = threading.Thread(target=self._install_job, daemon=True, name='VROSTBridge-baballonia-setup')
            self.job.start()
            return True

    def cancel_install(self):
        self.cancel.set()

    def _install_job(self):
        try:
            self._installed_note = None
            self.install()
            self._set(state='installed', message='', progress=None)
            self.log(self._installed_note or ('Eye tracking: Baballonia ' + self.pinned['version'] + ' set up in ' +
                                              str(self.root/self.pinned['version']) + '.'))
            if self.on_installed:
                self.on_installed()
        except Exception as error:  # reported on the page; nothing half-installed is kept
            if self.cancel.is_set():
                self._set(state='missing', message='Set-up cancelled. Nothing was kept.', progress=None)
                self.log('Eye tracking: Baballonia set-up cancelled.')
            else:
                self._set(state='error', message='Setting up Baballonia did not finish: ' + str(error)[:300], progress=None)
                self.log('Eye tracking: Baballonia set-up failed: ' + str(error)[:300])

    def install(self):
        """Download, verify and unpack the pinned release (blocking). Windows: install_windows."""
        if _windows():
            return self.install_windows()
        if lzma is None:
            raise OSError("this Python cannot unpack .xz archives")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root/'download.part').unlink(missing_ok=True)  # what a companion that was closed mid-way left
        for stale in self.root.glob('.unpack-*'):
            shutil.rmtree(stale, ignore_errors=True)
        need = self.pinned['size'] + self.pinned['extracted_mb'] * 1_000_000 + 200_000_000
        free = shutil.disk_usage(self.root).free
        if free < need:
            raise OSError('it needs about ' + str(round(need / 1e9, 1)) + ' GB free; this PC has ' +
                          str(round(free / 1e9, 1)) + ' GB.')
        part = self.root/'download.part'
        try:
            self._download(part)
            if self.cancel.is_set():
                raise OSError('cancelled')
            self._set(state='installing', message='Checking the download…', progress=None)
            self._verify(part)
            self._set(message='Unpacking Baballonia…')
            self._extract(part)
        finally:
            part.unlink(missing_ok=True)
        for old in self.root.iterdir():  # earlier pinned versions
            if old.is_dir() and old.name != self.pinned['version'] and not old.name.startswith('.'):
                shutil.rmtree(old, ignore_errors=True)

    def _download(self, part):
        part.unlink(missing_ok=True)
        url, size = self.pinned['url'], self.pinned['size']
        if self.curl and Path(self.curl).is_file() and not _windows():  # Windows: urllib, with Windows' own certificates
            command = [self.curl, '--fail', '--location', '--silent', '--show-error', '--proto', '=https',
                       '--proto-redir', '=https', '--tlsv1.2', '--retry', '2', '--max-time', '3600',
                       '--output', str(part), url]
            environment = launch_environment()
            environment.update({key: os.environ[key] for key in PROXY_ENV if os.environ.get(key)})
            # Under the watcher, so a companion that is killed does not leave a 517 MB download running.
            process = self.popen(watched(command, 1), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                 env=environment)
            try:
                while process.poll() is None:
                    if self.cancel.wait(.5):
                        process.terminate()
                        process.wait(5)
                        raise OSError('cancelled')
                    try:
                        self._set(progress=min(1.0, part.stat().st_size / size))
                    except OSError:
                        pass
                if process.returncode != 0:
                    detail = (process.stderr.read() if process.stderr else '').strip()[-200:]
                    raise OSError('the download failed (' + (detail or 'curl ' + str(process.returncode)) + ')')
            finally:
                if process.stderr:
                    process.stderr.close()
            return
        import ssl
        import urllib.request
        context = ssl.create_default_context()
        for bundle in CA_BUNDLES:
            if Path(bundle).is_file():
                context.load_verify_locations(bundle)
                break
        request = urllib.request.Request(url, headers={'User-Agent': 'VROSTBridge'})
        with urllib.request.urlopen(request, timeout=60, context=context) as response, part.open('wb') as out:
            done = 0
            while True:
                if self.cancel.is_set():
                    raise OSError('cancelled')
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if done > size:
                    raise OSError('the download is larger than the release')
                self._set(progress=min(1.0, done / size))

    def _verify(self, part):
        size = part.stat().st_size
        if size != self.pinned['size']:
            raise OSError('the download is ' + str(size) + ' bytes, not the release\'s ' + str(self.pinned['size']))
        digest = hashlib.sha256()
        with part.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b''):
                digest.update(chunk)
        if digest.hexdigest() != self.pinned['sha256']:
            raise OSError('the download does not match the pinned SHA-256')

    def _extract(self, part):
        """Unpack in one pass (an .xz archive cannot seek back: listing it first would decompress it
        twice), each entry checked before it is written: plain files and folders inside the target only."""
        staging = Path(tempfile.mkdtemp(prefix='.unpack-', dir=self.root))
        total = max(1, self.pinned['extracted_mb'] * 1_000_000)
        try:
            done = count = 0
            with tarfile.open(part, 'r|xz') as archive:
                for member in archive:
                    name = member.name
                    if name.startswith('/') or '..' in Path(name).parts or '\\' in name:
                        raise OSError('unsafe path in the archive: ' + name[:80])
                    if not (member.isfile() or member.isdir()):
                        raise OSError('unexpected entry in the archive: ' + name[:80])
                    if self.cancel.is_set():
                        raise OSError('cancelled')
                    archive.extract(member, staging, filter='data')
                    count += 1
                    done += member.size
                    self._set(progress=min(1.0, done / total))
            executable = staging/EXECUTABLE
            if not executable.is_file():
                raise OSError('the archive has no ' + EXECUTABLE)
            for path in (executable, staging/'Calibration/Linux/Trainer/BabbleTrainer',
                         staging/'Calibration/Linux/Overlay/BabbleCalibration.x86_64'):
                if path.is_file():
                    path.chmod(path.stat().st_mode | 0o700)
            # 1.0.0.3: the receipt also names the release's capture (a later VROSTBridge may use this one).
            (staging/'vrostbridge-receipt.json').write_text(json.dumps(
                {'version': self.pinned['version'], 'url': self.pinned['url'], 'sha256': self.pinned['sha256'],
                 'capture': self.pinned['capture'], 'files': count, 'installed': time.strftime('%Y-%m-%dT%H:%M:%S')},
                indent=1) + '\n')
            target = self.root/self.pinned['version']
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    # ------------------------------------------------------------------ Windows set-up (1.0.0.7)
    def install_windows(self):
        """Windows: the person's own installation of the pinned release when there is one (adopt_installation);
        otherwise download Project Babble's installer, check its size and SHA-256, and run it silently into
        root/<version> (run_installer). Blocking; raises OSError with the reason the page shows."""
        if self.adopt_installation():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        part, installer = self.root/'download.part', self.root/self._installer_name()
        for stale in (part, installer):  # what a companion that was closed mid-way left
            try:
                stale.unlink(missing_ok=True)
            except OSError:  # still open in a virus scanner: replaced below
                pass
        need = self.pinned['size'] + self.pinned['extracted_mb'] * 1_000_000 + 200_000_000
        free = shutil.disk_usage(self.root).free
        if free < need:
            raise OSError('it needs about ' + str(round(need / 1e9, 1)) + ' GB free; this PC has ' +
                          str(round(free / 1e9, 1)) + ' GB.')
        try:
            self._download(part)
            if self.cancel.is_set():
                raise OSError('cancelled')
            self._set(state='installing', message='Checking the download…', progress=None)
            self._verify(part)
            _replace(part, installer)  # an installer runs as .exe
            self._set(message='Installing Baballonia…', progress=0.0)
            self.run_installer(installer, self.root/self.pinned['version'])
        finally:
            part.unlink(missing_ok=True)
            try:
                installer.unlink(missing_ok=True)
            except OSError:  # still being scanned: the next set-up removes it
                pass
        for old in self.root.iterdir():  # earlier pinned versions
            if old.is_dir() and old.name != self.pinned['version'] and not old.name.startswith('.'):
                _rmtree(old)
        (self.root/ADOPTED).unlink(missing_ok=True)  # VROSTBridge's own copy is used from now on

    def _installer_name(self):
        name = str(self.pinned['url']).rsplit('/', 1)[-1]
        return name if re.fullmatch(r'[A-Za-z0-9._-]{1,120}\.exe', name) else 'Baballonia-Setup.exe'

    @staticmethod
    def installer_command(installer, target):
        """The installer's command line: silent (/S) and into target (/D=), which NSIS requires last and unquoted
        (it takes the rest of the line, spaces included). A string, not a list: Python would quote a path with
        spaces, and NSIS would take the quote as part of the folder's name."""
        target = str(target)
        if '"' in target or not Path(target).is_absolute():
            raise ValueError('Not a folder for an installer: ' + target[:120])
        return subprocess.list2cmdline([str(installer), '/S']) + ' /D=' + target

    def run_installer(self, installer, target):
        """Run Baballonia's installer silently into target and check what it installed. The installer runs in a job
        object that ends it with the companion and lets it start no program (it would start Baballonia when it
        finishes); a Baballonia it started anyway is ended. Its uninstall entry and shortcuts are put back as they
        were afterwards. On failure nothing it installed is kept."""
        target = Path(target)
        if target.exists():
            _rmtree(target)  # a set-up that did not finish
            if target.exists():
                raise OSError("Baballonia's earlier files in " + str(target) + ' are in use: close Baballonia and try again.')
        command = self.installer_command(installer, target)
        registrations = self._registrations()
        job = None
        try:
            try:
                job = self.win32.create_job(single_process=True)
            except OSError as error:
                self.log("Eye tracking: Baballonia's installer runs without a job object (" + str(error)[:120] + ').')
            process = self.popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)
            if job is not None:
                try:
                    self.win32.assign(job, process.pid)
                except OSError as error:
                    self.log("Eye tracking: Baballonia's installer could not be put in its job object (" + str(error)[:120] +
                             '); a Baballonia it starts is closed afterwards.')
            deadline = time.monotonic() + INSTALL_TIMEOUT_S
            total = max(1, self.pinned['extracted_mb'] * 1_000_000)
            while process.poll() is None:
                if self.cancel.wait(1.0):
                    self._end_installer(process, job)
                    raise OSError('cancelled')
                if time.monotonic() > deadline:
                    self._end_installer(process, job)
                    raise OSError("Baballonia's installer did not finish in " + str(INSTALL_TIMEOUT_S // 60) + ' minutes')
                self._set(progress=min(1.0, _folder_size(target) / total))
            code = process.returncode
            if job is not None:
                try:
                    self.win32.terminate_job(job)  # anything it started
                except OSError:
                    pass
            self._end_installed_copies(target)
            if code != 0:
                raise OSError("Baballonia's installer ended with exit code " + str(code))
            if not (target/WINDOWS_EXECUTABLE).is_file():
                raise OSError("Baballonia's installer did not put " + WINDOWS_EXECUTABLE + ' in ' + str(target))
            for name, digest in (self.pinned.get('files') or {}).items():
                if not self._file_matches(target/name, digest):
                    raise OSError('the installed ' + name + " is not the pinned release's")
            count = sum(1 for _, _, files in os.walk(target) for _ in files)
            (target/'vrostbridge-receipt.json').write_text(json.dumps(
                {'version': self.pinned['version'], 'url': self.pinned['url'], 'sha256': self.pinned['sha256'],
                 'capture': self.pinned['capture'], 'files': count, 'platform': 'windows',
                 'installed': time.strftime('%Y-%m-%dT%H:%M:%S')}, indent=1) + '\n', encoding='utf-8')
        except BaseException:
            if target.exists():
                _rmtree(target)
            raise
        finally:
            if job is not None:
                try:
                    self.win32.close_handle(job)
                except OSError:
                    pass
            self._restore_registrations(registrations)

    def _end_installer(self, process, job):
        if job is not None:
            try:
                self.win32.terminate_job(job)
            except OSError:
                pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            pass

    def _end_installed_copies(self, target):
        """A Baballonia the installer started from target (when its job object could not stop it): ended now, before
        it writes its settings (VROSTBridge starts it when eye tracking needs it)."""
        program = _path_key(Path(target)/WINDOWS_EXECUTABLE)
        try:
            running = [(pid, path) for pid, path in self._windows_processes() if _path_key(path) == program]
        except OSError:
            return
        if [pid for pid, path in running if self.win32.terminate(pid, path, 3000)]:
            self.log('Eye tracking: the Baballonia its installer started was closed; VROSTBridge starts it when needed.')

    @staticmethod
    def _file_matches(path, digest):
        try:
            return _sha256(path) == digest
        except OSError:
            return False

    def _points_here(self, values=None, data=None):
        """Whether an uninstall entry (its values) or a shortcut (its bytes) is of VROSTBridge's copy."""
        if values is not None:
            location = str((values.get('InstallLocation') or ('',))[0] or '')
            return bool(location) and (_path_key(location) + '\\').startswith(_path_key(self.root) + '\\')
        marks = [str(self.root).encode('utf-16-le'), str(self.root).encode('utf-8')]
        return data is not None and any(mark in data for mark in marks)

    def _registrations(self):
        """What Baballonia's installer writes outside its folder (its uninstall entry and shortcuts), as it is now;
        ones of VROSTBridge's own copy (a set-up cut off before they were put back) count as none."""
        saved = {'read': False, 'key': None, 'files': {}}
        try:
            key = self.registry.read_values('HKCU', UNINSTALL_KEY)
            saved['key'] = None if key is not None and self._points_here(values=key) else key
            saved['read'] = True
        except OSError as error:
            self.log("Eye tracking: Baballonia's uninstall entry could not be read (" + str(error)[:120] + ').')
        for folder, name in INSTALLER_SHORTCUTS:
            try:
                base = self.registry.user_folder(folder)
            except OSError:
                base = None
            if not base:
                continue
            path = Path(base)/name
            try:
                data = path.read_bytes() if path.is_file() else None
            except OSError:
                continue  # unreadable: left as the installer leaves it
            saved['files'][str(path)] = None if self._points_here(data=data) else data
        return saved

    def _restore_registrations(self, saved):
        """Put back what the installer replaced: its uninstall entry (a Baballonia the person installed keeps its own;
        VROSTBridge's copy gets none) and its shortcuts (VROSTBridge starts its copy, and removes it)."""
        if saved.get('read'):
            try:
                self.registry.restore_values('HKCU', UNINSTALL_KEY, saved['key'])
            except OSError as error:
                self.log("Eye tracking: Baballonia's uninstall entry could not be put back (" + str(error)[:120] + ').')
        for path, data in saved.get('files', {}).items():
            try:
                if data is None:
                    Path(path).unlink(missing_ok=True)
                else:
                    Path(path).write_bytes(data)
            except OSError as error:
                self.log('Eye tracking: the shortcut ' + Path(path).name + ' could not be put back (' + str(error)[:120] + ').')

    def _forget_registrations(self):
        """Remove: an uninstall entry or shortcuts of VROSTBridge's copy (a set-up cut off before they were put back)."""
        try:
            values = self.registry.read_values('HKCU', UNINSTALL_KEY)
            if values is not None and self._points_here(values=values):
                self.registry.delete_key('HKCU', UNINSTALL_KEY)
        except OSError:
            pass
        for folder, name in INSTALLER_SHORTCUTS:
            try:
                base = self.registry.user_folder(folder)
                path = Path(base)/name if base else None
                if path is not None and path.is_file() and self._points_here(data=path.read_bytes()):
                    path.unlink()
            except OSError:
                continue

    def _version_matches(self, version):
        text = str(version or '').strip()
        return bool(text) and text.lstrip('vV') == self.pinned['version']

    def find_installations(self):
        """Windows: Baballonia installations of the person (not VROSTBridge's): [{'path', 'version'}], from the
        programs Windows lists as installed (version: the entry's), then the installer's default folder."""
        if not _windows():
            return []
        found, seen, ours = [], set(), _path_key(self.root) + '\\'

        def add(folder, version):
            folder = str(folder or '').strip().strip('"')
            if not folder:
                return
            key = _path_key(folder)
            path = Path(folder)
            if key in seen or (key + '\\').startswith(ours) or not path.is_absolute() or \
                    not (path/WINDOWS_EXECUTABLE).is_file():
                return
            seen.add(key)
            found.append({'path': path, 'version': str(version).strip() if version else None})
        try:
            entries = self.registry.uninstall_entries()
        except OSError:
            entries = []
        for entry in entries:
            folder = str(entry.get('InstallLocation') or '').strip().strip('"')
            if not folder:
                for value in ('UninstallString', 'DisplayIcon'):
                    text = str(entry.get(value) or '').strip()
                    text = text[1:text.index('"', 1)] if text.startswith('"') and '"' in text[1:] else text.split(',')[0]
                    if text:
                        folder = ntpath.dirname(text)
                        break
            add(folder, entry.get('DisplayVersion'))
        local = os.environ.get('LOCALAPPDATA')
        if local:
            add(Path(local)/'Baballonia', None)
        return found

    def installed_here(self, max_age=30.0):
        """Windows: a Baballonia the person installed, for the pages: {'version', 'usable'} (usable: its entry names
        the pinned release; set-up checks its files), else None. Read at most every 30 s."""
        now = time.monotonic()
        when, value = self._found
        if when is not None and now - when < max_age:
            return value
        try:
            found = self.find_installations()
        except OSError:
            found = []
        value = None
        if found:
            pinned = [entry for entry in found if self._version_matches(entry.get('version'))]
            chosen = (pinned or found)[0]
            value = {'version': chosen.get('version'), 'usable': bool(pinned) and bool(self.pinned.get('files'))}
        self._found = (now, value)
        return value

    def adopt_installation(self):
        """Windows: use a Baballonia the person installed when it is the pinned release (its program files' SHA-256,
        and its uninstall entry's version when it has one) instead of downloading it: recorded in VROSTBridge's folder
        (ADOPTED), run from where it is and never removed (Remove Baballonia only stops using it). Returns its
        folder, or None."""
        if not _windows() or not self.pinned.get('files'):
            return None
        for candidate in self.find_installations():
            version = candidate.get('version') or ''
            if version and not self._version_matches(version):
                continue
            folder = candidate['path']
            if self.cancel.is_set():
                return None
            if not all(self._file_matches(folder/name, digest) for name, digest in self.pinned['files'].items()):
                continue
            stamp = self._stamp(folder)
            if stamp is None:
                continue
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = self.root/('.' + ADOPTED + '.tmp')
            temporary.write_text(json.dumps(
                {'version': self.pinned['version'], 'sha256': self.pinned['sha256'], 'path': str(folder), 'stamp': stamp,
                 'display_version': version or None, 'adopted': time.strftime('%Y-%m-%dT%H:%M:%S')}, indent=1) + '\n',
                encoding='utf-8')
            os.replace(temporary, self.root/ADOPTED)
            self._found = (None, None)
            self._installed_note = ('Eye tracking: the Baballonia ' + self.pinned['version'] + ' installed on this PC (' +
                                    str(folder) + ') is used; nothing was downloaded, and VROSTBridge never removes it.')
            return folder
        return None

    def adopt_earlier_copy(self):
        """1.0.0.3: before 1.0.0.1 VROSTBridge kept its Baballonia under another folder name in the same data folder
        (~/.local/share/<name>/baballonia/<version>, with its receipt <name>-receipt.json). An update from such a
        version installed fresh and asked to download Baballonia again. When the pinned release is not set up here,
        such a copy of it (its receipt naming this version and this SHA-256, its program there) is moved here, with
        VROSTBridge's receipt: nothing is downloaded. Returns the folder it came from, or None. Not on Windows (no
        earlier Windows copies; a Baballonia the person installed: adopt_installation)."""
        if _windows() or self.installed() or not self.root.parent.parent.is_dir():
            return None
        version, sha256 = self.pinned['version'], self.pinned['sha256']
        target = self.root/version
        try:
            folders = sorted(self.root.parent.parent.iterdir())
        except OSError:
            return None
        for folder in folders:
            candidate = folder/'baballonia'/version
            try:
                if folder == self.root.parent or candidate.is_symlink() or not (candidate/EXECUTABLE).is_file():
                    continue
                receipts = [path for path in candidate.glob('*-receipt.json') if path.is_file() and not path.is_symlink()]
            except OSError:
                continue
            for receipt in receipts:
                try:
                    data = json.loads(receipt.read_text())
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict) or data.get('version') != version or data.get('sha256') != sha256:
                    continue
                try:
                    if self.running() or (self.job is not None and self.job.is_alive()):
                        return None
                    self.root.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        shutil.rmtree(target)  # an unfinished copy without its receipt
                    shutil.move(str(candidate), str(target))
                    for old in target.glob('*-receipt.json'):
                        if old.name != 'vrostbridge-receipt.json':
                            old.unlink(missing_ok=True)
                    record = dict(data, capture=data.get('capture') or self.pinned['capture'],
                                  adopted=time.strftime('%Y-%m-%dT%H:%M:%S'))
                    (target/'vrostbridge-receipt.json').write_text(json.dumps(record, indent=1) + '\n')
                except OSError as error:
                    # The folder is not named: it carries that installation's earlier name.
                    self.log('Eye tracking: the Baballonia an earlier installation left could not be moved here (' +
                             (error.strerror or type(error).__name__) + ').')
                    return None
                self.log('Eye tracking: Baballonia ' + version + ' found where an earlier installation left it and moved '
                         'here; nothing was downloaded.')
                return candidate
        return None

    def remove(self):
        """Remove the downloaded Baballonia (its settings and calibrations in ~/.config/ProjectBabble stay).
        Windows: VROSTBridge's folder with its copy, and an uninstall entry or shortcuts of that copy a cut-off set-up
        left; a Baballonia the person installed is no longer used and stays; %APPDATA%\\ProjectBabble stays. Raises
        OSError when files are in use."""
        self.stop()
        with self.lock:
            if self.job is not None and self.job.is_alive():
                raise ValueError('Baballonia is still being set up.')
            if _windows():
                self._forget_registrations()
                if self.root.exists():
                    _rmtree(self.root)
                self._value = {'state': 'missing', 'message': '', 'progress': None}
                self._found = (None, None)
                if self.root.exists():
                    raise OSError("Some of Baballonia's files in " + str(self.root) + ' are in use: close Baballonia and '
                                  'try again.')
                return
            shutil.rmtree(self.root, ignore_errors=True)
            self._value = {'state': 'missing', 'message': '', 'progress': None}

    # ------------------------------------------------------------------ settings
    def _setup_state(self):
        try:
            value = json.loads(self.state_path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_setup_state(self, value):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name('.' + self.state_path.name + '.tmp')
        temporary.write_text(json.dumps(value, indent=1) + '\n')
        os.replace(temporary, self.state_path)

    def camera_name(self):
        """Windows: the name Windows gives the Beyond 2e's eye cameras, as Baballonia lists them (BigEye, or the
        generic name it may give them instead); BigEye when Windows does not list them now."""
        try:
            camera = self.camera_check() or {}
        except Exception:
            camera = {}
        name = camera.get('name') if camera.get('state') in ('present', 'generic-name') else None
        return str(name or WINDOWS_CAMERA_NAME)

    def configure(self, frame_size=None, camera=None):
        """Point Baballonia at the camera helper and the gaze relay. Only while it is closed: it keeps
        its settings in memory and writes them back. Returns what was changed. Windows (1.0.0.7): at the eye
        cameras themselves (camera: their name, as EyeCameras found it; else camera_name()), both eyes on that one
        camera through OpenCvCapture, the same output and filter as on Linux; the processor once (below)."""
        if not self.installed():
            raise ValueError('Set up eye tracking first.')
        if self.running():
            return []
        path = self.settings_path
        try:
            settings = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(settings, dict):
                raise ValueError('not an object')
            existed = True
        except FileNotFoundError:
            settings = json.loads((self.install_dir/'LocalSettings.json').read_text(encoding='utf-8-sig'))
            existed = False
        except ValueError:
            backup = path.with_name('LocalSettings.json.unreadable')
            shutil.copyfile(path, backup)
            self.log('Eye tracking: Baballonia\'s settings could not be read; kept as ' + backup.name + ' and started over.')
            settings = json.loads((self.install_dir/'LocalSettings.json').read_text(encoding='utf-8-sig'))
            existed = False
        setup = self._setup_state()
        # OSCQuery would follow VRChat on the host PC instead of 127.0.0.1:9005 (rc6 reads 'UseOSCQuery';
        # its defaults file names it 'AppSettings_UseOSCQuery': both are kept off).
        wanted = {'OSCAddress': '127.0.0.1', 'OSCOutPort': OSC_PORT, 'VRC_UseNativeTracking': True,
                  'UseOSCQuery': False, 'AppSettings_UseOSCQuery': False, 'AppSettings_SteamVRAutoStart': False,
                  'AppSettings_CheckForUpdates': False, 'AppSettings_UseGPU': False}
        windows = _windows()
        source = CAMERA_URL
        if windows:
            # Baballonia's own default on Windows is the GPU (DirectML), which the stream's decoding and SteamVR's
            # compositor need on time. VROSTBridge sets the processor once, as Linux runs it (one step down in
            # priority there and here); a later choice in Baballonia's settings is kept.
            del wanted['AppSettings_UseGPU']
            if not setup.get('cpu_inference_set'):
                wanted['AppSettings_UseGPU'] = False
                setup['cpu_inference_set'] = True
            # Both eyes read the one camera (its picture holds both side by side): Baballonia lists DirectShow
            # cameras by name and opens the same camera once for both.
            source = str(camera or self.camera_name())
        for side in ('LeftCamera', 'RightCamera'):
            wanted['LastOpened' + side] = source
            wanted['LastOpenedPreferredCapture' + side] = self.release['capture']
            wanted['ShouldAutostart' + side] = True
        if not setup.get('first_configured'):
            wanted['ShowOnboardingOnStartup'] = False
        # Baballonia's eye filter, once per filter version, to follow quick eye movements sooner (the sharp region
        # of the stream follows it). Only while it is as Baballonia or an earlier VROSTBridge set it: a filter
        # someone chose is kept, and so is a later change in Baballonia's settings.
        filter_set = False
        applied = setup.get('eye_filter_version', 1 if setup.get('eye_filter_set') else 0)
        if not isinstance(applied, int) or applied < EYE_FILTER_VERSION:
            untouched = all(settings.get(key, value) == value for key, value in BABALLONIA_EYE_FILTER.items())
            ours = any(all(settings.get(key) == value for key, value in earlier.items()) for earlier in EARLIER_EYE_FILTERS)
            if untouched or ours:
                wanted.update(EYE_FILTER)
                filter_set = any(settings.get(key) != value for key, value in EYE_FILTER.items())
            setup['eye_filter_set'] = True
            setup['eye_filter_version'] = EYE_FILTER_VERSION
        changed = [key for key, value in wanted.items() if settings.get(key) != value]
        settings.update(wanted)
        crops = eye_crops(frame_size)
        # The eye areas are set once, then left to the person; crops someone already chose in
        # Baballonia (that fit this camera picture and are not its 256 x 256 default) are kept,
        # and crops that no longer fit the picture are set again.
        roi_changed = []
        if crops and (not self._crops_fit(settings, frame_size) or
                      (not setup.get('crops_set') and self._default_crops(settings))):
            for side, crop in zip(('LeftCamera', 'RightCamera'), crops):
                camera = settings.get(side) if isinstance(settings.get(side), dict) else {}
                if camera.get('Roi') != crop:
                    camera = dict(camera, Roi=crop)
                    camera.setdefault('Camera', 0 if side == 'LeftCamera' else 1)
                    settings[side] = camera
                    roi_changed.append(side)
        if crops:
            setup['crops_set'] = True
        changed += [side + '.Roi' for side in roi_changed]
        if changed or not existed:
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = path.with_name('LocalSettings.json.before-vrostbridge')
            if existed and not backup.exists():
                shutil.copyfile(path, backup)
            temporary = path.with_name('.LocalSettings.json.vrostbridge')
            temporary.write_text(json.dumps(settings, indent=2) + '\n')
            os.chmod(temporary, 0o600)
            _replace(temporary, path)
        setup.update(first_configured=setup.get('first_configured') or time.strftime('%Y-%m-%dT%H:%M:%S'),
                     version=self.release['version'], frame_size=list(frame_size) if frame_size else setup.get('frame_size'))
        self._save_setup_state(setup)
        if roi_changed:
            self.log('Eye tracking: Baballonia\'s ' + ' and '.join(side.replace('Camera', '').lower() for side in roi_changed) +
                     ' eye area set to ' + ('the halves of' if crops[0] != crops[1] else 'the whole of') + ' the ' +
                     str(frame_size[0]) + ' x ' + str(frame_size[1]) + ' camera picture.')
        if filter_set:
            self.log('Eye tracking: Baballonia\'s eye filter set to follow quick eye movements sooner (smoothing from ' +
                     str(EYE_FILTER['AppSettings_OneEuroMinFreqCutoff']) + ' Hz, speed ' +
                     str(EYE_FILTER['AppSettings_OneEuroSpeedCutoff']) + '; Baballonia\'s own 0.5 Hz and 3, '
                     'VROSTBridge 1.0.0.3 to 1.0.0.4 1 Hz and 10). Baballonia\'s settings can change it; VROSTBridge '
                     'does not set it again.')
        if windows and changed.count('AppSettings_UseGPU'):
            self.log('Eye tracking: Baballonia set to run its eye model on the processor, one step down in priority '
                     '(the stream needs the GPU on time). Baballonia\'s settings can change it; VROSTBridge does not set '
                     'it again.')
        others = [key for key in changed if not key.endswith('.Roi') and key not in EYE_FILTER and
                  not (windows and key == 'AppSettings_UseGPU')]
        if others:
            self.log('Eye tracking: Baballonia set to read ' + (('the ' + source + ' camera (both eyes, ' +
                     str(self.release['capture']) + ')') if windows else CAMERA_URL) + ' and send to 127.0.0.1:' +
                     str(OSC_PORT) + ' (' + ', '.join(others[:8]) + (', …' if len(others) > 8 else '') + ').')
        return changed

    @staticmethod
    def _default_crops(settings):
        """Baballonia's own starting eye areas (256 x 256 at the corner), or none at all."""
        for side in ('LeftCamera', 'RightCamera'):
            camera = settings.get(side) if isinstance(settings.get(side), dict) else {}
            if camera.get('Roi') not in (None, {'X': 0, 'Y': 0, 'Width': 256, 'Height': 256}):
                return False
        return True

    @staticmethod
    def _crops_fit(settings, frame_size):
        width, height = frame_size
        for side in ('LeftCamera', 'RightCamera'):
            roi = (settings.get(side) or {}).get('Roi') if isinstance(settings.get(side), dict) else None
            try:
                if not roi or roi['X'] < 0 or roi['Y'] < 0 or roi['Width'] <= 0 or roi['Height'] <= 0 or \
                        roi['X'] + roi['Width'] > width or roi['Y'] + roi['Height'] > height:
                    return False
            except (KeyError, TypeError):
                return False
        return True

    # ------------------------------------------------------------------ start and stop
    def _systemctl(self, *arguments, timeout=15):
        if not self.systemctl or not Path(self.systemctl).is_file():
            return None
        try:
            return self.runner([self.systemctl, '--user', *arguments], capture_output=True, text=True, timeout=timeout,
                               env=launch_environment())
        except (OSError, subprocess.SubprocessError):
            return None

    def unit_active(self):
        if _windows():
            return False
        result = self._systemctl('is-active', '--quiet', UNIT + '.service', timeout=5)
        return bool(result) and result.returncode == 0

    def running(self, max_age=0.0):
        """Whether Baballonia from this install runs (VROSTBridge's user service, its own process, or
        one started by hand). max_age lets the pages reuse an answer a few seconds old."""
        when, value = self._running
        if max_age and time.monotonic() - when < max_age:
            return value
        value = bool((self.process is not None and self.process.poll() is None) or self.unit_active()
                     or self._own_processes())
        self._running = (time.monotonic(), value)
        return value

    @staticmethod
    def _processes():
        """(pid, executable) of this user's processes; the kernel reports executables fully resolved."""
        try:
            entries = [entry for entry in Path('/proc').iterdir() if entry.name.isdecimal()]
        except OSError:
            return []
        found = []
        for entry in entries:
            try:
                if entry.stat().st_uid == os.getuid():
                    found.append((int(entry.name), os.readlink(entry/'exe')))
            except OSError:
                continue
        return found

    def _windows_processes(self):
        """Windows: (pid, full program path) of the running processes named as Baballonia's programs are
        (WINDOWS_PROGRAMS: Toolhelp32 lists names; only those are opened for their path)."""
        names = {name.lower() for name in WINDOWS_PROGRAMS}
        found = []
        for pid, name in self.win32.processes():
            if str(name).lower() in names:
                path = self.win32.image_path(pid)
                if path:
                    found.append((pid, path))
        return found

    def _process_list(self):
        if not _windows():
            return self._processes()
        try:
            return self._windows_processes()
        except OSError:
            return []

    def _executable_keys(self):
        """Windows: the program's path as Windows reports it (resolved, any case), and as VROSTBridge names it."""
        return {_path_key(os.path.realpath(self.executable)), _path_key(os.path.abspath(self.executable))}

    def _is_ours(self, pid):
        """A live process of this user running this install's Baballonia."""
        if _windows():
            try:
                path = self.win32.image_path(pid)
            except OSError:
                return False
            return bool(path) and _path_key(path) in self._executable_keys()
        try:
            entry = Path('/proc')/str(pid)
            return (os.readlink(entry/'exe') == os.path.realpath(self.executable)
                    and entry.stat().st_uid == os.getuid())
        except OSError:
            return False

    def calibrating(self):
        """Baballonia's calibration overlay or trainer (from this install) is running."""
        if _windows():
            bases = {_path_key(os.path.realpath(self.install_dir/'Calibration')) + '\\',
                     _path_key(os.path.abspath(self.install_dir/'Calibration')) + '\\'}
            return any(_path_key(path).startswith(tuple(bases)) for _, path in self._process_list())
        base = os.path.realpath(self.install_dir/'Calibration') + os.sep
        return any(path.startswith(base) for _, path in self._processes())

    def _own_processes(self):
        """Baballonia processes started from this install (also ones started by hand)."""
        if _windows():
            keys = self._executable_keys()
            return [pid for pid, path in self._process_list() if _path_key(path) in keys]
        target = os.path.realpath(self.executable)
        return [pid for pid, path in self._processes() if path == target]

    def window_pids(self):
        """Process ids of this install's running Baballonia (to find its window)."""
        return self._own_processes()

    def other_instances(self):
        """Executables of Baballonia copies not from this install that run now (Steam's, one installed by
        hand): only one Baballonia can run, and a running one writes its settings back over ours."""
        if _windows():
            keys = self._executable_keys()
            return sorted({path for _, path in self._process_list()
                           if ntpath.basename(path).lower() == WINDOWS_EXECUTABLE.lower() and _path_key(path) not in keys})
        target = os.path.realpath(self.executable)
        return sorted({path for _, path in self._processes()
                       if os.path.basename(path) in (EXECUTABLE, 'Baballonia', 'Baballonia.exe') and path != target})

    def start(self, show='minimised'):
        """Start Baballonia unless it already runs. Returns 'started', 'running' or raises. show (Windows): its
        window 'minimised' without taking the focus (a session), or 'normal' (Open Baballonia)."""
        with self.run_lock:
            if not self.installed():
                raise ValueError('Set up eye tracking first.')
            if self.running():
                return 'running'
            log = self.root/'baballonia.log'
            try:
                if log.stat().st_size > 2 * 1024 * 1024:
                    log.replace(log.with_suffix('.previous.log'))
            except OSError:
                pass
            if _windows():
                return self._start_windows(log, show)
            environment = launch_environment()
            calibration = ''
            if openxr_calibration():
                # VROSTBRIDGE_CALIBRATION=openxr keeps Baballonia's own OpenXR calibration: pointed at SteamVR's
                # OpenXR runtime, whichever runtime this PC has active (see steamvr_openxr_runtime). The default
                # SteamVR overlay does not use OpenXR, and with Monado or WiVRn (no SteamVR) Baballonia's OpenXR
                # calibration keeps this PC's own runtime.
                runtime = steamvr_openxr_runtime()
                if runtime is not None:
                    environment['XR_RUNTIME_JSON'] = str(runtime)
                    calibration = "; its OpenXR calibration uses SteamVR's OpenXR runtime"
                else:
                    calibration = ("; SteamVR's OpenXR runtime (steamxr_linux64.json) was not found, so its OpenXR "
                                   "calibration uses this PC's OpenXR setting")
                with self.lock:
                    self._openxr = (time.monotonic(), runtime is not None)
                environment['VROSTBRIDGE_CALIBRATION'] = 'openxr'
            if os.environ.get('VROSTBRIDGE_VULKAN_LAYER', '').strip().lower() == 'off':
                environment['VROSTBRIDGE_VULKAN_LAYER'] = 'off'  # the calibration launcher leaves the layer out
            try:
                if self.prepare_calibration() == 'installed':
                    layer = (self.install_dir/CALIBRATION_FOLDER/VULKAN_LAYER_FOLDER/VULKAN_LAYER_FILES[1]).is_file()
                    self.log('Eye tracking: Baballonia\'s calibration starts as a SteamVR overlay over the stream '
                             '(VROSTBridge\'s launcher in its ' + CALIBRATION_FOLDER + ' folder' +
                             (', with VROSTBridge\'s Vulkan layer for SteamVR\'s overlay textures' if layer else '') + ').')
            except OSError as error:
                self.log('Eye tracking: the calibration launcher could not be put in place (' + str(error)[:160] +
                         '); Baballonia starts its calibration its own way.')
            # The watcher ends it when this companion is gone (crashed or killed) and gives it its grace.
            # nice replaces itself with Baballonia (same process), one step down in priority (1.0.0.4).
            lower = [NICE, '-n', str(NICENESS)] if Path(NICE).is_file() else []
            program = watched([*lower, self.executable], STOP_GRACE_S)
            if self.systemd_run and Path(self.systemd_run).is_file():
                self._systemctl('reset-failed', UNIT + '.service', timeout=5)
                output = [] if any(ch.isspace() for ch in str(log)) else [  # systemd splits such a path
                    '-p', 'StandardOutput=append:' + str(log), '-p', 'StandardError=append:' + str(log)]
                command = [self.systemd_run, '--user', '--quiet', '--collect', '--unit', UNIT,
                           '--description', 'Baballonia eye tracking, run by VROSTBridge',
                           '--working-directory', str(self.install_dir),
                           '-p', 'KillMode=mixed', '-p', 'TimeoutStopSec=' + str(STOP_GRACE_S + 3),
                           '-p', 'CPUWeight=' + str(CPU_WEIGHT), *output,
                           *('--setenv=' + key + '=' + value for key, value in environment.items()),
                           '--', *program]
                try:
                    result = self.runner(command, capture_output=True, text=True, timeout=15, env=environment)
                except (OSError, subprocess.SubprocessError) as error:
                    result = None
                    self.log('Eye tracking: systemd-run failed (' + str(error)[:120] + '); starting Baballonia directly.')
                if result is not None and result.returncode == 0:
                    self.unit_started = True
                    self._running = (time.monotonic(), True)
                    self.log('Eye tracking: Baballonia started as the user service ' + UNIT + calibration + '.')
                    return 'started'
                if result is not None:
                    self.log('Eye tracking: systemd-run refused (' + (result.stderr or '').strip()[-160:] +
                             '); starting Baballonia directly.')
            with log.open('ab') as output:
                self.process = self.popen(program, cwd=str(self.install_dir), env=environment,
                                          stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                          start_new_session=True)
            self._running = (time.monotonic(), True)
            self.log('Eye tracking: Baballonia started (process ' + str(self.process.pid) + ')' + calibration + '.')
            return 'started'

    def _start_windows(self, log, show):
        """Windows: Baballonia one step down in priority, its window minimised without taking the focus (the first
        window a program shows follows STARTUPINFO), in a job object that ends it, and what it starts (its
        calibration overlay and trainer), when this companion is gone. Without the job it still runs (logged)."""
        info_type = getattr(subprocess, 'STARTUPINFO', None)
        startupinfo = info_type() if info_type is not None else _StartupInfo()
        startupinfo.dwFlags |= STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_SHOWNORMAL if show == 'normal' else SW_SHOWMINNOACTIVE
        if self.process_job is not None:  # one an earlier Baballonia (since ended) had
            try:
                self.win32.close_handle(self.process_job)
            except OSError:
                pass
            self.process_job = None
        self.root.mkdir(parents=True, exist_ok=True)
        with log.open('ab') as output:
            process = self.popen([str(self.executable)], cwd=str(self.install_dir), env=launch_environment(),
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                 creationflags=BELOW_NORMAL_PRIORITY_CLASS | CREATE_NEW_PROCESS_GROUP,
                                 startupinfo=startupinfo)
        self.process = process
        tied = ''
        try:
            job = self.win32.create_job()
            try:
                self.win32.assign(job, process.pid)
            except OSError:
                self.win32.close_handle(job)
                raise
            self.process_job = job
        except OSError as error:
            tied = '; it could not be tied to VROSTBridge (' + str(error)[:120] + '), so it stays open if VROSTBridge ends'
        self._running = (time.monotonic(), True)
        self.log('Eye tracking: Baballonia started (process ' + str(process.pid) + ', ' +
                 ('minimised' if show != 'normal' else 'shown') + ')' + tied + '.')
        return 'started'

    def stop(self):
        """Stop Baballonia from this install (its calibration overlay and trainer with it). It goes on
        running after SIGTERM, so after STOP_GRACE_S seconds it is killed. Windows: its window is closed (it saves
        its settings), and after STOP_GRACE_WINDOWS_S it is ended."""
        with self.run_lock:
            return self._stop_windows() if _windows() else self._stop()

    def _stop_windows(self):
        stopped = False
        process, self.process = self.process, None
        job, self.process_job = self.process_job, None
        pids = set(self._own_processes())
        if process is not None and process.poll() is None:
            pids.add(process.pid)
        if pids:
            asked = set()  # asked to close their window: they save their settings, stop their cameras and end
            try:
                for pid in sorted(pids):
                    for hwnd in self.win32.windows([pid]):
                        if self.win32.post_close(hwnd):
                            asked.add(pid)
            except OSError:
                pass
            deadline = time.monotonic() + STOP_GRACE_WINDOWS_S  # one without a window yet is ended at once
            while asked and time.monotonic() < deadline:
                alive = set(self._own_processes())
                if process is not None and process.poll() is None:
                    alive.add(process.pid)
                if not asked & alive:
                    break
                time.sleep(.1)
            stopped = True
        if job is not None:
            try:
                self.win32.terminate_job(job, 1)  # what is left, its calibration overlay and trainer too
            except OSError:
                pass
            try:
                self.win32.close_handle(job)
            except OSError:
                pass
        # Ours not in that job (an earlier companion's, one started by hand or by its installer), and a calibration
        # overlay or trainer of this install left running.
        keys = self._executable_keys()
        calibration = {_path_key(os.path.realpath(self.install_dir/'Calibration')) + '\\',
                       _path_key(os.path.abspath(self.install_dir/'Calibration')) + '\\'}
        for pid, path in self._process_list():
            if _path_key(path) in keys or _path_key(path).startswith(tuple(calibration)):
                try:
                    if self.win32.terminate(pid, path, 3000):
                        stopped = True
                except OSError:
                    pass
        if process is not None:
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                pass
        self._running = (time.monotonic(), False)
        if stopped:
            self.log('Eye tracking: Baballonia stopped.')
        return stopped

    def show_window(self):
        """Windows: Baballonia's window back (restored, in front with the focus), as its taskbar button would."""
        pids = self.window_pids()
        if not pids:
            raise ValueError('Baballonia is not running.')
        try:
            windows = self.win32.windows(pids)
            if not windows:
                raise ValueError('Baballonia\'s window was not found: it may still be starting.')
            window = windows[0]
            self.win32.show(window, SW_RESTORE if self.win32.is_minimised(window) else SW_SHOW)
            self.win32.bring_to_front(window)
        except OSError as error:
            raise ValueError('Baballonia\'s window cannot be shown from here (' + str(error)[:120] + '); use the '
                             'taskbar.') from error

    def minimise_windows(self, still_wanted=None):
        """Windows: minimise Baballonia's windows without giving the focus to another. None while it has no window
        yet; else how many were showing and were minimised (none once still_wanted() says no: Open Baballonia)."""
        pids = self.window_pids()
        windows = self.win32.windows(pids) if pids else []
        if not windows:
            return None
        shown = [window for window in windows if not self.win32.is_minimised(window)]
        if shown and still_wanted is not None and not still_wanted():
            return 0
        for window in shown:
            self.win32.show(window, SW_SHOWMINNOACTIVE)
        return len(shown)

    def log_path(self):
        """Baballonia's log for reports: on Windows its own newest log in Documents\\ProjectBabble (a Windows
        program writes nothing to VROSTBridge's baballonia.log), else VROSTBridge's."""
        own = self.root/'baballonia.log'
        if not _windows():
            return own
        try:
            documents = self.registry.user_folder('Personal')
        except OSError:
            documents = None
        documents = Path(documents) if documents else Path.home()/'Documents'
        try:
            logs = sorted((documents/'ProjectBabble').glob('baballonia_desktop.*.log'), key=lambda path: path.stat().st_mtime)
        except OSError:
            logs = []
        return logs[-1] if logs else own

    def _stop(self):
        stopped = False
        if self.unit_started or self.unit_active():
            result = self._systemctl('stop', UNIT + '.service', timeout=STOP_GRACE_S + 15)
            stopped = bool(result) and result.returncode == 0
            self.unit_started = False
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)  # the watcher: SIGTERM, then SIGKILL after the grace
                process.wait(STOP_GRACE_S + 2)
            except subprocess.TimeoutExpired:
                pass
            except ProcessLookupError:
                pass
            try:  # what is left of its group (a calibration overlay or trainer that ignored SIGTERM)
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                pass
            stopped = True
        left = self._own_processes()  # one an earlier companion started without systemd, or by hand
        for pid in left:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + STOP_GRACE_S
        while left and time.monotonic() < deadline:
            time.sleep(.1)
            left = [pid for pid in left if self._is_ours(pid)]
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
                stopped = True
            except OSError:
                pass
        self._running = (time.monotonic(), False)
        if stopped:
            self.log('Eye tracking: Baballonia stopped.')
        return stopped


def remove_downloaded(log=print, root=None):
    """For VROSTBridge's uninstaller (launcher/install_maintenance.py): stop the Baballonia VROSTBridge set up and
    remove what it downloaded, data_root() (Windows: %LOCALAPPDATA%\\VROSTBridge\\Baballonia, with any uninstall entry
    or shortcuts of its copy; Linux: ~/.local/share/vrostbridge/baballonia). Baballonia's own settings and calibration
    (%APPDATA%\\ProjectBabble, ~/.config/ProjectBabble) and a Baballonia the person installed stay. Returns whether
    there was anything to remove; raises OSError (or ValueError while it is being set up) when it could not."""
    manager = BaballoniaManager(log=log, root=root, vulkan_layer=None)
    if not manager.root.exists():
        return False
    manager.remove()
    if manager.root.exists():
        raise OSError('Baballonia could not be removed from ' + str(manager.root) + ': some of its files are in use.')
    return True
