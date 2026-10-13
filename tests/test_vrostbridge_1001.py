"""1.0.0.1: one name throughout, the full meaning under the name, and the version on both programs.

- Every program, folder, setting, protocol and file name is VROSTBridge's, and the names the companion, the
  native client, the transport and the driver share agree with each other (they meet only on real hardware).
- Under the name, on both pages and in Game Mode: VR Rendering OutSide The Box - Bridge, with V, R (of
  Rendering), O, S, T and B (of Box) in the palette's colours and every letter of Bridge in the brand gradient.
- Host and client are both 1.0.0.1, and move on together (1.0.0.2: tests/test_vrostbridge_1002.py).
"""
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'packaging'))

from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402

# A product-style name ending in "Bridge" other than VROSTBridge: one word in any case, or two capitalised words.
OTHER_BRIDGE = re.compile(r'\b(?!(?i:vrostbridge)\b)[A-Za-z]+(?:Bridge|bridge|BRIDGE)\b|\b[A-Z][a-z]+[ _-]Bridge\b')
# Third-party trees keep their own words.
THIRD_PARTY = ('third_party/ffmpeg/', 'third_party/openvr/', 'third_party/wayland/', 'third_party/go-bsb-cams/vendor/',
               'third_party/stb/stb_', 'host/vendor/')


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


def patches():
    return ''.join(path.read_text(encoding='utf-8') for path in sorted((ROOT / 'patches').glob('*.patch')))


def tracked_text_files():
    try:
        names = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\n')
    except (OSError, subprocess.CalledProcessError):
        names = [path.relative_to(ROOT).as_posix() for path in ROOT.rglob('*') if path.is_file()
                 and not {'.git', 'dist', 'upstream', 'target', '__pycache__'}.intersection(path.relative_to(ROOT).parts)]
    for name in names:
        path = ROOT / name
        if not name or name.startswith(THIRD_PARTY) or not path.is_file():
            continue
        data = path.read_bytes()
        if b'\0' not in data[:8192]:
            yield name, data.decode('utf-8', 'replace')


class VersionTests(unittest.TestCase):
    def test_both_programs_share_one_version_from_1_0_0_1_on(self):
        self.assertEqual(CLIENT_VERSION, HOST_VERSION)
        self.assertGreaterEqual(tuple(int(part) for part in HOST_VERSION.split('.')), (1, 0, 0, 1))
        self.assertIn('#define VROSTBRIDGE_CLIENT_VERSION "%s"' % CLIENT_VERSION, read('core/version.hpp'))
        self.assertIn('#define VROSTBRIDGE_HOST_VERSION "%s"' % HOST_VERSION, read('core/version.hpp'))

    def test_a_four_part_version_is_a_release(self):
        from launcher import install_maintenance
        self.assertTrue(install_maintenance._RELEASE_VERSION.fullmatch('1.0.0.1'))


