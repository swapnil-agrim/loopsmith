# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Collectors, readers, and the DuckDB store bootstrap.

The store bootstrap (issue #99) lives in `store.py`. The collector adapter (issue #100) lives
in `collectors.py` (invocation) and `packs.py` (schema-keyed persistence). The ledger reader
(issue #101) lives in `ledger_reader.py` — entries + events streams, read-only, not yet wired
into `insight ingest` (see .sdlc/plans/101.md §F). The artifact reader (issue #102) lives in
`artifact_reader.py` — goal frontmatter, slice manifests, and the config snapshot, wired into
`insight ingest`. The git facts reader (issue #103) lives in `git_reader.py` — commit/merge
counts (schema="git-facts/v1", reusing packs.py's registry) and locally-derivable
first-commit-to-merge lead time, both wired into `insight ingest`; squash-merged PRs land with
an explicit degraded=["lead_time_requires_network"] row rather than a guessed number (see
.sdlc/plans/103.md §D). Other E1 readers are not implemented yet.

Deliberately imports nothing: `store.py` imports duckdb at module level, and a
re-export here would make a bare `import insight.ingest` — and so the whole CLI —
require duckdb on a checkout that never installed it.
"""
