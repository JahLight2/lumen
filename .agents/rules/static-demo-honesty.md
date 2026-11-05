---
trigger: glob
globs: "tools/export_static_demo.py, tools/pack_demo.js, tools/minify_demo.js, tools/static_demo_check.js, tools/pack_check.js, demo-static/**"
description: "Rules for the read-only static snapshot of the UI (export, pack, host)."
---

# The static snapshot must stay obviously a snapshot

1. Every exported page keeps the fixed banner: "Read-only demo snapshot —
   captured <date> … Nothing here is saved, no drive is connected, and no NLE is
   linked."
2. Every mutating request returns `403 {"error": "read_only_demo"}` with a
   plain-language explanation. Never return a fake success or a locally
   simulated write — a demo that appears to save is a lie.
3. Unknown endpoints return `{"error": "not_in_snapshot"}`. No silent empty
   arrays that look like real "no results".
4. Only synthetic data may be exported. Before adding an export path, assert the
   catalogue is the sample drive; refuse `--allow-real-data` style flags.
5. Keep the offline checks green: `node tools/static_demo_check.js` (12) and
   `node tools/pack_check.js` (9). They run with no network access by design.
6. Hosted copies are vendor-hosted by definition. Never describe one as the
   private/customer-controlled mode, and never point a client at it as the
   product.