class TaglineTests(unittest.TestCase):
    TEXT = 'VR Rendering OutSide The Box – Bridge'
    # The letters picked out, in order, with their colours: the palette for V R O S T B, the brand gradient
    # (cyan, blue at a third, purple at two thirds, magenta) sampled at sixths across Bridge.
    LETTERS = [('V', '#00E5FF'), ('R', '#00E5FF'), ('O', '#0085FF'), ('S', '#A855FF'), ('T', '#FF2DCB'), ('B', '#FF2DCB'),
               ('B', '#00E5FF'), ('r', '#00ABFF'), ('i', '#227BFF'), ('d', '#865FFF'), ('g', '#CB45EA'), ('e', '#FF2DCB')]

    def test_the_gradient_colours(self):
        stops = [(0, (0x00, 0xE5, 0xFF)), (1 / 3, (0x00, 0x85, 0xFF)), (2 / 3, (0xA8, 0x55, 0xFF)), (1, (0xFF, 0x2D, 0xCB))]

        def at(t):
            for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
                if t0 <= t <= t1:
                    f = (t - t0) / (t1 - t0)
                    return '#%02X%02X%02X' % tuple(round(a + (b - a) * f) for a, b in zip(c0, c1))
        self.assertEqual([colour for _, colour in self.LETTERS[6:]], [at(i / 5) for i in range(6)])
        self.assertIn('--brand:linear-gradient(90deg,#00e5ff,#0085ff 33%,#a855ff 66%,#ff2dcb)', read('launcher/desktop.html'))

    def test_the_pages(self):
        html = read('launcher/desktop.html')
        line = re.search(r'<small class="tagline" id="tagline">(.*?)</small>', html).group(1)
        self.assertEqual(re.sub(r'<[^>]+>', '', line), self.TEXT)
        self.assertTrue(line.startswith('<b class="cyan">V</b>R '), 'the R of VR is grey')
        css = dict(re.findall(r'\.tagline \.([\w-]+)\{color:([^}]+)\}', html))
        tokens = dict(re.findall(r'--(cyan|blue|purple|magenta):(#[0-9a-f]{6})', html))
        picked = []
        for name, letter in re.findall(r'<b class="([\w-]+)">(\w)</b>', line):
            colour = css[name]
            colour = tokens[colour[6:-1]] if colour.startswith('var(--') else colour
            picked.append((letter, colour.upper()))
        self.assertEqual(picked, self.LETTERS)
        self.assertIn('.tagline b{font-weight:700}', html)

    def test_game_mode(self):
        from launcher import game_ui
        self.assertEqual(''.join(text for text, _, _ in game_ui.TAGLINE), self.TEXT)
        picked = [(text, '#%06X' % (colour >> 8)) for text, colour, font in game_ui.TAGLINE if font == 'strong']
        self.assertEqual(picked, self.LETTERS)
        for text, colour, font in game_ui.TAGLINE:
            if font != 'strong':
                self.assertEqual(colour, game_ui.MUTED, text)

    def test_the_fonts_have_the_dash(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest('fontTools is not installed')
        for name in ('Inter-Regular.ttf', 'Inter-Medium.ttf'):
            path = ROOT / 'launcher/fonts' / name
            if path.is_file():
                self.assertIn(0x2013, TTFont(path).getBestCmap(), name)


class OneNameTests(unittest.TestCase):
    def test_every_name_is_vrostbridge(self):
        found = []
        for name, text in tracked_text_files():
            text = re.sub(r'\\[nrt]', ' ', text)  # "\nVROSTBridge" in a string literal
            for number, line in enumerate(text.splitlines(), 1):
                match = OTHER_BRIDGE.search(line)
                if match:
                    found.append('%s:%d: %s' % (name, number, match.group(0)))
        self.assertEqual(found, [])

    def test_the_rendered_specification(self):
        # docs/PROJECT_SPEC.pdf is compressed, so the text check above cannot see into it: it is rendered from
        # PROJECT_SPEC.md for this version and carries the one name.
        try:
            import pypdf
        except ImportError:
            self.skipTest('pypdf is not installed')
        reader = pypdf.PdfReader(str(ROOT / 'docs/PROJECT_SPEC.pdf'))
        pages = [' '.join((page.extract_text() or '').split()) for page in reader.pages]
        revision = re.search(r'Documentation revision: \*\*(\d{4}-\d{2}-\d{2}) / ' + re.escape(HOST_VERSION) + r'\*\*',
                             read('docs/PROJECT_SPEC.md'))  # the PDF is rendered from this revision of the text
        self.assertIsNotNone(revision)
        self.assertIn('Documentation ' + revision.group(1) + ' / ' + HOST_VERSION, pages[0])
        self.assertIn('Windows rendering host ' + HOST_VERSION, pages[0])
        self.assertIn('VR Rendering OutSide The Box – Bridge', pages[0])
        self.assertEqual([match.group(0) for page in pages for match in OTHER_BRIDGE.finditer(page)], [])
        self.assertIsNone(OTHER_BRIDGE.search(' '.join(str(value) for value in (reader.metadata or {}).values())))

    def test_the_programs_and_folders(self):
        cmake = read('CMakeLists.txt')
        for target in ('VROSTBridgeClient', 'VROSTBridgeLease', 'VROSTBridgeRuntimeProbe', 'vrosttext'):
            self.assertRegex(cmake, r'add_(executable|library)\(' + target + r' ')
        self.assertIn('/libvrostbridge_transport.so"', cmake)
        self.assertIn('name = "vrostbridge_transport"', read('client/transport/Cargo.toml'))
        self.assertIn("name='VROSTBridge-Client'", read('packaging/VROSTBridge-Client.spec'))
        self.assertIn('"$base/current/VROSTBridge-Client" --role client', read('packaging/install-bazzite.sh'))
        self.assertIn("b'VROSTBridge-Client' in exe", read('launcher/steam_shortcut.py'))
        self.assertIn("'libvrosttext.so'", read('launcher/game_ui.py'))
        maintenance = read('launcher/install_maintenance.py')
        self.assertIn("/'VROSTBridge'", maintenance)
        self.assertIn("/'vrostbridge'/'versions'", maintenance)
        self.assertIn('lstrcatW(state,L"\\\\VROSTBridge")', read('packaging/windows-launcher.c'))
        self.assertIn('base="$data/vrostbridge"', read('packaging/install-bazzite.sh'))
        self.assertTrue((ROOT / 'config/70-vrostbridge-bigeye.rules').is_file())
        self.assertIn("'/etc/udev/rules.d/70-vrostbridge-bigeye.rules'", read('launcher/eye_tracking.py'))

    def test_the_native_protocol_agrees(self):
        from launcher import session_link
        self.assertEqual(session_link.NATIVE_PROTOCOL, 'vrostbridge-native-1')
        self.assertEqual(session_link.PROTOCOL, 'vrostbridge-session-2')
        self.assertIn('format!("{base}-vrostbridge-native-1")', patches())
        manifest = read('scripts/native_build_manifest.py')
        self.assertIn('PROTOCOL = "21-dev14-vrostbridge-native-1"', manifest)
        self.assertIn('"native_protocol": "vrostbridge-native-1"', manifest)
        self.assertIn("metadata.get('native_protocol')!='vrostbridge-native-1'", read('packaging/build_distributions.py'))

    def test_the_driver_reads_the_files_the_companion_writes(self):
        from host import stream_options
        source = patches()
        for name in (stream_options.CONFIG_FILE, stream_options.INIT_FILE, stream_options.INIT_APPLIED_FILE):
            self.assertTrue(name.startswith('vrostbridge-'), name)
            self.assertIn(name, source)
        self.assertIn("'vrostbridge-timing.json'", read('launcher/app.py'))
        self.assertIn('vrostbridge-timing.json', source)

    def test_the_companion_parses_the_driver_lines(self):
        source = patches()
        events = read('host/events.py') + read('host/finger_diagnostics.py')
        tags = set(re.findall(r'\b(VROST_[A-Z_]+)\b', events))
        self.assertEqual(tags, {'VROST_HOST_AMF', 'VROST_HOST_CAPTURE', 'VROST_HOST_ENCODER', 'VROST_STREAM_CONFIG',
                                'VROST_GAZE_FOVEATION', 'VROST_INTRA_REFRESH', 'VROST_VSYNC_LOCK', 'VROST_NATIVE_HAND',
                                'VROST_COLOR', 'VROST_NO_HEAD_POSE',  # 1.0.0.5: patch 0018
                                'VROST_CONTROLLER_PAIR'})  # 1.0.0.6: patch 0020
        for tag in tags:
            self.assertRegex(source, r'(?m)^\+.*"' + tag + r' ', tag)

    def test_the_environment_the_native_side_reads_is_set(self):
        native = patches() + read('client/transport/lib.rs')
        wanted = set(re.findall(r'env::var\("([A-Z_]+)"\)', native))
        self.assertEqual(wanted, {'VROSTBRIDGE_BIND_IP', 'VROSTBRIDGE_INTERFACE', 'VROSTBRIDGE_MICROPHONE'})
        companion = read('launcher/app.py') + read('launcher/session_link.py')
        for name in wanted:
            self.assertIn("'" + name + "'", companion, name)

    def test_packets_between_the_companion_and_the_client(self):
        self.assertIn('"VRG1 %llu %d %f %f %f %f %c"', read('client/device/gaze.hpp'))
        self.assertIn("'VRG1 %d 1 ", read('launcher/gaze_provider.py'))
        self.assertIn("MAGIC = b'VRLT'", read('launcher/link_test.py'))
        runtime = read('client/platform/linux/runtime_settings.hpp')
        self.assertIn('"VROSTBridgeRuntime2"', runtime)
        self.assertIn("('VROSTBridgeRuntime1','VROSTBridgeRuntime2')", read('launcher/client_runtime.py'))


if __name__ == '__main__':
    unittest.main()
