#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Standalone fixture-seeder for the CI-only real-server proofs (issue #310 [E19.S2],
.sdlc/plans/310.md Task 6): `scripts/prove-ic-no-cross-actor-leak.mjs` and
`scripts/prove-ic-python-bridge-exit-codes.mjs` both spawn this as `python3
scripts/lib/seed-ic-fixture.py --db <path>` to populate a throwaway DuckDB store before booting a
real `next start` (or driving the bridge directly) against it.

Writes the SAME alice/bob/carol fixture `insight/tests/test_dash_ic_no_leak.py`,
`insight/tests/test_dash_ic.py`, and `insight/tests/test_cli_web_ic.py` already use --
`insight.tests.ic_fixture.seed_alice_bob_carol`, imported directly rather than duplicated a
fourth time, so there is ONE place these rows are defined for every leak proof in the repo to
share (Task 6's own instruction: reuse the shared helper if importable from a standalone script
context).

Run from ANY cwd -- `insight` is not `pip install -e`'d in the always-on local gate (only in CI's
`web` job, Decision 7), so this script cannot rely on `python3 -m insight`'s own cwd-prepend
trick the way every Node bridge does; REPO_ROOT is put on `sys.path` explicitly below instead,
before either import."""
import argparse
import pathlib
import sys

# insight/web/scripts/lib/seed-ic-fixture.py -> parents[0]=lib, [1]=scripts, [2]=web,
# [3]=insight, [4]=REPO_ROOT (the directory `insight` itself needs as an ancestor on sys.path to
# be importable as `insight.*`).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb  # noqa: E402

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.tests.ic_fixture import seed_alice_bob_carol  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to write the seeded DuckDB store to")
    args = parser.parse_args()

    conn = duckdb.connect(args.db)
    try:
        ensure_schema(conn)
        seed_alice_bob_carol(conn)
    finally:
        conn.close()
    print("seeded alice/bob/carol fixture at %s" % args.db)


if __name__ == "__main__":
    main()
