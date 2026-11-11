# Collaboration: roles, markers, and the project hub

What was built on the evening of 11 Nov 2026, why each piece exists, and — in
detail — what is *not* proven. Three self-contained slices were added behind the
existing host: scoped collaborator tokens, idempotent marker export, and a
project lock/version registry.

Test count went 103 → 161. No existing endpoint lost a capability, one gained a
much-needed restriction (below).

---

## 1. Scoped collaborator tokens (`lfmc/core/auth.py`)

### The problem

One host token meant every comment was authored by `member` and revoking one
person was a team-wide password change. Both are fatal in a 3–5 person pilot.

### Roles

| Role | read | comment | select | markers | share | hub | admin |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| host token | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| producer | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| editor | ✓ | ✓ | ✓ | ✓ | — | ✓ | — |
| reviewer | ✓ | ✓ | — | — | — | — | — |

`admin` covers registering roots and managing collaborators, so only the person
at the machine can widen access.

### Using it

```bash
python3 -m lfmc.cli collaborators add "Abebe K." --role editor
#   prints the token ONCE; only its SHA-256 hash is stored
python3 -m lfmc.cli collaborators add "Client review" --role reviewer --expires-hours 72
python3 -m lfmc.cli collaborators list [--all]
python3 -m lfmc.cli collaborators revoke <id>
```

A collaborator opens `http://<host>:8765/?token=<their token>`. The UI keeps the
token in `sessionStorage` for that tab, attaches it to every API call, and shows
their name and role in the sidebar. Actions outside their scope are explained
rather than hidden silently — a reviewer who clicks Share sees "this needs the
producer role", not a mystery failure.

### HTTP semantics

| Situation | Response |
|---|---|
| no credential | `401 unauthorized` |
| unknown / revoked / expired token | `401` — deliberately indistinguishable, so probing cannot confirm a token ever existed |
| valid token, wrong scope | `403 insufficient_scope`, naming the role and the scopes held |
| comment posted with somebody else's name in the body | author is forced to the token's identity |

### Security fix that came out of this work

**Before:** `/api/roots`, `/api/browse`, `/api/search`, `/api/assets`,
`/api/assets/<id>`, `/api/events` and `/api/transfers` answered **anyone who
could reach the port**, with no token at all. On a café or studio LAN that is a
full catalogue leak: drive labels, absolute folder structure, filenames,
comments and transfer history. Only `/media/*` and `/dav/*` were ever guarded.

**Now:** all of them require the `read` scope. Guest review links (`/r/<token>`,
`/api/guest/...`) are unchanged and remain accountless — they authenticate with
the link token itself.

If you have any build older than this commit on a shared network, treat it as
having leaked filenames.

### Token handling, stated plainly

* 24 random bytes (`secrets.token_urlsafe`), stored only as SHA-256. A stolen
  `catalogue.db` does not yield live tokens.
* SHA-256 without a salt is deliberate: these are high-entropy machine-generated
  secrets, not human passwords. **If tokens ever become user-chosen, this must
  move to scrypt.**
* No email verification, no password reset, no MFA, no rate limiting, no TLS.
  A token is a bearer secret handed over out of band. Cleartext HTTP is why
  mounts are LAN/VPN-only (see `docs/WEBDAV_MOUNT.md`) — the same applies to the
  web UI today.
* Anyone holding a token *is* that collaborator. There is no device binding.

---

## 2. Idempotent marker export (`lfmc/core/review.py`)

### The problem

Exporting markers appended a new marker per note every time. A note added
Thursday and re-imported Friday produced two markers for one request — the kind
of bug that quietly corrupts a timeline.

### The rule now

* Every note has a stable `marker_key` = `asset_id:comment_id`. Editing the note's
  text does **not** create a second marker.
* Each `(note, target)` pair is recorded in `marker_exports`, with a stable
  `external_id` (the marker name). Re-export increments `export_count` and keeps
  the name identical.
* Premiere and Resolve are tracked separately, because an editor may use both.

```
GET /api/assets/<id>/markers?format=premiere|resolve[&dry_run=1][&json=1]
GET /api/assets/<id>/markers.csv          # legacy route, now ledger-backed
```

| Format | Layout |
|---|---|
| `premiere` | CSV — `Name, Timecode, Frame, FPS, Description, Author, Resolved` |
| `resolve` | TSV — `Marker Name, Description, Timecode, Duration, Color, Notes`; colour is Blue for open notes, Green for resolved |

