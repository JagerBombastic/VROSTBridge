#!/usr/bin/env python3
"""Builds VROSTBridge's artwork from the logo in brand/.

Build-time only (rsvg-convert from librsvg, Pillow, numpy); the products are committed. Everything comes from
two sources: brand/tools/vrostbridge_symbol.py (the symbol rebuilt as vectors from the supplied icon, and the
stacked logo with the wordmark traced from the supplied logo) and, for 16 to 32 px, brand/tools/
refine_small_icons.py (the same symbol fitted to whole pixels). The only new drawing is the plain backgrounds
(dark, with soft glows in the brand colours).

Products:
- brand/assets/svg/
    vrostbridge-mark.svg, -mark-white.svg, -mark-black.svg   the symbol (colour; one colour for light and dark)
    vrostbridge-app.svg          the symbol centred in a square (app icon)
    vrostbridge-logo.svg, vrostbridge-logo-light.svg   the stacked logo for dark and for light backgrounds
    vrostbridge-wordmark-inline.svg, -inline-light.svg   the wordmark on one line: VROST, then Bridge (1.0.0.3;
                                 Bridge from vrostbridge-bridge.svg, tools/set_bridge.py)
- brand/assets/logo/vrostbridge-logo.png, vrostbridge-logo-light.png, vrostbridge-mark.png   PNG exports
- brand/previews/vrostbridge-icons.png   every icon size on dark and light, actual size and enlarged
- launcher/brand/
    vrostbridge.svg            app icon (square, transparent)
    vrostbridge-mark.svg       the symbol alone (page header)
    vrostbridge-logo.svg       the stacked logo for dark backgrounds (About)
    vrostbridge-name.svg       the wordmark on one line for dark backgrounds (page header; 1.0.0.3)
    vrostbridge-name.png       the same with VROST's capitals 96 px tall, for the Game Mode screen's header
    vrostbridge-256.png, vrostbridge-512.png   app icon PNGs
    vrostbridge.ico            Windows app icon: 16-32 px pixel-fitted, 40-256 px from the vector
    vrostbridge-tray-light.ico, vrostbridge-tray-dark.ico   one-colour tray icons (white for dark taskbars,
                               black for light ones), 16-32 px pixel-fitted
    favicon.ico, favicon-32.png   browser tab icons
    hicolor/                   Linux icon theme payload (Icon=vrostbridge)
    steam/                     Steam library artwork: capsule.jpg 600x900, header.jpg 920x430,
                               hero.jpg 3840x1240, logo.png 1280x480 (transparent), icon.png 256x256
- client/assets/hud/logo-192.rgba  in-headset cards (192 x 192 straight RGBA8, no header)
- launcher/game_ui_assets/background.jpg   Game Mode screen background, 1920 x 1200
- brand/social-preview.png     GitHub social preview, 1280 x 640 (the logo on the background)

    python3 scripts/build_brand_assets.py
"""
import io
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / 'brand'
SVG = BRAND / 'assets' / 'svg'
OUT = ROOT / 'launcher' / 'brand'
sys.path.insert(0, str(BRAND / 'tools'))
import refine_small_icons as refine  # noqa: E402
import vrostbridge_symbol as symbol  # noqa: E402

BACKGROUND = (0x08, 0x0B, 0x12)
# brand/brand.json's palette
CYAN, BLUE, PURPLE, MAGENTA = (0x00, 0xE5, 0xFF), (0x00, 0x85, 0xFF), (0xA8, 0x55, 0xFF), (0xFF, 0x2D, 0xCB)
APP_MARGIN = 0.04   # the app icon's margin each side, as a fraction of the side (40 px and up)


def render_svg(text, width, height=None, oversample=4):
    """SVG text at an exact size: rendered larger by librsvg, then area-averaged down."""
    height = height or width
    data = subprocess.run(['rsvg-convert', '--format', 'png', '--width', str(width * oversample),
                           '--height', str(height * oversample)], input=text.encode(), capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(data)).convert('RGBA').resize((width, height), Image.Resampling.BOX)


def app_svg(mode='colour'):
    return symbol.svg(mode, box=symbol.square_box(APP_MARGIN))


def app_icon(size, mode='colour'):
    """The app icon at one size: pixel-fitted up to 32 px, from the vector above."""
    if size in refine.SIZES:
        return refine.render(size, mode)
    return render_svg(app_svg(mode), size)


def write_ico(images, path):
    """An ICO with exactly these images (PNG entries, 32-bit), smallest first."""
    records = []
    for size in sorted(images):
        image = images[size]
        if image.size != (size, size) or not 1 <= size <= 256:
            raise ValueError('bad icon image %d' % size)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG', optimize=True)
        records.append((size, buffer.getvalue()))
    offset = 6 + 16 * len(records)
    head, body = bytearray(struct.pack('<HHH', 0, 1, len(records))), bytearray()
    for size, data in records:
        head += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        body += data
        offset += len(data)
    path.write_bytes(bytes(head + body))


