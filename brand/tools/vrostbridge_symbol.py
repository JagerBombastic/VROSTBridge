#!/usr/bin/env python3
"""The VROSTBridge symbol (a cube in front of a dome) as clean vector artwork.

The maintainer supplied the logo as raster images (brand/source/). This module rebuilds the symbol from them
with straight edges and true circles so it stays sharp at every size:
- GEOMETRY was fitted to brand/source/vrostbridge-icon.png (1254 x 1254) by least squares on its edges
  (left-right symmetric cube with one slope, even gaps; the dome on the cube's axis). It is in that image's
  pixels, so the vector lines up with the supplied picture (overlap 99.4 %).
- GRADIENTS were measured from the same image: for each part, the direction that best explains its colours and
  the colours along it.
Nothing is redrawn by hand. The cube's three faces are separated by gaps and the dome is cut around the cube;
the gaps are transparent, so the symbol works on any background.

    python3 brand/tools/vrostbridge_symbol.py OUTPUT.svg [colour|white|black]
"""
import math
import re
import sys
from pathlib import Path

BRAND = Path(__file__).resolve().parents[1]

# Fitted to brand/source/vrostbridge-icon.png (pixels). The cube: apex (cx, yT), half-width a, edge slope tan,
# vertical edges e; g: gap between faces; b: band between the cube and the dome (corner radius rc); rf: face
# corner radius. The dome: a circle about (cx, cyd) of radius R, cut flat at y = yb.
GEOMETRY = {'cx': 626.99, 'yT': 431.12, 'a': 223.79, 'tan': 0.5952, 'e': 249.33, 'g': 25.71, 'b': 29.63,
            'rf': 1.0, 'rc': 15.52, 'cyd': 644.14, 'R': 396.30, 'yb': 683.40}

# Measured from the same picture: direction (degrees, x right, y down), the range of x cos + y sin it spans,
# and colours along it (offset within that range, colour; lightly smoothed).
GRADIENTS = {
    'dome': (38.0, 497.2, 1215.8, [(0.021, '#00EDFD'), (0.104, '#00D1FD'), (0.188, '#00ACFD'), (0.271, '#0184FC'), (0.354, '#1C57FC'),
                                   (0.438, '#5E32FB'), (0.521, '#9E1EFC'), (0.604, '#CF0FFB'), (0.688, '#F005F4'), (0.771, '#FB06EA'),
                                   (0.854, '#FC0EDE'), (0.979, '#FD2DB7')]),
    'top': (38.0, 674.2, 997.1, [(0.021, '#01F1FD'), (0.104, '#00EFFD'), (0.229, '#00DEFD'), (0.354, '#00C1FD'), (0.479, '#01A3FD'),
                                 (0.604, '#0486FC'), (0.729, '#1F62FC'), (0.854, '#623EFC'), (0.979, '#A928FD')]),
    'left': (42.0, 696.7, 1074.1, [(0.021, '#01ECFC'), (0.104, '#00E5FD'), (0.229, '#00C6FD'), (0.354, '#00A4FD'), (0.479, '#0080FC'),
                                   (0.604, '#015DFC'), (0.729, '#033BFC'), (0.854, '#0618FB'), (0.979, '#1204FB')]),
    'right': (2.0, 669.2, 872.8, [(0.021, '#AA04FB'), (0.104, '#B803FC'), (0.229, '#D403FB'), (0.354, '#EE03F9'), (0.479, '#FB03F1'),
                                  (0.604, '#FC0CE4'), (0.729, '#FC1DD1'), (0.854, '#FC2FBF'), (0.979, '#FC3DAE')]),
}

MONO = {'white': '#FFFFFF', 'black': '#000000'}

# The stacked logo (brand/source/vrostbridge-logo.png): where the symbol sits in that picture (scale, then offset,
# from GEOMETRY's units to its pixels; fitted like GEOMETRY), and the wordmark traced from it
# (tools/trace_wordmark.py). The light variant is for light backgrounds.
LOGO_PLACEMENT = (0.7561, 152.41, 54.30)
LOGO_BOX = (157, 223, 938, 808)  # the logo's extent in that picture, with a margin
WORDMARK = BRAND / 'assets' / 'svg' / 'vrostbridge-wordmark.svg'
WORDMARK_COLOURS = {'dark': {'vrost': '#FFFFFF', 'bridge': '#CAD0E0'}, 'light': {'vrost': '#0B0F1A', 'bridge': '#4B5468'}}
# 1.0.0.3: the wordmark on one line (the pages' and Game Mode's headers): VROST, then "Bridge" with a capital B
# (tools/set_bridge.py: Montserrat outlines, the logo's BRIDGE has capitals only), its capitals as tall as VROST's and
# on the same baseline, this far after VROST (in VROST's capital heights).
BRIDGE_WORD = BRAND / 'assets' / 'svg' / 'vrostbridge-bridge.svg'
INLINE_GAP = 0.5


