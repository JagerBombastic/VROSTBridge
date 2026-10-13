#!/usr/bin/env python3
"""Pixel-fitted small VROSTBridge icons (16 to 32 px): the app icon (for the Windows ICO, the Linux icon theme
and the favicon) and the one-colour tray icons. Build-time only: needs rsvg-convert (librsvg) and Pillow.

Why: simply scaled down, the symbol's gaps (about half a pixel at 16 px) blur, and the cube runs into the dome.
This keeps the symbol of vrostbridge_symbol.py (the same cube, dome, slope and colours) and only
  - makes the gaps between the faces and around the cube one whole pixel,
  - puts the cube's centre gap on one pixel column and its outer edges and the dome's base on pixel edges, and
  - lets the dome span the width less one pixel (the symbol sits half a pixel left of centre),
then renders at 8x and averages down. Nothing is redrawn by hand.

    python3 brand/tools/refine_small_icons.py OUTPUT_FOLDER
writes vrostbridge-<size>.png (colour), vrostbridge-tray-light-<size>.png (white) and
vrostbridge-tray-dark-<size>.png (black) for each size in SIZES.
"""
import io
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vrostbridge_symbol as symbol  # noqa: E402

SIZES = (16, 20, 22, 24, 32)
OVERSAMPLE = 8
TRAY = {'light': 'white', 'dark': 'black'}


def geometry(size):
    """GEOMETRY in this size's pixels, fitted to the grid."""
    v = dict(symbol.GEOMETRY)
    x0, y0, x1, y1 = symbol.bounds(v)
    span = size - 1                       # the dome's width: odd, so its centre is a pixel centre
    k = span / (x1 - x0)
    cx = size / 2 - 0.5                   # the centre gap's pixel column
    top = (size - (y1 - y0) * k) / 2      # centred vertically, then the base line moved onto a pixel edge
    yb = top + (v['yb'] - y0) * k
    shift = round(yb) - yb
    top += shift

    def y(value):
        return top + (value - y0) * k

    out = {'cx': cx, 'cyd': y(v['cyd']), 'R': v['R'] * k, 'yb': round(yb), 'tan': v['tan'],
           'g': 1.0, 'b': 1.0, 'rc': v['rc'] * k, 'rf': 0.0}
    # The cube: its half-width made a whole number plus a half (outer edges on pixel edges), scaled about its
    # centre point so its proportions stay.
    a = v['a'] * k
    a_fit = round(a - 0.5) + 0.5
    scale = a_fit / a
    y_centre = y(v['yT'] + 2 * v['a'] * v['tan'])
    out['a'] = a_fit
    out['e'] = v['e'] * k * scale
    out['yT'] = y_centre - 2 * a_fit * v['tan']
    return out


def svg_for(size, mode='colour'):
    """The pixel-fitted symbol for this size as SVG (viewBox in pixels, drawn OVERSAMPLE times larger)."""
    v = geometry(size)
    side = size * OVERSAMPLE
    box = (0, 0, size, size)
    if mode == 'colour':
        # The gradients are measured in GEOMETRY's units: draw the colour symbol through the same mapping.
        base = dict(symbol.GEOMETRY)
        x0, y0, x1, y1 = symbol.bounds(base)
        k = (size - 1) / (x1 - x0)
        cx = size / 2 - 0.5
        ox = cx - base['cx'] * k
        top = (size - (y1 - y0) * k) / 2
        yb = top + (base['yb'] - y0) * k
        oy = top + round(yb) - yb - y0 * k
        # Shapes in pixels; gradients mapped from GEOMETRY's units into pixels.
        body = symbol.body('colour', v).replace(
            'gradientUnits="userSpaceOnUse"',
            'gradientUnits="userSpaceOnUse" gradientTransform="matrix(%.6f 0 0 %.6f %.4f %.4f)"' % (k, k, ox, oy))
    else:
        body = symbol.body(mode, v)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="%s">%s</svg>'
            % (side, side, ' '.join(str(n) for n in box), body))


def render(size, mode='colour'):
    data = subprocess.run(['rsvg-convert', '--format', 'png'], input=svg_for(size, mode).encode(),
                          capture_output=True, check=True).stdout
    big = Image.open(io.BytesIO(data)).convert('RGBA')
    if big.size != (size * OVERSAMPLE, size * OVERSAMPLE):
        raise RuntimeError('unexpected render size %r' % (big.size,))
    return big.resize((size, size), Image.Resampling.BOX)


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    folder = Path(argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        render(size).save(folder / ('vrostbridge-%d.png' % size), optimize=True)
        for variant, mode in TRAY.items():
            render(size, mode).save(folder / ('vrostbridge-tray-%s-%d.png' % (variant, size)), optimize=True)
        print(folder, size)


if __name__ == '__main__':
    main(sys.argv)
