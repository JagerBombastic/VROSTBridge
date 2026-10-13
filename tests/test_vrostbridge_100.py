"""1.0.0: the VROSTBridge name and artwork where people see them.

Checks the artwork (vector masters from the supplied pictures, pixel-fitted small icons), the pages and Game
Mode, the tray icon choice, report names, the Steam library entry and its artwork, and what the Windows and
Bazzite installers show and write.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))

from launcher import reporting, steam_shortcut  # noqa: E402
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402
from launcher.windows_tray import light_taskbar, tray_icon  # noqa: E402

BRAND = ROOT / 'launcher' / 'brand'
# A product-style name ending in "Bridge" other than VROSTBridge: one word in any case, or two capitalised words.
OTHER_BRIDGE = re.compile(r'\b(?!(?i:vrostbridge)\b)[A-Za-z]+(?:Bridge|bridge|BRIDGE)\b|\b[A-Z][a-z]+[ _-]Bridge\b')


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def ico_sizes(path):
    """Square sizes in an .ico file (its directory entries; 0 means 256)."""
    data = Path(path).read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', data, 0)
    assert reserved == 0 and kind == 1, path
    sizes = set()
    for index in range(count):
        width, height = data[6 + 16 * index], data[7 + 16 * index]
        assert width == height, path
        sizes.add(width or 256)
    return sizes


def fake_launcher(path, resource=True):
    header = bytearray(0x40)
    header[:2] = b'MZ'
    header[0x3c:0x40] = (0x40).to_bytes(4, 'little')
    text = 'VROSTBridge Host'.encode('utf-16-le') if resource else b''
    path.write_bytes(bytes(header) + b'PE\0\0' + (0x8664).to_bytes(2, 'little') + text)
    return path


class ArtworkTests(unittest.TestCase):
    def test_only_vrostbridge_artwork(self):
        for path in BRAND.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                relative = path.relative_to(BRAND)
                self.assertTrue(relative.parts[0] in ('hicolor', 'steam') or relative.name.startswith(('vrostbridge', 'favicon')),
                                str(relative))

    def test_every_file_the_programs_use_is_there(self):
        from launcher.app import BRAND_FILES
        for name in BRAND_FILES:
            self.assertTrue((BRAND / name).is_file(), name)
        for name in ('capsule.jpg', 'header.jpg', 'hero.jpg', 'logo.png', 'icon.png'):
            self.assertTrue((BRAND / 'steam' / name).is_file(), name)
        for size in (16, 22, 24, 32, 48, 64, 96, 128, 192, 256, 512):
            self.assertTrue((BRAND / 'hicolor' / ('%dx%d' % (size, size)) / 'apps' / 'vrostbridge.png').is_file(), size)
        self.assertTrue((BRAND / 'hicolor/scalable/apps/vrostbridge.svg').is_file())
        self.assertEqual((ROOT / 'client/assets/hud/logo-192.rgba').stat().st_size, 192 * 192 * 4)
        self.assertTrue((ROOT / 'launcher/game_ui_assets/background.jpg').is_file())

    def test_icon_sizes(self):
        self.assertEqual(ico_sizes(BRAND / 'vrostbridge.ico'), {16, 20, 24, 32, 40, 48, 64, 96, 128, 256})
        self.assertTrue({16, 32, 48} <= ico_sizes(BRAND / 'favicon.ico'))
        for name in ('vrostbridge-tray-light.ico', 'vrostbridge-tray-dark.ico'):
            self.assertTrue({16, 20, 24, 32} <= ico_sizes(BRAND / name), name)

    def test_the_supplied_logo_is_kept_and_the_masters_are_generated_from_it(self):
        # The maintainer's pictures, unchanged (2026-10-05).
        for name, digest in (('vrostbridge-icon.png', '317112884c0cc535835a72648d872afc11000c46fe22aa5a1274f9717bafb162'),
                             ('vrostbridge-logo.png', 'c82fa83b11b1ebbd4301ca3acb72aebc064c7607acb0675f4635dcd2e4412ea2')):
            self.assertEqual(hashlib.sha256((ROOT / 'brand/source' / name).read_bytes()).hexdigest(), digest, name)
        # The committed vector masters are exactly what brand/tools/vrostbridge_symbol.py makes, and the app's
        # copies are those masters.
        sys.path.insert(0, str(ROOT / 'brand' / 'tools'))
        import vrostbridge_symbol as symbol
        masters = ROOT / 'brand/assets/svg'
        self.assertEqual(read('brand/assets/svg/vrostbridge-mark.svg'), symbol.svg('colour'))
        self.assertEqual(read('brand/assets/svg/vrostbridge-mark-white.svg'), symbol.svg('white'))
        self.assertEqual(read('brand/assets/svg/vrostbridge-mark-black.svg'), symbol.svg('black'))
        self.assertEqual(read('brand/assets/svg/vrostbridge-logo.svg'), symbol.logo('dark'))
        self.assertEqual(read('brand/assets/svg/vrostbridge-logo-light.svg'), symbol.logo('light'))
        for ours, master in (('vrostbridge-mark.svg', 'vrostbridge-mark.svg'), ('vrostbridge-logo.svg', 'vrostbridge-logo.svg'),
                             ('vrostbridge.svg', 'vrostbridge-app.svg'), ('hicolor/scalable/apps/vrostbridge.svg', 'vrostbridge-app.svg')):
            self.assertEqual((BRAND / ours).read_bytes(), (masters / master).read_bytes(), ours)
        # The earlier symbol (two links and a rectangle) and the brand pack it came with are gone.
        for old in ('brand/manifest.json', 'brand/source/approved-logo.png', 'brand/assets/png', 'brand/assets/windows',
                    'launcher/brand/vrostbridge-compact.svg'):
            self.assertFalse((ROOT / old).exists(), old)
        for path in list(BRAND.rglob('*.svg')) + list(masters.glob('*.svg')):
            self.assertNotRegex(path.read_text(encoding='utf-8'), r'shape-(top|bottom)-link|shape-pc', str(path))

    def test_the_vector_symbol_matches_the_supplied_icon(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is not installed (pip install pillow)')
        import io
        import shutil
        import subprocess
        if not shutil.which('rsvg-convert'):
            self.skipTest('rsvg-convert (librsvg) is not installed')
        sys.path.insert(0, str(ROOT / 'brand' / 'tools'))
        import vrostbridge_symbol as symbol
        svg = symbol.svg('white', box=(0, 0, 1254, 1254), size=(1254, 1254), background='#000000')
        ours = Image.open(io.BytesIO(subprocess.run(['rsvg-convert', '--format', 'png'], input=svg.encode(),
                                                    capture_output=True, check=True).stdout)).convert('L')
        from PIL import ImageChops
        red, green, blue = Image.open(ROOT / 'brand/source/vrostbridge-icon.png').convert('RGB').split()
        theirs = ImageChops.lighter(ImageChops.lighter(red, green), blue)  # its shapes are bright on black
        inside_ours = ours.point(lambda v: 255 if v > 128 else 0).convert('1')
        inside_theirs = theirs.point(lambda v: 255 if v > 128 else 0).convert('1')
        both = ImageChops.logical_and(inside_ours, inside_theirs).convert('L').histogram()[255]
        either = ImageChops.logical_or(inside_ours, inside_theirs).convert('L').histogram()[255]
        self.assertGreater(both / either, 0.99)

    def test_small_icons_are_pixel_fitted(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is not installed (pip install pillow)')
        import io
        sys.path.insert(0, str(ROOT / 'brand' / 'tools'))
        import refine_small_icons as refine

        def entries(path):
            data = Path(path).read_bytes()
            found = {}
            for index in range(struct.unpack_from('<H', data, 4)[0]):
                width, _, _, _, _, _, size, offset = struct.unpack_from('<BBBBHHII', data, 6 + 16 * index)
                found[width or 256] = Image.open(io.BytesIO(data[offset:offset + size])).convert('RGBA')
            return found
        app = entries(BRAND / 'vrostbridge.ico')
        trays = {variant: entries(BRAND / ('vrostbridge-tray-%s.ico' % variant)) for variant in ('light', 'dark')}
        for variant, icons in trays.items():
            self.assertEqual(sorted(icons), [16, 20, 22, 24, 32, 40, 48, 64])
            # One colour only: every visible pixel of the white glyph is white, of the dark glyph dark.
            raw = icons[16].tobytes()
            colours = {tuple(raw[i:i + 3]) for i in range(0, len(raw), 4) if raw[i + 3] > 200}
            self.assertTrue(all(min(c) > 200 for c in colours) if variant == 'light' else all(max(c) < 90 for c in colours), colours)
        # At 16 to 32 px the gap between the cube's side faces is one whole, empty pixel column with full
        # pixels either side, and the dome's base is a pixel edge.
        for size in refine.SIZES:
            v = refine.geometry(size)
            column = int(v['cx'])
            middle = int((v['yT'] + 2 * v['a'] * v['tan'] + v['yT'] + v['a'] * v['tan'] + v['e']) / 2)
            picture = app.get(size) or Image.open(BRAND / 'hicolor' / ('%dx%d' % (size, size)) / 'apps' / 'vrostbridge.png').convert('RGBA')
            for image in (picture, trays['light'][size]):
                alpha = image.getchannel('A')
                self.assertLess(alpha.getpixel((column, middle)), 40, size)
                self.assertGreater(alpha.getpixel((column - 1, middle)), 215, size)
                self.assertGreater(alpha.getpixel((column + 1, middle)), 215, size)
            self.assertEqual(v['yb'], round(v['yb']))
            self.assertEqual(v['g'], 1.0)

    def test_pixel_sizes(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is not installed (pip install pillow)')
        for size in (16, 22, 24, 32, 48, 64, 96, 128, 192, 256, 512):
            with Image.open(BRAND / 'hicolor' / ('%dx%d' % (size, size)) / 'apps' / 'vrostbridge.png') as image:
                self.assertEqual(image.size, (size, size))
                self.assertEqual(image.mode, 'RGBA')
        for name, size in (('vrostbridge-256.png', (256, 256)), ('vrostbridge-512.png', (512, 512)),
                           ('favicon-32.png', (32, 32))):
            with Image.open(BRAND / name) as image:
                self.assertEqual(image.size, size, name)
        with Image.open(ROOT / 'brand/social-preview.png') as image:
            self.assertEqual(image.size, (1280, 640))


class PageTests(unittest.TestCase):
    def setUp(self):
        from launcher.app import server_for

        class App:
            role = 'client'
            token = 'test-token-long-enough-for-the-page'
            lock = threading.RLock()

            def running(self):
                return False

            def status(self):
                return {'role': self.role}

            def action(self, body):
                pass
        self.server = server_for(App())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_the_page_serves_its_artwork_only(self):
        for name, kind in (('vrostbridge-mark.svg', 'image/svg+xml'), ('vrostbridge-logo.svg', 'image/svg+xml'),
                           ('favicon.ico', 'image/x-icon'), ('favicon-32.png', 'image/png'),
                           ('vrostbridge-256.png', 'image/png')):
            with urllib.request.urlopen(self.url + '/brand/' + name) as response:
                self.assertEqual(response.headers['Content-Type'], kind, name)
                self.assertEqual(response.read(), (BRAND / name).read_bytes(), name)
        for other in ('icon.svg', 'logo.png', '../app.py'):
            with self.assertRaises(urllib.error.HTTPError, msg=other):
                urllib.request.urlopen(self.url + '/brand/' + other)

    def test_the_page_names_vrostbridge(self):
        html = read('launcher/desktop.html')
        self.assertIn('<title>VROSTBridge</title>', html)
        self.assertIn('alt="VROSTBridge logo"', html)
        self.assertIn('</span> · An open-source remote PCVR bridge. It connects a SteamVR headset', html)
        self.assertIn('Tested with Valve Index and Bigscreen Beyond 2e', html)
        self.assertIsNone(OTHER_BRIDGE.search(html))

    def test_game_mode_about(self):
        game = read('launcher/game_ui.py')
        self.assertIn("'About VROSTBridge'", game)
        self.assertIn("'  ·  An open-source remote PCVR bridge',", game)
        self.assertIn("b'VROSTBridge'", game)
        self.assertIsNone(OTHER_BRIDGE.search(game))


class TrayTests(unittest.TestCase):
    def test_the_glyph_follows_the_taskbar(self):
        with tempfile.TemporaryDirectory() as folder:
            icons = {}
            for name in ('light', 'dark', 'app'):
                icons[name] = Path(folder) / (name + '.ico')
                icons[name].write_bytes(b'ico')
            self.assertEqual(tray_icon(icons, light=True), icons['dark'], 'dark glyph on a light taskbar')
            self.assertEqual(tray_icon(icons, light=False), icons['light'], 'white glyph on a dark taskbar')
            icons['dark'].unlink()
            self.assertEqual(tray_icon(icons, light=True), icons['app'])
            for path in icons.values():
                path.unlink(missing_ok=True)
            self.assertIsNone(tray_icon(icons, light=False))
            self.assertEqual(tray_icon('single.ico'), 'single.ico')

    def test_the_taskbar_theme_is_read_from_windows(self):
        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def registry(value):
            class Registry:
                HKEY_CURRENT_USER = 1

                @staticmethod
                def OpenKey(root, path):
                    if value is None:
                        raise FileNotFoundError(path)
                    return Key()

                @staticmethod
                def QueryValueEx(key, name):
                    assert name == 'SystemUsesLightTheme'
                    return value, 4
            return Registry
        self.assertTrue(light_taskbar(registry(1)))
        self.assertFalse(light_taskbar(registry(0)))
        self.assertFalse(light_taskbar(registry(None)), 'Windows 10 before 1903: dark')

    def test_the_tray_names_vrostbridge_host(self):
        tray = read('launcher/windows_tray.py')
        self.assertIn('VROSTBridge Host', tray)
        self.assertIn('ImmersiveColorSet', tray)
        app = read('launcher/app.py')
        self.assertIn("'light':logo_resource('vrostbridge-tray-light.ico'),'dark':logo_resource('vrostbridge-tray-dark.ico')", app)


class ReportNameTests(unittest.TestCase):
    def test_report_names(self):
        name = reporting.report_name('client', CLIENT_VERSION, 1790000000)
        self.assertTrue(name.startswith('VROSTBridge-Client-report_' + CLIENT_VERSION + '_'), name)
        self.assertTrue(reporting.report_name('host', HOST_VERSION).startswith('VROSTBridge-Host-report_' + HOST_VERSION + '_'))
        self.assertTrue(reporting.SENT_NAME.fullmatch(name))
        self.assertTrue(reporting.REPORT_NAME.fullmatch(name))
        for other in ('Client-report_1.0.0.1_2026-10-05_09-00-00.json', 'X-Client-report_1.0.0.1_2026-10-05_09-00-00.json'):
            self.assertIsNone(reporting.SENT_NAME.fullmatch(other), other)

    def test_the_host_stores_a_report_under_the_name_it_was_sent_with(self):
        with tempfile.TemporaryDirectory() as folder:
            store = reporting.ReceivedReports(folder)
            sent = 'VROSTBridge-Client-report_1.0.0.1_2026-10-05_09-00-00.json'
            self.assertEqual(store.save(sent, {'role': 'client'}), sent)
            self.assertEqual(store.save(sent, {'role': 'client'}), sent[:-5] + '-1.json', 'never overwritten')
            self.assertEqual({row['name'] for row in store.list()}, {sent, sent[:-5] + '-1.json'})
            self.assertEqual(json.loads(store.read(sent)), {'role': 'client'})
            with self.assertRaises(ValueError):
                store.save('Some-Client-report_1.0.0.1_2026-10-05_09-00-00.json', {})


def vdf_entry(index, name, exe, options=b''):
    return (steam_shortcut.MAP, str(index).encode(), [
        (steam_shortcut.INT32, b'appid', -12345),
        (steam_shortcut.STRING, b'AppName', name),
        (steam_shortcut.STRING, b'Exe', exe),
        (steam_shortcut.STRING, b'StartDir', b'"/games/"'),
        (steam_shortcut.STRING, b'LaunchOptions', options),
    ])


class SteamEntryTests(unittest.TestCase):
    """The library entry: what counts as VROSTBridge's, adding it, and keeping its artwork up to date."""

    def setUp(self):
        import subprocess
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        steam = self.home / '.local/share/Steam'
        self.config = steam / 'userdata/123456/config'
        (self.config / 'grid').mkdir(parents=True)
        (steam / 'config').mkdir()
        (steam / 'config/loginusers.vdf').write_text('"users"\n{\n\t"%d"\n\t{\n\t\t"MostRecent"\t\t"1"\n\t}\n}\n'
                                                      % (steam_shortcut.STEAMID64_BASE + 123456))
        install = self.home / '.local/share/vrostbridge/current'
        install.mkdir(parents=True)
        self.exe = install / 'VROSTBridge-Client'
        self.exe.write_text('#!/bin/sh\n')
        (self.config / 'shortcuts.vdf').write_bytes(steam_shortcut.dump_shortcuts([
            vdf_entry(0, b'Some Game', b'"/games/game"')[2]]))
        self.receipt = self.home / 'steam-shortcut.json'
        self.running = [False]
        self.shortcut = steam_shortcut.SteamShortcut(
            install, self.exe, BRAND / 'steam', self.receipt, log=lambda line: None, home=self.home,
            steam=['/usr/bin/steam'], processes=lambda: [42] if self.running[0] else [],
            runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''),
            spawn=lambda command, **kwargs: None, sleep=lambda seconds: None)

    def names(self):
        entries = steam_shortcut.load_shortcuts((self.config / 'shortcuts.vdf').read_bytes())
        return [steam_shortcut.field(entry, 'AppName') for entry in entries]

    def test_what_counts_as_ours(self):
        quoted = ('"' + str(self.exe) + '"').encode()
        ours = steam_shortcut.ours
        self.assertTrue(ours(vdf_entry(0, b'VROSTBridge', quoted, b'--role client --game-mode')[2]))
        self.assertTrue(ours(vdf_entry(0, b'My headset', quoted, b'--role client --play')[2]), 'added by hand')
        self.assertTrue(ours(vdf_entry(0, b'VROSTBridge', quoted)[2]))
        self.assertFalse(ours(vdf_entry(0, b'Some Game', b'"/games/game"', b'--game-mode')[2]))
        self.assertFalse(ours(vdf_entry(0, b'My page', quoted, b'--role client')[2]), 'the desktop page, not Game Mode')

    def test_add_replaces_an_entry_of_ours_and_its_unchanged_artwork(self):
        # An entry of ours that the user renamed, with artwork VROSTBridge wrote under its id (one picture since
        # replaced in Steam).
        quoted = '"' + str(self.exe) + '"'
        (self.config / 'shortcuts.vdf').write_bytes(steam_shortcut.dump_shortcuts([
            vdf_entry(0, b'Some Game', b'"/games/game"')[2],
            vdf_entry(1, b'Headset', quoted.encode(), b'--role client --game-mode')[2]]))
        earlier = steam_shortcut.shortcut_appid(quoted, 'Headset')
        untouched, replaced = self.config / 'grid' / ('%dp.jpg' % earlier), self.config / 'grid' / ('%d_hero.jpg' % earlier)
        untouched.write_bytes(b'capsule')
        replaced.write_bytes(b'hero')
        self.receipt.write_text(json.dumps({'version': 1, 'users': [{'config': str(self.config), 'appid': earlier, 'files': {
            str(untouched): hashlib.sha256(b'capsule').hexdigest(), str(replaced): hashlib.sha256(b'hero').hexdigest()}}]}))
        replaced.write_bytes(b'my own picture')
        self.assertFalse(self.shortcut.status()['added'], 'only an entry named VROSTBridge counts as added')
        status = self.shortcut.add()
        self.assertTrue(status['added'])
        self.assertNotIn('outdated', status)
        self.assertEqual(self.names(), [b'Some Game', b'VROSTBridge'])
        self.assertFalse(untouched.exists(), 'artwork VROSTBridge wrote for the earlier entry goes')
        self.assertEqual(replaced.read_bytes(), b'my own picture', "the user's own picture stays")
        new = steam_shortcut.shortcut_appid(quoted)
        self.assertTrue((self.config / 'grid' / ('%dp.jpg' % new)).is_file())

    def test_an_update_brings_the_library_artwork_up_to_date(self):
        # Add to Steam with an earlier version's artwork, then this version.
        self.shortcut.add()
        new = steam_shortcut.shortcut_appid('"' + str(self.exe) + '"')
        grid = self.config / 'grid'
        receipt = json.loads(self.receipt.read_text())
        files = receipt['users'][0]['files']
        capsule, hero, layout = str(grid / ('%dp.jpg' % new)), str(grid / ('%d_hero.jpg' % new)), str(grid / ('%d.json' % new))
        for name, old in ((capsule, b'earlier capsule'), (hero, b'earlier hero')):
            Path(name).write_bytes(old)
            files[name] = hashlib.sha256(old).hexdigest()
        self.receipt.write_text(json.dumps(receipt))
        Path(hero).write_bytes(b'a picture the user chose')  # changed in Steam since: stays
        self.assertEqual(self.shortcut.refresh_artwork(), 1)
        self.assertEqual(Path(capsule).read_bytes(), (BRAND / 'steam/capsule.jpg').read_bytes())
        self.assertEqual(Path(hero).read_bytes(), b'a picture the user chose')
        self.assertEqual(json.loads(self.receipt.read_text())['users'][0]['files'][capsule],
                         hashlib.sha256((BRAND / 'steam/capsule.jpg').read_bytes()).hexdigest())
        self.assertEqual(self.shortcut.refresh_artwork(), 0, 'nothing more to do')
        self.assertTrue(Path(layout).is_file())
        # Not in Steam any more: nothing is written.
        Path(capsule).write_bytes(b'earlier capsule')
        receipt = json.loads(self.receipt.read_text())
        receipt['users'][0]['files'][capsule] = hashlib.sha256(b'earlier capsule').hexdigest()
        self.receipt.write_text(json.dumps(receipt))
        entries = steam_shortcut.load_shortcuts((self.config / 'shortcuts.vdf').read_bytes())
        (self.config / 'shortcuts.vdf').write_bytes(steam_shortcut.dump_shortcuts(
            [entry for entry in entries if not steam_shortcut.ours(entry)]))
        self.assertEqual(self.shortcut.refresh_artwork(), 0)
        self.assertEqual(Path(capsule).read_bytes(), b'earlier capsule')

    def test_the_companion_refreshes_the_artwork_at_start(self):
        source = read('launcher/app.py')
        self.assertIn("threading.Thread(target=self.steam_shortcut.refresh_artwork,daemon=True,name='VROSTBridge-steam-artwork').start()", source)

    def test_remove(self):
        self.shortcut.add()
        self.shortcut.remove()
        self.assertEqual(self.names(), [b'Some Game'])
        source = read('launcher/install_maintenance.py')
        self.assertIn("if shortcut.receipt.exists() or shortcut.status().get('added'):shortcut.remove()", source)


