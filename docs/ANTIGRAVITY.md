# Building Lumen in Google Antigravity

Antigravity is the agentic IDE from Google (Gemini 3, launched Nov 2025; this repo
was prepared against the docs as of **23 Oct 2026**). It is a **build tool**, not a
place to run the product: Lumen still runs on the machine that owns the drive.

Everything Antigravity needs is already committed to this repo:

```
AGENTS.md                  # always-on working contract (the important one)
.agents/rules/*.md         # 7 scoped rules: privacy, tests, UI, NLE, demo honesty, cost, blueprint
docs/ANTIGRAVITY.md        # this file
```

## 1. Install

Download from the official page only — **https://antigravity.google/download**

| Platform | Requirement |
|---|---|
| macOS | 12 (Monterey)+, Apple Silicon or Intel (the 2.0 docs mark x86 as unsupported — check the live page) |
| Windows | 10 64-bit+ (x64 or ARM64) |
| Linux | glibc ≥ 2.28, glibcxx ≥ 3.4.25 (Ubuntu 20+, Debian 10+, Fedora 36+, RHEL 8+) |

There are several surfaces: the Antigravity app, a standalone IDE, the `agy` CLI,
and extensions for VS Code / JetBrains / Zed / Xcode / Visual Studio. Any of them
picks up `AGENTS.md` + `.agents/rules/`. Pick one and stay in it; two surfaces
editing the same slice at once just produces conflicts.

**Account and region:** Antigravity requires a Google account, and Google's FAQ
limits personal accounts to adults (18+) in *approved regions*. Ethiopia is not
one I can confirm from the docs, and I will not guess — do the sign-in check
first, before you plan any work around it. If sign-in is refused for your
region/account, the fallback is the `agy` CLI behind a supported account, or
continue with the rules pack in another agent (it is plain Markdown).

**Cost:** the Individual plan is $0/month with no card required — unlimited Tab
completions and command requests, but a **weekly agent quota whose size Google
does not publish**, and which has been cut several times since launch. Treat it
as: free for evaluation, unreliable as a guarantee. Nothing in this repo requires
a paid plan; if the quota runs out, the tests and the product still run locally.
See `docs/COSTS.md`.

## 2. Open the project correctly (do this before the first prompt)

Antigravity agents work inside a **Project** whose folders define what they can
reach. Two rules for this repo:

1. **Open the repo folder itself** (`lfmc/`, the folder containing `AGENTS.md`)
   as the project root. Antigravity walks up to the repo root and loads
   `AGENTS.md` and `.agents/rules/` from there.
2. **Keep the drive out of the project.** Your footage lives at something like
   `/Volumes/ShootSSD/…` or `D:\Shoot_Day1\…` — register it with the CLI
   (`python3 -m lfmc.cli add-root …`) and *never* add it as a project folder.
   `data/` stays gitignored, and media reads stay inside the running app, not
   inside the agent's context.

If you ever want a second checkout for experiments, clone it outside the project
rather than widening the boundary.

## 3. Permission preset: "Request Review", then loosen deliberately

Antigravity ships four presets — **Request Review**, **Default**, **Auto**,
**Turbo** — and commands are `Ask` unless you say otherwise. For this repo start
at **Request Review** and use an allow/deny list. Recommended, adapted from the
official permissions syntax:

```json
{
  "permissions": {
    "allow": [
      "command(regex:^git (status|diff|log|add|commit))",
      "command(regex:^python3 -m unittest)",
      "command(regex:^python3 -u -m lfmc\\.cli (serve|seed-demo|seed-reviews|add-root|scan|status))",
      "command(regex:^node tools/)",
      "command(regex:^npm install --no-save)",
      "read_file(.agents/)",
      "read_file(docs/)",
      "write_file(lfmc/)",
      "write_file(tests/)",
      "write_file(docs/)",
      "write_file(tools/)"
    ],
    "deny": [
      "read_file(data/)",
      "write_file(data/)",
      "read_file(uploads/)",
      "write_file(uploads/)",
      "write_file(.git/)",
      "command(rm -rf)",
      "command(sudo)",
      "execute_url(*)"
    ],
    "ask": [
      "command(*)",
      "read_url(*)",
      "mcp(*)"
    ]
  }
}
```

