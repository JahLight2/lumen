# Seeing Lumen in Google Antigravity

Everything here is written against Antigravity **v2.31.1** (current build at the
time of writing) and the docs as they stand on 11 Nov 2026. Antigravity ships
fast and has cut its free quota without notice before — if a menu name differs,
the shape of the steps is still right.

**What I cannot do from here:** install Antigravity, sign in as you, or take a
screenshot of the running app. It needs a Google account, a desktop, and a
browser. What this document does is make the repo *open cleanly* in Antigravity,
give you two copy-paste prompts that make the agent show you the product, and
name the places where Antigravity will confidently do the wrong thing.

---

## 0. Read this first if you have real footage indexed

Antigravity's agent reads your workspace and sends that context to Google's
models. The `.agents/rules/media-privacy.md` rule forbids it from reading media
bytes, and nothing in `lfmc/` opens footage during normal work — but a *catalogue
database containing real filenames and folder structure* would still be text the
agent can read if it chooses to.

Two safe options:

1. **Preview with synthetic data (recommended).** `tools/preview.sh` builds a
   sample drive of fake clips. This is what the prompts below assume.
2. **Keep real work in a separate checkout.** Open the repo in Antigravity with
   no real roots registered (`python3 -m lfmc.cli status` should say
   "no roots registered yet"), and run the real catalogue from a different copy.

Do not open a checkout whose `data/catalogue.db` indexes a client drive and then
let an agent loose on it without deciding that explicitly.

---

## 1. Pick the right surface

| Surface | Version | Why you'd want it |
|---|---|---|
| **Antigravity IDE** | v2.6.0+ (standalone) | **This is the one for seeing the app.** Only the IDE and the VS Code extension have `/browser`, the subagent that drives Chrome, screenshots the UI and records a WebM |
| Antigravity App | v2.5+ | The general desktop app; good for planning/slices, no `/browser` subagent |
| `agy` CLI | v1.2+ | Terminal client. Fine for "run the tests", no browser |
| VS Code extension | v1.9.0+ | Same `/browser` capability if you already live in VS Code |

So: **IDE (or the VS Code extension) if you want to watch it work.** Everything
else is fine for building.

### Requirements, checked against the current download page

- **macOS 13 (Ventura)** or later — Apple Silicon or 64-bit Intel. (This moved
  up from macOS 12; older machines are out.)
- **Windows 10 (64-bit)** or Windows 11.
- **Linux**: glibc ≥ 2.28, glibcxx ≥ 3.4.25 (Ubuntu 20+, Debian 10+, Fedora 36+,
  RHEL 8+). Newer than the last time I checked — if you're on Linux, this is now
  a real option.
- **Python 3.10+** for this repo specifically.
- **Node.js** on `PATH` for `npx` — the `/browser` subagent starts Chrome
  DevTools MCP through it.
- **Google Chrome 144+**, same machine as the workspace, with
  `chrome://inspect/#remote-debugging` → *Allow remote debugging for this browser
  instance* switched on.
- A Google account, 18+, in a region Antigravity supports. **Ethiopia is still
  unconfirmed** — if sign-in refuses, the fallback is the VS Code extension (or
  just running the host and opening `http://127.0.0.1:8765/` in Chrome yourself,
  which needs no agent at all).

---

## 2. Sign in and open the folder

1. Install, launch, sign in through the browser.
2. At the "What best describes your work?" screen pick **Software Engineering**
   (this is the layout with the terminal and the agent panel where you want them,
   and it offers to import settings from Cursor/Claude Code/Gemini CLI).
3. **Accept the Terms & Data Use notice only after you've read §0 above.**
4. `File → Open Folder` → the `lfmc` directory (the one containing `AGENTS.md`).
5. Approve **workspace trust** when asked — the agent will not index the repo
   until you do.
6. Confirm the rules loaded. In the agent panel, ask:

   > Which rule files are you following in this workspace, and what does
   > `AGENTS.md` say about reading footage?

   You should get back `AGENTS.md` plus the seven files in `.agents/rules/`, and
   an answer about never reading media bytes. If it says it found no rules,
   something is off with the folder root — you probably opened the parent
   directory instead of `lfmc/`.

---

## 3. Get the files in

Either unzip the bundle:

```bash
unzip lumen-source-2026-10-23.zip -d lfmc
cd lfmc
```

…or clone the repo you already have. Then confirm the starting state **before**
asking the agent for anything:

```bash
python3 -m unittest discover -s tests -t .     # expect: Ran 161 tests ... OK
```

If that number isn't 161, stop and find out why. A green baseline is what makes
"the agent broke something" detectable.

---

## 4. First prompt — make it show you the app

Paste this into the Antigravity agent panel (not a plain "build me X" prompt):

> Read `AGENTS.md`, `.agents/rules/`, `docs/COLLAB.md` and `docs/PROGRESS.md`
> first. Do not change any code in this session.
>
> Then:
> 1. Run `python3 -m unittest discover -s tests -t .` and report the exact count.
> 2. Run `./tools/preview.sh 8765` in the background (it seeds a synthetic sample
>    drive on first run — this is demo mode, synthetic data only, and demo mode
>    disables authentication by design).
> 3. Confirm `http://127.0.0.1:8765/api/health` returns `ok: true, demo: true`.
> 4. Tell me the three commands I would run to create a real collaborator token
>    and start the host without `--demo`.
>
> Do not read any file under `data/`. Do not add dependencies. Report what you
> observed; if something fails, paste the error rather than fixing it silently.

