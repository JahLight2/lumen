# Architecture and decision log

## Recommended stack (small team, $0, macOS + Windows, NLE-aware)

```
┌───────────────────────────── HOST COMPUTER (macOS / Windows) ─────────────────────────────┐
│  lfmc agent  (single Python process, stdlib only)                                         │
│                                                                                           │
│   web/index.html ──┐                                                                      │
│   catalogue API ───┤  ThreadingHTTPServer  ──►  /media/{asset}  (HTTP Range, read-only)     │
│   scanner ─────────┘         │                    │                                       │
│                              │                    ▼                                       │
│   SQLite + FTS5  ◄───────────┘            user-selected root (external drive)             │
│   (catalogue, availability, transfer log, audit, NLE bindings)                            │
│                                                                                           │
│   optional local tiers: ffprobe/ffmpeg · whisper.cpp · YuNet+SFace · YOLOX                │
└───────────────┬───────────────────────────────────────────────────────────────────────────┘
                │ customer-owned network path (LAN / their VPN / their port-forward)
                ▼
┌──────────────── REMOTE EDITOR (macOS / Windows) ───────────────┐
│  mount/browse layer  ──►  Premiere Pro / DaVinci Resolve       │
│  (candidate: WebDAV + OS-native client, or user-installed      │
│   rclone; NOT our own kernel driver — see ADR-004)             │
└───────────────────────────────────────────────────────────────┘
```

There is **no cloud control plane**. The host is the control plane. That single
decision removes hosting, managed-database, storage, relay and monitoring spend in
one stroke, and it matches the product promise (originals never leave the drive).

## Why this shape, not microservices

| Alternative | Rejected because |
|---|---|
| Hosted control plane (even a free tier) | Free tiers are not dependable, are usually non-commercial, and put filenames/paths on someone else's disk. Also the *only* thing a cloud control plane would add for V1 is NAT traversal, which is best solved by the customer's own network. |
| Electron/Tauri desktop app now | The risky unknowns are media access, NLE behaviour and AI quality — not packaging. A browser UI over a local service keeps the loop fast and tests the same HTTP contract the mount will use. |
| Separate scanner service, index service, review service | One process, one database, one failure domain. The pilot has ≤10 users; microservices would add deployment and debugging cost with no benefit. |
| Postgres / Vector DB | SQLite + FTS5 is enough for a drive-scale catalogue and is genuinely $0, backup-able by copying a file. Vector search can wait until the AI gate, and then still be local (sqlite-vec or numpy). |

---

## Decision log

Each ADR records **status**, **decision**, and what would make us revisit it.

### ADR-001 — Host-local control plane, no cloud service
**Status:** accepted.
The host process owns identity tokens, the catalogue, review data and the media
endpoint. Consequence: remote editors need a customer-owned network path; we do not
provide internet-wide reachability. Revisit only if a pilot genuinely cannot provide
a path **and** you approve E3 in `COSTS.md`.

### ADR-002 — Stable random asset IDs; paths are attributes
**Status:** accepted, tested.
An asset ID is a UUID generated once. `rel_path` is mutable and every change is
recorded in `asset_path_history`. Moves are detected by a sampled content
fingerprint (size + head/middle/tail blake2b, ≤3 MiB read per file).
**Tested:** rename keeps the ID, identical-content duplicates do *not* steal an ID,
moved files are not tombstoned.
**Limitation:** sampled hash, not adversarial-proof identity. Full hash available for
spot verification.

### ADR-003 — Availability is derived and multi-valued
**Status:** accepted, tested.
Four facts are tracked independently: owner intent (`share_enabled`), reachability
(`online`), original presence, preview presence. The UI must be able to say
"catalogued + preview cached + original offline" at once.
**Critical rule:** an unreachable root can never produce a deletion. Only a completed
walk of a reachable root may tombstone. Enforced in `scanner.scan_root` and covered
by `test_scan_while_offline_never_deletes`.

### ADR-004 — Do not ship our own filesystem mount; expose read-only WebDAV
**Status:** **implemented** (protocol level). Mount verification on real OS clients and
NLEs is still open (Gate A).
Rationale: the mount is the highest technical risk *and* it carries licence cost.
WinFsp is GPLv3+FLOSS-exception with a US$6,000/3yr commercial licence for
closed-source distribution; macFUSE requires a paid licence to bundle with commercial
software. Both would break the $0 target (exceptions E1/E2).
**Candidate paths to evaluate, in order:**
1. **WebDAV served by the host + OS-native client** (Windows WebDAV redirector,
   macOS Finder). Zero third-party components, $0. Unknowns: Windows WebClient's
   historically small file-size limits and its behaviour under random seeks.
2. **User-installed rclone** mounting our endpoint, with its VFS cache doing the
   on-demand reads and reconnect logic. rclone is MIT; the user installs it (and any
   FUSE/WinFsp layer) themselves, which keeps us out of the bundling question.
3. Roll our own FUSE/WinFsp client — only with your approval of E1/E2 spend.
**Implemented:** option 1 + 2. `lfmc/net/webdav.py` serves a read-only DAV view;
offline originals return **503 not 404** (so an NLE does not treat a disconnected host
as deleted media); all write verbs are refused before touching the drive; Depth:
infinity is capped at 1. See `docs/WEBDAV_MOUNT.md`.
**What settles the choice:** Gate A tests on real machines with real NLEs
(`docs/NLE_TEST_LOG.md`), measuring open/scrub success and bytes-on-wire — including a
direct comparison between Finder/webdavfs, the Windows redirector and rclone.

