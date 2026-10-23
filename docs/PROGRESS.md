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
- [x] **Test harness + exact commands** → `python3 -m unittest discover -s tests -t . -v` (85 passing)

## Deliberately not started (next slices, in order)

- [ ] Verify the WebDAV mount with real OS clients + real NLEs and record Gate A numbers (code done; nothing proven on your machines yet)
- [ ] Review slice: scoped accountless review links, timestamped comments, marker export → Gate D
- [ ] AI slice: ffprobe metadata → thumbnails → whisper.cpp transcripts → YuNet/SFace clusters, pausable, offline-searchable → Gate B
- [ ] ffmpeg tier: proxies for phone review and weak links (explicit "proxy ≠ backup" wording)
- [ ] Mobile PWA selects (Keep/Maybe/Reject) → Gate E
- [ ] TLS + certificate pinning; per-collaborator scoped tokens (currently one host token)
- [ ] NLE adapters' real behaviour → Gate C (needs user-side licences; `docs/NLE_TEST_LOG.md`)

## Requires your machines / accounts (I cannot do these here)

1. Run the 63 tests on macOS and Windows (currently only exercised on Linux/Python 3.13).
2. Catalogue a **real** camera drive with real footage; confirm scan speed on a large card and that nothing is modified.
3. Mount `http://<host>:8765/dav/` on macOS (Finder) and Windows (raise `BasicAuthLevel` and `FileSizeLimitInBytes` first), then open/scrub a real clip in **Premiere Pro** and **DaVinci Resolve**; fill Gate A rows. Note which client (Finder/redirector/rclone) you used — they behave differently.
4. Two-editor co-edit test per NLE and per residency mode; record licence/entitlement facts verbatim.
5. Induced offline conflict → confirm both users' work survives.
6. Amharic filename + transcript search on both OSes (Unicode normalization, tokenizer).
7. Confirm whether your plan includes Team Projects, and whether your Resolve build exposes collaboration (`NLE_PREREQUISITES.md`).
