"""The first public release: the documentation pages link up, the licences are explained, and the source is split by
part for the release page.

README.md is the front page and docs/README.md the documentation's home. scripts/package_component_sources.py makes
one source archive for each part (the rendering PC, the Windows headset PC and the Bazzite headset PC), each with what
that part is built from and a SOURCE.md that says so; every file of the whole source belongs to a part or is named as
whole-source only, so a new file can't fall out of every archive.
"""
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

import package_component_sources as split  # noqa: E402
from launcher.version import CLIENT_VERSION  # noqa: E402

LINK = re.compile(r'\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)|\bhref="([^"]+)"|^\[[^\]]+\]:\s*(\S+)', re.M)
HEADING = re.compile(r'^#{1,6}\s+(.*?)\s*#*\s*$', re.M)


def slug(heading):
    """GitHub's anchor for a Markdown heading."""
    text = re.sub(r'[`*_~]|<[^>]+>', '', heading.strip().lower())
    text = ''.join(ch for ch in text if ch.isalnum() or ch in ' -' or unicodedata.category(ch).startswith('L'))
    return text.replace(' ', '-')


def anchors(text):
    """The anchors of a Markdown text's headings, as GitHub numbers repeats."""
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    seen, found = {}, set()
    for match in HEADING.finditer(text):
        name = slug(match.group(1))
        count = seen.get(name, 0)
        seen[name] = count + 1
        found.add(name if count == 0 else f'{name}-{count}')
    return found


def broken_links(text, base):
    """Relative links in `text` (a file in folder `base`) that name no file, or no heading of a Markdown file. Links
    that leave the repository are GitHub's own pages (the README's ../../releases) and are not checked."""
    problems = []
    own = anchors(text)
    for match in LINK.finditer(text):
        target = match.group(1) or match.group(2) or match.group(3)
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith('/'):
            continue
        name, _, anchor = target.partition('#')
        resolved = Path(os.path.normpath(base / name)) if name else None
        if resolved is not None and not resolved.is_relative_to(ROOT):
            continue
        if resolved is not None and not resolved.exists():
            problems.append(target + ': no such file')
        elif anchor and resolved is None and anchor not in own:
            problems.append(target + ': no such heading')
        elif anchor and resolved is not None and resolved.suffix == '.md' and \
                anchor not in anchors(resolved.read_text(encoding='utf-8')):
            problems.append(target + ': no such heading')
    return problems


def documents():
    paths = [ROOT / name for name in ('README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', 'THIRD_PARTY.md',
                                      'START_HERE.html', 'packaging/README.md', 'brand/README.md')]
    paths += [path for path in sorted((ROOT / 'docs').rglob('*.md')) if 'evidence' not in path.relative_to(ROOT).parts]
    paths += sorted((ROOT / '.github').rglob('*.md'))
    return [path for path in paths if path.is_file()]


def public():
    """The whole source's file list (what git tracks plus new files it does not ignore)."""
    try:
        return split.relative_paths(split.public_files())
    except (OSError, subprocess.CalledProcessError):
        raise unittest.SkipTest('needs a git checkout: the file list is what git tracks')