Every response carries `X-LFMC-Export-Verified: no`.

### Verification status: UNVERIFIED in both NLEs

Neither file has been imported into a running Premiere Pro or DaVinci Resolve.
The column sets are documentation-derived. Frames are computed at 25 fps unless
the note carries its own rate, which will be wrong for 23.976/29.97 material
until real media is probed. **Do not tell an editor this works until
`docs/NLE_TEST_LOG.md` has a machine, a version and a date.**

---

## 3. Project hub (`lfmc/core/hub.py`)

References and status only. This is the module where the blueprint's integrity
rule is easiest to violate, so it was built to make violation hard.

### What it stores

* `project_locks` — holder, acquired_at, expires_at, released_at
* `project_versions` — label, author, **source_path**, stat fingerprint, size, note

### What it never does

* No project bytes are read. `register_version()` calls `os.stat()` and nothing
  else. The fingerprint is `stat:v1:<size>:<mtime_ns>`, deliberately **not** a
  content hash, because hashing would mean reading a `.prproj`.
* No copy, no upload, no sync, no byte-merge, no second copy of project contents.
* No "restore" button. Recovery reports; a human opens the file in the NLE.

Two tests enforce this: one patches `open()` to raise for the project path and
then exercises every hub operation; the other tokenises `hub.py`, strips strings
and comments, and asserts the remaining code contains no `open`, `read_text`,
`shutil`, `subprocess` or `copyfile` at all.

### Locks

A lock is a courtesy flag, not a mutex — the platform has no authority over the
filesystem, and an editor who ignores it cannot be stopped. What it does buy:
a second collaborator is told *who* holds the project and *until when*.

* Conflict → `409 lock_held`, naming the holder and expiry.
* Taken by the same holder → extends rather than deadlocks.
* **Expired lock → reclaimed**, and the response names who it was taken from.
  (The runbook originally proposed `410` here; reclaiming is better behaviour,
  and the conflict stays visible in the event log.)
* Only the holder may release, unless the host forces it.

```
POST /api/projects                          {"name": "..."}
GET  /api/projects | /api/projects/<id>
POST /api/projects/<id>/lock                {"holder","ttl_minutes"}
POST /api/projects/<id>/lock/release        {"holder","force"}
POST /api/projects/<id>/versions            {"label","author","source_path"}
GET  /api/projects/<id>/versions
POST /api/projects/<id>/recover
```

```bash
python3 -m lfmc.cli project create "Addis Documentary"
python3 -m lfmc.cli project lock <id> --holder abebe --ttl 90
python3 -m lfmc.cli project register <id> --label v1 --path "/Volumes/Edit/Addis_Doc_v1.prproj" --author abebe
python3 -m lfmc.cli project recover <id>
```

`recover` returns the newest version, the version chain, the current lock, and
NLE-specific instructions — including a blunt warning when the drive is not
mounted ("the registered path is NOT reachable right now … do not create a new
empty project at that path").

### What this does not solve

Two editors in the same `.prproj` at the same time is still unsolved, and this
module does not pretend otherwise. It detects and names the conflict; it does not
prevent it. Prevention needs the NLE's own collaboration features (Team Projects,
Resolve shared storage), each with its own licensing — see
`docs/NLE_PREREQUISITES.md` and `docs/NLE_TEST_LOG.md`.

---

## Test map

| File | Tests | Proves |
|---|---|---|
| `tests/test_auth.py` | 21 | hashing, expiry, revocation, 401 vs 403, impersonation, anonymous-read closed |
| `tests/test_markers.py` | 14 | idempotent re-export, stable names, format layouts, unverified header |
| `tests/test_hub.py` | 23 | lock conflict/expiry/release, version chain, recovery honesty, **no project bytes read** |
| existing suite | 103 | catalogue, range server, WebDAV, review, NLE residency |

Run everything: `python3 -m unittest discover -s tests -t .` → **161 OK**.

---

## Still not built (and why)

| Item | Blocker |
|---|---|
| Semantic search / scene / face / speech discovery | needs a local model + indexing pipeline; not an evening slice |
| Real NLE import verification | needs licensed Premiere/Resolve on real machines |
| Two-editor conflict rehearsal | needs two machines and two editors |
| macOS/Windows WebDAV mount proof | needs those hosts; Linux-only here |
| TLS for the UI and the mount | certificate handling is its own slice; until then LAN/VPN only |
| Supabase control plane | deferred by decision, see `docs/COSTS.md` |
