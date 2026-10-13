"""Add VROSTBridge to Steam, with its artwork, in one step (RC4.2).

Steam keeps non-Steam games in userdata/<account>/config/shortcuts.vdf (binary
KeyValues) and their artwork in userdata/<account>/config/grid/. A running Steam
rewrites shortcuts.vdf when it exits, so VROSTBridge closes Steam (`steam -shutdown`),
edits the file and starts Steam again (`steam -silent`). That is refused inside Game
Mode, where Steam is the session itself; add or remove from Desktop Mode.

Safety:
- the file is parsed and re-serialised; unless that reproduces the original bytes
  exactly, nothing is changed (an unknown format is never rewritten);
- a backup (shortcuts.vdf.vrostbridge-backup) is kept before the first change and the
  new file replaces the old one atomically;
- only entries that run VROSTBridge-Client are replaced or removed; other shortcuts
  keep their order, fields and values;
- artwork is written under the shortcut's own id, and removal deletes only files whose
  contents are still the ones VROSTBridge wrote (a receipt records their hashes).

The shortcut is called "VROSTBridge", runs `VROSTBridge-Client --role client --game-mode`
from the installed `current` folder (so it survives updates), and carries a portrait
cover, a wide capsule, a hero background, a title logo and an icon.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

APP_NAME = 'VROSTBridge'
LAUNCH_OPTIONS = '--role client --game-mode'
MAP, STRING, INT32, FLOAT32, POINTER, WSTRING, COLOR, UINT64, END, INT64 = 0, 1, 2, 3, 4, 5, 6, 7, 8, 10
STEAMID64_BASE = 76561197960265728
# Logo placement on the hero (Steam's own per-game layout file).
LOGO_POSITION = {'nVersion': 1, 'logoPosition': {'pinnedPosition': 'BottomLeft', 'nWidthPct': 42, 'nHeightPct': 62}}


# ---------------------------------------------------------------- binary KeyValues
class FormatError(ValueError):
    pass


def _cstring(data, pos):
    end = data.find(b'\x00', pos)
    if end < 0:
        raise FormatError('unterminated string')
    return data[pos:end], end + 1


def parse(data, pos=0, depth=0):
    """[(type, key bytes, value)] up to the END marker; maps hold nested lists. Returns (nodes, position)."""
    if depth > 16:
        raise FormatError('nested too deeply')
    nodes = []
    while True:
        if pos >= len(data):
            raise FormatError('missing end marker')
        kind = data[pos]
        pos += 1
        if kind == END:
            return nodes, pos
        key, pos = _cstring(data, pos)
        if kind == MAP:
            value, pos = parse(data, pos, depth + 1)
        elif kind == STRING:
            value, pos = _cstring(data, pos)
        elif kind in (INT32, POINTER, COLOR):
            if pos + 4 > len(data):
                raise FormatError('truncated number')
            value = struct.unpack_from('<i', data, pos)[0]
            pos += 4
        elif kind == FLOAT32:
            if pos + 4 > len(data):
                raise FormatError('truncated number')
            value = data[pos:pos + 4]  # kept as bytes: re-serialised exactly
            pos += 4
        elif kind in (UINT64, INT64):
            if pos + 8 > len(data):
                raise FormatError('truncated number')
            value = data[pos:pos + 8]
            pos += 8
        else:
            raise FormatError('unsupported value type ' + str(kind))
        nodes.append((kind, key, value))


def serialise(nodes):
    out = bytearray()
    for kind, key, value in nodes:
        out.append(kind)
        out += key + b'\x00'
        if kind == MAP:
            out += serialise(value)
        elif kind == STRING:
            out += value + b'\x00'
        elif kind in (INT32, POINTER, COLOR):
            out += struct.pack('<i', value)
        else:
            out += value
    out.append(END)
    return bytes(out)


def load_shortcuts(data):
    """The list of shortcut maps in a shortcuts.vdf, or FormatError."""
    if not data:
        return []
    nodes, end = parse(data)
    if end != len(data) or len(nodes) != 1 or nodes[0][0] != MAP or nodes[0][1].lower() != b'shortcuts':
        raise FormatError('not a shortcuts file')
    if serialise(nodes) != data:
        raise FormatError('the file would not be preserved exactly')
    for kind, _, _ in nodes[0][2]:
        if kind != MAP:
            raise FormatError('unexpected shortcut entry')
    return [value for _, _, value in nodes[0][2]]


def dump_shortcuts(entries):
    return serialise([(MAP, b'shortcuts', [(MAP, str(index).encode(), fields) for index, fields in enumerate(entries)])])


def field(fields, name):
    lowered = name.lower().encode()
    return next((value for _, key, value in fields if key.lower() == lowered), None)


def shortcut_appid(exe, name=APP_NAME):
    """Steam's id for a non-Steam shortcut (unsigned; also the artwork file prefix)."""
    return (zlib.crc32((exe + name).encode('utf-8')) & 0xFFFFFFFF) | 0x80000000


