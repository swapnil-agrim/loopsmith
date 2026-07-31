# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Collectors, readers, and the DuckDB store bootstrap.

The store bootstrap (issue #99) lives in `store.py`. The collectors and readers
of E1 (issue #98) are not implemented yet.

Deliberately imports nothing: `store.py` imports duckdb at module level, and a
re-export here would make a bare `import insight.ingest` — and so the whole CLI —
require duckdb on a checkout that never installed it.
"""
