# No-cost dependency ledger

**Verified:** 2026-10-23 · **Scope:** development build + first controlled pilot
**Target:** US$0 in new third-party **platform-service** spend.

This file answers one question for every dependency: *what does it cost, where does
it run, what data leaves the host, and what is the fallback if it is unavailable?*

> Re-verify before each pilot. Prices, free-tier terms and vendor entitlements change;
> several entries below already contradict widely repeated folklore.

---

## 0. What "$0" does and does not mean

| | |
|---|---|
| **Covered by the $0 target** | Any recurring or usage-billed *platform* service the product depends on: hosting, managed database, blob/object storage, relay/TURN, email, push, monitoring, domain, AI inference API, CI. |
| **Not covered, must be stated plainly** | Customer hardware, drives, internet, electricity, labour, and **each user's own NLE software/licence**. The $0 claim never means the pilot is free to run. |
| **Hard rule** | No payment details entered, no paid plan enabled, no auto-upgrade path, no usage-billed service that can silently bill. Where a limit exists, the product fails safe (see §5). |

**Current status: no exception is required for this build.** The running spike uses
Python stdlib + SQLite only, on the user's own machine. §6 lists the *specific* cases
that would break the $0 target and therefore need your written approval before I code them.

---

## 1. Platform-controlled dependencies (all mandatory, all $0)

| Dependency | Version / form | Licence | Runs where | Verified cost | Limits that matter | Data leaving the host | Fallback |
|---|---|---|---|---|---|---|---|
| Python | 3.11+ | PSF-2.0 | Local (host + editor) | $0 | none | none | Bundled runtime in a later packaging step |
| SQLite (+FTS5) | 3.35+ | Public domain | Local | $0 | ~281 TB theoretical; practical limits are disk | none | None needed |
| Python stdlib HTTP server | `http.server` | PSF-2.0 | Local (host) | $0 | thread-per-connection; fine for a pilot team, not for a CDN | none | Replace with an ASGI server later, still $0 |
| Web UI | Single HTML file, inline CSS/JS | ours | Local browser | $0 | no external CDN calls (deliberate: works offline) | none | — |
| OS-native network path | Customer's LAN / their own VPN / port-forward | user-owned | Customer network | $0 *if* they have one (see §6) | bandwidth, latency, public IP availability | media bytes to authorised peers only | Self-hosted WireGuard on customer hardware |

Everything above is installed on hardware the customer already owns. There is no
sign-up, no account, no quota, no expiry, no card, and nothing that can bill.

---

## 2. Optional capability tiers (still $0, local inference)

Nothing in this section is required to run the current milestone. Each is a
**host-side, offline** addition. Licences were checked because two popular choices in
this space are *not* usable in a commercial product.

| Capability | Chosen component | Licence | Cost | Notes / limits |
|---|---|---|---|---|
| Metadata probe, thumbnails, proxies | **FFmpeg / ffprobe** | **LGPL-2.1+** (GPL-2.0+ only if built with `--enable-gpl`) | $0 | Ship/invoke an LGPL build (no `--enable-gpl`, no `--enable-nonfree`). FFmpeg's own legal page lists no commercial licence option, so build configuration is the compliance lever. Not installed in this sandbox — the spike degrades to structure-only parsing. |
| Speech → transcript | **whisper.cpp** + OpenAI Whisper weights | **MIT** (code and weights) | $0 | CPU-capable, Metal/CUDA optional. Caveat: whisper.cpp's FFmpeg *example* file is GPL-2.0, so invoke ffmpeg as a separate process (we do) rather than linking that example. |
| Face detection + face embedding | **YuNet + SFace** (OpenCV Zoo) | **Apache-2.0** (repo licence) | $0 | Commercial-safe. One downstream index lists the YuNet *weights* as MIT — confirm against the model card before shipping. |
| Face recognition — **rejected** | ~~InsightFace `buffalo_l` / `antelopev2`~~ | code MIT, **models non-commercial research only** | would need a paid commercial licence | **Excluded.** InsightFace's own site: "Pre-trained models for non-commercial academic research only". Cannot be used in a product sold to agencies. |
| Object / scene detection | **YOLOX** or **RT-DETR** (Apache-2.0) | Apache-2.0 | $0 | Commercial-safe. |
| Object detection — **rejected** | ~~Ultralytics YOLOv8/YOLO11/YOLO26~~ | **AGPL-3.0** (or paid Enterprise licence) | free only if we open-source the whole product | **Excluded.** Ultralytics' licence page states using their code/models requires open-sourcing the entire derivative work — including use behind an API — or buying an Enterprise licence. |
| Semantic (text→image) embeddings | OpenCLIP + a permissively licensed checkpoint | code MIT; **weights vary per model card** | $0 | Must pin a specific checkpoint whose card permits commercial use; record it here before adoption. |

