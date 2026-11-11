# Pushing this to GitHub

## Why I can't do it for you

Arena has no GitHub connector (`list_connector_tools github` → *unsupported*), and
this sandbox holds no credentials for your account. So the push is a two-minute
job on your machine. **Do not paste a personal access token into this chat** — a
token pasted here is a token in a transcript.

Everything is prepared: the tree is clean, nothing sensitive is tracked, and a
CI workflow is in place so the repo proves itself on the first push.

---

## Option A — GitHub CLI (fastest, one line)

```bash
cd lfmc
gh auth status || gh auth login
gh repo create lumen --private --source=. --remote=origin --push
```

That creates the repo, sets `origin`, and pushes `master`.

## Option B — plain git

1. Create an empty repo at github.com (no README, no .gitignore — you already
   have both).
2. Then:

```bash
cd lfmc
git remote add origin git@github.com:<your-account>/lumen.git   # or https://...
git push -u origin master
```

If your default branch is now `main` on GitHub and you want to match:

```bash
git branch -M main
git push -u origin main
```

## Option C — no network / different machine

A full-history bundle is in the workspace root:

```bash
# on this box, already created:
#   ../lumen-2026-11-11.bundle
git clone lumen-2026-11-11.bundle lumen
cd lumen && git remote set-url origin <your repo URL> && git push -u origin master
```

A bundle carries every commit, so you can move the repo over a USB stick and push
later without losing history.

---

## Before you push: decide visibility

**Recommendation: private.** Two specific reasons, not vibes:

1. `docs/COSTS.md`, `docs/PROGRESS.md` and `docs/BLUEPRINT_SUMMARY.md` describe
   your pricing position, the free-tier analysis, the pilot plan and what is
   unfinished. That is business strategy, not code.
2. The product's value is partly the method in `docs/` and `.agents/rules/`.
   Publishing it gives that away with no licence decided.

If you do want it public, do these first:

- [ ] Scrub or move `docs/COSTS.md` and `docs/BLUEPRINT_SUMMARY.md` (they contain
      your commercial reasoning).
- [ ] Choose a license (README says all rights reserved until you do).
- [ ] Check the third-party terms in `docs/COSTS.md` — Ultralytics is AGPL,
      InsightFace is non-commercial. Shipping a public repo that later adds those
      creates an obligation.

---

## What I already verified (nothing sensitive is tracked)

| Check | Result |
|---|---|
| Media, catalogue DBs, tokens, `.env` committed | **none** — `.gitignore` covers `data/`, `*.db`, `*.token`, `.env` |
| The blueprint file inside the repo | **no** — it stays in `uploads/`, outside version control |
| Secret patterns (`ghp_`, `sk-`, `AKIA…`, private keys, inline passwords) | **none found** |
| Personal email in tracked files | **none** |
| Host absolute paths in the shipped demo (`/home/...`, `/Users/...`, `C:\Users`) | **scrubbed** — the exporter now rewrites root paths to `/Volumes/<label>` and *refuses to export* if a host path survives |
| Demo assets | 7 synthetic clips, self-generated; no real footage anywhere |
| Test suite | 161 passing, from a clean unzip as well as the repo |

Re-run the sensitive-file check yourself any time:

```bash
git ls-files | grep -E '\.(db|db-wal|token|mp4|mov|mxf|wav)$|^data/' || echo "clean"
```

---

## After the push

1. **Watch the first CI run.** `.github/workflows/tests.yml` runs the suite on
   Linux (3.10 / 3.12 / 3.13) and — non-blocking — on macOS and Windows. Those
   two exist to find platform assumptions before a customer does. If they fail,
   record it in `docs/PROGRESS.md` under "Requires your machines"; do not delete
   the jobs to get a green tick.
2. **Turn on branch protection** on `master`: require the `linux` checks to pass.
   It is the cheapest way to stop a tired 1 a.m. commit from landing.
3. **Keep `data/` ignored, forever.** The catalogue indexes real drive contents.
   If a real `catalogue.db` ever gets committed, treat it as a data incident:
   remove it, rotate, and force-push with the team's agreement.
4. **Store the host token nowhere in the repo.** It lives in
   `data/catalogue.token` (0600) and is regenerated when absent.
5. Optional, still $0: GitHub Actions free minutes cover this workload easily on
   a private repo; there is no paid plan involved.

---

## Suggested repo hygiene after the first week

- Tag the state you're shipping to the pilot: `git tag -a v0.1-pilot -m "first
  controlled pilot"` — then you can always answer "what exactly did they run?".
- Add `docs/GATES.md` when the first acceptance gate closes, so the evidence for
  each gate lives in one place instead of being scattered across commits.
- Keep the blueprint out of git deliberately. It is the source of truth precisely
  because it does not drift with the code.
