---
trigger: glob
globs: "lfmc/web/*.html, docs/UI.md"
description: "Look-and-feel and interaction rules for the Shade-style application shell and the guest review page."
---

# UI: Shade as a reference, never as a source

The interaction model we mirror: drive tree + asset grid + a right-hand
inspector with **Details / Comments / Transcript / Similar**, threaded comments
with timecodes and ranges, multiple share links, version stacking, and "send
notes to the NLE as markers".

Rules:

1. **No copying.** Do not reproduce Shade Inc.'s marketing copy, claims, logos,
   screenshots or image assets. Structure and interaction patterns are fine;
   text and brand assets are not. Write our own labels.
2. **Single self-contained file.** `lfmc/web/index.html` is one file with inline
   CSS and data-URI thumbnails — the workspace preview has no network, so no
   CDN fonts, scripts, stylesheets or remote images. Same for `guest.html`.
3. **Honest states.** Availability must be visible and truthful
   (original online vs catalogue-only vs offline). No fake progress bars, no
   invented counts, no spinner that hides a real error — show the error text.
4. **The guest page is the accountless one.** It must never expose the root
   label/id, host paths, internal comments, or other assets. Keep that
   regression test green; add to it whenever the payload grows.
5. **Phone first for review.** Tap targets ≥ 44 px, comments readable at 360 px
   wide, timecodes tappable to seek.
6. Keep `tools/ui_smoke.js` passing (17 assertions) after any DOM change, and
   update its selectors deliberately rather than loosening assertions.
