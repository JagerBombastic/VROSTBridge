# Display UI validation — 0.2.13

The two `ui-0.2.13-gamemode-*.png` images use the actual SDL renderer with a synthetic Beyond 2e status fixture at 1280 × 800. They verify label layout, font/icon loading, and the separate Game Mode display page. They are not hardware, transport or performance evidence.

The display page and main page were visually inspected: labels fit their buttons, the detected model and active rate are visible, and render size is separate from stream-budget descriptions. The standard desktop JavaScript DOM suite also verifies detected 75 Hz / 3560 × 3560 render information, stopped-session freshness labeling, and physical-mode limits.

A new desktop browser screenshot was not available in this environment: Playwright has no bundled browser here, and its browser download returned an unusable archive. The existing desktop layout is retained, with added controls and ordinary wrapping text; automated DOM/action checks pass. No browser screenshot from an older release is presented as current verification.