**Privacy invariant for all of the above:** inference runs on the host. No footage,
face crop, embedding, transcript or tag is sent to any external service. Disclosed in
the UI, and the blueprint's opt-in rule applies to any future external option.

---

## 3. Hosted / free-tier services: none required, and one specifically rejected

| Service | Free tier as published | Usable for this pilot at $0? | Why |
|---|---|---|---|
| **Tailscale** | Personal: $0, up to **6 users**, unlimited user devices, 50 tagged resources | **No** | Tailscale's pricing page says the Personal plan "is only suitable for **non-commercial use**", and a custom-domain tailnet is auto-enrolled in a business trial. A paid agency pilot is commercial use. Paid Standard is **$8/user/month** — not $0. |
| Cloudflare Tunnel / ngrok / similar | Various | **No (unverified + recurring)** | Free tiers are usage-billed with shifting terms/limits; also routes client media through a third party. Excluded under both the cost and privacy rules. Required alternative: customer-controlled VPN. |
| Any managed DB / object storage / email / push / monitoring | — | **No** | Not needed: catalogue is local SQLite; review links are served by the host; no email is required for V1 review links (copy/paste a URL). |

**Consequence, stated plainly:** the platform does not provide internet-wide
reachability at $0. Remote editors need a connectivity path the **customer** owns
(LAN, or their own VPN / port-forward + DNS). If a pilot team has no such path, the
$0 target cannot deliver remote access for them — that is an exception for you to
approve (§6), not something I will quietly solve by signing up for a service.

---

## 4. NLE-side requirements — **user-side cost, never a platform cost**

These are prerequisites each pilot user/team must obtain and maintain. The platform
does not buy, bundle, reimburse or resell them. Full detail in
[`NLE_PREREQUISITES.md`](NLE_PREREQUISITES.md).

| Item | Requirement (as published) | Who pays | Verified? |
|---|---|---|---|
| Adobe Premiere Pro | Active Creative Cloud subscription per editor | the user/team | Yes — Adobe's plans page |
| Adobe **Team Projects** (vendor-hosted Premiere path) | Included with "all Creative Cloud subscriptions, including Creative Cloud Individual" per Adobe's help page. A widely cited third-party article says Teams/Enterprise only, and one Adobe page frames it as included with Teams/Enterprise. **Contradiction unresolved.** | the user/team | **NO — must be checked on the pilot's actual plan** |
| Adobe **Productions** (private Premiere path) | Premiere feature; needs compatible shared storage everyone can reach | the user/team | Partly — Adobe documents shared-storage dependence |
| DaVinci Resolve | Free download, or Studio US$295 perpetual | the user/team | Yes — Blackmagic product page |
| Resolve **Project Server** app (private path) | Blackmagic lists it as **free**, self-hosted PostgreSQL | the user/team (hardware) | Yes — Blackmagic collaboration page |
| Blackmagic **Cloud Project Library** (vendor-hosted Resolve path) | **Paid add-on.** Official free plan includes only 2 GB storage, 20 presentations, and *access to libraries shared with you*; hosting a library is "add and pay for what you need". Secondary sources report ~US$5/month per active library (not published on the page we verified). | the user/team | Partly — free-plan contents verified; per-library price **unconfirmed** |
| Collaboration edition question (Resolve) | Blackmagic marketing says the free version "includes multi-user collaboration"; some third-party guides claim Studio is required | the user/team | **NO — settle on the pilot's installed build** |

**Seats needed for the planned co-editing tests:** 2 editors per NLE at minimum, on
both macOS and Windows for the cross-platform matrix → **4 Premiere seats and 4
Resolve installs** across the test team, plus one machine able to host a private
Resolve Project Server. Confirm during onboarding, before scheduling Gate C.

---

## 5. Fail-safe mechanics in the current build

