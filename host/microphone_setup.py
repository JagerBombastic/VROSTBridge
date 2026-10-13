"""One-step headset microphone setup on the Windows host (RC4.3).

Windows lists a microphone only when a signed audio driver provides one, so the
headset's microphone reaches apps on the host (Discord, games) through a virtual
audio cable: the streaming driver plays the microphone into the cable's playback
side ("CABLE Input") and apps record from its recording side ("CABLE Output
(VB-Audio Virtual Cable)"). RC4.2 asked the user to find and install a cable.

Set up headset microphone on the host page now:
1. downloads VB-CABLE, the free virtual audio cable by VB-Audio Software (Vincent
   Burel; donationware, https://vb-cable.com), from VB-Audio's own server, only when
   the user asks. VROSTBridge does not include or redistribute it;
2. extracts it to a new folder of its own and, while it holds every extracted file
   open so nothing can change, replace, rename or delete them (links are refused), checks
   them through those handles: unchanged since the download, no other file in the folder,
   and the SHA-256 of the VB-CABLE release pinned here, or for a newer package a valid
   Authenticode signature on its setup program by VB-Audio's author (the EV-validated
   business "BUREL VINCENT Entrepreneur individuel", French registration 423 734 177);
3. opens VB-Audio's own setup program itself through the Windows administrator prompt,
   which names VB-Audio's verified publisher; the user reads its notice and selects
   Install Driver. VB-CABLE's licence does not allow building it into another program's
   installation, so it is never run silently;
4. deletes the extracted files and checks the audio devices again: ready, Windows needs a
   restart to finish, or one side of the cable is disabled in Windows Sound settings.
"""
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from host.audio import select_pair, system_endpoints
from host.power import run_powershell

HOME = 'https://vb-cable.com'
PACKAGE_URL = 'https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip'
PACKAGE_SHA256 = 'b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb'  # 1,318,877 bytes
PACKAGE_LIMIT = 16 * 1024 * 1024
SETUP = 'VBCABLE_Setup_x64.exe'
# The Authenticode signer of VB-Audio's setup programs (an EV code-signing certificate):
# common name and the French company registration number (SIREN) in its SERIALNUMBER field.
SIGNER_NAME = 'BUREL VINCENT Entrepreneur individuel'
SIGNER_REGISTRATION = '423 734 177'
MICROPHONE = 'CABLE Output (VB-Audio Virtual Cable)'
NAME = re.compile(r'[A-Za-z0-9_.-]{1,80}')
EXTENSIONS = ('.exe', '.inf', '.sys', '.cat', '.ico', '.txt')
FOLDER = 'vb-cable-'
ERROR_CANCELLED = 1223  # the administrator prompt was declined
SETUP_TIMEOUT_S = 900
DOWNLOAD_DEADLINE_S = 180
RUNNING_SCRIPT = "@(Get-Process -Name 'VBCABLE_Setup_x64','VBCABLE_Setup' -ErrorAction SilentlyContinue).Count"
DRIVER_SCRIPT = ("$ErrorActionPreference='SilentlyContinue';"
                 "$found=@(Get-PnpDevice -PresentOnly | Where-Object {$_.FriendlyName -like '*VB-Audio Virtual Cable*' "
                 "-and $_.Class -eq 'MEDIA'});if ($found.Count) {'installed'} else {'absent'}")


def quote(text):
    """A PowerShell single-quoted literal (every quote PowerShell accepts is doubled)."""
    for mark in ("'", '‘', '’', '‚', '‛'):
        text = text.replace(mark, mark + mark)
    return "'" + text + "'"


