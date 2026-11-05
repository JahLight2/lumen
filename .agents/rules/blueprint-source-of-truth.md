---
trigger: always_on
description: "Protects the blueprint v0.5 source of truth and the docs that record decisions, costs and progress."
---

# Blueprint and decision records

- `uploads/Local_First_Media_Collaboration_MVP_Blueprint (2).md` is the source of
  truth, version 0.5 (22 Oct 2026). It is **read-only**: never edit, move,
  rename, reformat, "tidy", summarise in place, or regenerate it. Do not write
  implementation notes into it.
- Implementation notes and deviations live in `docs/`. When behaviour differs
  from the blueprint, record the deviation and the reason in
  `docs/ARCHITECTURE.md` under a dated decision entry, and note the effect on
  acceptance gates in `docs/PROGRESS.md`.
- Do not delete or renumber acceptance gates. If a gate cannot be met, leave it
  open and state what is blocked and why (e.g. "Gate A blocked: no Adobe
  entitlement on this machine").
- Do not remove "Requires your machines / accounts" items from
  `docs/PROGRESS.md` unless the user has confirmed they were run.