| Risk | Mechanism already implemented |
|---|---|
| Accidental billable dependency | No dependency in §1–§2 has an account, API key, quota or network egress. `pip install` is not required; there is no `requirements.txt` to drift. |
| Whole-file streaming that would blow bandwidth | `/media/*` refuses a non-ranged request for files > 8 MB (`400 range_required`) instead of streaming the original. |
| Silent cloud fallback for NLE state | `nle/registry.py` fails closed: private mode with no configured private server is **blocked**, never downgraded to a vendor service. Mode changes raise `SilentFallbackRefused` unless explicitly approved. |
| Media/proxy upload by accident | `media_sync_enabled` is a preflight **blocker** until separately approved; DB default is 0. |
| Unbounded media exposure | Every `/media/*` response requires a bearer token; tokens are written to disk with mode 0600; path containment is enforced against the registered root. |

---

## 6. Exceptions that would break the $0 target — needs your approval

None are implemented. Each is flagged because it is the point where "free" quietly
becomes "someone's credit card".

| # | Situation | Why it costs | No-cost alternative | Paid alternative |
|---|---|---|---|---|
| E1 | We bundle our **own** filesystem mount for Windows | **WinFsp** is GPLv3 **with a FLOSS exception**; distributing it with proprietary software requires a commercial licence — **US$6,000 / 3 years** (org, ≤10 devs) or **US$2,400 / 3 years** (individual) per WinFsp's commercial page. | Don't bundle: target an already-installed user-supplied mount, or expose **WebDAV** and let Windows' native redirector / macOS Finder mount it (no kernel driver of ours). | WinFsp commercial licence |
| E2 | We bundle a mount for macOS | **macFUSE**'s kext has been closed-source since 3.9; its wiki states "Bundling or integrating macFUSE with commercial software requires a paid-for license." | Same as E1. Also evaluate **Fuse-T** (kext-less); **its licence is unverified — must be read before use.** | macFUSE commercial licence |
| E3 | A pilot team has no LAN path and no VPN/public IP, but needs internet-wide remote editing | Requires a relay/TURN or mesh VPN; every dependable option is paid or non-commercial-use-only at pilot scale | Customer-hosted WireGuard on their own hardware with a public IP; or start the pilot on-LAN | Tailscale Standard US$8/user/mo, or a small VPS relay |
| E4 | A user insists on vendor-hosted **Resolve** collaboration and has no paid library entitlement | Blackmagic Cloud Project Library is a paid add-on | Private Resolve Project Server on customer hardware (free app) | User pays for the Cloud library — still user-side, not platform spend |
| E5 | A commercial face-recognition model is required for quality | InsightFace models are non-commercial | YuNet+SFace (Apache-2.0), lower accuracy | InsightFace commercial licence (unpublished pricing, reportedly 4–5 figures) |

---

## 7. Verification log

| Date | Item | Source type | Finding |
|---|---|---|---|
| 2026-10-23 | Adobe Team Projects inclusion | Official help pages | "available in Premiere and After Effects with all Creative Cloud subscriptions, including Creative Cloud Individual"; another Adobe page frames it as included with Teams/Enterprise. **Conflict open.** |
| 2026-10-23 | Resolve free version collaboration | Official product page | Marketing states the free version "includes multi-user collaboration and HDR grading"; third-party guides disagree about Studio. **Unresolved for real installs.** |
| 2026-10-23 | Blackmagic free plan contents | Official Blackmagic Cloud guide | 2 GB storage, 20 presentations, access to shared project libraries. Hosting a Cloud Project Library is a paid add-on. |
| 2026-10-23 | Tailscale Personal eligibility | Official pricing/ToS | Free for ≤6 users, "non-commercial use"; custom-domain tailnets auto-enter a business trial. Paid from $8/user/mo. |
| 2026-10-23 | InsightFace model licence | Official site + repo model zoo | Code MIT; **pretrained models non-commercial research only**. |
| 2026-10-23 | Ultralytics YOLO licence | Official licence/ToS | AGPL-3.0 or paid Enterprise; copyleft reaches the larger application. |
| 2026-10-23 | YOLOX / RT-DETR / YuNet+SFace | Upstream repos and downstream licence indexes | Apache-2.0 (commercial-safe); YuNet weight-level licence to re-confirm. |
| 2026-10-23 | whisper.cpp / Whisper weights | Upstream repo + LICENSE | MIT for code and weights; GPL-2.0 only in the FFmpeg example file. |
| 2026-10-23 | FFmpeg | Official legal page | LGPL-2.1+ by default; GPL applies if built with GPL parts; no paid licence exists. |
| 2026-10-23 | WinFsp / macFUSE | Official pages | WinFsp GPLv3+FLOSS exception, commercial $6,000/3yr org; macFUSE commercial bundling requires a paid licence. |

