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

Run from ANY cwd -- mirrors seed-ic-fixture.py's own reasoning: `insight` is not `pip
install -e`'d in the always-on local gate (only in CI's `web` job), so REPO_ROOT is put on
`sys.path` explicitly below, before either import.
"""
import argparse
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
    finally:
        conn.close()
    print(
        "seeded cold-start delivery fixture at %s (metric_12 populated: %s, metric_14 populated: %s)"
        % (args.db, args.populate_metric_12, args.populate_metric_14)
    )


if __name__ == "__main__":
    main()
