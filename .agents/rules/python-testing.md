---
trigger: glob
globs: "lfmc/**/*.py, tests/**/*.py"
description: "Python conventions and the test loop used in this repository."
---

# Python and tests

- Standard library only for runtime code. Dev-only tooling (jsdom, terser,
  fflate) lives under `tools/` and must stay out of `lfmc/`.
- Tests: stdlib `unittest`, discovered with
  `python3 -m unittest discover -s tests -t .` (needs `-t .`). Expect **161**
  tests passing before your change; keep the count honest — if you add tests,
  say so, and never delete or `skip` an existing test to go green.
- Every behavioural change needs a test that fails before and passes after.
  Bug fixes get a regression test that names the bug, not a vague
  `test_works`.
- Keep route-order, guest-leak, href-encoding and availability semantics covered:
  those four have already caused real bugs (see `AGENTS.md` gotchas).
- Long-running processes: never `pkill -f "lfmc.cli serve"` (it kills your own
  shell). Start servers with a captured PID and stop by PID.
- Run `python3 -u` for anything that streams output; stdout is block-buffered.