def _offset(points, d):
    """Offsets a convex polygon (clockwise on screen) outward by d (inward if negative); sharp corners."""
    n = len(points)
    lines = []
    for i in range(n):
        (px, py), (qx, qy) = points[i], points[(i + 1) % n]
        length = math.hypot(qx - px, qy - py)
        tx, ty = (qx - px) / length, (qy - py) / length
        nx, ny = ty, -tx  # outward for clockwise-on-screen order (y down)
        lines.append(((px + nx * d, py + ny * d), (tx, ty)))
    out = []
    for i in range(n):
        ((ax, ay), (atx, aty)), ((bx, by), (btx, bty)) = lines[i - 1], lines[i]
        det = atx * (-bty) - aty * (-btx)
        s = ((bx - ax) * (-bty) - (by - ay) * (-btx)) / det
        out.append((ax + atx * s, ay + aty * s))
    return out


def _clockwise(points):
    area = sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points)))
    return points if area > 0 else points[::-1]


def _rounded(points, r):
    """Path data for a convex polygon with every corner rounded by radius r (r must fit the corners)."""
    pts = _clockwise(points)
    inner = _offset(pts, -r) if r > 0 else pts
    n = len(inner)
    segs = []
    for i in range(n):
        (px, py), (qx, qy) = inner[i], inner[(i + 1) % n]
        length = math.hypot(qx - px, qy - py)
        nx, ny = (qy - py) / length, -(qx - px) / length
        segs.append(((px + nx * r, py + ny * r), (qx + nx * r, qy + ny * r)))
    d = 'M%s' % _xy(segs[0][0])
    for i in range(n):
        d += 'L%s' % _xy(segs[i][1])
        if r > 0:
            d += 'A%s %s 0 0 1 %s' % (_n(r), _n(r), _xy(segs[(i + 1) % n][0]))
    return d + 'Z'


def _n(value):
    text = ('%.2f' % value).rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def _xy(point):
    return _n(point[0]) + ' ' + _n(point[1])


def corners(v=None):
    """The cube's outline (hexagon) and its centre: T (top), UL, LL, B (bottom), LR, UR, C."""
    v = dict(GEOMETRY, **(v or {}))
    cx, a, rise = v['cx'], v['a'], v['a'] * v['tan']
    return {'T': (cx, v['yT']), 'UL': (cx - a, v['yT'] + rise), 'UR': (cx + a, v['yT'] + rise), 'C': (cx, v['yT'] + 2 * rise),
            'LL': (cx - a, v['yT'] + rise + v['e']), 'LR': (cx + a, v['yT'] + rise + v['e']), 'B': (cx, v['yT'] + 2 * rise + v['e'])}


def faces(v=None):
    """The three faces as polygons: the outline's edges kept, the inner edges moved back by half the gap."""
    v = dict(GEOMETRY, **(v or {}))
    k = corners(v)
    half = v['g'] / 2
    out = {}
    for name, ring, outer in (('top', ['T', 'UR', 'C', 'UL'], {('T', 'UR'), ('UL', 'T')}),
                              ('left', ['UL', 'C', 'B', 'LL'], {('B', 'LL'), ('LL', 'UL')}),
                              ('right', ['UR', 'LR', 'B', 'C'], {('UR', 'LR'), ('LR', 'B')})):
        pts = [k[p] for p in ring]
        if sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1] for i in range(4)) < 0:
            raise AssertionError('face order')
        lines = []
        for i in range(4):
            (px, py), (qx, qy) = pts[i], pts[(i + 1) % 4]
            length = math.hypot(qx - px, qy - py)
            tx, ty = (qx - px) / length, (qy - py) / length
            nx, ny = ty, -tx
            d = 0.0 if (ring[i], ring[(i + 1) % 4]) in outer else half
            lines.append(((px - nx * d, py - ny * d), (tx, ty)))
        poly = []
        for i in range(4):
            ((ax, ay), (atx, aty)), ((bx, by), (btx, bty)) = lines[i - 1], lines[i]
            det = atx * (-bty) - aty * (-btx)
            s = ((bx - ax) * (-bty) - (by - ay) * (-btx)) / det
            poly.append((ax + atx * s, ay + aty * s))
        out[name] = poly
    return out