### ADR-005 — HTTP Range is the only media transport
**Status:** accepted, tested.
`/media/{id}` answers `206` with `Content-Range`, `ETag`, `Accept-Ranges: bytes`.
Multi-range and un-ranged requests for files > 8 MB are **refused with a structured
error**, never fulfilled by streaming the whole file. This is a product requirement
(“no full-file staging”) expressed as a protocol constraint.

### ADR-006 — NLE state is referenced, never copied or merged
**Status:** accepted, tested at the data layer only.
We store NLE type, residency mode, authoritative service, consent, media-sync flag
and status. We never store project bytes and never implement a .prproj/.drp merger.
Preflight **fails closed**: vendor mode without consent → blocked; private mode
without a configured server → blocked; mode change without approval →
`SilentFallbackRefused`; media sync → blocked until separately approved.

### ADR-007 — AI runs on the host; commercial-safe models only
**Status:** accepted (no AI code yet, deliberately).
Inference stays local. Model choice is constrained by licence before it is constrained
by quality: InsightFace models and Ultralytics YOLO are excluded from a commercial
product (`COSTS.md` §2). Speech via whisper.cpp (MIT); faces via YuNet+SFace
(Apache-2.0); objects via YOLOX/RT-DETR (Apache-2.0).

### ADR-009 — One token per person, scopes enforced at the server

Status: **accepted and implemented** (11 Nov 2026).

A single host token made every comment anonymous and every revocation a
team-wide password change. Collaborators now hold their own token with a role
(`producer`, `editor`, `reviewer`); the host keeps an all-scopes token. Decisions
that matter:

* The server is the authority. The UI hides what a token cannot do, but a hidden
  button is not a control — every route checks the scope itself.
* Tokens are hashed with SHA-256 (high-entropy secrets, not passwords). If they
  ever become user-chosen this must become scrypt.
* `401` for "no valid credential", `403` for "valid credential, wrong scope", and
  revoked/unknown/expired are all `401` so probing cannot confirm a token exists.
* The author of a comment comes from the token, never from the request body.

### ADR-010 — The project hub never touches project bytes

Status: **accepted and implemented** (11 Nov 2026).

Locks and version references, nothing else. `register_version()` stats the file;
it does not open it. The fingerprint is a stat signature precisely so that no
content read is ever needed. Recovery produces instructions for a human in the
NLE, never a restore. Two tests fail the build if project reads are introduced.

### ADR-008 — Python stdlib only for the first slices
**Status:** accepted, revisit at packaging.
No `pip install` in the hot path means: no dependency drift, no build toolchain, no
supply-chain surface, and the pilot runs on a machine with no internet. Trade-off:
`http.server` and `sqlite3` are adequate for a pilot team, not for scale. Revisit when
packaging (likely PyInstaller/Nuitka or a signed installer).

---

## Data model (implemented)

Schema **v2** (11 Nov 2026) adds, additively and without rewriting user data:
`collaborators`, `marker_exports`, `project_locks`, `project_versions`. Migration
is create-if-missing plus a version bump; no table is ever altered destructively.

```
roots(id, label, abs_path, online, share_enabled, last_seen_at, read_only, …)
assets(id PK, root_id, rel_path, name, size_bytes, mtime, fingerprint,
       duration_s, fps, width, height, codec, probe_state, deleted_at, …)
asset_path_history(asset_id, rel_path, first_seen_at, last_seen_at)
asset_search  : FTS5(asset_id, name, rel_path, tags, transcript)
cached_previews(asset_id, kind, location, path, bytes, …)
transfer_log(ts, asset_id, session_id, start_byte, end_byte, bytes, outcome)
scan_runs(root_id, started_at, status, added, changed, moved, removed, unchanged)
events(ts, kind, actor, subject, detail)            -- audit trail
projects(id, name) / nle_bindings(...)              -- references only, no project bytes
```

Deliberately absent so far: comments, selects, review links, AI tables. They land in
slice 2 with the same ID discipline (comment → `asset_id` + timecode + frame rate +
reviewed version, never filename-only).

---

## Data-flow boundary (what the product must show per project)

| Data | Where it lives | Leaves the host? |
|---|---|---|
| Original media | user's drive | only as ranged bytes to an authorised editor; never uploaded |
| Catalogue / index | host SQLite | no |
| Thumbnails / proxies | host cache | no, unless separately approved |
| Review comments | host SQLite | no (served by the host) |
| Account identity | none in this build | — |
| NLE project state (vendor mode) | the vendor's service | yes — by definition, needs consent |
| NLE project state (private mode) | customer-controlled server/storage | no |
| Relay traffic | none | no third-party relay exists in this build |

---

## Next increments (in order)

1. ~~WebDAV façade~~ **done** (protocol level). Remaining: real-client/NLE verification.
2. **Review slice**: review links, timestamped comments, marker export
   (Premiere/Resolve), then verify marker accuracy against real timelines.
3. **TLS + per-collaborator scoped tokens** before any non-LAN use (Basic creds are
   cleartext today).
4. **AI slice**: ffprobe metadata → thumbnails → whisper.cpp transcripts →
   YuNet/SFace clusters, all incremental and pausable, all offline-searchable.
5. **ffmpeg tier**: proxies for phone review and weak links, with explicit
   "proxy cache ≠ backup" messaging.
