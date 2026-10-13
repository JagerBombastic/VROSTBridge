# VROSTBridge brand assets

**VR outside the box.** *Think outside the box. Render there too.*

This folder holds the VROSTBridge name and logo as the maintainer supplied them, so they are kept in one
place and the application's artwork can be rebuilt from them. `brand.json` records the spelling, the
meaning of the name, the descriptor and the palette.

| Name | Use |
| --- | --- |
| **VROSTBridge** | The project, in prose, titles and metadata (not "VROSTBridge VR", no ® or ™) |
| **VROSTBridge Host** / **VROSTBridge Client** | The two programs (rendering PC / headset PC) |
| *VR Rendering OutSide The Box – Bridge* | What the name stands for; shown under the name on the pages and in Game Mode, with V, R (of Rendering), O, S, T and B (of Box) in the palette's colours and every letter of *Bridge* in the brand gradient (from 1.0.0.1) |
| *An open-source remote PCVR bridge.* | Descriptor |

## The logo

An isometric cube in front of a dome. The dome is cut flat at its base and cut back around the cube, and the
cube's three faces are separated by gaps; the gaps are empty, so the background shows through them. The colours
run from cyan and blue on the left to purple and magenta on the right. The wordmark is *VROST* in a wide, bold
geometric face with *BRIDGE* spaced out beneath it; the name is still written VROSTBridge.

**On one line (from 1.0.0.3).** The pages' and Game Mode's headers show the name beside the symbol on one line:
*VROST* as in the logo, then *Bridge* with a capital B and lower-case *ridge*, in the logo's light grey-blue, its
capitals as tall as VROST's and on the same baseline. The logo's BRIDGE has capitals only, so *Bridge* is set in
Montserrat (weight 400, 0.1 em of extra letter spacing), the open font closest to the logo's BRIDGE letters, and kept
as outlines: no font is included, and the SIL Open Font License does not apply to artwork made with a font.
*VR Rendering OutSide The Box – Bridge* stays under it, unchanged.

| Folder or file | Contents |
| --- | --- |
| `source/vrostbridge-icon.png`, `source/vrostbridge-logo.png` | The symbol and the stacked logo as the maintainer supplied them (1254 × 1254), unchanged |
| `assets/svg/` | Vector masters: the symbol in colour, white and black (`vrostbridge-mark*.svg`), the symbol in a square (`vrostbridge-app.svg`), the stacked logo for dark and for light backgrounds (`vrostbridge-logo*.svg`), the traced wordmark (`vrostbridge-wordmark.svg`), the wordmark on one line for dark and for light backgrounds (`vrostbridge-wordmark-inline*.svg`) and *Bridge* as outlines (`vrostbridge-bridge.svg`) |
| `assets/logo/` | PNG exports of the logo (dark and light backgrounds) and the symbol |
| `previews/vrostbridge-icons.png` | Every icon size, actual size and enlarged, in colour and one colour, on dark and light |
| `tools/` | `vrostbridge_symbol.py` (the symbol, the logo and the one-line wordmark as vectors), `trace_wordmark.py` (traces the wordmark), `set_bridge.py` (sets *Bridge* in Montserrat as outlines; needs the font file it names), `refine_small_icons.py` (16 to 32 px, below) |
| `docs/SOURCES.md` | Platform guidance followed and how the vectors were made |
| `social-preview.png` | The GitHub repository's social preview image (1280 × 640) |

**How the vectors were made.** The supplied pictures are raster images, so the symbol was rebuilt as vectors
that line up with them: `tools/vrostbridge_symbol.py` holds the shapes' measurements, fitted to
`source/vrostbridge-icon.png` (straight edges, a true circle, the cube symmetric with even gaps; 99.4 %
overlap with the picture), and the colours measured from it along each part's gradient. The wordmark is traced
from `source/vrostbridge-logo.png` with Potrace (`tools/trace_wordmark.py`), so no font is included. Nothing
is redrawn by hand.

**Small sizes.** Simply scaled down, the gaps (half a pixel at 16 px) blur and the cube runs into the dome. So
the 16, 20, 22, 24 and 32 px icons, app and tray, come from `tools/refine_small_icons.py`: the same symbol with
one-pixel gaps, the cube's centre gap on one pixel column and its edges and the dome's base on pixel edges.
From 40 px the vector is used as it is.

## How the application uses it

`scripts/build_brand_assets.py` builds the runtime artwork from this folder (`rsvg-convert`, Pillow, numpy;
build time only) and the results are committed:

- `launcher/brand/`: the app icon (`vrostbridge.svg`, PNGs), the symbol and the one-line wordmark for the page
  header (`vrostbridge-name.svg`; `vrostbridge-name.png` for Game Mode's header), the logo for About, the Windows icon `vrostbridge.ico`, the tray icons (`vrostbridge-tray-light.ico` for dark taskbars,
  `vrostbridge-tray-dark.ico` for light ones), favicons, the Linux icon theme (`hicolor/`, installed as
  `vrostbridge`) and the Steam library artwork (`steam/`).
- `client/assets/hud/logo-192.rgba`: the symbol on the in-headset cards.
- `launcher/game_ui_assets/background.jpg`: the Game Mode screen's background.
- `brand/social-preview.png` and the exports above.

Never squeeze the whole logo with its wordmark into an icon, crop the dome or the cube, fill in the gaps, or
use colour alone to show a state or a role.

## Status

No trademark registration, clearance or vendor approval is claimed for the name or the logo. The code's
MIT licence is in [LICENSE](../LICENSE); this artwork is © 2026 JagerBombastic under CC BY-ND 4.0
([LICENSE.md](LICENSE.md)): share it unchanged, with credit; a fork uses its own name and logo.