def launch_command(setup):
    """Not elevated itself: opens VB-Audio's setup program through the administrator prompt (the
    runas verb, as Start-Process -Verb RunAs does, keeping Windows' "declined" code 1223) and waits
    until the user closes it. Prints 'closed <exit code>'. No arguments: never a silent install."""
    setup = Path(setup)
    return ('try { $info = New-Object System.Diagnostics.ProcessStartInfo; '
            '$info.FileName = ' + quote(str(setup)) + '; $info.WorkingDirectory = ' + quote(str(setup.parent)) + '; '
            "$info.Verb = 'runas'; $info.UseShellExecute = $true; "
            '$process = [System.Diagnostics.Process]::Start($info); if ($null -eq $process) { exit 3 }; '
            "$process.WaitForExit(); [Console]::Out.WriteLine('closed ' + $process.ExitCode); exit 0 } "
            'catch { $failure = $_.Exception; while ($failure) { '
            'if ($failure -is [System.ComponentModel.Win32Exception] -and $failure.NativeErrorCode -eq 1223) { exit 1223 }; '
            '$failure = $failure.InnerException }; [Console]::Error.WriteLine($_.Exception.Message); exit 1 }')


def signature_script(path):
    """Windows' Authenticode verdict on path, with the signer's subject one field per line."""
    return ("$ErrorActionPreference='Stop';$s=Get-AuthenticodeSignature -LiteralPath " + quote(str(path)) + ";"
            "$c=$s.SignerCertificate;"
            "ConvertTo-Json -Compress -InputObject ([pscustomobject]@{status=[string]$s.Status;"
            "subject=[string]$(if ($c) {$c.Subject});"
            "names=@(if ($c) {$c.SubjectName.Format($true) -split '\\r?\\n' | Where-Object {$_}})})")


def signer_fields(signature):
    """{'CN': [...], 'SERIALNUMBER': [...], ...} from the subject lines of signature_script."""
    fields = {}
    names = signature.get('names') if isinstance(signature, dict) else None
    for line in names if isinstance(names, list) else []:
        key, separator, value = str(line).partition('=')
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace('""', '"')
        fields.setdefault(key.strip().upper(), []).append(value)
    return fields


def signed_by_vb_audio(signature):
    """A valid signature (trusted chain, not revoked) whose signer is exactly VB-Audio's author."""
    if not isinstance(signature, dict) or signature.get('status') != 'Valid':
        return False
    fields = signer_fields(signature)
    return fields.get('CN') == [SIGNER_NAME] and fields.get('SERIALNUMBER') == [SIGNER_REGISTRATION]


def only_expected(folder, files):
    """The folder holds exactly the extracted files: a file added later (a library the setup program
    would load) is refused."""
    folder = Path(folder)
    present = {entry.name for entry in folder.iterdir()}
    if present != set(files) or folder.resolve() != folder.absolute():  # also no link or junction on the way
        raise ValueError('The VB-CABLE folder changed after the download, so it was not used.')


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class HeldFiles:
    """The extracted files, held open with read sharing only while they are checked and used.

    On Windows nothing can then write, replace, rename or delete them, nor rename their
    folder; a link (reparse point) is refused, and the checks read through the held handles,
    so what was checked is what the setup program finds. Elsewhere (tests) plain reads.
    """

    GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING = 0x80000000, 0x00000001, 3
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_SEQUENTIAL_SCAN = 0x00200000, 0x08000000
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_ATTRIBUTE_DIRECTORY = 0x400, 0x10

    def __init__(self, paths):
        self.paths = [Path(path) for path in paths]
        self.handles = {}
        self.kernel32 = None

    def __enter__(self):
        if os.name != 'nt':
            return self
        import ctypes
        from ctypes import wintypes
        kernel32 = self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        class Information(ctypes.Structure):  # BY_HANDLE_FILE_INFORMATION
            _fields_ = [('attributes', wintypes.DWORD), ('times', wintypes.DWORD * 6), ('volume', wintypes.DWORD),
                        ('size', wintypes.DWORD * 2), ('links', wintypes.DWORD), ('index', wintypes.DWORD * 2)]
        kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Information)]
        kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
        invalid = wintypes.HANDLE(-1).value
        try:
            for path in self.paths:
                handle = kernel32.CreateFileW(str(path), self.GENERIC_READ, self.FILE_SHARE_READ, None, self.OPEN_EXISTING,
                                              self.FILE_FLAG_OPEN_REPARSE_POINT | self.FILE_FLAG_SEQUENTIAL_SCAN, None)
                if handle in (None, 0, invalid):
                    raise OSError(ctypes.get_last_error(), 'The VB-CABLE file ' + path.name + ' is in use')
                self.handles[path] = handle
                information = Information()
                if (not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information))
                        or information.attributes & (self.FILE_ATTRIBUTE_REPARSE_POINT | self.FILE_ATTRIBUTE_DIRECTORY)
                        or information.links != 1):
                    raise OSError('The VB-CABLE file ' + path.name + ' is not a plain file')
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def sha256(self, path):
        path = Path(path)
        handle = self.handles.get(path)
        if handle is None:
            return file_sha256(path)
        import ctypes
        from ctypes import wintypes
        digest = hashlib.sha256()
        if not self.kernel32.SetFilePointerEx(handle, 0, None, 0):
            raise OSError(ctypes.get_last_error(), 'The VB-CABLE file ' + path.name + ' could not be read')
        buffer = ctypes.create_string_buffer(1 << 20)
        read = wintypes.DWORD()
        while True:
            if not self.kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
                raise OSError(ctypes.get_last_error(), 'The VB-CABLE file ' + path.name + ' could not be read')
            if not read.value:
                return digest.hexdigest()
            digest.update(buffer.raw[:read.value])

    def __exit__(self, *_):
        for handle in self.handles.values():
            self.kernel32.CloseHandle(handle)
        self.handles = {}
        return False


