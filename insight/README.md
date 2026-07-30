# LoopSmith Insight

The analytics platform and configuration UI that sits on top of LoopSmith. Design spec:
[`docs/superpowers/specs/2026-07-30-loopsmith-insight-data-platform-design.md`](../docs/superpowers/specs/2026-07-30-loopsmith-insight-data-platform-design.md).

## This directory is NOT MIT

Everything under `insight/` is licensed under the **Business Source License 1.1** — see
[`insight/LICENSE`](LICENSE). It is source-available, not open source: you may read it, modify it,
and run it against your own projects, but you may not offer it to third parties as a hosted service
until the Change Date (2030-07-30), when it converts to MIT. Parameter rationale and the standing caveats are in [`LICENSE-NOTES.md`](LICENSE-NOTES.md).

**Everything outside `insight/` — the LoopSmith plugin — stays MIT**, under the repository root
[`LICENSE`](../LICENSE). The folder boundary is the licence boundary.

## Every Python source file here carries the licence marker

Each `.py` file in this directory must begin with the exact line in
[`HEADER.txt`](HEADER.txt), on line 1 — or on line 2 when line 1 is a shebang or an encoding
cookie, so `#!/usr/bin/env python3` keeps working.

```
# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
```

This is enforced by [`tests/test_licence_boundary.py`](../tests/test_licence_boundary.py), which
reads the marker from `HEADER.txt` rather than restating it, so the two cannot drift.

Only `.py` is enforced today. Subsystems D and E bring TypeScript and SQL into this folder; extending the enforced extension set is tracked as **issue #163**.

## The boundary with the plugin

The plugin and this product communicate through **file formats, never imports** (spec §1.1 rule 1).
Nothing under `skills/` or `hooks/` may import `insight`, and nothing here may import them as
Python modules — reading their output files by path is the entire contract. `insight/` will also
get its own dependencies, CI job, and version, none of which the plugin installs — that is
[issue #95 (E0.S2)](https://github.com/swapnil-agrim/loopsmith/issues/95) for the package and its
dependencies, and [#96 (E0.S3)](https://github.com/swapnil-agrim/loopsmith/issues/96) for the
separate CI job and coverage gate.
