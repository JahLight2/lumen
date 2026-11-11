# NLE verification log

**Every entry here is empty on purpose.** No NLE integration in this repository has
been executed against a real Premiere Pro or DaVinci Resolve installation. Until a row
below is filled in with real evidence, the corresponding path is **unverified** and must
not be described to a pilot team as working.

Rules for filling a row
1. Real machine, real OS version, real NLE build number, real footage.
2. Record what you actually observed, including failures.
3. A pass requires: file opens, scrubbing works, no source corruption, host-drop
   behaviour is survivable, and bytes-on-wire are measured.
4. Keep user-side licence facts (plan name, edition) in the row — they are part of
   whether the test was valid.

## Gate A — media access (open/scrub through a mounted/browsed path)

| Date | Host OS | Editor OS | NLE + exact version | Codec / container | Mount path used | Opens? | Scrubs? | MB read before first playback | Host-drop behaviour | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | |

## Gate C — native project co-editing and residency

| Date | NLE + version | Mode | Authoritative state | Editors (count, OS) | User licence/entitlement (verbatim) | Locks visible? | Version/publish works? | Induced offline conflict → both preserved? | Originals stayed local? | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| | Premiere | vendor_hosted (Team Projects) | Adobe | | | | | | | |
| | Premiere | private_controlled (Productions) | customer shared storage | | | | | | | |
| | Resolve | vendor_hosted (Cloud Project Library) | Blackmagic | | | | | | | |
| | Resolve | private_controlled (Project Server) | customer server | | | | | | | |

## Gate D — timecoded note → NLE marker

| Date | NLE + version | Note timecode | Imported marker timecode | Frame delta | Frame rate | Method (file/panel) | Verdict |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Onboarding facts still unconfirmed (fill before scheduling tests)

| Question | Answer for this pilot |
|---|---|
| Does the team's actual Creative Cloud plan include Team Projects? | |
| Does the installed Resolve build expose multi-user collaboration? | |
| Who hosts and pays for a Blackmagic Cloud Project Library (if used)? | |
| Is there compatible shared storage for Premiere Productions? | |
| Is there a private Resolve Project Server host that stays online? | |


## Pending verification: marker import (added 11 Nov 2026)

| Target | Artefact | Status |
|---|---|---|
| Premiere Pro | `/api/assets/<id>/markers?format=premiere` (CSV) | **unverified** — no import attempted |
| DaVinci Resolve | `/api/assets/<id>/markers?format=resolve` (TSV) | **unverified** — no import attempted |

To close either row: import the file into a real project on a licensed machine,
record the NLE version, the OS, whether the marker landed on the intended frame,
and whether a re-import created duplicates (it must not). Until then every
response carries `X-LFMC-Export-Verified: no` and the UI says "not yet verified".