def dome(v=None):
    """Path data for the dome: the circle cut flat at yb, with the cube's outline (grown by b, corners
    rounded by rc) cut out of it."""
    v = dict(GEOMETRY, **(v or {}))
    k = corners(v)
    cx, cyd, R, yb, b, rc = v['cx'], v['cyd'], v['R'], v['yb'], v['b'], v['rc']
    half = math.sqrt(R * R - (yb - cyd) ** 2)
    hexagon = [k['T'], k['UR'], k['LR'], k['B'], k['LL'], k['UL']]  # clockwise on screen
    grown = _offset(hexagon, b - rc)  # sharp outline; the arcs below round it by rc
    T, UR, LR, _, LL, UL = grown
    side_r, side_l = UR[0] + rc, UL[0] - rc

    def unit(p, q):
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        return (q[0] - p[0]) / length, (q[1] - p[1]) / length

    def normal(p, q):  # outward normal of the edge p -> q of the clockwise outline
        tx, ty = unit(p, q)
        return ty, -tx

    def at(p, n):
        return p[0] + n[0] * rc, p[1] + n[1] * rc

    n_ul_t, n_t_ur, n_ur_lr, n_ll_ul = normal(UL, T), normal(T, UR), normal(UR, LR), normal(LL, UL)
    d = 'M%s' % _xy((cx - half, yb))
    d += 'A%s %s 0 1 1 %s' % (_n(R), _n(R), _xy((cx + half, yb)))       # over the top, left to right
    d += 'L%s' % _xy((side_r, yb))                                      # base, right part
    d += 'L%s' % _xy(at(UR, n_ur_lr))                                   # up the cut's right side
    d += 'A%s %s 0 0 0 %s' % (_n(rc), _n(rc), _xy(at(UR, n_t_ur)))       # round the right shoulder
    d += 'L%s' % _xy(at(T, n_t_ur))
    d += 'A%s %s 0 0 0 %s' % (_n(rc), _n(rc), _xy(at(T, n_ul_t)))        # round the apex
    d += 'L%s' % _xy(at(UL, n_ul_t))
    d += 'A%s %s 0 0 0 %s' % (_n(rc), _n(rc), _xy(at(UL, n_ll_ul)))      # round the left shoulder
    d += 'L%s' % _xy((side_l, yb))                                      # down the cut's left side
    return d + 'Z'


def bounds(v=None):
    """The symbol's extent (x0, y0, x1, y1) in GEOMETRY's units."""
    v = dict(GEOMETRY, **(v or {}))
    # The dome is cut below its centre, so it is widest at the centre's height.
    half = v['R'] if v['yb'] >= v['cyd'] else math.sqrt(v['R'] ** 2 - (v['yb'] - v['cyd']) ** 2)
    bottom = max(y for poly in faces(v).values() for _, y in poly)
    return v['cx'] - half, v['cyd'] - v['R'], v['cx'] + half, bottom


def gradient(name, ident):
    angle, t0, t1, stops = GRADIENTS[name]
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    stop = ''.join('<stop offset="%s" stop-color="%s"/>' % (_n(offset), colour) for offset, colour in stops)
    return ('<linearGradient id="%s" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" x2="%s" y2="%s">%s</linearGradient>'
            % (ident, _n(t0 * c), _n(t0 * s), _n(t1 * c), _n(t1 * s), stop))


def body(mode='colour', v=None, prefix='vb'):
    """The symbol's <defs> and shapes (no <svg> element), in GEOMETRY's units."""
    v = dict(GEOMETRY, **(v or {}))
    parts = [('dome', dome(v))] + [(name, _rounded(poly, v['rf'])) for name, poly in faces(v).items()]
    if mode in MONO:
        return ''.join('<path d="%s" fill="%s"/>' % (d, MONO[mode]) for _, d in parts)
    defs = '<defs>' + ''.join(gradient(name, '%s-%s' % (prefix, name)) for name, _ in parts) + '</defs>'
    return defs + ''.join('<path d="%s" fill="url(#%s-%s)"/>' % (d, prefix, name) for name, d in parts)


def svg(mode='colour', box=None, size=None, v=None, title='VROSTBridge', background=None):
    """A complete SVG. box: the viewBox (x, y, w, h) in GEOMETRY's units (default: the symbol's extent);
    size: width and height attributes (pixels); background: a colour behind the symbol."""
    x0, y0, x1, y1 = bounds(v)
    box = box or (x0, y0, x1 - x0, y1 - y0)
    attrs = 'viewBox="%s"' % ' '.join(_n(n) for n in box)
    if size:
        attrs += ' width="%s" height="%s"' % (_n(size[0]), _n(size[1]))
    back = '<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>' % (_n(box[0]), _n(box[1]), _n(box[2]), _n(box[3]), background) if background else ''
    return ('<svg xmlns="http://www.w3.org/2000/svg" %s role="img"><title>%s</title>%s%s</svg>\n'
            % (attrs, title, back, body(mode, v)))


