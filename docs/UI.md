# UI — what it looks like and what each screen actually does

The interface follows the **workspace model** popularised by tools like Shade:
drive tree on the left, media browser in the middle, an inspector on the right
with **Details / Comments / Transcript / Similar / Share**. Dark, low-chrome,
media-forward, one accent colour.

**It is inspired by that model, not a clone of Shade's design.** No Shade assets,
fonts, copy or code are used. The layout follows the pattern the blueprint points
at (§14 references Shade for review + AI search), and every capability shown is
either genuinely running or explicitly labelled as not built.

---

## Screen map

| Screen | Route | State | What actually happens |
|---|---|---|---|
| Browse | `#browse` | **live** | Catalogue-derived folder tiles + file cards from SQLite. Works with the drive unplugged. Search box queries the local FTS5 index. |
| Inspector → Details | right pane | **live** | Stable asset ID, path, size, fingerprint, probe state, availability, selects |
| Inspector → Comments | right pane | **live** | Threaded, timecoded comments bound to `version_label`; resolve/reopen; reactions; internal vs shared |
| Inspector → Share | right pane | **live** | Creates scoped accountless review links with expiry, passphrase, watermark, revocation |
| Guest review | `/r/<token>` | **live** | Phone-friendly, no login, one clip only, no folder tree, passphrase gate |
| Selects | `#selects` | **live** | Keep/Maybe/Reject bound to asset ID; CSV export for the editor |
| Mount | `#mount` | **live** | Read-only WebDAV facts and copy-paste steps |
| NLE projects | `#nle` | **disclosure only** | Shows the 4 adapters, marks all as unverified, lists what is unproven |
| AI index | `#ai` | **not built** | Says so plainly; lists the planned local models and what *is* live (FTS5) |
| Inspector → Transcript | right pane | **not built** | Empty state naming whisper.cpp and why it isn't running |
| Inspector → Similar | right pane | **partly** | Visual similarity needs embeddings (absent); fingerprint duplicate-finding works now |

---

## Deliberate honesty in the UI

The blueprint forbids claiming integrations that are not proven. That shows up as
concrete UI rules rather than a disclaimer at the bottom:

* The **Semantic** search toggle does not fake results. It explains that no index
  exists on this host, then lists which pipeline would serve it.
* **NLE projects** renders `unverified-needs-real-nle-test` on every adapter and
  puts "what is NOT proven" behind a disclosure, per adapter.
* The **Transcript** and **Similar** tabs are empty states that name the missing
  component. They never render invented transcript text.
* Thumbnails are clearly generated tiles ("no frame decoded"), because no ffmpeg
  is installed — they are not frames from the media.
* A clip whose drive is offline says so, and offers catalogue + comments instead
  of pretending playback is available.

---

## Design tokens

```
bg #0a0a0b · panel #141416 · line #232327
text #f2f1ee · dim #8d8c92 · dim-2 #6a6970
accent lime #c8ff4d · ok #54d17a · offline #ff6b6b · planned (dashed)
radius 14px · grid 236px | 1fr | 372px · collapses to 2 columns < 1180px, 1 < 820px
```

Everything is inline: no CDN, no web fonts, no external requests. The app works on
a machine with no internet, which is a hard requirement for the pilot.

---

## Verifying the UI

Automated (no browser needed):

```bash
npm install --no-save jsdom          # dev-only, not a product dependency
python3 -m lfmc.cli serve --port 8765 --demo &
node tools/ui_smoke.js
```

`tools/ui_smoke.js` boots the real `index.html` in jsdom against the live host and
asserts: the drive tree renders, folder tiles appear, opening a folder lists files,
the inspector populates with identity fields, comments and their timecode chips
render, the composer and share controls exist, all routes render non-empty, and the
AI/NLE screens disclose that they are unbuilt/unverified. It also fails on any JS
runtime error. 17 assertions, ~10 s.

Manual (what a browser adds that jsdom cannot): real video decode, layout at real
viewport sizes, touch gestures on a phone, and the visual result itself.

## Known gaps

* No video playback in-app — needs the ffmpeg/proxy tier.
* No real thumbnails/waveforms — same reason.
* No AI index, so the Semantic toggle is informational.
* No version stacking UI; `version_label` is stored and enforced on comments, but
  there is no v1/v2 stack switcher yet.
* Comments are not yet pushed live (no WebSocket); the team sees them on refresh
  or navigation. "Promptly", not "real time".
* Timeline feedback is CSV export only; no NLE import has been verified.
