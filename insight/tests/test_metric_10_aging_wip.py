# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #10, Aging WIP (issue #109). See #109 plan Design decision F for why the
fact_event/kind-vocabulary choice is an open question for #180, not a confirmed fact. VERIFIED
live: a1 has one closed claim (ga) and one open claim (gd, claimed 1/5) -- gd is a1's oldest OPEN
claim, ga (closed) never competes. a2 has THREE open-or-was-open claims: gb (claimed 1/2, open),
gc (claimed 1/10, open), and ge (claimed 1/15 -- a re-claim by a2 after a1's earlier episode of
the same goal was parked). gb is the oldest and must win; gc and ge must NOT appear, and a1's own
now-closed first episode of ge must not appear for a1 either.

POST-PR-REVIEW addition: the same author-blind review that found metric_7's double-counting
defect (see test_metric_7_wip.py) checked this sibling file against the identical
lease-contention shape -- a goal (gy) claimed by TWO actors with no release between them -- and
found metric_10 does NOT need the same fix, because its per-actor grain makes "one goal open
under two different actors" a defensible read rather than a double count: each actor genuinely
does hold their own open claim on gy. gy is claimed 2025-12-20 by a1 and 2025-12-22 by a2, both
older than every other open claim in this fixture -- VERIFIED live: metric_10 correctly returns
BOTH a1 and a2 with goal_id='gy' (their own respective claimed_ts), superseding a1's previous
winner (gd) and a2's previous winner (gb), with no cross-actor collision or error from the same
goal_id appearing twice under two different actor_id partitions."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "10.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_10_returns_each_actors_single_oldest_open_claim(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["10"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_10 ORDER BY actor_id"))
    by_actor = {r["actor_id"]: r["goal_id"] for r in rows}
    # gy (claimed 12/20 by a1, 12/22 by a2 -- lease contention, spec #35) is older than every
    # other open claim in this fixture, so it correctly supersedes both a1's previous oldest
    # (gd) and a2's previous oldest (gb) -- see this file's own module docstring for why this
    # is the deliberately-correct behavior (per-actor grain), not the metric_7 double-count bug.
    assert by_actor == {"a1": "gy", "a2": "gy"}
    assert len(rows) == 2  # one row per actor, even though both point at the SAME goal_id


def test_metric_10_lease_contended_goal_appears_once_per_actor_not_collapsed_or_duplicated(conn):
    """Isolated, minimal repro of the same overlapping-claim shape metric_7's own fix targets --
    a fresh, single-goal fixture so this assertion does not depend on any of 10.jsonl's other
    rows. metric_10's per-actor grain must show gy for BOTH a1 and a2 (each actor really does
    hold an open claim on it), never zero rows and never more than one row per actor."""
    conn.execute("DELETE FROM fact_event")
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind) VALUES "
        "('p1','gy','2026-01-01T00:00:00','a1','claimed'),"
        "('p1','gy','2026-01-05T00:00:00','a2','claimed')"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_10 ORDER BY actor_id"))
    assert [(r["actor_id"], r["goal_id"]) for r in rows] == [("a1", "gy"), ("a2", "gy")]
