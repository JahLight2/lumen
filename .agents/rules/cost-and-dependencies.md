---
trigger: model_decision
description: "Applies when adding a dependency, service, free tier, SDK, model, or anything that could cost money or change data-handling terms."
---

# Money, free tiers and dependencies

1. **$0 for platform services** is a hard constraint for build and pilot. Before
   adding anything: check current official pricing and terms, note the region
   availability, quota, expiry, egress, card requirement, billing behaviour on
   overage, and data-retention/training clauses.
2. Record every evaluated service in `docs/COSTS.md` with the citation, and give
   a local/self-hosted fallback. A free tier is never the only path.
3. Flag non-commercial free tiers explicitly (e.g. Vercel Hobby, Tailscale
   Personal) — they are not usable for client work and must be labelled as
   internal-evaluation only.
4. Prefer local/open-source AI on hardware the team already owns. No required
   paid AI API. Check the licence of anything bundled (AGPL, non-commercial,
   paid redistribution: e.g. Ultralytics AGPL, InsightFace non-commercial,
   WinFsp/macFUSE licensing) before proposing it.
5. Never add billing details, enable a paid plan, or configure auto-upgrade.
6. Quotas can change without notice and free tiers get cut — write the plan as
   if access could disappear, and keep the local path working.
