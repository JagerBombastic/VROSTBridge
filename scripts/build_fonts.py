#!/usr/bin/env python3
"""Builds the fonts VROSTBridge ships, from the Google Fonts releases of Inter and Outfit.

Both families are SIL Open Font License 1.1 without Reserved Font Names. Sources:
google/fonts ofl/inter (Inter[opsz,wght].ttf, upstream rsms/inter 66647c0b) and
ofl/outfit (Outfit[wght].ttf, upstream Outfitio/Outfit-Fonts 90277380), google/fonts
commit 9710da1e. Build-time only (fontTools); the products are committed:

- static instances (glyf outlines, no hinting) for the in-headset status card and the
  Game Mode screen, which rasterise with stb_truetype (no variable-font support);
- variable subsets (weight axis only) for the desktop pages.

All are subset to Latin, Latin-1, Latin Extended-A, punctuation, arrows and a few
symbols the interface uses.

    python3 scripts/build_fonts.py --google-fonts /path/to/google/fonts
"""
import argparse
import shutil
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'launcher' / 'fonts'
UNICODES = [*range(0x20, 0x7F), *range(0xA0, 0x180), 0x131, 0x152, 0x153, 0x2C6, 0x2DA, 0x2DC,
            *range(0x2000, 0x2070), 0x20AC, 0x2122, *range(0x2190, 0x21A0), 0x2212, 0x2215, 0x2248,
            0x2260, 0x2264, 0x2265, 0x2022, 0x25CF, 0x25CB, 0x25B2, 0x25B6, 0x25BC, 0x25C0, 0x2713,
            0x2715, 0x2717, 0x2318]
FEATURES = ['kern', 'liga', 'calt', 'ccmp', 'locl', 'mark', 'mkmk', 'tnum', 'case', 'zero']
STATIC = [
    # (output, source, axis location)
    ('Inter-Regular.ttf', 'inter/Inter[opsz,wght].ttf', {'wght': 400, 'opsz': 14}),
    ('Inter-Medium.ttf', 'inter/Inter[opsz,wght].ttf', {'wght': 500, 'opsz': 14}),
    ('Inter-SemiBold.ttf', 'inter/Inter[opsz,wght].ttf', {'wght': 600, 'opsz': 14}),
    ('Outfit-Medium.ttf', 'outfit/Outfit[wght].ttf', {'wght': 500}),
    ('Outfit-SemiBold.ttf', 'outfit/Outfit[wght].ttf', {'wght': 600}),
]
VARIABLE = [
    ('Inter-Variable.ttf', 'inter/Inter[opsz,wght].ttf', {'wght': (400, 700), 'opsz': 14}),
    ('Outfit-Variable.ttf', 'outfit/Outfit[wght].ttf', {'wght': (300, 700)}),
]


def shrink(font):
    options = subset.Options()
    options.layout_features = FEATURES
    options.hinting = False
    options.name_IDs = ['*']
    options.name_languages = ['*']
    options.notdef_outline = True
    options.glyph_names = False
    options.drop_tables += ['DSIG']
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=UNICODES)
    subsetter.subset(font)
    return font


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--google-fonts', type=Path, required=True, help='google/fonts checkout (ofl/ is used)')
    args = parser.parse_args()
    ofl = args.google_fonts / 'ofl'
    OUT.mkdir(parents=True, exist_ok=True)
    for name, source, location in STATIC + VARIABLE:
        # Subset the variable font first: subsetting a partial instance trips over its lazily
        # loaded glyph variations.
        font = shrink(TTFont(ofl / source, lazy=False))
        font = instancer.instantiateVariableFont(font, location, updateFontNames=False)
        font.save(OUT / name)
        print(f'{name}: {(OUT / name).stat().st_size // 1024} KiB')
    shutil.copyfile(ofl / 'inter' / 'OFL.txt', OUT / 'Inter-OFL.txt')
    shutil.copyfile(ofl / 'outfit' / 'OFL.txt', OUT / 'Outfit-OFL.txt')


if __name__ == '__main__':
    main()
