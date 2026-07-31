# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Collectors, readers, and the DuckDB store bootstrap.

The store bootstrap (issue #99) lives in `store.py`. The collector adapter (issue #100) lives
in `collectors.py` (invocation) and `packs.py` (schema-keyed persistence). Other E1 readers are
not implemented yet.

Deliberately imports nothing: `store.py` imports duckdb at module level, and a
re-export here would make a bare `import insight.ingest` — and so the whole CLI —
require duckdb on a checkout that never installed it.
"""
