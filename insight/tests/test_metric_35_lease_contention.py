# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #35, Lease contention (issue #112, E2.S5, Task 5). "Is parallel work actually safe?"
-- goals claimed by 2+ actors; expired leases. Derived from ledger.py's own open_claims() state
machine (re-derived, not re-invented, Decision C): held[goal] = (actor, ts) on every 'claimed'
event is a total overwrite, and held.pop(goal, None) on a terminal event (done/parked/failed) is
a total clear -- the goal's current state is entirely determined by its own last event.

"Lease contention" (issue's own words: "goals claimed by 2+ actors") means: a claimed event
arrived for a goal while a DIFFERENT actor's claimed event was still the live state -- i.e. no
terminal event released the goal between the two claims. Computed via
`LAG(kind), LAG(actor_id) OVER (PARTITION BY project_id, goal_id ORDER BY ts)` restricted to
claimed/done/parked/failed: a claimed row is a contended transition when the immediately
preceding row is ALSO claimed and by a different actor_id.

Two outputs, one row per (project_id, goal_id) that was ever claimed at all:
  1. contended (bool_or() of the transition flag across the goal's ENTIRE history -- a
     historical safety signal, not a live-state one -- a goal contended in the past and now
     closed still reports contended=true).
  2. current_actor_id, claimed_ts (nullable) -- the goal's CURRENT holder, using the identical
     last-event-wins shape 10.sql already ships; NULL when the goal's current state is closed.

"Expired leases" is deliberately NOT computed in-SQL (Decision C, following 10.sql's own
precedent for the identical "no runtime now in a static view" problem) -- this view exposes the
raw claimed_ts for every currently-open lease; a consumer applies ttl_hours=12 against it at
render time.

Fixture (35.jsonl) reuses the LOGICAL SHAPE of test_metric_7_wip.py's own hand-verified `gy`
scenario (re-claim by a second actor with no terminal event), using that test's own real dates
(2026-01-01/2026-01-05, re-read directly from test_metric_7_wip.py:109-110 this session, NOT
the earlier misquoted 2025-12-20/2025-12-22):
  - gy: claimed by a1 (2026-01-01), claimed by a2 (2026-01-05), no terminal ever -- expect
    contended=true, current_actor_id='a2', claimed_ts=2026-01-05 (last-claim-wins, matching
    open_claims()'s own verified output {'gy': 'a2'} for this exact shape).
  - ga: claimed by a1 (2026-01-10), done by a1 (2026-01-12) -- expect contended=false,
    current_actor_id=NULL, claimed_ts=NULL (closed, single actor, no contention).
  - gd: claimed by a1 (2026-01-15), never terminated -- expect contended=false,
    current_actor_id='a1', claimed_ts=2026-01-15 (currently open, single actor)."""
import datetime
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "35.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_35_flags_a_reclaim_with_no_terminal_event_as_contended(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_35 WHERE goal_id = 'gy'"))
    assert rows == [{
        "project_id": "p1",
        "goal_id": "gy",
        "contended": True,
        "current_actor_id": "a2",
        "claimed_ts": datetime.datetime(2026, 1, 5, 0, 0, 0),
    }]


def test_metric_35_does_not_flag_a_normal_single_actor_lifecycle(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_35 WHERE goal_id = 'ga'"))
    assert rows == [{
        "project_id": "p1",
        "goal_id": "ga",
        "contended": False,
        "current_actor_id": None,
        "claimed_ts": None,
    }]


def test_metric_35_exposes_the_current_holder_of_a_still_open_lease(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    cursor = conn.execute("SELECT * FROM metric_35 WHERE goal_id = 'gd'")
    columns = [d[0] for d in cursor.description]
    assert not any(c in ("age", "age_seconds", "age_days", "expired") for c in columns)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_35 WHERE goal_id = 'gd'"))
    assert rows == [{
        "project_id": "p1",
        "goal_id": "gd",
        "contended": False,
        "current_actor_id": "a1",
        "claimed_ts": datetime.datetime(2026, 1, 15, 0, 0, 0),
    }]


def test_metric_35_declares_itself_dark(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["35"]["extra"]["data_status"] == "dark"


def test_metric_35_class_2_events_are_invisible_across_all_three_fact_event_reads(conn):
    """Reliability-class enforcement (#114, spec line 563: "a NOW metric must not read any
    reliability_class=2 row"). Deliberately shaped to independently exercise each of 35.sql's
    THREE separate fact_event reads (transitions, current_holder's own inner subquery,
    all_claimed_goals) -- a fix that only filtered one or two of the three must still fail this
    test. Fresh, isolated insert -- no fixture load, matching this file's own isolation style
    for a from-scratch scenario.

    g1: control, class-1 only, single open claim.
    g2: class-2 ONLY (no class-1 claim ever for this goal). This is the case that only
        all_claimed_goals's own filter can produce: if that one occurrence's filter were dropped
        while transitions/current_holder kept theirs, g2 would still surface a row
        (contended=false, current_actor_id=NULL) -- a bug the other two occurrences' filters
        cannot mask.
    g4: class-1 claim by a1 (2026-01-01), then a class-2 claim by a2 (2026-01-05) -- the same
        shape as this repo's own real gy lease-contention scenario, but the SECOND claim is
        untrusted. If transitions's filter were dropped, a2's claim would look like a genuine
        second actor and contended would flip true. If current_holder's filter were dropped,
        current_actor_id would flip to a2. With all three filters intact, g4 reads exactly like
        a single, uncontended, still-open claim by a1."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g1','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','g2','2026-01-02T00:00:00','a9','claimed',2),"
        "('p1','g4','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','g4','2026-01-05T00:00:00','a2','claimed',2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_35 ORDER BY goal_id"))
    assert [r["goal_id"] for r in rows] == ["g1", "g4"]  # g2 never appears
    by_goal = {r["goal_id"]: r for r in rows}
    assert by_goal["g1"]["contended"] is False
    assert by_goal["g1"]["current_actor_id"] == "a1"
    assert by_goal["g1"]["claimed_ts"] == datetime.datetime(2026, 1, 1, 0, 0, 0)
    assert by_goal["g4"]["contended"] is False  # a2's class-2 claim invisible
    assert by_goal["g4"]["current_actor_id"] == "a1"  # a2 never becomes the holder
    assert by_goal["g4"]["claimed_ts"] == datetime.datetime(2026, 1, 1, 0, 0, 0)
