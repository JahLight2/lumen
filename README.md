# LFMC — Local-First Media Collaboration (MVP scaffold)

A local-drive-first media collaboration platform for small production teams: originals
stay on the team's own drives, the catalogue keeps working when the drive is unplugged,
and remote editors reach media over a private path the customer controls.

**Status: milestone 1 feasibility spike.** This is real, running code — not a mockup —
but it is **not** a shippable product. No Adobe or Blackmagic integration has been
executed against a real NLE. See [`docs/PROGRESS.md`](docs/PROGRESS.md) for exactly
what is proven and what is not.

Implementing blueprint: `Local_First_Media_Collaboration_MVP_Blueprint.md` v0.5
(uploaded, unmodified, source of truth).

---

## Quick start

Requires **Python 3.11+** only. No `pip install`, no virtualenv, no network.

```bash
cd lfmc

# 1. Create a synthetic "camera drive" (~168 MB of sparse files) and catalogue it
python3 -m lfmc.cli seed-demo

# 2. (optional) add a demo review session: comment thread, selects, guest link
python3 -m lfmc.cli seed-reviews

# 3. Run the host agent + web UI
python3 -m lfmc.cli serve --port 8765
#    → open http://localhost:8765
#    → a bearer token is written to data/catalogue.token (mode 0600)

# 4. Or run without token checks — DEMO ONLY, synthetic data only
python3 -m lfmc.cli serve --port 8765 --demo
```

### Catalogue a real drive

```bash
python3 -m lfmc.cli add-root "/Volumes/CAMERA_01" --label "Camera 01" --scan
# or on Windows:  python3 -m lfmc.cli add-root "E:\\" --label "Camera 01" --scan
python3 -m lfmc.cli status
python3 -m lfmc.cli search interview
```

### Mount it as a drive (read-only)

```bash
# macOS Finder: Go > Connect to Server… → http://<host-ip>:8765/dav/
#               username: anything, password: the token from data/catalogue.token
# or:           mount_webdav http://<host-ip>:8765/dav ~/lfmc_mount
```
Windows needs two registry defaults raised first (50 MB file-size cap, Basic-over-HTTP)
— see [`docs/WEBDAV_MOUNT.md`](docs/WEBDAV_MOUNT.md).

### Prove the offline behaviour yourself

```bash
# in the UI: "Disconnect drive"  (or:)
curl -X POST localhost:8765/api/roots/<root_id>/disconnect
curl "localhost:8765/api/search?q=drone"    # still returns results
curl "localhost:8765/api/browse?root_id=<root_id>&path=02_DRONE"   # tree intact
curl -H "Range: bytes=0-9" localhost:8765/media/<asset_id>
#   → 409 original_unavailable, with an explanation — never a misleading 404
```

## Tests

```bash
cd lfmc
python3 -m unittest discover -s tests -t . -v
```

103 tests, ~8 s, no third-party test runner. They cover: range-header parsing, HTTP
206/416/401/403/409 behaviour, byte accounting, path-traversal containment, catalogue
persistence across disconnects, move/rename identity, duplicate-content safety, FTS
search, MP4 box parsing, ranged-open cost, and the NLE residency fail-closed rules.

The end-to-end spike (real HTTP range requests, measured bytes):

```bash
python3 -m lfmc.cli spike-open data/sample_drive/02_DRONE/DJI_0007.mp4 --seeks 10
# or against the running server:
curl "localhost:8765/api/spike/open?asset_id=<asset_id>&seeks=10"
```

## What is actually proven vs still open

| Area | Evidence |
|---|---|
| Catalogue survives a disconnected drive; search + folder browse work offline | `tests/test_catalogue.py::OfflineBehaviourTests` |
| An unreachable drive can never be mistaken for deleted media | `test_scan_while_offline_never_deletes` |
| Rename/move keeps the asset ID; identical duplicates do not steal one | `MoveDetectionTests`, `ScanTests` |
| Byte-range media serving with auth, containment and accounting | `tests/test_range_server.py` |
| Reading a 64 MB MP4's structure costs **80 bytes / 5 range requests** | live measurement, `spike-open` |
| NLE residency rules fail closed; no silent mode fallback | `tests/test_nle_residency.py` |
| **Originals open in Premiere / Resolve through a mount** | **NOT TESTED — needs your machines** |
| **NLE co-editing, locks, conflict recovery** | **NOT TESTED — data layer only** |
| **Review loop**: threaded timecoded comments, resolve/reactions, internal vs shared visibility, scoped accountless guest links with expiry/passphrase/revocation, selects, marker CSV | `tests/test_review.py` — 18 tests |
| **UI renders and operates** (boot, browse, inspector, tabs, all routes, no JS errors) | `tools/ui_smoke.js` — 17 assertions in jsdom |
| **Read-only WebDAV mount point** (PROPFIND, ranges, read-only refusals, offline 503, traversal, Amharic names) | `tests/test_webdav.py` — 22 tests |
| **An OS client or NLE mounting it** | **NOT TESTED — needs your Mac/Windows + NLE** (`docs/WEBDAV_MOUNT.md`) |
| **AI search, transcripts, face clustering** | **Not implemented (model licences decided first)** |
| **Review links, comments, markers, mobile selects** | **Not implemented (next slice)** |

## Safety notes

* `--demo` disables authentication. Use it only with the synthetic sample drive, never
  with real client media. Without `--demo`, every `/media/*` request needs a bearer token.
* The server binds `0.0.0.0` so you can test from another machine. On a real network,
  bind to the customer's private interface (LAN/VPN) or firewall the port.
