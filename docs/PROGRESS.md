# Progress checklist

Blueprint v0.5 · milestone 1 of the build plan (§15) · updated 2026-10-23

## Done and verified in this repo

- [x] **Cost/dependency inventory before stack choice** → `docs/COSTS.md` (every dependency: licence, local vs hosted, cost, limits, data egress, fallback; verified findings + exceptions E1–E5)
- [x] **Stack chosen to fit $0 + cross-platform + privacy** → `docs/ARCHITECTURE.md` (host-local control plane, SQLite+FTS5, stdlib only, ADR-001…008)
- [x] **Runnable scaffold, not a mockup** → CLI + local web UI + HTTP API
- [x] **Local catalogue persistence across disconnect** → tested (`OfflineBehaviourTests`)
- [x] **Never infer deletion from an unreachable drive** → tested (`test_scan_while_offline_never_deletes`)
- [x] **Stable asset IDs across rename/move; duplicate files do not steal IDs** → tested (`MoveDetectionTests`, `test_new_file_is_added`)
- [x] **Explicit availability states** (original / preview / catalogue-only) → implemented + tested
- [x] **Read-only ranged media endpoint** with auth, containment, 206/416 semantics, byte accounting, and refusal of bulk full-file reads → tested
- [x] **Ranged-open measurement spike** (real ISO-BMFF parsing over HTTP ranges) → 64 MB file opened with **80 bytes / 5 requests**
- [x] **NLE residency fail-closed** (no consent → no vendor project; no private server → no private project; no silent fallback; media sync blocked) → tested
- [x] **Hardest risks identified with what would disprove them** → `docs/RISKS.md`
- [x] **Read-only WebDAV mount façade** (no third-party driver, no licence cost, so it stays $0) → 22 tests; `docs/WEBDAV_MOUNT.md`
- [x] **Review loop (Gate D groundwork)**: threaded timecoded comments, guest links with expiry/passphrase/revocation, selects, marker CSV → 18 tests. **Marker import into Premiere/Resolve still unverified.**
- [x] **Workspace UI** (drive tree → browser → inspector with Details/Comments/Transcript/Similar/Share) → `docs/UI.md`, jsdom smoke test (17 assertions)
- [x] **Test harness + exact commands** → `python3 -m unittest discover -s tests -t . -v` (103 passing)

## Deliberately not started (next slices, in order)

- [ ] Verify the WebDAV mount with real OS clients + real NLEs and record Gate A numbers (code done; nothing proven on your machines yet)
- [ ] Verify marker import in Premiere and Resolve; add version-stacking UI; live comment push
- [ ] AI slice: ffprobe metadata → thumbnails → whisper.cpp transcripts → YuNet/SFace clusters, pausable, offline-searchable → Gate B
- [ ] ffmpeg tier: proxies for phone review and weak links (explicit "proxy ≠ backup" wording)
- [ ] Mobile PWA selects (Keep/Maybe/Reject) → Gate E
- [~] TLS + certificate pinning still open; **per-collaborator scoped tokens done** (11 Nov)
- [x] Project hub: locks, version references, conflict recovery (11 Nov)
- [ ] NLE adapters' real behaviour → Gate C (needs user-side licences; `docs/NLE_TEST_LOG.md`)

## Requires your machines / accounts (I cannot do these here)

1. Run the 63 tests on macOS and Windows (currently only exercised on Linux/Python 3.13).
2. Catalogue a **real** camera drive with real footage; confirm scan speed on a large card and that nothing is modified.
3. Mount `http://<host>:8765/dav/` on macOS (Finder) and Windows (raise `BasicAuthLevel` and `FileSizeLimitInBytes` first), then open/scrub a real clip in **Premiere Pro** and **DaVinci Resolve**; fill Gate A rows. Note which client (Finder/redirector/rclone) you used — they behave differently.
4. Two-editor co-edit test per NLE and per residency mode; record licence/entitlement facts verbatim.
5. Induced offline conflict → confirm both users' work survives.
6. Amharic filename + transcript search on both OSes (Unicode normalization, tokenizer).
7. Confirm whether your plan includes Team Projects, and whether your Resolve build exposes collaboration (`NLE_PREREQUISITES.md`).

## Build tooling

- [x] Agent-IDE kit committed: `AGENTS.md` + `.agents/rules/*.md` (7 scoped
      rules: blueprint protection, media privacy, tests, UI/Shade limits, NLE
      integrity, static-demo honesty, cost/dependencies).