class DocumentationTests(unittest.TestCase):
    def test_every_relative_link_and_heading_link_resolves(self):
        problems = []
        for path in documents():
            text = path.read_text(encoding='utf-8')
            if path.suffix == '.md':
                text = re.sub(r'```.*?```', '', text, flags=re.S)
            problems += [f'{path.relative_to(ROOT)}: {problem}' for problem in broken_links(text, path.parent)]
        self.assertEqual(problems, [])

    def test_the_documentation_home_lists_every_current_document(self):
        home = (ROOT / 'docs/README.md').read_text(encoding='utf-8')
        listed = {match.group(1) or match.group(2) or match.group(3) for match in LINK.finditer(home)}
        listed = {target.partition('#')[0] for target in listed}
        current = sorted(path.name for path in (ROOT / 'docs').glob('*.md') if path.name not in ('README.md',))
        self.assertEqual([name for name in current if name not in listed], [])
        self.assertIn('[front page](../README.md)', home)
        self.assertIn('[`history/`](history/)', home)

    def test_the_front_page_points_at_the_documentation_and_the_licences(self):
        front = (ROOT / 'README.md').read_text(encoding='utf-8')
        targets = {match.group(1) or match.group(2) or match.group(3) for match in LINK.finditer(front)}
        targets |= set(re.findall(r'\bsrc="([^"]+)"', front))
        missing = [link for link in ('docs/README.md', 'docs/QUICK_START.md', 'docs/TROUBLESHOOTING.md',
                                     'docs/LICENSING.md', 'LICENSE', 'docs/images/client-page.png') if link not in targets]
        self.assertEqual(missing, [])
        # Addresses on the front page are the documentation ranges, never a real home network's.
        self.assertEqual(re.findall(r'\b(?:192\.168|10)\.\d+\.\d+\.\d+\b', front), [])

    def test_the_licences_in_plain_words(self):
        page = ' '.join((ROOT / 'docs/LICENSING.md').read_text(encoding='utf-8').split())
        self.assertIn('Copyright (c) 2026 JagerBombastic', (ROOT / 'LICENSE').read_text(encoding='utf-8'))
        missing = [words for words in ('MIT licence', 'JagerBombastic', 'FFmpeg', 'LGPL 2.1 or later', 'go-bsb-cams',
                                       'GPL 2', 'Baballonia', 'non-commercial', 'VB-CABLE', 'not legal advice',
                                       "isn't affiliated with") if words not in page]
        self.assertEqual(missing, [])
        # The two programs VROSTBridge can use but never ships stay out of every package.
        for name in ('Baballonia', 'VB-CABLE'):
            self.assertIn(f'**{name}**', page.split('## Not included, on purpose', 1)[1].split('##', 1)[0])

    def test_the_artwork_licence(self):
        # 1.0.0.11: the logo, icons and other artwork are CC BY-ND 4.0; the code, the tools that make the artwork
        # included, stays MIT. Every place that states the licences names it, and every package carries NOTICE
        # and THIRD_PARTY.md, which say so.
        licence = ' '.join((ROOT / 'brand/LICENSE.md').read_text(encoding='utf-8').split())
        for words in ('© 2026 JagerBombastic', 'Attribution-NoDerivatives 4.0', 'https://creativecommons.org/licenses/by-nd/4.0/',
                      'their own name and logo', '`launcher/brand/`', 'MIT licence'):
            self.assertIn(words, licence)
        for name, words in (('NOTICE', 'Attribution-NoDerivatives 4.0'), ('THIRD_PARTY.md', 'Attribution-NoDerivatives 4.0'),
                            ('docs/LICENSING.md', 'Attribution-NoDerivatives 4.0'), ('README.md', 'CC BY-ND 4.0'),
                            ('brand/README.md', 'CC BY-ND 4.0'), ('docs/OPEN_SOURCE_CREDITS.md', 'CC BY-ND 4.0')):
            text = ' '.join((ROOT / name).read_text(encoding='utf-8').split())
            self.assertIn(words, text, name)
            self.assertNotIn('not been chosen', text, name)

    def test_troubleshooting_uses_the_pages_own_words(self):
        page = ' '.join((ROOT / 'docs/TROUBLESHOOTING.md').read_text(encoding='utf-8').split())
        # Each message or button the page names is one the companion really shows.
        companion = '\n'.join(path.read_text(encoding='utf-8') for path in sorted((ROOT / 'launcher').rglob('*'))
                              if path.suffix in ('.py', '.html', '.js')).replace('&amp;', '&')
        words = ('Allow home-network clients', 'Allow home-network connection', 'Wake host now', 'Allow & connect',
                 'Use recommended settings', 'Send report to host', 'Set up headset microphone', 'Allow eye camera access',
                 'Measure again', 'Repair Windows Firewall access', 'Turn off Wi-Fi power saving', 'Stay on one access point',
                 'Set the GPU clock to automatic', 'Companion unavailable', 'Client approval needed', 'Video not arriving',
                 'Headset not tracked', 'Waiting for the headset’s position', 'SteamVR is not set up on this PC yet',
                 "This PC's graphics can't keep up with the stream", 'Fewer frames are arriving from the host')
        self.assertEqual([w for w in words if w not in page], [])
        self.assertEqual([w for w in words if w not in companion], [])


