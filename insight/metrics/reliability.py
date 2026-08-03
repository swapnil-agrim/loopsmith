# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The Class-2 coverage-denominator SQL fragment (issue #114, [E2.S7], Task 2; see
.sdlc/plans/114.md Design decision 4). Pure stdlib (no `duckdb` import -- matching
`header.py`/`loader.py`/`testing.py`'s own zero-dependency style): this module never touches a
database, only exports one string constant.

WHAT THIS IS, AND WHAT IT IS NOT: `COVERAGE_DENOMINATOR_COLUMNS` is a SQL column-fragment
CONVENTION a future class-2 metric's own `.sql` file pastes verbatim into its `SELECT` list --
NOT a callable helper and NOT a registered database object. `loader.py`'s own mechanism is
`CREATE OR REPLACE VIEW {view_name} AS {text}`, which treats every `.sql` file as opaque text; it
has no templating or `import` mechanism that could share a Python string INTO a `.sql` file at
runtime. A human author copy-pastes this constant's value at authoring time -- this is
documentation-as-code, not infrastructure a metric depends on at query time.

WHY NO PERSISTED, JOINABLE VIEW EXISTS INSTEAD: at the time this module was written, zero metrics
declared `-- reliability_class: 2` (every one of the 25 files in `insight/metrics/` declared `1`)
-- there was no live consumer for a coverage-denominator mechanism to serve yet. That is no longer
true: `insight/metrics/22.sql` (issue #144, [E7.S1], "Prevented rework") is the first real class-2
metric, and it pastes `COVERAGE_DENOMINATOR_COLUMNS` verbatim into its own `blocked` CTE -- the
live consumer this paragraph originally said did not exist yet. The reasoning below (why a
persisted view would repeat `phase_trace_completeness`'s mistake) still holds and is not
revisited by #144 landing; only the "there is no live consumer" premise has changed. This repo already
carries one dated, cautionary example of building exactly this kind of infrastructure ahead of
any consumer: `fact_goal.phase_trace_completeness` (`insight/ingest/store.py`), a schema column
added by issue #99 for this identical Class-2 coverage use case, still carrying zero writers and
zero readers four issues later (`store.py`'s own module docstring says so outright). Building a
persisted DuckDB view for coverage right now would repeat that mistake one layer up -- a VIEW
nobody joins against, instead of a COLUMN nobody reads -- and would additionally need a
registration home `loader.py` does not have (it globs every `*.sql` in this directory and
requires each to carry the full 5-field header; a helper view has no such header to offer
honestly). This constant is the tested, correct mechanism ready to paste in the day a class-2
metric ships, without adding a second unexercisable abstraction to the pile in the meantime.

`total_count` is a PLAIN `count(*)` -- deliberately including any untagged/NULL row, not just
`class1_count + class2_count` -- so a future untagged row dilutes `coverage_pct` instead of being
invisible to it (the identical NULL-is-not-proven-good reasoning `insight/metrics/*.sql`'s own
`reliability_class = 1` filters apply to a metric's own rows, applied here to the denominator
itself)."""

COVERAGE_DENOMINATOR_COLUMNS = """\
    count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
    count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
    count(*) AS total_count,
    ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4) AS coverage_pct"""
