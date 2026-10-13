# Implementation and notice references

These sources support platform integration and the distinction between independent branding and third-party rights. They do not establish trademark clearance for VROSTBridge or its logo.

1. Microsoft Learn — Construct your Windows app's icon. Multi-size application-icon guidance, including 16, 24, 32, 48 and 256 px.  
   https://learn.microsoft.com/en-us/windows/apps/design/iconography/app-icon-construction
2. Microsoft Learn — About Icons. ICO/icon resources can contain multiple sizes and RGBA imagery.  
   https://learn.microsoft.com/en-us/windows/win32/menurc/about-icons
3. freedesktop.org — Icon Theme Specification. Icon lookup, hicolor installation, raster/scalable assets.  
   https://specifications.freedesktop.org/icon-theme/latest/
4. freedesktop.org — Desktop Entry Specification. Desktop-entry fields, icon names and application launch metadata.  
   https://specifications.freedesktop.org/desktop-entry-spec/latest/
5. Valve — Steam Branding Guidelines. Rules for uses of Steam branding, including avoiding implied affiliation/endorsement. This is not a general licence to use Valve marks.  
   https://partner.steamgames.com/doc/marketing/branding
6. UK Government — Using somebody else's intellectual property. General rights/infringement guidance.  
   https://www.gov.uk/using-somebody-elses-intellectual-property
7. UK Government — Search for a trade mark. Identical/similar-name and image-search entry points. No register clearance has been done for this logo.  
   https://www.gov.uk/search-for-trademark

## Asset provenance

- `source/vrostbridge-icon.png` and `source/vrostbridge-logo.png` are the symbol and the stacked logo the maintainer supplied on 5 October 2026. They are kept unmodified. A brand board supplied with them is not kept; its palette is recorded in `brand.json`.
- The symbol's vectors were rebuilt to line up with `source/vrostbridge-icon.png`: a symmetric geometric model (dome, cube, gaps, corner rounding) fitted by least squares to the picture's edges, and each part's gradient measured from its colours (`tools/vrostbridge_symbol.py`). No claim of pixel-identical reproduction is made.
- The wordmark's outlines were traced from `source/vrostbridge-logo.png` with Potrace 1.16 (`tools/trace_wordmark.py`); no font files are distributed.
- The 16 to 32 px icons fit the same symbol to the pixel grid (`tools/refine_small_icons.py`); nothing is redrawn by hand.
- The previous VROSTBridge symbol (a cube and a rectangle joined by two links) and its asset pack were removed on 5 October 2026, before the first public release.
- No Valve/Index official logo assets, stock icons or third-party font files are included.
- No software licence, exclusive trademark rights, trademark registration or legal clearance is granted or asserted by these provenance notes.