def logo(variant='dark', title='VROSTBridge'):
    """The stacked logo (symbol over the wordmark) as SVG, in the supplied picture's pixels."""
    text = WORDMARK.read_text(encoding='utf-8')
    paths = ''.join('<path d="%s" fill="%s"/>' % (d, WORDMARK_COLOURS[variant][name])
                    for name, d in re.findall(r'<path id="(\w+)" d="([^"]+)"', text))
    s, tx, ty = LOGO_PLACEMENT
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s" role="img"><title>%s</title>'
            '<g transform="matrix(%s 0 0 %s %s %s)">%s</g>%s</svg>\n'
            % (' '.join(_n(n) for n in LOGO_BOX), title, s, s, _n(tx), _n(ty), body('colour', prefix='vl'), paths))


def path_bounds(d):
    """The extent (x0, y0, x1, y1) of absolute M/L/H/V/Q/C/Z path data, the curves followed closely."""
    tokens = re.findall(r'[MLHVQCZ]|-?\d+(?:\.\d+)?', d)
    xs, ys = [], []
    i, x, y, command = 0, 0.0, 0.0, None

    def take(count):
        nonlocal i
        values = [float(v) for v in tokens[i:i + count]]
        i += count
        return values
    while i < len(tokens):
        if tokens[i] in 'MLHVQCZ':
            command = tokens[i]
            i += 1
            if command == 'Z':
                continue
        if command in 'ML':
            x, y = take(2)
        elif command == 'H':
            x, = take(1)
        elif command == 'V':
            y, = take(1)
        elif command in 'QC':
            points = take(4 if command == 'Q' else 6)
            for step in range(1, 33):
                t = step / 32
                u = 1 - t
                if command == 'Q':
                    x1, y1, x2, y2 = points
                    xs.append(u * u * x + 2 * u * t * x1 + t * t * x2)
                    ys.append(u * u * y + 2 * u * t * y1 + t * t * y2)
                else:
                    x1, y1, x2, y2, x3, y3 = points
                    xs.append(u * u * u * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3)
                    ys.append(u * u * u * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3)
            x, y = points[-2:]
        else:
            raise ValueError('unsupported path data')
        xs.append(x)
        ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _inline_parts():
    """VROST's path and extent, and Bridge's path with its placement (scale, x, y) beside it."""
    vrost = dict(re.findall(r'<path id="(\w+)" d="([^"]+)"', WORDMARK.read_text(encoding='utf-8')))['vrost']
    vx0, vy0, vx1, vy1 = path_bounds(vrost)
    cap = vy1 - vy0
    found = re.search(r'<path id="bridge" data-cap-height="(\d+)" d="([^"]+)"', BRIDGE_WORD.read_text(encoding='utf-8'))
    bridge = found.group(2)
    bx0, by0, bx1, by1 = path_bounds(bridge)
    scale = cap / float(found.group(1))
    tx, ty = vx1 + INLINE_GAP * cap - bx0 * scale, vy1  # Bridge's baseline on VROST's (the foot of its capitals)
    top, bottom = min(vy0, ty + by0 * scale), max(vy1, ty + by1 * scale)
    box = (vx0, top, tx + bx1 * scale - vx0, bottom - top)
    return vrost, bridge, (scale, tx, ty), box, (vy0, vy1)


def wordmark_inline(variant='dark', title='VROSTBridge'):
    """1.0.0.3: the wordmark on one line, for the headers: VROST as traced from the logo, then "Bridge" (capital B,
    lower-case ridge) in the logo's colours, its capitals as tall as VROST's on the same baseline. The viewBox runs
    from the top of d to the foot of g; inline_metrics() gives where VROST's capitals are in it."""
    vrost, bridge, (scale, tx, ty), box, _ = _inline_parts()
    colours = WORDMARK_COLOURS[variant]
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s" role="img"><title>%s</title><path d="%s" fill="%s"/>'
            '<path transform="matrix(%s 0 0 %s %s %s)" d="%s" fill="%s"/></svg>\n'
            % (' '.join(_n(n) for n in box), title, vrost, colours['vrost'], ('%.6f' % scale).rstrip('0'),
               ('%.6f' % scale).rstrip('0'), _n(tx), _n(ty), bridge, colours['bridge']))


def inline_metrics():
    """The one-line wordmark's width / height, and VROST's capital height and top as fractions of its height."""
    _, _, _, box, (vy0, vy1) = _inline_parts()
    return {'aspect': box[2] / box[3], 'cap': (vy1 - vy0) / box[3], 'cap_top': (vy0 - box[1]) / box[3]}


def square_box(margin=0.0, v=None):
    """A square viewBox around the symbol, the symbol centred, with margin (fraction of the side) each side."""
    x0, y0, x1, y1 = bounds(v)
    side = max(x1 - x0, y1 - y0) / (1 - 2 * margin)
    return ((x0 + x1) / 2 - side / 2, (y0 + y1) / 2 - side / 2, side, side)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    with open(sys.argv[1], 'w', encoding='utf-8') as stream:
        stream.write(svg(sys.argv[2] if len(sys.argv) > 2 else 'colour'))