Ethiopia-specific availability: nothing in §1–§2 depends on a hosted service, so no
country availability question arises. The remaining Ethiopia-relevant factors are
**connectivity cost/reliability and electricity**, which belong in the pilot's
operational plan, not in this ledger — note that a 64 MB test clip and a 4 TB camera
drive are not the same problem over a typical Addis uplink.

## Vercel (evaluated Oct 2026 — deploy attempted, verdict: demo-only)

**Plan available to this account:** Hobby (no card on file). Pro is US$20/seat/month.

| Item | Hobby value | Consequence for us |
|---|---|---|
| Commercial use | **Prohibited** — Hobby is personal/non-commercial only (fair-use guidelines list paid client work, ads, affiliate sites) | A client-facing pilot demo is commercial. A static demo is tolerable as internal R&D only; **do not** present it as the pilot deployment |
| Fast Data Transfer | 100 GB/mo | fine for a demo |
| Function invocations | 1M/mo | N/A — no functions used |
| Builds | 100 deployments/day, 1 concurrent build | fine |
| Overage | features **pause**, no auto-billing | no surprise charges |
| Data use | Hobby content may be used for model training (opt-out in account settings), terms updated 1 Jun 2026 | **never** put client footage or real project data on Hobby |
| Deployment protection | Vercel Authentication ON by default for this team (`all_except_custom_domains`) | every URL returns 302 → vercel.com/sso-api; **only signed-in account members can open it** |

**Runtime incompatibility (decisive):** the product needs a long-lived process (`serve_forever`), a writable SQLite/WAL catalogue on local disk, byte-range reads of originals from a local `abs_path`, and a WebDAV `/dav/` mount. Vercel is serverless with ephemeral disk. Only a captured, read-only snapshot can be hosted there.

**What was deployed:** project `lfmc-demo-static` (id `prj_nblyiy6VgJfFjgXH27oKtmYc5mMj`), production deployment `dpl_JCEGpfnH9VMB6VAssQpR5ku1WAvo` → https://lfmc-demo-static-kiruproof-7222.vercel.app — a static read-only snapshot (loader + gzip payload). Requires the owner to be signed in to Vercel. Remove with `delete_project` when done; nothing else in the account was touched.

## Google Antigravity (build tooling, evaluated 5 Nov 2026)

Used to *write* the code, never to serve it. No customer data ever reaches it.

| Item | Current position | Consequence |
|---|---|---|
| Individual plan | $0/month, no card required; unlimited Tab completions and command requests | fits the $0 rule |
| Agent quota | **Size not published by Google**; described as "basic weekly rate limits", refreshed weekly | free tier is evaluation-grade, not a delivery guarantee |
| Quota history | cut roughly 250 → 20 requests/day between Nov 2025 and Mar 2026; described by press as four unannounced cuts | plan work as if access can pause mid-slice |
| Paid tiers | Google AI Plus ~$7.99, Pro $19.99, Ultra $99.99/$199.99 per month (US) | **not required** by this repo; do not enable |
| Fallback if access is lost | everything in `.agents/` is plain Markdown + the suite runs on stdlib Python | another agent or a human can pick up the same rules |
| Region / account gating | Google's FAQ limits personal accounts to 18+ in approved regions; **Ethiopia not confirmed** | verify at sign-in before planning around it |
| Data handling | code/workspace contents are sent to Google's models; I have **not** verified current training/retention terms for the free tier | therefore: no footage, proxies, transcripts or client project data in the workspace — enforced by `.agents/rules/media-privacy.md` and a permission deny-list |

Sources: antigravity.google/docs/{getting-started,rules,permissions}, antigravity.google/download;
pricing corroborated by CloudZero (18 Sep 2026), preuve.ai (10 Oct 2026) and vibecoding.tech (26 Sep 2026).
