---
trigger: model_decision
description: "Applies whenever reading files, running scans, handling catalogue data, or making any network request in this project — footage and project metadata must never leave the machine or be pulled into the agent context."
---

# Media and metadata privacy

Non-negotiables:

1. **Never read media bytes.** Do not open, `cat`, hash, hexdump, copy or
   "peek" at files under any registered root (`data/sample_drive/`, any
   `add-root` path, or any `abs_path` in the catalogue). Do not run `ffprobe`,
   `file`, `exiftool` or `md5sum` on them "just to check".
2. **Never read the catalogue DB as data to analyse.** You may inspect schema
   (`PRAGMA table_info`) and row *counts*. Do not dump comments, transcripts,
   filenames of real client projects, or review links into the conversation.
3. **No external requests carrying project content.** No uploads, no pasting
   file contents into a web request, no "free API" transcribe/vision/translate
   call, no analytics or telemetry beacons. Localhost requests to the running
   server are fine. Public documentation lookups are fine.
4. **Console/artifact output is captured.** When demonstrating behaviour, use the
   synthetic sample drive and redact real paths and client names, e.g.
   `/Users/<name>/Shoot/<redacted>`.
5. **Face data, transcripts and biometrics are special-category data.** Do not
   propose features that ship them to third parties. Local-only processing with
   explicit user consent is the default; anything else is an open decision for
   the user, documented in `docs/RISKS.md`.
6. If a task appears to require breaking 1–5, stop and ask the user. Do not
   proceed on a "probably fine" basis.
