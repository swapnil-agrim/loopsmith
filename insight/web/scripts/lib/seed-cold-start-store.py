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
    args = parser.parse_args()

    conn = open_store(args.db)
    try:
        if args.populate_metric_12:
            conn.execute(
                "CREATE VIEW metric_12 AS "
                "SELECT 3 AS autonomous_done_count, 4 AS terminal_count, 0.75 AS autonomy_rate"
            )
    finally:
        conn.close()
    print(
        "seeded cold-start delivery fixture at %s (metric_12 populated: %s)"
        % (args.db, args.populate_metric_12)
    )


if __name__ == "__main__":
    main()