* Tokens live in `data/catalogue.token` (mode 0600) and are never committed; `.env.example`
  shows the environment overrides. Do not paste tokens into chat or tickets.
* The current build has **no TLS**. Run it over a trusted LAN or a customer VPN until
  TLS + certificate pinning is added (next hardening slice).
* V1 is **not a backup system**. Connected drives, previews and caches are not copies.

## HTTP surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Web UI (single file, no external assets) |
| GET | `/dav/…` | **read-only WebDAV mount point** (see `docs/WEBDAV_MOUNT.md`) |
| GET | `/api/dav/info` | mount facts, including the offline-503 contract |
| GET | `/api/health` | liveness + whether demo mode is on |
| GET/POST | `/api/roots` | list roots / register a folder and scan it |
| POST | `/api/roots/{id}/scan` | incremental re-scan (offline → skipped, never deletes) |
| POST | `/api/roots/{id}/disconnect` · `/reconnect` | pause/hold sharing; catalogue retained |
| GET | `/api/browse?root_id=&path=` | catalogue-derived folder tree (works offline) |
| GET | `/api/search?q=` | FTS5 search over name/path/tags/transcript |
| GET | `/api/assets/{id}` | metadata + computed availability state |
| GET | `/media/{id}` | **read-only, Range-required** original bytes (auth) |
| GET | `/preview/{id}` | cached preview (works while the drive is offline) |
| GET | `/api/spike/open?asset_id=` | runs the ranged-open measurement against this server |
| GET | `/api/nle/capabilities` | per-NLE/per-mode matrix incl. unproven claims |
| POST | `/api/nle/preflight` | fail-closed check for an NLE binding |
| GET/POST | `/api/assets/{id}/comments` | threaded timecoded comments (internal/shared) |
| POST | `/api/comments/{id}/resolve` · `/react` | resolve or reopen · react |
| GET/POST | `/api/review-links` · `POST /api/review-links/{id}/revoke` | accountless guest links |
| GET | `/r/{token}` · `/api/guest/{token}` | guest review page + its scoped data (no auth) |
| POST | `/api/assets/{id}/select` · `GET /api/selects` | Keep/Maybe/Reject |
| GET | `/api/assets/{id}/markers.csv` | timecoded marker export (flagged unverified) |
| GET | `/api/transfers` · `/api/events` | byte accounting · audit trail |

*Transfer rows are written by the server thread after the body is streamed, so
`/api/transfers` is eventually consistent (milliseconds). Secrets, file contents and
client media never enter the log — only asset id, byte offsets, counts and outcome.*

## Documentation

- [docs/COLLAB.md](docs/COLLAB.md) — collaborator roles/scoped tokens, idempotent
  marker export, and the project hub (locks, version references, recovery).

| File | Contents |
|---|---|
| [`docs/COSTS.md`](docs/COSTS.md) | every dependency: licence, cost, limits, data egress, fallback; verified findings + cost exceptions needing approval |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | recommended stack, decision log (ADR-001…008), data model, data-flow boundary |
| [`docs/BLUEPRINT_SUMMARY.md`](docs/BLUEPRINT_SUMMARY.md) | fixed decisions, active V1 requirements, gates, open questions + defaults |
| [`docs/NLE_PREREQUISITES.md`](docs/NLE_PREREQUISITES.md) | user-side Premiere/Resolve access, seat counts, onboarding checklist |
| [`docs/RISKS.md`](docs/RISKS.md) | hardest-first risk register: mount, cost of mount, NAT, NLE semantics, AI licensing |
| [`docs/NLE_TEST_LOG.md`](docs/NLE_TEST_LOG.md) | the empty tables your Mac/Windows + NLE runs must fill |
| [`docs/UI.md`](docs/UI.md) | screen map, what each tab really does, honesty rules, how to verify the UI |
| [`docs/WEBDAV_MOUNT.md`](docs/WEBDAV_MOUNT.md) | how to mount on macOS/Windows/rclone + the registry defaults that break video |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | milestone checklist and what to test locally next |

## Layout

```
lfmc/
  lfmc/core/    db.py (schema) · identity.py (stable IDs, fingerprints) ·
                scanner.py (incremental scan, offline rules) · catalogue.py (state/search/browse)
  lfmc/net/     http_range.py (Range semantics) · webdav.py (read-only DAV façade) ·
                server.py (control plane + media endpoint + DAV + API)
  lfmc/nle/     registry.py (capability matrix, fail-closed preflight) · residency.py (bindings, mode guard)
  lfmc/spikes/  mp4_remote_open.py (ISO-BMFF parser over ranged reads + cost measurement)
  lfmc/web/     index.html (single-file UI, no CDN)
  lfmc/cli.py   init · seed-demo · add-root · scan · status · search · spike-open · nle-matrix · serve
  tests/        103 stdlib unittest tests
  tools/        ui_smoke.js (jsdom UI smoke test, dev-only)
  docs/         cost ledger, architecture, blueprint extract, NLE prereqs, risks, test log, progress
```

## Building it with an agent IDE

The repo carries an agent contract: `AGENTS.md` (always-on rules) and
`.agents/rules/*.md` (scoped rules for privacy, tests, UI, NLE integrity, demo
honesty, cost). Google Antigravity, or any agent that reads `AGENTS.md`, picks
them up automatically. Setup, permission preset and starter prompts:
[`docs/ANTIGRAVITY.md`](docs/ANTIGRAVITY.md).
