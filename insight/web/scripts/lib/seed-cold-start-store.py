#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Standalone fixture-seeder for scripts/prove-delivery-cold-start-no-numerals.mjs (issue #312
[E20.S1] Goal B, Task B4, done-when 4): seeds a schema-only, zero-row DuckDB store -- the SAME
fixture shape insight/tests/test_api_metrics_route.py's own cold-start test uses
(`open_store(db_path).close()`, no ingest, no `metric_*` views) -- and, when
`--populate-metric-12` is passed, additionally creates the `metric_12` view the SAME fixture
shape test_api_metrics_route.py::
test_populated_store_serialises_autonomy_rate_as_measured_via_the_real_endpoint uses.

The `--populate-metric-12` mode exists for this proof's own MANDATORY executed negative control
(.sdlc/plans/312.md §7 Task B4): a populated store must make at least one numeral appear
somewhere on the page, proving the "no digits anywhere" check has real teeth rather than passing
vacuously regardless of input.

`--populate-metric-14` exists for the SAME reason, for scripts/prove-manager-cold-start-no-
numerals.mjs's negative control (issue #313 [E20.S2]): metric 12 (autonomy rate) is not in that
page's curated id list, so populating it would never make a digit appear on /manager, and the
negative control would find zero digits for the wrong reason. Metric 14 (park rate) IS curated by
the manager page and has a real extractor, so populating it gives that proof's negative control
real teeth the same way metric 12 gives the delivery proof's.

`--populate-metric-5` and `--populate-metric-23` exist for scripts/prove-leadership-cold-start-
no-numerals.mjs's negative control (issue #314 [E20.S3], .sdlc/plans/314.md Step 8 -- a
plan-review amendment: Decision A calls id 23 "the concrete, non-vacuous exercise of done-when 2",
a WIRED class-2 metric rendering a numeral WITH its coverage denominator, but nothing populated it
before this fix). Metric 5 (change failure rate) uses `_change_failure_rate`
(insight/api/metrics.py:78-84): row[1]=window_commit_count, row[2]=repeated_revert_or_fixup_count,
row[3]=change_failure_rate. Metric 23 (gate catch rate by gate) uses the AGGREGATE extractor
`_gate_catch_rate` (insight/api/metrics.py:120-129), which reads EVERY row returned by
`SELECT * FROM metric_23` and sums r[3] (gate_event_count) and r[4] (catch_count) -- the seeded
view below mirrors insight/metrics/23.sql's own 13-column SELECT list in order so `SELECT *`
matches positionally, giving the extractor a real (value, numerator, denominator) to resolve
rather than an absence.

