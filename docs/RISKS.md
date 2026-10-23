# Risk register — hardest problems first

Status legend: **PROVEN** (tested here) · **PARTIAL** · **OPEN** (needs your machines)
· **BLOCKED** (needs a decision or money).

## R1 — Filesystem mount behaviour under NLE random reads  · OPEN · highest risk
Premiere and Resolve issue many small random reads, re-open files, and expect POSIX
semantics (stat, seek, sometimes mmap, file locking). A network filesystem that looks
right in Finder/Explorer can still make an NLE hang, corrupt a cache, or refuse to
import.

**What is proven here:** the HTTP contract underneath is correct — 206 responses,
byte-exact ranges, suffix ranges, 416 for unsatisfiable, refusal of un-ranged bulk
downloads, per-request byte accounting; and a **read-only WebDAV façade** over it
(PROPFIND depth 0/1, capability OPTIONS, Basic auth challenge, write refusals,
offline **503**, traversal rejection, percent/Amharic round-tripping) —
`tests/test_range_server.py` + `tests/test_webdav.py`, 85 tests total.
**What is NOT proven:** that any OS client or NLE exposes/drives this correctly. The
Windows WebDAV redirector's 50 MB default file-size cap and Basic-over-HTTP default are
the two known traps; `docs/WEBDAV_MOUNT.md` lists the fixes and the test script.
**How to settle it:** mount via candidate path (ADR-004), open a real ProRes/H.264 clip
in Premiere and Resolve on macOS and Windows, scrub, and record bytes-on-wire vs file
size. Fill `NLE_TEST_LOG.md`.
**Mitigation while open:** never describe remote editing as working; keep the
`/media` range server as the only transport so a change of mount does not change
the security model.

## R2 — Which mount technology we are allowed to ship · RESOLVED (decision), cost still avoided
Bundling WinFsp in a proprietary Windows app needs a commercial licence
(US$6,000/3yr org). Bundling macFUSE with commercial software requires a paid licence.
**Resolution:** ADR-004 — we ship **no kernel driver**. The host exposes read-only
WebDAV; macOS Finder and the Windows redirector mount it natively, and rclone (MIT,
installed by the user) is the fallback for a stronger on-demand cache. Implemented and
tested at the protocol level. Spending on WinFsp/macFUSE remains an exception (E1/E2)
that needs your approval; nothing in the current path requires it.

## R3 — Host-offline reality
Originals cannot be served when the host or drive is offline; the catalogue can.
**Proven here:** an unreachable root can never tombstone assets
(`test_scan_while_offline_never_deletes`), search and folder browsing work offline
(`test_offline_drive_keeps_catalogue_and_search`, `test_browse_offline_reconstructs_folder_tree`),
media requests return a structured `409 original_unavailable` with an explanation
rather than a misleading 404, and a cached preview still serves
(`test_preview_served_while_original_offline`).
**Open:** what a *real* Premiere/Resolve session does mid-playback when the host
drops — the failure must be survivable and never corrupt the source. Untestable
without real NLEs.

## R4 — NAT / firewall traversal at $0
Peer-to-peer and inbound connections fail behind carrier NAT, which is common on
Ethiopian mobile/fixed broadband.
**Mitigation:** do not promise a relay. Pilot on-LAN first. Remote = customer's own
VPN or port-forward. Tailscale free is non-commercial-only → not eligible for a paid
pilot; paid is a cost exception (E3).

## R5 — Performance is not free just because transmission is ranged
"No full download" ≠ "lightweight". A 4K ProRes clip still needs tens of MB/s for
smooth scrubbing.
**Mitigation:** treat proxies as a first-class path, publish a tested format list, and
measure "bytes before first usable playback" per codec in the pilot.

## R6 — NLE collaboration semantics differ per NLE and per mode · OPEN
Premiere Team Projects (Adobe-hosted, publish/version model), Premiere Productions
(shared-storage, project/bin locks), Resolve Cloud library (vendor-hosted),
Resolve private Project Server (customer-hosted). Locks, versioning and conflict
behaviour are four different systems.
**Proven here:** the product refuses unsafe configurations — vendor mode without
consent is blocked, private mode without a configured server is blocked, mode changes
raise `SilentFallbackRefused`, media sync is blocked until approved, and a test asserts
no project bytes are ever stored (`test_nle_residency.py`).
**Open:** every behavioural claim. `registry.py` lists, per adapter, the specific
claims that are unproven; `capability_matrix()` exposes them in the UI so nobody
mistakes "documented" for "verified".

## R7 — Premiere Productions over anything that is not a NAS
Adobe's model assumes shared storage; it warns VPN is slow for media. Our mount is
**not** claimed to provide NAS semantics. Induced-conflict recovery must preserve both
users' work; that is a hard, real test on a real topology.

## R8 — Vendor entitlements and cost after all · USER-SIDE
Team Projects may require a specific plan; Resolve vendor-hosted collaboration needs a
paid Cloud Project Library. These are user-side, disclosed in
`NLE_PREREQUISITES.md`, and must be confirmed before scheduling Gate C.

## R9 — AI licensing trap · RESOLVED BY EXCLUSION
The two most common "just use" choices are unusable in a commercial product:
InsightFace's pretrained models (non-commercial research only) and Ultralytics YOLO
(AGPL-3.0 or paid Enterprise). Chosen instead: whisper.cpp (MIT), YuNet+SFace
(Apache-2.0), YOLOX/RT-DETR (Apache-2.0). See `COSTS.md` §2.

## R10 — AI privacy surface
Transcripts, face clusters and embeddings are sensitive even though originals stay
local. Requirements already fixed: host-only inference, face analysis off by default
per project, correction/deletion, no guest exposure, no training on customer footage.
The moment any external inference is proposed, it becomes a consent + cost decision.

## R11 — Metadata leakage via catalogue
Filenames and folder paths can name an unreleased client. Current build sends nothing
off-host and search is served locally; that is the strongest possible answer and should
stay true unless a future feature forces catalogue sync (then: per-field opt-in).

## R12 — Backup is deferred
V1 is not backup. A drive failure loses media and the catalogue. Product wording must
say so; the pilot onboarding must state it in writing.

## R13 — Cross-platform path semantics
Drive letters vs `/Volumes`, case-insensitive vs case-sensitive, Unicode normalization
on macOS (NFD) vs Windows (NFC) can break path matching and FTS lookups.
**Mitigation implemented:** paths stored as POSIX-style relative paths; identity is a
UUID, never a path. **Open:** Unicode normalization must be verified with real
Ethiopian filenames (Amharic script) on both OSes.

## R14 — Amharic / local-language handling
Search tokenizer currently keeps Ethiopic ranges (`\u1200-\u137F`) so Amharic
filenames are searchable, but transcript search in Amharic depends on the speech model;
Whisper's Amharic quality is untested here. Treat as an open product question, not a
solved feature.