class MicrophoneSetup:
    def __init__(self, helper, directory, log=print, version='', opener=None, runner=None, launcher=None,
                 endpoints=None):
        self.helper, self.directory, self.log, self.version = Path(helper), Path(directory), log, version
        self.opener = opener or urllib.request.urlopen
        self.runner = runner
        self.launcher = launcher or self.launch
        self.endpoints = endpoints or (lambda: system_endpoints(self.helper)[0])
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._value = {'state': 'unknown', 'message': "Checking this PC's audio devices…", 'home': HOME}

    def status(self):
        with self._lock:
            return dict(self._value, busy=self._busy.locked())

    def _set(self, state, message, from_setup=True, **extra):
        """Record the state. A check from the page or at startup (from_setup False) never replaces
        the progress of a running setup."""
        with self._lock:
            if not from_setup and self._busy.locked():
                changed = False
            else:
                changed = (self._value.get('state'), self._value.get('message')) != (state, message)
                self._value = dict({'state': state, 'message': message, 'home': HOME}, **extra)
        if changed and state not in ('unknown', 'downloading'):
            self.log('Headset microphone setup: ' + message)
        return self.status()

    # ------------------------------------------------------------------ checks
    def driver_installed(self):
        try:
            result = run_powershell(DRIVER_SCRIPT, self.runner, 30)
        except Exception:
            return False
        return result.returncode == 0 and (result.stdout or '').strip().endswith('installed')

    def check(self, from_setup=False):
        """Read-only: is a virtual microphone ready (any supported cable), installed awaiting a restart, or missing?"""
        try:
            available = self.endpoints()
        except Exception as error:
            return self._set('unknown', "Could not list this PC's audio devices: " + str(error)[:200], from_setup)
        pair = select_pair({'variant': 'Automatic'}, available)
        if pair:
            return self._set('ready', 'Ready: apps on this PC can use “' + pair[1] + '” as the microphone.', from_setup,
                             sink=pair[0], source=pair[1])
        half = ([x for x in available.get('render', []) if 'CABLE Input' in x][:1] +
                [x for x in available.get('capture', []) if 'CABLE Output' in x][:1])
        if len(half) == 1:
            return self._set('disabled', 'VB-CABLE is installed, but Windows lists only “' + half[0][:80] + '”. Enable both '
                             'CABLE Input (Playback) and CABLE Output (Recording) in Windows Sound settings, then select '
                             'Check again.', from_setup)
        if self.driver_installed():
            return self._set('restart', 'VB-CABLE is installed. Restart Windows to finish, then choose “' + MICROPHONE +
                             '” as the microphone in your apps.', from_setup)
        return self._set('missing', 'Not set up: Windows has no virtual microphone for the headset yet.', from_setup)

    # ------------------------------------------------------------------ setup
    def start(self):
        """Begin the setup in the background; status() reports progress and the outcome."""
        if not self._busy.acquire(blocking=False):
            raise ValueError('The headset microphone setup is already running.')
        state = self.status()['state']
        refusal = {'ready': 'The headset microphone is already set up.',
                   'restart': 'VB-CABLE is installed: restart Windows to finish.',
                   'disabled': 'VB-CABLE is installed: enable CABLE Input and CABLE Output in Windows Sound settings.'}.get(state)
        if not refusal and self.setup_running():
            refusal = "VB-CABLE's setup is already open: finish or close it, then select Check again."
        if refusal:
            self._busy.release()
            raise ValueError(refusal)
        threading.Thread(target=self._run, daemon=True, name='VROSTBridge-microphone-setup').start()

    def setup_running(self):
        """Whether a VB-CABLE setup window is open (from an earlier attempt that was not closed)."""
        try:
            result = run_powershell(RUNNING_SCRIPT, self.runner, 20)
            return result.returncode == 0 and int((result.stdout or '0').strip() or '0') > 0
        except Exception:
            return False

    def _run(self):
        folder, keep = None, False
        try:
            self._set('downloading', 'Downloading VB-CABLE from VB-Audio (vb-audio.com)…')
            data = self.download()
            folder, files = self.unpack(data)
            with HeldFiles([folder / name for name in files]) as held:
                self.verify(folder, files, data, held)
                self._set('installing', "VB-CABLE's setup is opening: allow it in the Windows prompt (verified "
                                        'publisher: ' + SIGNER_NAME + '), select Install Driver, then close the '
                                        'setup window.')
                only_expected(folder, files)  # nothing added to the folder since it was checked
                result = self.launcher(folder / SETUP)
            if result.get('still_open'):
                keep = True  # the setup may still read its files; the next attempt removes the folder
                self._set('error', str(result.get('message')))
                return
            if result.get('declined'):
                self._set('missing', 'Administrator permission was not given, so nothing was installed.')
                return
            if not result.get('ok'):
                self._set('error', str(result.get('message') or 'The VB-CABLE setup could not be opened.'))
                return
            if self.check(from_setup=True)['state'] == 'missing':
                self._set('missing', "VB-CABLE's setup closed without installing the driver. Select Set up headset "
                                     'microphone to try again.')
        except Exception as error:  # every failure ends in a readable state, never a stuck "busy"
            self._set('error', str(error)[:300] or type(error).__name__)
        finally:
            if folder is not None and not keep:
                shutil.rmtree(folder, ignore_errors=True)
            self._busy.release()

    def launch(self, setup):
        """Open VB-Audio's setup program through the administrator prompt; returns when it is closed."""
        try:
            result = run_powershell(launch_command(setup), self.runner, SETUP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {'ok': False, 'declined': False, 'still_open': True,
                    'message': 'The VB-CABLE setup stayed open for 15 minutes. Close it, then select Check again.'}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return {'ok': False, 'declined': False, 'message': 'The VB-CABLE setup could not be opened: ' + str(error)[:200]}
        if result.returncode == ERROR_CANCELLED:
            return {'ok': False, 'declined': True, 'message': 'Administrator permission was not given.'}
        if result.returncode == 0:
            return {'ok': True, 'declined': False, 'message': (result.stdout or '').strip()[:80]}
        detail = ((result.stderr or '').strip().splitlines() or ['exit code ' + str(result.returncode)])[-1]
        return {'ok': False, 'declined': False, 'message': 'The VB-CABLE setup could not be opened: ' + detail[:200]}

    def download(self):
        request = urllib.request.Request(PACKAGE_URL, headers={'User-Agent': 'VROSTBridge-Host/' + self.version})
        deadline = time.monotonic() + DOWNLOAD_DEADLINE_S
        try:
            with self.opener(request, timeout=60) as response:
                chunks, size = [], 0
                while size <= PACKAGE_LIMIT:
                    if time.monotonic() > deadline:
                        raise ValueError('VB-CABLE took more than ' + str(DOWNLOAD_DEADLINE_S // 60) + ' minutes to download. '
                                         'Check the internet connection, or get VB-CABLE from ' + HOME + '.')
                    chunk = response.read(min(1 << 16, PACKAGE_LIMIT + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                data = b''.join(chunks)
        except OSError as error:
            raise ValueError('VB-CABLE could not be downloaded from VB-Audio (' + str(error)[:160] + '). Check the '
                             'internet connection, or get VB-CABLE from ' + HOME + '.') from error
        if len(data) > PACKAGE_LIMIT:
            raise ValueError('The VB-CABLE download is larger than expected, so it was not used.')
        return data

    def unpack(self, data):
        """Extract the package to a new folder of its own: (folder, {file name: SHA-256 of the downloaded bytes})."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as error:
            raise ValueError('The VB-CABLE download is not a valid package, so it was not used.') from error
        members = archive.infolist()
        if len(members) > 64 or sum(item.file_size for item in members) > 4 * PACKAGE_LIMIT:
            raise ValueError('The VB-CABLE package is not in the expected form, so it was not used.')
        names = set()
        for item in members:
            if (item.is_dir() or not NAME.fullmatch(item.filename) or not item.filename.lower().endswith(EXTENSIONS)
                    or item.filename.lower() in names):
                raise ValueError('The VB-CABLE package has an unexpected file (' + item.filename[:80] + '), so it was not used.')
            names.add(item.filename.lower())
        if SETUP not in archive.namelist():
            raise ValueError('The VB-CABLE package has no ' + SETUP + ', so it was not used.')
        self.directory.mkdir(parents=True, exist_ok=True)
        for old in self.directory.glob(FOLDER + '*'):  # left by an interrupted setup
            shutil.rmtree(old, ignore_errors=True)
        folder = self.directory / (FOLDER + secrets.token_hex(8))
        folder.mkdir()
        files = {}
        try:
            for item in members:
                content = archive.read(item)
                with open(folder / item.filename, 'xb') as handle:
                    handle.write(content)
                files[item.filename] = hashlib.sha256(content).hexdigest()
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return folder, files

    def verify(self, folder, files, data, held=None):
        """Run while the files are held unchanged: they are the downloaded ones, and the package is
        the pinned release or a newer one whose setup program VB-Audio's author signed."""
        only_expected(folder, files)
        for name, digest in files.items():
            if (held.sha256(folder / name) if held else file_sha256(folder / name)) != digest:
                raise ValueError('The VB-CABLE files changed after the download, so they were not used.')
        if hashlib.sha256(data).hexdigest() == PACKAGE_SHA256:
            return 'pinned'
        # A newer VB-CABLE release than this VROSTBridge knows: accept it only when Windows confirms
        # that its setup program carries a valid signature by VB-Audio's author.
        if not signed_by_vb_audio(self.signature(folder / SETUP)):
            raise ValueError('The downloaded VB-CABLE package could not be verified, so it was not used. Get '
                             'VB-CABLE from ' + HOME + ' instead.')
        self.log('Headset microphone setup: VB-Audio published a newer VB-CABLE package; its setup program is '
                 "signed by VB-Audio's author.")
        return 'signed'

    def signature(self, path):
        try:
            result = run_powershell(signature_script(path), self.runner, 60)
            value = json.loads((result.stdout or '').strip() or '{}') if result.returncode == 0 else {}
        except Exception:
            value = {}
        return value if isinstance(value, dict) else {}