class ComponentSourceTests(unittest.TestCase):
    def test_patterns_follow_folders(self):
        self.assertTrue(split.matches('docs/history/RELEASE_1.0.0.md', ['docs/**']))
        self.assertTrue(split.matches('host/control.py', ['host/*.py']))
        self.assertFalse(split.matches('host/vendor/websockets/client.py', ['host/*.py']))
        self.assertFalse(split.matches('tests/fixtures/software_decode_8bit.h264', ['tests/*.h264']))
        self.assertTrue(split.matches('client/platform/linux/VkLayer_vrostbridge_openvr.json',
                                      ['client/platform/linux/VkLayer_*']))
        self.assertFalse(split.matches('LICENSE.md', ['LICENSE']))

    def test_every_file_of_the_whole_source_has_a_place(self):
        self.assertEqual(split.unassigned(public()), [])

    def test_each_part_carries_the_notices_and_what_it_is_built_from(self):
        files = public()
        host, windows, bazzite = (set(split.part_files(part, files)) for part in ('host', 'windows-client', 'bazzite-client'))
        for chosen in (host, windows, bazzite):
            for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY.md', 'licenses/FFmpeg-LGPL-2.1.txt', 'docs/LICENSING.md',
                         'docs/BUILDING.md', 'packaging/README.md', 'launcher/app.py', 'host/control.py',
                         'scripts/fetch_upstream.py', 'patches/0001-local-microphone-consent.patch'):
                self.assertIn(name, chosen)
            self.assertEqual([name for name in chosen if split.matches(name, ['tests/test_*', '.github/**'])], [])
        # The rendering PC: the driver's build and the helpers, and nothing of the headset PCs' native client.
        for name in ('scripts/Build-Host-Cross.py', 'Build-Host-Windows.cmd', 'host/vr_helper.cpp',
                     'packaging/windows-installer.nsi', 'tests/host_color_tests.cpp'):
            self.assertIn(name, host)
        self.assertEqual([name for name in host if name.startswith(('client/', 'third_party/ffmpeg/',
                                                                    'third_party/go-bsb-cams/'))], [])
        # Both headset PCs: the native client with its tests, and FFmpeg's exact source beside the libraries built
        # from it (LGPL).
        for chosen in (windows, bazzite):
            for name in ('CMakeLists.txt', 'client/transport/lib.rs', 'client/platform/linux/vulkan.cpp',
                         'tests/video_pass_tests.cpp', 'host/chaperone_geometry.hpp',
                         'third_party/ffmpeg/ffmpeg-n6.1.4-source.tar.xz', 'third_party/ffmpeg/LICENSE.md'):
                self.assertIn(name, chosen)
        for name in ('client/platform/windows/d3d11_video.cpp', 'scripts/Build-Client-Windows.py',
                     'packaging/windows-client-installer.nsi', 'cmake/windows-clang-cl.cmake'):
            self.assertIn(name, windows)
            self.assertNotIn(name, bazzite)
        # go-bsb-cams is GPL 2: its complete source goes with the Bazzite package's camera helper.
        for name in ('client/platform/linux/lease.cpp', 'third_party/go-bsb-cams/main.go',
                     'third_party/go-bsb-cams/VROSTBRIDGE_CHANGES.md', 'packaging/install-bazzite.sh',
                     'packaging/build_client.py', 'Build-Client-Linux.sh'):
            self.assertIn(name, bazzite)
            self.assertNotIn(name, windows)

    def test_the_note_says_what_is_in_it_and_how_to_build_it(self):
        for part, title in (('host', 'the rendering PC'), ('windows-client', 'the Windows headset PC'),
                            ('bazzite-client', 'the Bazzite headset PC')):
            note = split.source_note(part, '9.9.9.9', 42)
            self.assertTrue(note.startswith(f'# VROSTBridge 9.9.9.9: source for {title}'), note[:80])
            for words in ('**Building:**', '**Not in this archive:**', 'This archive holds 42 of its files, unchanged.',
                          "the project's repository, which each release also offers as *Source code*",
                          'MIT-licensed', 'the Python tests'):
                self.assertIn(words, note)
            # SOURCE.md sits at the archive's top, where the repository's top is.
            self.assertEqual(broken_links(note, ROOT), [], part)

    def test_an_archive_holds_its_files_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            target = split.write_archive('bazzite-client', Path(folder), ['LICENSE', 'docs/LICENSING.md'])
            self.assertEqual(target.name, f'VROSTBridge-{CLIENT_VERSION}-bazzite-client-source.zip')
            prefix = f'VROSTBridge-{CLIENT_VERSION}-bazzite-client-source/'
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(sorted(archive.namelist()),
                                 [prefix + 'LICENSE', prefix + 'SOURCE.md', prefix + 'docs/LICENSING.md'])
                self.assertEqual(archive.read(prefix + 'LICENSE'), (ROOT / 'LICENSE').read_bytes())
                self.assertIn(b'This archive holds 2 of its files, unchanged.', archive.read(prefix + 'SOURCE.md'))


if __name__ == '__main__':
    unittest.main()
