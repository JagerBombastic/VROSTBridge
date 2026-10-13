#!/usr/bin/env python3
"""Traces the wordmark (VROST and BRIDGE) of the supplied stacked logo, brand/source/vrostbridge-logo.png,
into brand/assets/svg/vrostbridge-wordmark.svg: two outlines in that picture's pixel coordinates.

Build-time only; needs potrace, numpy and Pillow. Each line of text is enlarged 8 times (Lanczos), cut at half
its colour's brightness and traced; the result is moved back into the picture's coordinates. The colours are
the picture's own (VROST white, BRIDGE light grey-blue).

    python3 brand/tools/trace_wordmark.py
"""
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

BRAND = Path(__file__).resolve().parents[1]
SOURCE = BRAND / 'source' / 'vrostbridge-logo.png'
OUTPUT = BRAND / 'assets' / 'svg' / 'vrostbridge-wordmark.svg'
UP = 8
# (name, crop box in the picture, its colour)
LINES = (('vrost', (160, 795, 1095, 935), '#FFFFFF'), ('bridge', (330, 950, 925, 1025), '#CAD0E0'))


def transform_path(d, sx, sy, tx, ty):
    """Applies x' = sx x + tx, y' = sy y + ty to SVG path data of M/m/L/l/C/c/Z commands; returns absolute data."""
    tokens = re.findall(r'[MmLlCcZz]|-?\d+(?:\.\d+)?', d)
    out, i, cmd = [], 0, None
    x = y = start_x = start_y = 0.0

    def number():
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    def fmt(px, py):
        return '%s %s' % (('%.2f' % (sx * px + tx)).rstrip('0').rstrip('.'), ('%.2f' % (sy * py + ty)).rstrip('0').rstrip('.'))

    while i < len(tokens):
        if re.match(r'[A-Za-z]', tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in 'Zz':
                out.append('Z')
                x, y = start_x, start_y
                continue
        if cmd in 'Mm':
            dx, dy = number(), number()
            x, y = (x + dx, y + dy) if cmd == 'm' else (dx, dy)
            start_x, start_y = x, y
            out.append('M' + fmt(x, y))
            cmd = 'l' if cmd == 'm' else 'L'  # further pairs are line-tos
        elif cmd in 'Ll':
            dx, dy = number(), number()
            x, y = (x + dx, y + dy) if cmd == 'l' else (dx, dy)
            out.append('L' + fmt(x, y))
        elif cmd in 'Cc':
            values = [number() for _ in range(6)]
            if cmd == 'c':
                values = [values[0] + x, values[1] + y, values[2] + x, values[3] + y, values[4] + x, values[5] + y]
            out.append('C' + ' '.join(fmt(values[j], values[j + 1]) for j in (0, 2, 4)))
            x, y = values[4], values[5]
        else:
            raise ValueError('unexpected path command %r' % cmd)
    return ''.join(out)


def trace(image, box, colour):
    level = max(int(colour[i:i + 2], 16) for i in (1, 3, 5)) / 2
    crop = np.asarray(image.crop(box)).astype(float).max(axis=2)
    width, height = box[2] - box[0], box[3] - box[1]
    big = np.asarray(Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8)).resize((width * UP, height * UP), Image.LANCZOS))
    with tempfile.TemporaryDirectory() as folder:
        bitmap, svg = Path(folder) / 'line.pbm', Path(folder) / 'line.svg'
        Image.fromarray(np.where(big >= level, 0, 255).astype(np.uint8)).convert('1').save(bitmap)
        subprocess.run(['potrace', str(bitmap), '-s', '-o', str(svg), '--turdsize', '40', '--alphamax', '1.0',
                        '--opttolerance', '0.2', '--flat'], check=True)
        text = svg.read_text()
    match = re.search(r'translate\(([-\d.]+),([-\d.]+)\) scale\(([-\d.]+),([-\d.]+)\)', text)
    ptx, pty, psx, psy = (float(v) for v in match.groups())
    d = ' '.join(re.findall(r'<path d="([^"]+)"', text, re.S)).replace('\n', ' ')
    # potrace's own transform, then the 8x enlargement undone, then the crop's offset.
    return transform_path(d, psx / UP, psy / UP, box[0] + ptx / UP, box[1] + pty / UP)


def main():
    image = Image.open(SOURCE).convert('RGB')
    paths = ''.join('<path id="%s" d="%s" fill="%s"/>' % (name, trace(image, box, colour), colour) for name, box, colour in LINES)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d"><!-- Traced from source/vrostbridge-logo.png '
                      'by tools/trace_wordmark.py; coordinates are that picture\'s pixels. -->%s</svg>\n'
                      % (image.width, image.height, paths), encoding='utf-8')
    print(OUTPUT, OUTPUT.stat().st_size)


if __name__ == '__main__':
    main()