def glow_background(width, height, arc=True):
    """Dark background with soft cyan, purple and magenta glows; optionally one faint arc that echoes the
    symbol's dome (its colours running cyan to magenta like the dome's)."""
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    scale = float(max(width, height))
    image = np.zeros((height, width, 3), np.float32) + np.array(BACKGROUND, np.float32)
    for (cx, cy), radius, colour, strength in (((0.08, 0.10), 0.55, CYAN, 0.28), ((0.92, 0.06), 0.45, PURPLE, 0.30),
                                               ((0.86, 0.98), 0.50, MAGENTA, 0.24), ((0.50, 1.05), 0.40, BLUE, 0.10)):
        distance = np.hypot(x - cx * width, y - cy * height) / (radius * scale)
        weight = (np.clip(1.0 - distance, 0.0, 1.0) ** 2.2) * strength
        image += weight[..., None] * (np.array(colour, np.float32) - np.array(BACKGROUND, np.float32))
    picture = Image.fromarray(np.clip(image, 0, 255).astype(np.uint8), 'RGB')
    if arc:
        mask = Image.new('L', (width, height), 0)
        draw = ImageDraw.Draw(mask)
        thickness = max(3, round(scale * 0.006))
        radius = scale * 0.55
        centre = (width * 0.78, height * 1.10)
        draw.arc((centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius), 200, 340,
                 fill=255, width=thickness)
        t = np.clip((x - (centre[0] - radius)) / (2 * radius), 0, 1)[..., None]
        colours = (1 - t) * np.array(CYAN, np.float32) + t * np.array(MAGENTA, np.float32)
        layer = Image.fromarray(colours.astype(np.uint8), 'RGB').convert('RGBA')
        layer.putalpha(mask.filter(ImageFilter.GaussianBlur(thickness * 0.5)).point(lambda v: v * 0.20))
        picture = Image.alpha_composite(picture.convert('RGBA'), layer).convert('RGB')
    # A little noise so that JPEG compression does not band the gradients.
    noise = np.random.default_rng(5).normal(0, 1.2, (height, width, 1)).astype(np.float32)
    return Image.fromarray(np.clip(np.asarray(picture, np.float32) + noise, 0, 255).astype(np.uint8), 'RGB')


def logo_image(height=None, width=None, variant='dark'):
    """The stacked logo (transparent), cropped to its content, at a height or width."""
    box = symbol.LOGO_BOX
    if height:
        width = round(box[2] * height / box[3])
    else:
        height = round(box[3] * width / box[2])
    logo = render_svg(symbol.logo(variant), width, height)
    return logo.crop(logo.getchannel('A').getbbox())


def place(background, image, centre):
    canvas = background.convert('RGBA')
    canvas.alpha_composite(image, (round(centre[0] - image.width / 2), round(centre[1] - image.height / 2)))
    return canvas.convert('RGB')


