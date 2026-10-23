# Blueprint v0.5 — extracted fixed decisions, requirements, gates, open questions

Source: `Local_First_Media_Collaboration_MVP_Blueprint.md` v0.5 (22 Oct 2026).
This is a working extract for engineering. **The blueprint remains the source of
truth**; if this file and the blueprint disagree, the blueprint wins and this file is
wrong. Nothing here changes scope.

## Fixed decisions (do not re-litigate)

| Area | Decision |
|---|---|
| Market / segment | Ethiopia; small production teams and agencies |
| Source media | Users' local drives; **no automatic upload of originals** |
| Desktop | macOS and Windows |
| NLEs | Adobe Premiere Pro and DaVinci Resolve — separate adapters, never unified formats |
| NLE residency | **Both** modes are V1: vendor-hosted (first pilot, with consent) **and** private/customer-controlled |
| NLE licensing | User-side prerequisite; platform does not buy/bundle/reimburse/resell |
| Cost | **$0 new platform-service spend** for build + first controlled pilot |
| Free tiers | Verify Ethiopia availability, quotas, expiry, card/billing, egress, terms; always keep a local fallback |
| Cloud/S3 storage | Deferred |
| Payments | Deferred |
| Backup system | Deferred — V1 must **not** be described as backup |
| Active V1 (not deferred) | Local-first catalogue; semantic AI search with scene/object/face/speech; mobile selects; accountless guest review; timecode feedback into NLE; NLE co-editing with locking and conflict recovery; privacy controls |

## Active V1 requirements, condensed

1. **Catalogue** — connect drive → scan → persist locally → browse/search while disconnected; stable asset IDs; explicit Online/Offline/Indexing/Preview/Original states; never modify source media.
2. **Remote editing** — remote editor on either OS opens media via a mounted/browsed location in Premiere/Resolve; on-demand reads + local cache + reconnect; no mandatory full download; clear host-offline errors.
3. **AI discovery** — natural-language, scene, object, face/person (when enabled) and transcript search returning **file + time range**; incremental, pausable, throttled; results searchable with the drive offline; correction + deletion controls; no silent upload for inference.
4. **Mobile selects** — phone-friendly proxy review; Keep/Maybe/Reject + notes; bound to asset + source timecode; PWA acceptable for pilot.
5. **Guest review** — accountless link scoped to an asset/version; timestamped comments + replies; Approved / Changes requested / In review; disable, expiry, optional passphrase/watermark; no folder-tree leakage; no-download control.
6. **Feedback into NLE** — comments carry asset/version/timecode/framerate; export/import to timeline markers in both NLEs; marker file is acceptable if native panels are unreliable; bind notes to the reviewed version; no silent drift.
7. **NLE co-editing** — native collaboration/locking per NLE; publish + version history; visible locks with lease/heartbeat; conflict → preserve **both**, never silent overwrite; no byte-merge; per-project residency disclosure + consent; no silent mode fallback.
8. **Privacy** — least privilege; revocable scoped links; access logs; explicit availability states; face data stricter and excludable from guest shares; no training on customer footage; per-project residency disclosure.

## Acceptance gates (release criteria — not yet passed)

| Gate | Statement | Status in this repo |
|---|---|---|
| A — Media access | Remote editor opens/scrubs supported media in both NLEs without mandatory full-file staging | **Open.** Wire-level range economics proven; no mount, no NLE test yet |
| B — AI discovery | Useful file+time-range results for natural-language/scene/object/face/transcript, respecting offline + privacy states | **Not started** (licence constraints resolved first) |
| C — Native co-editing + residency | 2+ editors co-edit in both modes per NLE; locks, versions, mapping, conflict recovery; no silent fallback | **Data layer only** — fail-closed preflight implemented and tested; no NLE has been driven |
| D — Review loop | Accountless client reviews a version, notes timecode, editor imports into correct timeline | **Not started** |
| E — Mobile selects | Producer evaluates proxies on phone, hands reliable selects list to editor | **Not started** |
| F — No-cost + NLE eligibility | Every platform dependency verified $0/self-hosted; no auto-billing; users confirm NLE access | **Passed for current build** (`COSTS.md`); NLE access confirmation is a human step per user |
| G — Pilot readiness | Permissions, revocation, connection status, AI controls, recovery, cost limits, failure handling clear enough for real client work | **Not started** |

## Open questions — status and recommended defaults

| # | Question | My default if you don't answer | Blocking? |
|---|---|---|---|
| 1 | Must guest links work while the host is offline? | **No for V1**: host-online streaming; label it clearly in the link's consent text. Optional proxy-caching later. | No |
| 2 | Proxy policy: host-only or cached off-host? | Host-only by default; off-host proxy cache is an explicit per-project opt-in with retention controls. | No |
| 3 | Is synchronized playback a launch requirement? | Live comment updates first (as the blueprint allows); sync playback after Gate D. | No |
| 4 | Drive sources: attached only, or NAS too? | Attached drives first; NAS/SMB supported only after a lock/topology test (see RISKS). | No |
| 5 | Mobile web/PWA or native apps? | PWA for the pilot. | No |
| 6 | What catalogue metadata may leave the host? | **Nothing** in this build: catalogue is host-local, search is host-served. Any future sync is opt-in per field. | No |
| 7 | Supported formats? | Publish only after real testing: likely ProRes/H.264/H.265 in .mov/.mp4 first, then MXF/BRAW/R3D as tested. No promises before then. | No |
| 8 | AI location; face-data retention? | Host-only inference; face data off by default per project, correctable/deletable, excluded from guest shares. | No |
| 9 | NLE residency | Decided: both modes; pilot uses vendor-hosted with consent. Remaining work: prove private paths. | Decided |
| 10 | Conflict policy | Preserve both branches, duplicate the sequence, human reconciles. Never auto-discard. | No |
| 11 | NLE access | Decided: user-side responsibility, verified at onboarding. | Decided |

**Blueprint's own recommended spike default** (keep): attached drives; host-online
access to originals; optional local proxies; no new paid services; local AI; both NLE
modes tested; pilot on vendor-hosted with consent; PWA; marker export before native
panels.