Why those specifics:

- `deny read_file(data/)` and `deny write_file(uploads/)` enforce rule §2 of
  `AGENTS.md` mechanically: the agent cannot wander into the catalogue or the
  blueprint. `uploads/` holds the blueprint — read-only by policy, so nothing
  writes to it.
- `command(rm -rf)` / `command(sudo)` / `execute_url(*)` are the three ways an
  agent ruins your afternoon.
- Keep `read_url(*)` on **ask**, not deny: reading public docs is legitimate and
  often necessary, but you want to see each domain.

Also leave **Agent Non-Workspace File Access** off (it is off by default) and do
not enable Turbo on this repo — Turbo grants unrestricted commands and full
filesystem access, which defeats the media-privacy rules entirely.

## 4. First prompts (small, verifiable slices)

Paste these one at a time. Each one is sized to the weekly quota and ends in
something you can check.

**Prompt 1 — confirm the agent read the contract**

> Read AGENTS.md and .agents/rules/. Then, without changing any file, tell me:
> what is off-limits for you here, which single command proves the suite is
> green, and what you cannot verify on this machine. Then run the test suite once
> and report the exact count.

**Prompt 2 — a real vertical slice (marker round-trip)**

> Slice: make note→marker export idempotent. Add a stable note→marker id map in
> lfmc/core/review.py so a second export updates markers instead of duplicating
> them, expose it through the existing `/api/assets/{id}/markers.csv` route, and
> cover it with a regression test in tests/test_review.py that fails before your
> change. Then run the full suite and show the count. Do not touch
> uploads/ or data/.

**Prompt 3 — the UI comparison you actually want**

> Start the host on 127.0.0.1:8765 with --demo, then use the browser to compare
> the app shell against the interaction model described in docs/UI.md: drive tree,
> asset grid, Details/Comments/Transcript/Similar tabs, threaded timecoded
> comments, share-link panel. Screenshot what you see, list concrete mismatches,
> and propose the smallest fix for the top three. Do not copy any Shade Inc. copy,
> logos or assets.

**Prompt 4 — a decision, not code**

> Read docs/COSTS.md and docs/RISKS.md. Then give me the three cheapest options
> for letting a remote editor (not on my LAN) reach the host with $0 platform
> spend, with each option's data-flow, failure mode and what it would cost to
> make it reliable. No implementation yet.

## 5. What Antigravity should not be asked to do

- **Host the product.** It is serverless-hostile by design: long-lived process,
  SQLite/WAL on disk, byte-range reads from the attached drive, WebDAV mount.
  The only hostable artefact is the read-only snapshot (`docs/DEMO_DEPLOY.md`).
- **Verify the unverifiable.** macOS/Windows mounts, FUSE/WinFsp, real Premiere
  and Resolve behaviour, and real codecs cannot be exercised in this environment.
  They go on the "Requires your machines" list in `docs/PROGRESS.md`.
- **Handle footage.** No reading, hashing, summarising or uploading media, ever.
  It is in the rules; it is also in the permission deny-list.

## 6. Working rhythm that fits the quota

1. One slice per session; keep the todo list in the agent's task view, not in chat.
2. Let it run the suite itself; you review the diff, not the transcript.
3. Commit small (`git add -A && git commit -m "slice: …"`). If a session dies on
   quota mid-slice, `git status` tells you exactly where it stopped.
4. Anything the agent asserts about NLE or host behaviour is **unverified** until
   it is in `docs/NLE_TEST_LOG.md` with a machine and a date next to it.
