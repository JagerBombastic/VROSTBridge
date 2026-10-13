#!/usr/bin/env python3
"""Sets "Bridge" for the one-line wordmark (1.0.0.3): brand/assets/svg/vrostbridge-bridge.svg.

The headers show the name on one line: VROST as traced from the supplied logo, then "Bridge" with a capital B and
lower-case "ridge". The logo's BRIDGE has capitals only, so "Bridge" is set in Montserrat (SIL Open Font License
1.1), the open font closest to the logo's BRIDGE letters, at weight 400 with 0.1 em of extra letter spacing. The
letters are kept as outlines, so no font is included in VROSTBridge; the OFL does not apply to artwork made with
a font.

Build-time only; needs fontTools, and Pillow with its raqm (HarfBuzz) layout for the letters' positions
(Montserrat's own spacing and kerning). The outlines are in the font's units (1000 per em), the baseline at y = 0
and y pointing down; the SVG records the capital height for scaling.

    python3 brand/tools/set_bridge.py path/to/Montserrat[wght].ttf

Montserrat[wght].ttf from https://github.com/google/fonts/tree/main/ofl/montserrat (the file used for 1.0.0.3:
SHA-256 0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7).
"""
import hashlib
import sys
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1]
OUTPUT = BRAND / 'assets' / 'svg' / 'vrostbridge-bridge.svg'
TEXT, WEIGHT, TRACKING = 'Bridge', 400, 0.10
FONT_SHA256 = '0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7'


def number(value):
    text = ('%.2f' % value).rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def main(font_path):
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    from PIL import ImageFont, features

    digest = hashlib.sha256(Path(font_path).read_bytes()).hexdigest()
    if digest != FONT_SHA256:
        raise SystemExit('Not the Montserrat file this was made with (SHA-256 %s).' % digest)
    if not features.check('raqm'):
        raise SystemExit("Pillow's raqm layout (HarfBuzz) is needed for the letters' positions.")
    font = TTFont(font_path)
    upm = font['head'].unitsPerEm
    static = instancer.instantiateVariableFont(font, {'wght': WEIGHT})
    glyphs, cmap, cap = static.getGlyphSet(), static.getBestCmap(), static['OS/2'].sCapHeight
    layout = ImageFont.truetype(font_path, upm, layout_engine=ImageFont.Layout.RAQM)
    layout.set_variation_by_axes([WEIGHT])
    pen, bounds = SVGPathPen(glyphs, number), BoundsPen(glyphs)
    for index, letter in enumerate(TEXT):
        # Where HarfBuzz puts the letter (Montserrat's advances and kerning), plus the extra spacing.
        x = layout.getlength(TEXT[:index + 1]) - layout.getlength(letter) + TRACKING * upm * index
        for target in (pen, bounds):
            glyphs[cmap[ord(letter)]].draw(TransformPen(target, (1, 0, 0, -1, x, 0)))
    x0, y0, x1, y1 = bounds.bounds
    OUTPUT.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s %s %s %s"><!-- "Bridge" set in Montserrat (SIL OFL 1.1) '
        'weight %d, %.2f em extra spacing, by tools/set_bridge.py; font units, baseline at 0; capital height %d. -->'
        '<path id="bridge" data-cap-height="%d" d="%s"/></svg>\n'
        % (number(x0), number(y0), number(x1 - x0), number(y1 - y0), WEIGHT, TRACKING, cap, cap, pen.getCommands()),
        encoding='utf-8')
    print(OUTPUT, OUTPUT.stat().st_size)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