def ours(fields):
    """A VROSTBridge Game Mode shortcut: this one, or one added by hand that runs the client for Game Mode or
    a browserless session."""
    exe = field(fields, 'Exe') or b''
    name = (field(fields, 'AppName') or b'').decode('utf-8', 'replace').strip().lower()
    options = field(fields, 'LaunchOptions') or b''
    return b'VROSTBridge-Client' in exe and (name == APP_NAME.lower() or b'--game-mode' in options or b'--play' in options)


def new_entry(exe, start_dir, icon):
    quoted = '"' + exe + '"'
    appid = shortcut_appid(quoted)
    signed = appid - (1 << 32) if appid >= (1 << 31) else appid
    fields = [(INT32, b'appid', signed), (STRING, b'AppName', APP_NAME.encode()), (STRING, b'Exe', quoted.encode()),
              (STRING, b'StartDir', ('"' + start_dir + '"').encode()), (STRING, b'icon', icon.encode()),
              (STRING, b'ShortcutPath', b''), (STRING, b'LaunchOptions', LAUNCH_OPTIONS.encode()),
              (INT32, b'IsHidden', 0), (INT32, b'AllowDesktopConfig', 1), (INT32, b'AllowOverlay', 1),
              (INT32, b'OpenVR', 0), (INT32, b'Devkit', 0), (STRING, b'DevkitGameID', b''),
              (INT32, b'DevkitOverrideAppID', 0), (INT32, b'LastPlayTime', 0), (STRING, b'FlatpakAppID', b''),
              (MAP, b'tags', [])]
    return fields, appid


# ---------------------------------------------------------------- Steam installation
def steam_roots(home=None):
    home = Path(home or Path.home())
    candidates = [home / '.local/share/Steam', home / '.steam/steam', home / '.steam/root',
                  home / '.var/app/com.valvesoftware.Steam/.local/share/Steam']
    roots = []
    for path in candidates:
        try:
            real = path.resolve()
        except OSError:
            continue
        if (real / 'userdata').is_dir() and real not in roots:
            roots.append(real)
    return roots


def _most_recent_account(root):
    try:
        text = (root / 'config/loginusers.vdf').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    for match in re.finditer(r'"(\d{17})"\s*\{([^{}]*)\}', text):
        if re.search(r'"MostRecent"\s*"1"', match.group(2), re.I):
            account = int(match.group(1)) - STEAMID64_BASE
            return str(account) if account > 0 else None
    return None


def user_configs(root):
    """config folders of the Steam accounts to change: the most recent one, or all."""
    base = root / 'userdata'
    recent = _most_recent_account(root)
    if recent and (base / recent / 'config').is_dir():
        return [base / recent / 'config']
    try:
        return sorted(path / 'config' for path in base.iterdir()
                      if path.name.isdigit() and path.name != '0' and (path / 'config').is_dir())
    except OSError:
        return []


def steam_processes(proc='/proc'):
    pids = []
    try:
        entries = list(Path(proc).iterdir())
    except OSError:
        return pids
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if (entry / 'comm').read_text().strip() == 'steam' and entry.stat().st_uid == os.getuid():
                pids.append(int(entry.name))
        except OSError:
            continue
    return pids


def default_environment():
    """Steam's environment without the frozen companion's private library path."""
    from launcher.client_runtime import steam_environment
    return steam_environment()


def _sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ''


