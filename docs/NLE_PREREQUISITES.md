# User-side NLE access prerequisites (not platform costs)

The platform does **not** buy, bundle, reimburse or resell NLE software, accounts or
collaboration entitlements. Each pilot user/team obtains and maintains their own
access. Nothing here is a platform-service expense.

## What the test team must have

| # | Requirement | Why it is needed | Minimum for the planned tests |
|---|---|---|---|
| 1 | **Premiere Pro** licence/active subscription, current version (Team Projects broke on builds < 24.6.1) | Open/scrub media; vendor-hosted co-editing | **4 seats**: 2 editors × (macOS + Windows) |
| 2 | **Team Projects** access on the *actual* plan purchased (see open question below) | Vendor-hosted Premiere path | same 4 seats |
| 3 | **DaVinci Resolve** installed (free or Studio) on each editor machine | Open/scrub media; co-editing | **4 installs**: 2 editors × 2 OSes |
| 4 | Resolve collaboration entitlement for the pilot's installed build | Vendor-hosted Resolve path | confirm on the installed build |
| 5 | **Blackmagic Cloud** account per collaborator (free) **+ one paid Cloud Project Library** if vendor-hosted Resolve is used | Hosting the project library | 1 library, paid by the hosting user/team |
| 6 | A machine + network to host a **private Resolve Project Server** (free app, PostgreSQL) | Private mode gate | 1 host, stays online during sessions |
| 7 | Compatible **customer-controlled shared storage** for Premiere Productions | Private Premiere mode | per pilot topology; NAS/SAN class, not our P2P mount |
| 8 | Adobe ID signed in to Creative Cloud desktop app on each machine | Team Projects requires it | all editors |

## Two facts that must be confirmed by the user, not assumed by us

1. **Team Projects plan eligibility.** Adobe's help page says it is "available in
   Premiere and After Effects with all Creative Cloud subscriptions, including
   Creative Cloud Individual"; other Adobe pages and third-party write-ups say
   Teams/Enterprise. Check on the plan the team actually holds **before** the
   vendor-hosted Premiere test is scheduled. If ambiguous, treat vendor-hosted
   Premiere as **blocked** and test Productions/private mode instead.
2. **Resolve collaboration edition.** Blackmagic's product page states the free
   version "includes multi-user collaboration"; several third-party guides claim
   Studio is required. Confirm on the installed build.

## Blocking rules (implemented, not aspirational)

* No NLE path is enabled until the user confirms eligible access *for that NLE*.
  `nle/registry.py::preflight` returns `nle_access_not_confirmed` and refuses.
* If a participating editor lacks access, that **NLE workflow is marked blocked for
  that user**. We do not fake an integration and do not silently switch
  collaboration modes.
* Vendor-hosted setup requires a recorded consent row (`consent_recorded_at`,
  `consent_by`) because project metadata, locks and version history leave the
  customer's control.
* Media sync / proxy upload stays off and is a preflight blocker until separately
  approved.

## Onboarding checklist (fill per user)

```
[ ] Name / role / machine (macOS version or Windows version)
[ ] Premiere Pro version installed: ............  eligible? Y / N / UNKNOWN
[ ] Team Projects visible in Premiere (Edit > Team Project)? Y / N / UNKNOWN
[ ] Creative Cloud plan name (as billed): ............
[ ] Resolve version installed: ............  edition: free / Studio
[ ] Multi-user collaboration available in that build? Y / N / UNKNOWN
[ ] Blackmagic Cloud ID: yes / no
[ ] Who hosts a Cloud Project Library, and who pays? ............
[ ] Access to a private Project Server host? yes / no
[ ] Access to compatible shared storage for Productions? yes / no
[ ] Consent recorded for vendor-hosted project state? yes / no   by: ............
[ ] Resulting workflow status: premiere=vendor/private/blocked, resolve=vendor/private/blocked
```

Record the outcome in `docs/NLE_TEST_LOG.md`.