class WindowsInstallerTests(unittest.TestCase):
    nsi = read('packaging/windows-installer.nsi')

    def test_the_install_identity(self):
        self.assertIn('InstallDir "$LOCALAPPDATA\\VROSTBridge\\Host\\' + HOST_VERSION + '"', self.nsi)
        self.assertIn('!define APPKEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VROSTBridgeHost"', self.nsi)
        self.assertIn('WriteRegStr HKCU "${RUNKEY}" "VROSTBridgeHost" \'"$INSTDIR\\${LAUNCHER}" --background\'', self.nsi)
        self.assertIn('WriteRegStr HKCU "${APPKEY}" "DisplayName" "VROSTBridge Host"', self.nsi)
        self.assertIn('!define MUI_ICON "${PAYLOAD}/brand/vrostbridge.ico"', self.nsi)
        maintenance = read('launcher/install_maintenance.py')
        self.assertIn(r"UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\VROSTBridgeHost'", maintenance)
        self.assertIn("VALUE='VROSTBridgeHost'", read('launcher/windows_startup.py'))

    def test_the_start_menu_and_sign_in_command(self):
        self.assertIn('!define LAUNCHER "VROSTBridge Host.exe"', self.nsi)
        self.assertIn('!define MENU "$SMPROGRAMS\\VROSTBridge"', self.nsi)
        for name in ('VROSTBridge Host.lnk', 'Open-source credits.lnk', 'Startup logs.lnk', 'Uninstall VROSTBridge Host.lnk'):
            self.assertIn('CreateShortcut "${MENU}\\' + name + '"', self.nsi)
            self.assertIn('Delete "${MENU}\\' + name + '"', self.nsi)
        self.assertIn('RMDir "${MENU}"', self.nsi, 'only an empty folder is removed')
        # The sign-in command of the previous installation is replaced; a foreign one never.
        self.assertIn("${AndIf} $0 == '\"$PreviousInstall\\${LAUNCHER}\" --background'", self.nsi)
        self.assertIn("${If} $0 != '\"$PreviousInstall\\${LAUNCHER}\" --background'", self.nsi)
        self.assertEqual(self.nsi.count('!macro'), 0)

    def test_everything_shown_says_vrostbridge(self):
        shown = re.findall(r'(?:MessageBox \w+(?:\|\w+)? |DetailPrint |_TEXT \$\{\w+\} |TEXT |TITLE |Caption |Name |'
                           r'BrandingText |VIAddVersionKey /LANG=1033 "\w+" |Section )"([^"]*)"', self.nsi)
        self.assertGreater(len(shown), 15)
        for text in shown:
            self.assertIsNone(OTHER_BRIDGE.search(text), text)
        self.assertIsNone(OTHER_BRIDGE.search(self.nsi))
        self.assertEqual(self.nsi.encode('utf-8'), self.nsi.encode('ascii', 'strict'), 'makensis reads ASCII safely')

    def test_the_launcher_resource(self):
        resource = read('packaging/windows-icon.rc')
        self.assertIn('1 ICON "../launcher/brand/vrostbridge.ico"', resource)
        for line in ('VALUE "FileDescription", "VROSTBridge Host"', 'VALUE "ProductName", "VROSTBridge"',
                     'VALUE "OriginalFilename", "VROSTBridge Host.exe"', 'VALUE "FileVersion", "' + HOST_VERSION + '"'):
            self.assertIn(line, resource)
        self.assertNotIn('CompanyName', resource, 'no publisher company is claimed')
        self.assertIn('JagerBombastic', resource)

    def test_the_helpers_and_package_names(self):
        self.assertIn("'host/VROSTBridgeVRHelper.exe'", read('host/control.py'))
        self.assertEqual(read('launcher/app.py').count("ROOT/'host/VROSTBridgeAudioProbe.exe'"), 2)
        self.assertTrue((ROOT / 'host/VROSTBridgeVRHelper.exe').is_file())
        import build_distributions
        self.assertEqual((build_distributions.VR_HELPER, build_distributions.AUDIO_PROBE),
                         ('VROSTBridgeVRHelper.exe', 'VROSTBridgeAudioProbe.exe'))
        self.assertEqual(build_distributions.host_setup_name(), 'VROSTBridge-Host-Setup-' + HOST_VERSION + '.exe')
        self.assertEqual(build_distributions.host_portable_name(), 'VROSTBridge-Host-Portable-' + HOST_VERSION + '.zip')
        self.assertEqual(build_distributions.client_archive_name(), 'VROSTBridge-Bazzite-Installer-' + CLIENT_VERSION + '.zip')

    def test_repack_swaps_the_launcher_and_replaces_the_artwork(self):
        import repack_host_payload as repack
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            previous = root / 'previous'
            (previous / 'dist/host/bin/win64').mkdir(parents=True)
            (previous / 'dist/host/bin/win64/driver_alvr_server.dll').write_bytes(b'driver')
            for name in ('launcher', 'host', 'licenses', 'docs', 'brand'):
                (previous / name).mkdir(exist_ok=True)
            (previous / 'host/VROSTBridgeVRHelper.exe').write_bytes(b'MZ vr helper')
            (previous / 'host/VROSTBridgeAudioProbe.exe').write_bytes(b'MZ audio probe')
            (previous / 'brand/logo.png').write_bytes(b'earlier artwork')
            (previous / 'HOST_PROVENANCE.txt').write_text('seed\n')
            (previous / 'PACKAGED.json').write_text(json.dumps({'version': '1.0.0', 'role': 'host', 'alvr_commit': repack.PIN}))
            repack.manifest(previous)
            with self.assertRaisesRegex(RuntimeError, 'pass --launcher'):
                repack.repack(previous, root / 'refused')
            with self.assertRaisesRegex(RuntimeError, 'version resource'):
                repack.repack(previous, root / 'refused', launcher=fake_launcher(root / 'plain.exe', resource=False))
            output = root / 'out'
            launcher = fake_launcher(root / 'VROSTBridge Host.exe')
            repack.repack(previous, output, launcher=launcher)
            self.assertEqual((output / 'VROSTBridge Host.exe').read_bytes(), launcher.read_bytes())
            self.assertEqual((output / 'host/VROSTBridgeVRHelper.exe').read_bytes(), b'MZ vr helper')
            self.assertEqual((output / 'host/VROSTBridgeAudioProbe.exe').read_bytes(), b'MZ audio probe')
            shipped = sorted(p.relative_to(output / 'brand').as_posix() for p in (output / 'brand').rglob('*') if p.is_file())
            source = sorted(p.relative_to(BRAND).as_posix() for p in BRAND.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
            self.assertEqual(shipped, source, 'brand/ is launcher/brand, with no earlier artwork')
            provenance = (output / 'HOST_PROVENANCE.txt').read_text()
            self.assertIn(hashlib.sha256(launcher.read_bytes()).hexdigest(), provenance)
            self.assertEqual(json.loads((output / 'PACKAGED.json').read_text())['version'], HOST_VERSION)
            repack.verify_input(output)
            # Without --launcher the previous payload's own launcher is kept.
            again = root / 'again'
            repack.repack(output, again)
            self.assertEqual((again / 'VROSTBridge Host.exe').read_bytes(), launcher.read_bytes())


class BazziteInstallerTests(unittest.TestCase):
    script = read('packaging/install-bazzite.sh')

    def test_menu_entries(self):
        for entry, name in (('io.vrostbridge.Client.desktop', 'VROSTBridge Client'),
                            ('io.vrostbridge.Uninstall.desktop', 'Uninstall VROSTBridge Client'),
                            ('io.vrostbridge.GameMode.desktop', 'VROSTBridge Game Mode')):
            block = self.script[self.script.index('"$applications/' + entry + '"'):]
            block = block[:block.index('\nEOF')]
            self.assertIn('\nName=' + name + '\n', block)
            self.assertIn('\nIcon=vrostbridge\n', block)
            self.assertIn('\nX-VROSTBridge-Managed=true\n', block)
        self.assertNotRegex(self.script, r'(?m)^Icon=.*\.(png|svg)$', 'a theme icon name has no extension')
        self.assertIn('base="$data/vrostbridge"', self.script)
        self.assertIsNone(OTHER_BRIDGE.search(self.script))

    def test_the_icons_are_installed_and_removed(self):
        self.assertIn('for icon in "$base/current/brand/hicolor"/*/apps/vrostbridge.*; do', self.script)
        maintenance = read('launcher/install_maintenance.py')
        self.assertIn("(data/'icons/hicolor').glob('*/apps/vrostbridge.*')", maintenance)
        self.assertIn("for name in ('io.vrostbridge.Client.desktop', 'io.vrostbridge.GameMode.desktop', 'io.vrostbridge.Uninstall.desktop'):",
                      maintenance)

    def test_messages_and_the_installer_entry(self):
        self.assertIn('say "VROSTBridge is installed. Open VROSTBridge Client', self.script)  # 1.0.0.3: with the eye-tracking line
        self.assertIsNone(OTHER_BRIDGE.search(read('packaging/uninstall-bazzite.sh')))
        import build_distributions
        self.assertEqual(build_distributions.INSTALLER_DESKTOP, 'Install VROSTBridge.desktop')
        self.assertIn('Name=Install VROSTBridge', read('packaging/build_distributions.py'))
        self.assertIsNone(OTHER_BRIDGE.search(build_distributions.CLIENT_README))


if __name__ == '__main__':
    unittest.main()