That gives you a running host and a verified baseline without touching a line.

---

## 5. Second prompt — see it, with pictures

Now use the browser subagent. In the Antigravity IDE panel:

> /browser Open http://127.0.0.1:8765/ and walk through this, taking a
> screenshot at each step:
>
> 1. The catalogue: drive tree on the left, asset grid in the middle. Screenshot.
> 2. Click one video asset, open the **Comments** tab. Screenshot the threaded,
>    timecoded comments.
> 3. Open the **Share** tab and create a review link. Screenshot the link panel
>    (the token is shown once — that fact is part of the point).
> 4. Open the generated `/r/<token>` review page. Screenshot it.
> 5. Go to `#nle` and `#ai`. Screenshot both, and tell me exactly which claims on
>    those screens are marked unverified and why.
> 6. Report any console errors. Do not fix anything yet.

Two things to watch for, because they're the real test:

- On step 3, the app should say the marker formats are **not verified in an NLE**.
  If an agent ever removes that wording to make a screenshot look cleaner, that's
  a rule violation, not a polish.
- On step 6, note whether asset thumbnails render. They are synthetic MP4s and
  this environment has no real codec pipeline, so placeholders are expected and
  correct. A pretty thumbnail appearing would mean something is faking it.

The screenshots and the WebM land as Antigravity *artifacts* you can review and
comment on — that's the "see it" part.

---

## 6. Seeing the roles actually differ

Worth doing once, because it's the slice that changed tonight. In a terminal
inside the IDE:

```bash
python3 -m lfmc.cli --db data/real.db seed-demo --dir data/sample_drive
python3 -m lfmc.cli --db data/real.db collaborators add "Client reviewer" --role reviewer
#   copy the token it prints (shown once)
python3 -m lfmc.cli --db data/real.db serve --port 8766
```

Then ask the agent:

> /browser Open http://127.0.0.1:8766/?token=<the reviewer token> and:
> screenshot the sidebar role chip; open an asset's Share tab and screenshot what
> it says; confirm the server rejects a review-link creation from this token with
> a 403 and that no link was created. Then open the same URL without the token
> and screenshot the result.

You should see: the chip reading *Client reviewer · reviewer*, a Share tab that
explains the limitation rather than failing mysteriously, a 403, and a `401` for
the anonymous case. That's the difference between a hidden button and a real
control.

---

## 7. What Antigravity cannot verify, no matter how good the prompt

Say this to yourself before reading any confident summary an agent produces:

| Claim | Why Antigravity can't settle it |
|---|---|
| "Markers import into Premiere / Resolve" | No licensed NLE on the machine, and it is macOS/Windows-only software. Both formats are **unverified** until `docs/NLE_TEST_LOG.md` has a machine, a version and a date |
| "The WebDAV mount works on macOS/Windows" | Needs Finder and the Windows redirector. Linux-only testing here; `docs/WEBDAV_MOUNT.md` says so |
| "Two editors, one project, no lost work" | Needs two machines and two humans ignoring/respecting a lock. The hub *detects* conflicts; it does not prevent them |
| "Search is semantic" | There is no model and no index running. Anything semantic today is a label on a screen, and the UI says so |
| Thumbnails are real frames | No ffmpeg/ffprobe in this pipeline yet |

An agent that reports any of these as working has started inventing. The rules
in `.agents/rules/nle-integrity.md` and `static-demo-honesty.md` exist to make
that a caught mistake rather than a shipped claim.

---

## 8. Working rhythm (the quota is real and unpublished)

Google does not publish the free weekly agent quota, and cut it roughly 250 → 20
requests/day once before without announcement. So:

- **One slice per session.** Start a fresh conversation per slice so context
  doesn't carry junk; `AGENTS.md` and the rules reload anyway.
- **Let the agent run the suite, you review the diff.** Every prompt above ends
  with "report what you observed", not "make it work".
- **Never enable a paid tier to get more quota.** `docs/COSTS.md` has the current
  prices (Pro $19.99, Ultra $99.99–199.99) and the $0 rule that says no.
- If access is cut mid-slice: everything in `.agents/` is plain Markdown and the
  suite runs on stdlib Python, so another agent — or you, in a terminal — can
  pick up exactly where it stopped. That's deliberate.

---

## 9. If sign-in fails for your account

Ethiopia is not on the confirmed region list, and I could not verify it for you.

1. Try the **VS Code extension** (v1.9.0+) — same agent, different distribution.
2. Try the `agy` CLI installer:
   `curl -fsSL https://antigravity.google/cli/install.sh | bash`, then
   `agy --version`.
3. If all routes refuse: you have lost nothing. The product runs on plain Python:

   ```bash
   ./tools/preview.sh 8765
   ```

   …and open `http://127.0.0.1:8765/` in any browser. Antigravity writes code; it
   was never in the serving path.

---

## 10. Where the state actually lives

| Thing | File |
|---|---|
| What shipped tonight, roles/scopes/API | `docs/COLLAB.md` |
| Decisions and why (ADR-009, ADR-010, schema v2) | `docs/ARCHITECTURE.md` |
| Progress and the "requires your machines" list | `docs/PROGRESS.md` |
| Free-tier facts and the Supabase decision | `docs/COSTS.md` |
| The agent contract | `AGENTS.md` |
| Scoped rules | `.agents/rules/*.md` |
| NLE verification ledger (currently empty of proofs) | `docs/NLE_TEST_LOG.md` |
