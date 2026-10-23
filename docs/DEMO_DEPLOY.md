# Sharing the demo: what can go where

The product is a **local-drive-first host**. It is not a website: it owns a
long-lived process, a SQLite/WAL catalogue on disk, byte-range reads of the
originals on the attached drive, and a read-only WebDAV mount. For that reason
it cannot be deployed to a serverless host — only a captured snapshot can.

## 1. The product itself (real backend)
Run on the machine that has the drive:

    python3 -m lfmc.cli serve --host 0.0.0.0 --port 8765

Remote reviewers reach it over Tailscale/LAN/VPN. Originals are only available
while the host and drive are online; the catalogue stays browsable offline.

## 2. `LUMEN-DEMO.html` — one file, works offline
A capture of the real UI with the synthetic sample drive baked in. No network,
no backend, no writes (every POST is refused with a plain-language message).
Open it from a USB stick, email it, or drop it on any static host.
Regenerate after UI changes:

    python3 -m lfmc.cli serve --port 8765 --demo &
    python3 tools/export_static_demo.py --base http://127.0.0.1:8765
    node tools/minify_demo.js && node tools/pack_demo.js

## 3. Vercel — possible, with three caveats
Feasible only as the static snapshot in (2), and:
* **Hobby is non-commercial** — internal R&D viewing only, never a client demo
  on the pilot's behalf (see COSTS.md).
* **Deployment protection is on** — every URL 302s to Vercel SSO, so only
  signed-in account members can open it. To let anyone else view it, turn off
  *Project → Settings → Deployment Protection → Vercel Authentication*.
* **It is a snapshot, not the product** — no scanning, no WebDAV, no writes,
  no real media, and it is vendor-hosted by definition, so it must never be
  presented as the private/customer-controlled mode.

Current deployment: `lfmc-demo-static` → https://lfmc-demo-static-kiruproof-7222.vercel.app
(production, `dpl_JCEGpfnH9VMB6VAssQpR5ku1WAvo`). Tooling in `tools/`
(`export_static_demo.py`, `minify_demo.js`, `pack_demo.js`) plus offline checks
(`static_demo_check.js` 12/12, `pack_check.js` 9/9).
