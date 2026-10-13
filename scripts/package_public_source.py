"""Make the clean public source archive: the files git tracks plus new files it does not ignore,
without history, build outputs or anything in .gitignore (dist/, upstream/, build folders).

    python3 scripts/package_public_source.py [--output DIRECTORY]

Refuses to write the archive if a file is over GitHub's 50 MB warning size or matches one of the
maintainer's private patterns: regular expressions, one per line, in the environment variable
VROSTBRIDGE_PRIVATE_PATTERNS (for example your home network's address prefix). They are kept out of the
source on purpose, since a pattern naming your network would publish it. docs/PUBLISHING.md says what to do
with the archive.
"""
import argparse
import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from launcher.version import CLIENT_VERSION, HOST_VERSION  # noqa: E402

NAME = 'VROSTBridge'
LIMIT = 50 * 1024 * 1024


def private_patterns(environment=None):
    """The maintainer's own patterns (VROSTBRIDGE_PRIVATE_PATTERNS, one regular expression per line)."""
    import os
    text = (os.environ if environment is None else environment).get('VROSTBRIDGE_PRIVATE_PATTERNS', '')
    return [re.compile(line.strip().encode()) for line in text.splitlines() if line.strip()]


def public_files():
    listing = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard', '-z'], cwd=ROOT, check=True,
                             capture_output=True).stdout.split(b'\0')
    files = []
    for entry in listing:
        if not entry:
            continue
        path = ROOT / entry.decode('utf-8')
        if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            files.append(path)
    return sorted(files)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT.parent)
    arguments = parser.parse_args()
    if CLIENT_VERSION != HOST_VERSION:
        raise SystemExit('The public archive is made for a release of both roles; versions differ.')
    files = public_files()
    patterns = private_patterns()
    if not patterns:
        print('VROSTBRIDGE_PRIVATE_PATTERNS is not set: only the size check runs. See docs/PUBLISHING.md.')
    problems = []
    for path in files:
        data = path.read_bytes()
        relative = path.relative_to(ROOT).as_posix()
        if len(data) > LIMIT:
            problems.append(relative + ': larger than 50 MB')
        for pattern in patterns:
            if pattern.search(data):
                problems.append(relative + ': matches a private pattern (' + pattern.pattern.decode(errors='replace')[:40] + ')')
    if problems:
        raise SystemExit('Not packaged:\n' + '\n'.join(problems))
    prefix = f'{NAME}-{CLIENT_VERSION}/'
    output = arguments.output.resolve() / f'{NAME}-{CLIENT_VERSION}-public-source.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=7, strict_timestamps=False) as archive:
        for path in files:
            info = zipfile.ZipInfo.from_file(path, prefix + path.relative_to(ROOT).as_posix(), strict_timestamps=False)
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open('rb') as stream:
                archive.writestr(info, stream.read(), compresslevel=7)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise SystemExit('Archive check failed')
        for path in files:
            name = prefix + path.relative_to(ROOT).as_posix()
            if hashlib.sha256(archive.read(name)).digest() != hashlib.sha256(path.read_bytes()).digest():
                raise SystemExit('Archive content differs: ' + name)
    print(output)
    print(f'{len(files)} files; {output.stat().st_size} bytes; checked')


if __name__ == '__main__':
    main()