- [x] `docs/ANTIGRAVITY.md` — install, project boundary, permission preset with
      allow/deny lists, first four prompts, quota-aware rhythm.
- [ ] Verify Antigravity sign-in works for an Ethiopian Google account, and note
      the actual free-tier behaviour (quota window) in `docs/COSTS.md`.


## Evening of 11 Nov 2026 — three slices built in-repo

Built directly in this repository (not in an agent IDE). Test count 103 → 161.

- [x] **Slice A — scoped collaborator tokens** (`lfmc/core/auth.py`): producer /
      editor / reviewer roles, SHA-256-hashed tokens, expiry, revocation,
      `401` anonymous vs `403 insufficient_scope`, comment author bound to the
      token. CLI: `collaborators add|list|revoke`. 21 tests.
- [x] **Security fix found while testing A**: `/api/roots`, `/api/browse`,
      `/api/search`, `/api/assets`, `/api/assets/<id>`, `/api/events`,
      `/api/transfers` were readable by anyone on the LAN with no token. Now
      behind the `read` scope. Guest link routes unchanged.
- [x] **Slice B — idempotent marker export** (`lfmc/core/review.py`): stable
      marker key per note, `marker_exports` ledger, Premiere CSV + Resolve TSV,
      re-export updates instead of duplicating. **Still unverified inside either
      NLE** (`X-LFMC-Export-Verified: no`). 14 tests.
- [x] **Slice C — project hub** (`lfmc/core/hub.py`): locks with TTL and expiry
      reclaim, version registry storing path + stat signature only, recovery
      report with NLE instructions. No byte reads, no copies, no merges —
      enforced by two tests. 23 tests.
- [x] UI: token-carrying session (`?token=` → sessionStorage), role chip, both
      marker formats, and read-only collaborators see *why* Share/Markers are
      unavailable instead of a dead button. UI smoke 17/17.
- [x] `docs/COLLAB.md` — roles, API, limits, and what is still unverified.

Still blocked, unchanged: real NLE import proof, two-editor rehearsal,
macOS/Windows mounts, TLS, AI indexing.

## Antigravity handoff (11 Nov, late)

- [x] `docs/ANTIGRAVITY_OPEN.md` — surface choice (IDE/VS Code extension for
      `/browser` screenshots; App/CLI otherwise), current minimums (macOS 13+,
      Win10/11, Linux glibc 2.28+), Chrome 144+ remote-debugging setup, two
      verify-don't-build prompts, and the privacy warning about real catalogues.
- [x] `tools/preview.sh` — one command to a running host (seeds synthetic data on
      first run; `real` mode keeps auth on). Verified from a cold start.
- [x] `AGENTS.md` + rules refreshed for the new code: 161 tests, `auth`/`hub`
      modules, scope checks on every route, hub-never-reads-bytes, idempotent
      markers, `--demo` disables auth.
- [ ] Confirm Antigravity sign-in works for an Ethiopian Google account.

## First CI run on GitHub (11 Nov, late) — one real finding

Workflow `.github/workflows/tests.yml` ran on the first push:

| Job | Result |
|---|---|
| linux · py3.10 / 3.12 / 3.13 | pass |
| **macos-latest** | **pass** — first evidence this suite runs on a macOS host |
| windows-latest | **fail** → fixed, see below |

**Finding:** `tests/test_hub.py` cleaned its fixture by deleting `catalogue.db`
between tests. Unix lets you unlink a file that a live connection still holds;
Windows does not (`WinError 32: the file is being used by another process`). The
HTTP fixture keeps a server thread that can hold the file, so 2 tests errored on
Windows only. This is a *test-fixture* bug, not a product bug — but it is exactly
the class of thing that would have surfaced as a confusing one-off during a
Windows pilot, so it was worth catching now.

**Fix:** the fixture now clears rows (with `foreign_keys=OFF`, skipping FTS5
shadow tables) instead of deleting the file, which behaves identically on all
three platforms. Re-run: **all five jobs green** (linux ×3, macOS, Windows), so
`continue-on-error` was removed and the macOS/Windows jobs now block.

Note this does **not** close any of the platform gaps in "Requires your
machines": no real footage, no NLE, no actual WebDAV mount, no WinFsp/macFUSE.
It means the *Python* suite is portable, which is a much smaller claim.