`--populate-actor-rows` exists for scripts/prove-leadership-no-individual-grain-leak.mjs (issue
#314 [E20.S3], Decision C): the SAME real actor-bearing row shapes
insight/tests/test_dash_leadership_guardrail.py's own `conn` fixture (lines ~47-65) already uses
-- a carol `fact_event` row, the carol->dave `fact_handoff` row, and one `dim_project` row -- so
both the Python guardrail and this web proof exercise the identical fixture shape, verified by
reading that fixture directly rather than guessed.

Run from ANY cwd -- mirrors seed-ic-fixture.py's own reasoning: `insight` is not `pip
install -e`'d in the always-on local gate (only in CI's `web` job), so REPO_ROOT is put on
`sys.path` explicitly below, before either import.
"""
import argparse
import datetime
import pathlib
import sys

# insight/web/scripts/lib/seed-cold-start-store.py -> parents[0]=lib, [1]=scripts, [2]=web,
# [3]=insight, [4]=REPO_ROOT (mirrors seed-ic-fixture.py's own identical derivation).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from insight.ingest.store import open_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to write the seeded DuckDB store to")
    parser.add_argument(
        "--populate-metric-12", action="store_true",
        help="also create metric_12 (autonomy rate) with a real row -- the negative control",
    )
    parser.add_argument(
        "--populate-metric-14", action="store_true",
        help="also create metric_14 (park rate) with a real row -- the manager page's negative "
             "control (issue #313 [E20.S2])",
    )
    parser.add_argument(
        "--populate-metric-5", action="store_true",
        help="also create metric_5 (change failure rate) with a real row -- the leadership "
             "page's negative control (issue #314 [E20.S3])",
    )
    parser.add_argument(
        "--populate-metric-23", action="store_true",
        help="also create metric_23 (gate catch rate by gate) with a real row -- leadership's "
             "own class-2 positive proof (issue #314 [E20.S3], Decision A)",
    )
    parser.add_argument(
        "--populate-actor-rows", action="store_true",
        help="also insert carol/dave's real actor-bearing rows (fact_event, fact_handoff, "
             "dim_project) -- prove-leadership-no-individual-grain-leak.mjs's own positive "
             "control (issue #314 [E20.S3], Decision C)",
    )
    args = parser.parse_args()

    conn = open_store(args.db)
    try:
        if args.populate_metric_12:
            conn.execute(
                "CREATE VIEW metric_12 AS "
                "SELECT 3 AS autonomous_done_count, 4 AS terminal_count, 0.75 AS autonomy_rate"
            )
        if args.populate_metric_14:
            # Same (numerator, denominator, rate) column shape
            # insight/tests/test_dash_panel_absence.py:112 establishes for metric 14's fixture
            # (that test creates it NULL; this creates it populated) -- metric 14 uses
            # `_numerator_denominator_rate` (insight/api/metrics.py:35-50): row[0]=numerator,
            # row[1]=denominator, row[2]=rate.
            conn.execute("CREATE VIEW metric_14 AS SELECT 3 AS n, 4 AS d, 0.75 AS r")
        if args.populate_metric_5:
            # The four-column shape insight/api/metrics.py's `_change_failure_rate(row)` requires:
            # row[1]=window_commit_count, row[2]=repeated_revert_or_fixup_count,
            # row[3]=change_failure_rate -- mirrors insight/metrics/5.sql's own SELECT list.
            conn.execute(
                "CREATE VIEW metric_5 AS SELECT TIMESTAMP '2026-01-01' AS collected_ts, "
                "20 AS window_commit_count, 3 AS repeated_revert_or_fixup_count, "
                "0.15 AS change_failure_rate"
            )
        if args.populate_metric_23:
            # Mirrors insight/metrics/23.sql's own 13-column SELECT list, in order, so
            # `SELECT * FROM metric_23` matches `_gate_catch_rate`'s positional reads
            # (insight/api/metrics.py:120-129: r[3]=gate_event_count, r[4]=catch_count, summed
            # across every row). gate_event_count=10, catch_count=4 gives a real
            # (value=0.4, numerator=4, denominator=10) -- the class-2 metric's own coverage
            # denominator, not fabricated.
            conn.execute(
                "CREATE VIEW metric_23 AS SELECT "
                "'p1' AS project_id, 'post_review' AS gate, TRUE AS late_catch, "
                "10 AS gate_event_count, 4 AS catch_count, 120.0 AS avg_catch_cycle, "
                "4 AS project_catch_count, 2 AS project_late_catch_count, "
                "0.5 AS project_late_catch_share, 8 AS class1_count, 2 AS class2_count, "
                "10 AS total_count, 0.8 AS coverage_pct"
            )
        if args.populate_actor_rows:
            # Verbatim row shapes from test_dash_leadership_guardrail.py's own `conn` fixture --
            # a real actor-bearing row DOES exist in the store (mirrors production: the ledger has
            # actor data), and the leadership view's own fetchers never read fact_event/
            # fact_handoff/dim_actor at all, which is what the leak proof exercises structurally.
            now = datetime.datetime(2026, 8, 1)
            conn.execute(
                "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, "
                "reliability_class) VALUES ('p1', 'g-carol-1', ?, 'carol', 'claimed', 1)", [now],
            )
            conn.execute(
                "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, "
                "priority, opened_ts) VALUES ('p1', 'carol', 'dave', 'insight', 601, 'p1', ?)",
                [now],
            )
            conn.execute("INSERT INTO dim_project (project_id, repo) VALUES ('p1', 'org/p1')")
    finally:
        conn.close()
    print(
        "seeded cold-start delivery fixture at %s (metric_12 populated: %s, metric_14 populated: "
        "%s, metric_5 populated: %s, metric_23 populated: %s, actor rows populated: %s)"
        % (
            args.db, args.populate_metric_12, args.populate_metric_14,
            args.populate_metric_5, args.populate_metric_23, args.populate_actor_rows,
        )
    )


if __name__ == "__main__":
    main()
