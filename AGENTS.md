# AGENTS.md — Lumen (local-drive-first media collaboration MVP)

This file is loaded on every turn by Antigravity (and read by any other agent).
It is the working contract for this repository. Read it before acting.

## What this product is

A local-drive-first media collaboration platform for small production teams
(Addis Ababa pilot): originals stay on the team's own drives, the catalogue is a
searchable SQLite index, reviewers use accountless links, and notes become NLE
markers. Hosts/editors are macOS and Windows; NLEs are Adobe Premiere Pro and
DaVinci Resolve.

Python 3.10+ standard library only at runtime. No paid service, no cloud media
storage, no required AI API. Tests are stdlib `unittest`.

## Hard rules

1. **The blueprint is the source of truth and is read-only.**
   `uploads/Local_First_Media_Collaboration_MVP_Blueprint (2).md` must never be
   edited, moved, reformatted or deleted. Implementation notes, decisions and
   deviations go in `docs/` — never back into the blueprint.
2. **Never read, copy, upload, print or "sample" footage, proxies, thumbnails or
   any file under a registered root** (`data/`, `data/sample_drive/`, or any
   path added via `add-root`). The catalogue DB is also off-limits to summarise.
   You may read *counts* and *field names* through the API, never media bytes.
   No external request may include media, face data, transcripts or project
   metadata. There is no exception for "just to test".
3. **$0 spend.** Do not introduce a paid platform service, managed DB, host,
   relay, email, storage, monitoring, domain or AI inference as a *required*
   dependency. Do not add billing details, paid plans or metered services.
   Any new dependency needs an entry in `docs/COSTS.md` citing current official
   pricing/terms, plus a free/self-hosted fallback. If a service's free tier has
   a non-commercial clause (e.g. Vercel Hobby), say so instead of using it for
   client work.
4. **Never fake an integration.** No stubbed WebDAV `LOCK`/`PUT`, no invented NLE
   "collaboration" support, no simulated success. If something is unverified or
   blocked for a user (e.g. missing Adobe entitlement), mark it
   **blocked/unverified** in docs and UI copy. Label mocks as mocks.
5. **Never present a demo as the product.** `demo-static/`, `LUMEN-DEMO.html`
   and any hosted snapshot are read-only captures with fixture data: keep the
   "read-only snapshot" banner, keep write-refusal responses, and never describe
   them as the private/customer-controlled residency mode.
6. **Don't touch git history or other people's work.** No force-push, no
   `reset --hard` on committed work, no deleting `docs/`. Small commits with a
   plain message. Keep secrets out of the repo and out of chat.

## How to work in this repo

- Inspect first: read `docs/ARCHITECTURE.md`, `docs/PROGRESS.md`,
  `docs/COSTS.md`, `docs/RISKS.md` before proposing changes.
- Slice work: one small vertical slice at a time, then run the tests.
- Run tests after every slice, before every commit:

  ```
  python3 -m unittest discover -s tests -t .        # expect 103 tests, all OK
  ```

- UI changes: also run the headless smoke test (needs a dev-only jsdom install
  and a running server):

  ```
  npm install --no-save jsdom
  python3 -u -m lfmc.cli serve --host 0.0.0.0 --port 8765 --demo &
  node tools/ui_smoke.js
  ```

- Update `docs/PROGRESS.md` when a gate or slice moves; record *why* in
  `docs/ARCHITECTURE.md` (decision log). State plainly what is verified vs
  assumed vs untestable on this machine.
- Things you cannot verify here: macOS/Windows mounts, FUSE/WinFsp, real
  Premiere/Resolve behaviour, real codecs (no ffmpeg). Do not claim otherwise;
  add them to the "Requires your machines" list instead.

## Layout you should know

| Path | What it is |
|---|---|
| `lfmc/core/` | db schema, scanner, identity/fingerprints, catalogue browse+search, review (comments/links/selects/markers) |
| `lfmc/net/` | threaded HTTP server, auth, `/media` `/preview` `/api/*` `/dav/*` `/r/{token}` `/api/guest/*`, read-only WebDAV façade |
| `lfmc/nle/` | NLE registry + residency modes (authoritative project state stays with the team) |
| `lfmc/web/` | `index.html` (Shade-style app shell), `guest.html` (accountless review page) |
| `tools/` | dev-only: export/pack/minify the static demo, jsdom smoke tests |
| `docs/` | architecture, costs, risks, UI notes, NLE log, progress |
| `tests/` | stdlib unittest suite |

Gotchas that have already bitten us — do not "fix" them back:

- In `lfmc/net/server.py`, specific `/api/assets/*` routes must be registered
  **above** the generic `_asset_detail` route or review routes get shadowed.
- Guest payloads must not leak the root label/id or file timestamps (there is a
  regression test; keep it).
- WebDAV hrefs store raw paths and are percent-encoded **once** at render time.
- `pkill -f "lfmc.cli serve"` kills the shell you are running in; stop servers by
  PID/process handle instead.
- Use `python3 -u` for long-running output; stdout is block-buffered otherwise.

## Style

- Match the surrounding code: small modules, explicit names, no clever
  one-liners, docstrings that state the *why*.
- Comments explain intent, not syntax. Never leave "TODO: implement" in a
  shipped path — either implement it or label it unverified in docs.
- New user-visible copy: plain language, no marketing claims, no invented
  statistics, no Shade Inc. wording, logos or assets.
