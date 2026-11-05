---
trigger: model_decision
description: "Applies to anything touching Premiere Pro / DaVinci Resolve projects, markers, locks, versions or the project hub."
---

# NLE integrity and residency

1. **Never byte-merge or "sync" `.prproj` / `.drp` as documents.** These are
   project state, not text. Collaboration is: locks, versions, checkpoints, and
   conflict *recovery* through the owning NLE's own facilities.
2. **Never fake an NLE integration.** If an adapter's real behaviour has not
   been observed on a licensed machine, it is *unverified* — say so in
   `docs/NLE_TEST_LOG.md` and in the UI, and do not ship copy implying support.
3. **Licences are user-side.** Each pilot user obtains their own Premiere Pro /
   Resolve access. Never bundle, resell, reimburse or assume a free
   collaboration feature. If a user lacks entitlement, mark that NLE workflow
   **blocked for that user** — do not silently switch collaboration modes.
4. **Residency modes are explicit.** Vendor-hosted (first pilot, informed
   consent) vs private/customer-controlled (active V1). Never fall back from
   private → vendor-hosted without asking. The project hub stores references and
   status only, never a second copy of proprietary project contents.
5. **Markers are additive.** Notes → markers must be repeatable and
   non-destructive: keep a stable note→marker id map so re-export updates rather
   than duplicates, and state clearly which NLE/target format is used.
