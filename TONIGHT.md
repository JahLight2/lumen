# Tonight — one evening, one demoable cut

Reality check first: a full MVP in one evening is not possible, and pretending
otherwise wastes the evening. What *is* possible is a **tonight cut** — three
vertical slices that close real V1 gaps, each with a test, each leaving the repo
green, ending in a demo you can actually show someone tomorrow.

Everything below is sized for Antigravity's free-tier weekly quota: one slice per
session, agent runs the suite, you review the diff and commit.

**Starting point:** 103 tests green, host runs, review links work, WebDAV mount
read-only, static snapshot deployed. Nothing here is broken; these are gaps.

---

## Slice A — Per-collaborator tokens (≈45 min)

Right now one host token opens everything. A pilot team needs to know *who*
commented and later revoke one person without changing everyone's password.

**Antigravity prompt:**

> Slice: per-collaborator tokens with scopes. Add a `collaborators` table
> (id, name, role, token_hash, created_at, revoked_at) and a `tokens` scope model
> in `lfmc/core/auth.py`. Accept `Authorization: Bearer <token>` alongside the
> existing `?token=` and `X-LFMC-Token` paths. Roles: `producer` (read+comment+
> share+select), `editor` (read+comment+marker export), `reviewer` (read+comment
> on links only). The host token keeps full rights. Store only hashes. Add
> `lfmc/cli.py collaborators add|list|revoke`. Cover: valid token, wrong scope
> rejected with 403, revoked token rejected with 401, expired token 401.
> Keep `--demo` working. Run the full suite and report the count.

**Done when:** `python3 -m unittest discover -s tests -t .` is green, and
`curl -H "Authorization: Bearer <revoked>" .../api/roots` returns 401.

**Commit:** `auth: per-collaborator tokens with roles and revocation`

---

## Slice B — Notes → NLE markers, idempotent (≈45 min)

Markers exist but re-exporting duplicates them. A reviewer who adds a note on
Thursday must not get a second marker on Friday.

**Antigravity prompt:**

> Slice: make note→marker export idempotent and add a Resolve-readable format.
> In `lfmc/core/review.py`, add a stable `marker_key` per note (asset_id +
> comment_id) and persist the exported marker id per target NLE, so a second
> export updates instead of appending. Add `format=premiere` (existing CSV) and
> `format=resolve` (tab-separated: marker name, description, timecode, duration,
> colour, notes) behind `/api/assets/{id}/markers` with `?format=`. Keep
> `/api/assets/{id}/markers.csv` working for compatibility. Add a regression test
> that exports twice and asserts the marker count is unchanged and the id is
> stable. Do not claim either format is verified inside Premiere or Resolve —
> mark them unverified in the docstring.

**Done when:** suite green; export twice, count unchanged; both formats download.

**Commit:** `review: idempotent marker export + resolve layout (unverified in NLE)`

---

## Slice C — Project hub: locks, versions, conflict recovery (≈90 min)

The biggest remaining V1 gap, and the one that needs the most care. This is
*references and status only* — never a second copy of a `.prproj` or `.drp`, and
never a byte-merge.

**Antigravity prompt:**

> Slice: project hub lock/version registry. Add tables `project_locks`
> (project_id, holder, acquired_at, expires_at, released_at) and
> `project_versions` (id, project_id, label, author, created_at, source_path,
> fingerprint, note). Endpoints: `POST /api/projects/{id}/lock` (409 with the
> current holder if taken, 410 if the lock expired), `POST …/lock/release`,
> `POST …/versions` (register a checkpoint by fingerprint — store the *path and
> hash only*, never the file), `GET …/versions`, and
> `POST …/recover` which returns the newest version metadata plus the
> instructions string for the owning NLE ("reopen from <path>, do not overwrite").
> Do not implement byte-merge, do not copy project contents into the catalogue,
> and do not fake any NLE API. Cover: lock conflict 409, expiry reclaim, version
> chain ordering, recovery returns newest without touching bytes.

**Done when:** suite green; a second lock attempt returns 409 naming the holder;
`recover` returns version metadata and the instruction text, and no project file
bytes are read.

**Commit:** `hub: project locks, version registry, conflict recovery`

---

## If you still have energy: Slice D — ship the snapshot (≈20 min)

After A–C: `node tools/export_static_demo.py` refresh, re-pack, redeploy the
Vercel snapshot, and update the Notion board. The banner says read-only; keep it.

---

## Explicitly NOT tonight

| Temptation | Why not |
|---|---|
| Semantic AI search + scene/person/speech discovery | Needs a local model + indexing pipeline. "Semantic" without a model is a lie on the label. Multi-day slice. |
| Real Premiere/Resolve verification | Needs your licensed machines. Everything NLE stays **unverified** until `docs/NLE_TEST_LOG.md` has a machine and a date. |
| Moving the catalogue to Supabase | See below. It changes residency from private to vendor-hosted. That is a decision, not a chore. |
| Cloud media storage / S3 | Deferred by the blueprint and breaks the local-first promise. |
| Two-editor conflict test | Cannot be done on one machine; goes on the "Requires your machines" list. |

---

## Supabase: the honest answer

You turned it on, so here is the full picture rather than a quiet no.

**Why it doesn't fit tonight (or the local-first design):** Lumen's catalogue is a
SQLite file on the editor's own machine. Moving it to Supabase makes the service
dependent on a vendor database in another country, adds a network hop to every
browse, and — decisively — makes the deployment **vendor-hosted by definition**,
which the blueprint treats as a separate residency mode requiring informed
consent, not a swap-in. It also would not make the WebDAV mount or originals
streaming work, because those live next to the drive.

**Where it could legitimately go, later:** the *first pilot's vendor-hosted
control plane* — account identity, review-link metadata, and comment/reaction
sync so a client's phone can post a note while your host is asleep. Metadata
only, never footage, never transcripts. That is a real blueprint-sanctioned mode;
it is also a multi-evening architecture job with its own consent and disclosure
work.

**Free-tier facts if you do go there** (recorded in `docs/COSTS.md`): 2 active
projects, 500 MB DB each, 1 GB storage, 5 GB + 5 GB cached egress, 50k MAU,
**projects pause after 7 days idle and do not wake on request**, no automatic
backups, 2 auth emails/hour on the built-in sender. Commercial use *is* permitted
on free — unlike Vercel Hobby — but the 7-day pause means it cannot be the only
path for anything a client depends on.

**Recommendation:** leave it off tonight. Local SQLite stays authoritative. If
you want the vendor-hosted pilot mode, that's the first thing we plan after this
cut, with the disclosure written before the code.

---

## End-of-night checklist

1. `python3 -m unittest discover -s tests -t .` — green, count recorded.
2. `git log --oneline -5` — three slice commits.
3. Host restarted, `/api/health` 200, one review link opened on your phone.
4. `docs/PROGRESS.md` updated: what moved, what stayed open.
5. Anything NLE-related still marked **unverified**.
6. Snapshot regenerated and redeployed (if you did Slice D).