def preview(path):
    """Every icon size on a dark and a light background, actual size and enlarged, with the tray icons."""
    sizes = (16, 20, 22, 24, 32, 40, 48, 64)
    rows = []
    for mode, back in (('colour', (20, 22, 30)), ('colour', (242, 242, 245)), ('white', (32, 32, 36)), ('black', (226, 226, 230))):
        icons = [app_icon(size, mode) for size in sizes]
        row = Image.new('RGBA', (sum(s + 14 for s in sizes) + sum(s * 4 + 14 for s in sizes[:5]) + 14, 64 * 2 + 28), back + (255,))
        x = 14
        for size, icon in zip(sizes, icons):
            row.alpha_composite(icon, (x, 14 + (64 - size) // 2))
            x += size + 14
        for size, icon in zip(sizes[:5], icons[:5]):
            big = icon.resize((size * 4, size * 4), Image.Resampling.NEAREST)
            row.alpha_composite(big, (x, 14))
            x += size * 4 + 14
        rows.append(row)
    sheet = Image.new('RGBA', (rows[0].width, sum(r.height for r in rows)), (0, 0, 0, 255))
    top = 0
    for row in rows:
        sheet.alpha_composite(row, (0, top))
        top += row.height
    sheet.convert('RGB').save(path, optimize=True)


def main():
    SVG.mkdir(parents=True, exist_ok=True)
    for mode, name in (('colour', 'vrostbridge-mark.svg'), ('white', 'vrostbridge-mark-white.svg'), ('black', 'vrostbridge-mark-black.svg')):
        (SVG / name).write_text(symbol.svg(mode), encoding='utf-8')
    (SVG / 'vrostbridge-app.svg').write_text(app_svg(), encoding='utf-8')
    (SVG / 'vrostbridge-logo.svg').write_text(symbol.logo('dark'), encoding='utf-8')
    (SVG / 'vrostbridge-logo-light.svg').write_text(symbol.logo('light'), encoding='utf-8')
    (SVG / 'vrostbridge-wordmark-inline.svg').write_text(symbol.wordmark_inline('dark'), encoding='utf-8')
    (SVG / 'vrostbridge-wordmark-inline-light.svg').write_text(symbol.wordmark_inline('light'), encoding='utf-8')
    exports = BRAND / 'assets' / 'logo'
    exports.mkdir(parents=True, exist_ok=True)
    logo_image(width=1200).save(exports / 'vrostbridge-logo.png', optimize=True)
    logo_image(width=1200, variant='light').save(exports / 'vrostbridge-logo-light.png', optimize=True)
    x0, y0, x1, y1 = symbol.bounds()
    render_svg(symbol.svg(), 1024, round(1024 * (y1 - y0) / (x1 - x0))).save(exports / 'vrostbridge-mark.png', optimize=True)
    (BRAND / 'previews').mkdir(exist_ok=True)
    preview(BRAND / 'previews' / 'vrostbridge-icons.png')

    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SVG / 'vrostbridge-app.svg', OUT / 'vrostbridge.svg')
    shutil.copyfile(SVG / 'vrostbridge-mark.svg', OUT / 'vrostbridge-mark.svg')
    shutil.copyfile(SVG / 'vrostbridge-logo.svg', OUT / 'vrostbridge-logo.svg')
    shutil.copyfile(SVG / 'vrostbridge-wordmark-inline.svg', OUT / 'vrostbridge-name.svg')
    metrics = symbol.inline_metrics()
    height = round(96 / metrics['cap'])
    render_svg(symbol.wordmark_inline('dark'), round(height * metrics['aspect']), height).save(OUT / 'vrostbridge-name.png',
                                                                                               optimize=True)
    for size in (256, 512):
        app_icon(size).save(OUT / ('vrostbridge-%d.png' % size), optimize=True)
    write_ico({size: app_icon(size) for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)}, OUT / 'vrostbridge.ico')
    for variant, mode in refine.TRAY.items():
        write_ico({size: app_icon(size, mode) for size in (16, 20, 22, 24, 32, 40, 48, 64)}, OUT / ('vrostbridge-tray-%s.ico' % variant))
    write_ico({size: app_icon(size) for size in (16, 32, 48)}, OUT / 'favicon.ico')
    app_icon(32).save(OUT / 'favicon-32.png', optimize=True)

    hicolor = OUT / 'hicolor'
    if hicolor.exists():
        shutil.rmtree(hicolor)
    for size in (16, 22, 24, 32, 48, 64, 96, 128, 192, 256, 512):
        target = hicolor / ('%dx%d' % (size, size)) / 'apps' / 'vrostbridge.png'
        target.parent.mkdir(parents=True)
        app_icon(size).save(target, optimize=True)
    (hicolor / 'scalable' / 'apps').mkdir(parents=True)
    shutil.copyfile(SVG / 'vrostbridge-app.svg', hicolor / 'scalable' / 'apps' / 'vrostbridge.svg')

    steam = OUT / 'steam'
    steam.mkdir(exist_ok=True)
    place(glow_background(600, 900), logo_image(width=420), (300, 430)).save(steam / 'capsule.jpg', quality=92, optimize=True)
    place(glow_background(920, 430), logo_image(height=330), (460, 215)).save(steam / 'header.jpg', quality=92, optimize=True)
    glow_background(3840, 1240).save(steam / 'hero.jpg', quality=90, optimize=True)
    title = Image.new('RGBA', (1280, 480), (0, 0, 0, 0))
    logo = logo_image(height=480)
    title.alpha_composite(logo, (0, 480 - logo.height))  # Steam pins it bottom-left over the hero
    title.save(steam / 'logo.png', optimize=True)
    app_icon(256).save(steam / 'icon.png', optimize=True)

    hud = ROOT / 'client' / 'assets' / 'hud' / 'logo-192.rgba'
    hud.write_bytes(app_icon(192).tobytes())
    glow_background(1920, 1200).save(ROOT / 'launcher' / 'game_ui_assets' / 'background.jpg', quality=90, optimize=True)
    social = BRAND / 'social-preview.png'
    place(glow_background(1280, 640), logo_image(height=470), (640, 320)).save(social, optimize=True)
    for path in sorted(SVG.iterdir()) + sorted(exports.iterdir()) + sorted(OUT.rglob('*')) + [
            hud, ROOT / 'launcher' / 'game_ui_assets' / 'background.jpg', social, BRAND / 'previews' / 'vrostbridge-icons.png']:
        if path.is_file():
            print(path.relative_to(ROOT), path.stat().st_size)


if __name__ == '__main__':
    main()