def _atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.vrostbridge-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class SteamShortcut:
    def __init__(self, root, executable, artwork, receipt, log=print, home=None, steam=None,
                 processes=steam_processes, runner=subprocess.run, spawn=subprocess.Popen, in_game_mode=None,
                 clock=time.monotonic, sleep=time.sleep, environment=default_environment):
        """root: installed package folder; executable: the VROSTBridge-Client to run (installed `current` path);
        artwork: folder with capsule.jpg, header.jpg, hero.jpg, logo.png and icon.png; receipt: JSON path."""
        self.root, self.executable, self.artwork = Path(root), Path(executable), Path(artwork)
        self.receipt = Path(receipt)
        self.log, self.home = log, home
        self.steam = steam
        self.processes, self.runner, self.spawn = processes, runner, spawn
        self.in_game_mode = in_game_mode or (lambda: False)
        self.clock, self.sleep = clock, sleep
        # Steam must not inherit PyInstaller's LD_LIBRARY_PATH: its bundled libraries
        # would replace the system ones Steam and its runtime expect.
        self.environment = environment
        self.lock = threading.Lock()
        self._cache = (None, None)

    # ------------------------------------------------------------ status
    def _configs(self):
        return [config for root in steam_roots(self.home) for config in user_configs(root)]

    def status(self):
        configs = self._configs()
        if not configs:
            return {'available': False, 'added': False, 'in_game_mode': self.in_game_mode(),
                    'message': 'Steam was not found for this user. Start Steam once, then add VROSTBridge.'}
        def modified(path):
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0
        stamp = tuple((str(config), modified(config / 'shortcuts.vdf')) for config in configs)
        if self._cache[0] != stamp:
            added = False
            for config in configs:
                try:
                    entries = load_shortcuts((config / 'shortcuts.vdf').read_bytes()) if (config / 'shortcuts.vdf').exists() else []
                except (OSError, FormatError):
                    continue
                added = added or any(ours(entry) and (field(entry, 'AppName') or b'') == APP_NAME.encode() for entry in entries)
            self._cache = (stamp, added)
        added = self._cache[1]
        game = self.in_game_mode()
        message = ('VROSTBridge is in your Steam library with its artwork. Open it from Game Mode.' if added else
                   'Add VROSTBridge to Steam to start sessions from Game Mode with a controller.')
        if game:
            message += ' Changes need Desktop Mode, because Steam restarts to apply them.'
        return {'available': True, 'added': bool(added), 'in_game_mode': game, 'message': message,
                'steam_running': bool(self.processes())}

    # ------------------------------------------------------------ Steam control
    def _steam_command(self):
        if self.steam:
            return list(self.steam)
        for path in ('/usr/bin/steam', '/usr/games/steam'):
            if Path(path).is_file():
                return [path]
        found = shutil.which('steam')
        if found:
            return [found]
        if shutil.which('flatpak'):
            return [shutil.which('flatpak'), 'run', 'com.valvesoftware.Steam']
        return None

    def _close_steam(self):
        if not self.processes():
            return False
        command = self._steam_command()
        if not command:
            raise ValueError('Steam is running but its launcher was not found; close Steam and try again.')
        self.log('STEAM closing Steam to update its shortcuts (it starts again afterwards).')
        try:
            self.runner(command + ['-shutdown'], timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env=self.environment())
        except (OSError, subprocess.SubprocessError):
            pass
        deadline = self.clock() + 40
        while self.processes():
            if self.clock() > deadline:
                raise ValueError('Steam did not close within 40 seconds. Close Steam yourself, then try again.')
            self.sleep(.5)
        self.sleep(1)  # let Steam finish writing its files
        return True

    def _start_steam(self):
        command = self._steam_command()
        if not command:
            return
        try:
            self.spawn(command + ['-silent'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True, env=self.environment())
            self.log('STEAM started again.')
        except OSError as error:
            self.log('STEAM could not be started again (' + str(error)[:120] + '); open it yourself.')

    # ------------------------------------------------------------ receipt
    def _read_receipt(self):
        try:
            value = json.loads(self.receipt.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_receipt(self, value):
        _atomic(self.receipt, json.dumps(value, indent=1).encode(), 0o600)

    # ------------------------------------------------------------ changes
    def _check_allowed(self):
        if self.in_game_mode():
            raise ValueError('Switch to Desktop Mode to add or remove VROSTBridge: Steam restarts to apply it.')
        if not self.executable.is_file():
            raise ValueError('Install the VROSTBridge package before adding it to Steam.')
        missing = [name for name in ('capsule.jpg', 'header.jpg', 'hero.jpg', 'logo.png', 'icon.png')
                   if not (self.artwork / name).is_file()]
        if missing:
            raise ValueError('VROSTBridge artwork is missing (' + ', '.join(missing) + '). Repair the installation.')

    def _prepare(self, configs):
        """Parse every shortcuts file first, so nothing is changed unless all are understood."""
        parsed = {}
        for config in configs:
            path = config / 'shortcuts.vdf'
            data = path.read_bytes() if path.exists() else b''
            try:
                parsed[config] = (data, load_shortcuts(data))
            except FormatError as error:
                raise ValueError('Steam\'s shortcut list in ' + str(path) + ' has a format VROSTBridge does not '
                                 'recognise (' + str(error) + '); it was left unchanged. Add VROSTBridge '
                                 'from Steam instead: Games > Add a Non-Steam Game.') from None
        return parsed

    def add(self):
        with self.lock:
            self._check_allowed()
            configs = self._configs()
            if not configs:
                raise ValueError('Steam was not found for this user. Start Steam and sign in once, then try again.')
            self._prepare(configs)  # refuse before closing Steam if a file is not understood
            restart = self._close_steam()
            try:
                parsed = self._prepare(configs)  # again: Steam rewrites the file as it exits
                previous = self._read_receipt()
                receipt = {'version': 1, 'users': []}
                exe = str(self.executable)
                for config, (data, entries) in parsed.items():
                    fields, appid = new_entry(exe, str(self.executable.parent), str(self.artwork / 'icon.png'))
                    kept = [entry for entry in entries if not ours(entry)]
                    replaced = len(entries) - len(kept)
                    path = config / 'shortcuts.vdf'
                    backup = config / 'shortcuts.vdf.vrostbridge-backup'
                    if data and not backup.exists():
                        _atomic(backup, data)
                    _atomic(path, dump_shortcuts(kept + [fields]))
                    written = self._artwork(config / 'grid', appid)
                    receipt['users'].append({'config': str(config), 'appid': appid, 'files': written})
                    self.log('STEAM added VROSTBridge to Steam (' + str(config.parent.name) + ')' +
                             (' replacing ' + str(replaced) + ' older VROSTBridge shortcut(s)' if replaced else '') + '.')
                # Artwork an earlier entry had under another id (a program in another folder gives another id)
                # goes, if it is still exactly what was written.
                current = {name for user in receipt['users'] for name in user['files']}
                for user in previous.get('users', []):
                    for name, digest in (user.get('files') or {}).items():
                        path = Path(name)
                        if name not in current and path.is_file() and _sha256(path) == digest:
                            path.unlink()
                self._write_receipt(receipt)
                self._cache = (None, None)
            finally:
                if restart:
                    self._start_steam()
            return self.status()

    def _artwork_files(self, appid):
        """The library images for this entry: file name in Steam's grid folder -> its content."""
        sources = {'%dp.jpg' % appid: 'capsule.jpg', '%d.jpg' % appid: 'header.jpg', '%d_hero.jpg' % appid: 'hero.jpg',
                   '%d_logo.png' % appid: 'logo.png'}
        files = {name: (self.artwork / source).read_bytes() for name, source in sources.items()}
        files['%d.json' % appid] = json.dumps(LOGO_POSITION).encode()
        return files

    def _artwork(self, grid, appid):
        files = {}
        for name, data in self._artwork_files(appid).items():
            _atomic(grid / name, data)
            files[str(grid / name)] = hashlib.sha256(data).hexdigest()
        return files

    def refresh_artwork(self):
        """After an update whose artwork changed, VROSTBridge's own library images are brought up to date: only
        for an entry that is still in Steam, and only images that are still exactly what Add to Steam wrote (a
        picture the user chose instead stays). No restart; Steam shows them after it next starts. Returns how many
        images were replaced."""
        replaced = 0
        try:
            with self.lock:
                if not self.status().get('added'):
                    return 0
                receipt = self._read_receipt()
                for user in receipt.get('users', []):
                    files, appid = user.get('files') or {}, user.get('appid')
                    if not isinstance(appid, int) or not isinstance(files, dict):
                        continue
                    wanted = self._artwork_files(appid)
                    for name, digest in list(files.items()):
                        path = Path(name)
                        data = wanted.get(path.name)
                        if data is None or not path.is_file():
                            continue
                        fresh = hashlib.sha256(data).hexdigest()
                        if fresh != digest and _sha256(path) == digest:
                            _atomic(path, data)
                            files[name] = fresh
                            replaced += 1
                if replaced:
                    self._write_receipt(receipt)
                    self.log('STEAM updated ' + str(replaced) + ' VROSTBridge library image(s) to this version\'s artwork; '
                             'Steam shows them after it next starts.')
        except (OSError, ValueError) as error:
            self.log('STEAM could not update the library artwork: ' + str(error))
        return replaced

    def remove(self):
        with self.lock:
            if self.in_game_mode():
                raise ValueError('Switch to Desktop Mode to add or remove VROSTBridge: Steam restarts to apply it.')
            configs = self._configs()
            self._prepare(configs)
            restart = self._close_steam() if configs else False
            removed = 0
            try:
                parsed = self._prepare(configs)
                for config, (data, entries) in parsed.items():
                    kept = [entry for entry in entries if not ours(entry)]
                    if len(kept) != len(entries):
                        removed += len(entries) - len(kept)
                        _atomic(config / 'shortcuts.vdf', dump_shortcuts(kept))
                for user in self._read_receipt().get('users', []):
                    for name, digest in (user.get('files') or {}).items():
                        path = Path(name)
                        if path.is_file() and _sha256(path) == digest:
                            path.unlink()
                self.receipt.unlink(missing_ok=True)
                self._cache = (None, None)
            finally:
                if restart:
                    self._start_steam()
            self.log('STEAM removed VROSTBridge from Steam (' + str(removed) + ' shortcut(s)).')
            return self.status()
