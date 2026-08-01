# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #7, Flow load / WIP over time (issue #109). DARK METRIC (fact_event is 0 rows in real
ingest today, per #109 research dossier) -- fixture-verified only; see #109 plan Design decision
F for why the fact_event/kind-vocabulary choice itself is an open question for #180, not a
confirmed fact. VERIFIED live: ga closes (claimed 1/1, done 1/3); gb stays open a long time
(claimed 1/2, done 1/20); gc opens late and never closes (claimed 1/10); ge is claimed by a1,
parked, then RE-CLAIMED by a2 and left open -- the multi-episode case the reviewer's ABSENT list
flagged. Hand-checked: ge's first (closed) episode must not leak its parked-event into the
second (open) episode's status, and the second episode must attribute correctly to a2, not a1.

POST-PR-REVIEW BLOCKING FIX (round 2): an independent, author-blind pre-PR review reproduced a
real defect -- gy is claimed by a1 (1/1), then claimed AGAIN by a2 (1/5) with no terminal event
released in between (spec metric #35's own named scenario, "lease contention: goals claimed by
2+ actors"). The original SQL paired each claimed event with its own next terminal event and
counted `count(*)` over open episodes, so one lease-contended goal produced TWO open episodes
and read as 2 units of WIP for one goal -- reproduced live: [0, 1, 2, 3] became [0, 2, 3, 4] once
gy was added, i.e. gy alone contributed +1 to every week from 1/5 onward instead of the correct 0
(before 1/1) then +1 (from 1/1 onward, one unit, not two). Fixed by `count(DISTINCT
cc.goal_id) FILTER (...)` instead of `count(*) FILTER (...)` -- verified live: gy now
contributes exactly one unit of WIP from week 1/5 onward (both of its overlapping episodes
collapse to the same goal_id), giving the hand-checked series below. Deliberately NOT applied to
metric_10 (see test_metric_10_aging_wip.py's own docstring for why the per-actor grain there
makes the same overlapping-claim shape a defensible read, not a double count).

POST-PR-REVIEW BLOCKING FIX (round 3), DERIVED FROM skills/sdlc-loop/scripts/ledger.py's own
open_claims() (the spec's named ground truth for this metric, at line 293 -- neither the round-2
review nor the round-2 fix opened that file): ledger timestamps are whole-second
(`_epoch()` parses `%Y-%m-%dT%H:%M:%SZ`), so a fast automated claim-then-done, or a backfill
stamping historical events with one recorded second, has claimed_ts == terminal_ts. The
per-episode closing computation used a STRICT `>` (`t.terminal_ts > c.claimed_ts`), which
excludes an exactly-equal terminal from ever closing the episode -- reproduced live: gq
(claimed AND done at 2026-01-01T00:00:00, folded into the existing 7.jsonl fixture) read as an
extra +1 unit of WIP in every week from 1/5 onward, forever (no upper bound on the defect
itself; bounded only by whatever the surrounding data's own date range happens to be). Fixed by
`>=` in BOTH occurrences of the comparison (the max-date-range subquery's own `closed` CTE, and
the main `cc` CTE the outer join reads) -- a same-second claim-then-done now correctly closes
with zero duration and contributes 0 WIP at every week boundary. Re-verified algebraically, not
just numerically: for any true open/closed interval per open_claims()'s own state-machine
semantics (a re-claim while already open does NOT start a new interval; only a claim while
closed does), every claim episode belonging to that interval resolves to the SAME correct
closing terminal_ts under the (now `>=`) per-episode computation, so count(DISTINCT goal_id)
remains provably correct after this fix -- metric 7's aggregate count never needed to know WHICH
actor/episode is current, only whether ANY is open, which is exactly why it does not also need
metric 10's last-claim-wins actor attribution (see test_metric_10_aging_wip.py)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "7.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_7_weekly_wip_replays_claimed_and_terminal_events(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["7"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_7 ORDER BY week_start"))
    # Hand-checked with gy folded in (claimed by a1 1/1, claimed again by a2 1/5, never
    # released -- lease contention, spec #35): gy contributes exactly ONE unit of WIP from
    # week 1/5 onward (both overlapping episodes are the same goal_id), on top of the
    # pre-existing [0, 1, 2, 3] series -- giving [0, 1+1, 2+1, 3+1] = [0, 2, 3, 4]. gq (claimed
    # AND done at the identical instant 2026-01-01T00:00:00 -- round 3's fix) correctly
    # contributes 0 to every week and does not change this series at all.
    assert [r["wip_count"] for r in rows] == [0, 2, 3, 4]


def test_metric_7_a_same_second_claim_then_done_contributes_zero_wip_forever(conn):
    """Round-3 BLOCKING fix, derived from open_claims(): ledger timestamps are whole-second, so
    a fast automated claim-then-done (or a backfill stamping one recorded second) legitimately
    has claimed_ts == terminal_ts. Isolated, minimal repro -- gq alone contributes 0 WIP always;
    a second, genuinely-open goal (gr, claimed 1/1, done 1/15) is included so the fixture has a
    real, non-degenerate date range and gq's zero contribution is checked at every week in it,
    not just the one week its own claim falls in."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','gq','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','gq','2026-01-01T00:00:00','a1','done',1),"
        "('p1','gr','2026-01-01T00:00:00','a2','claimed',1),"
        "('p1','gr','2026-01-15T00:00:00','a2','done',1)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_7 ORDER BY week_start"))
    # gr alone (claimed 1/1, open through 1/12, closed by 1/15) accounts for the entire series;
    # gq must never add anything on top of it.
    assert [r["wip_count"] for r in rows] == [0, 1, 1]


def test_metric_7_does_not_double_count_a_goal_claimed_by_two_actors_with_no_release(conn):
    """The BLOCKING defect an author-blind review found: two overlapping claim episodes on the
    SAME goal (no terminal event between them) used to read as 2 units of WIP for one goal.
    Isolated, minimal repro of the reviewer's own example -- a fresh fixture, not the shared
    7.jsonl, so this test's pass/fail does not depend on any of the other fixture rows."""
    load_fixture_jsonl(conn, FIXTURE)  # baseline schema only; overwritten below with a minimal case
    conn.execute("DELETE FROM fact_event")
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','gy','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','gy','2026-01-05T00:00:00','a2','claimed',1)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_7 ORDER BY week_start"))
    # A single lease-contended goal must read as at most 1 unit of WIP per week, never 2.
    assert all(r["wip_count"] <= 1 for r in rows)
    assert [r["wip_count"] for r in rows] == [0, 1]


def test_metric_7_excludes_a_class_2_claim_from_wip(conn):
    """Reliability-class enforcement (#114, spec line 563: "a NOW metric must not read any
    reliability_class=2 row"). Fresh, isolated insert -- no fixture load, matching this file's
    own test_metric_7_does_not_double_count_... isolation style. A class-1 claim on ga and a
    class-2 claim on a DISTINCT goal gz, both stamped to 2026-01-05T00:00:00 -- a MONDAY, chosen
    deliberately (not 2026-01-01, a Thursday): with the filter correct, ga is the only row events
    ever sees, weeks becomes a single-point generate_series(X, X, INTERVAL 7 DAY) bucket at
    date_trunc('week', ...) == 2026-01-05 itself (VERIFIED live before trusting this, per this
    plan's own named risk: a Thursday claim truncates its week bucket back to the PRECEDING
    Monday, which then sits strictly BEFORE the claim's own ts and never satisfies candidate's
    `e.ts <= w.week_start` join at all -- a degenerate single-bucket case that reads wip_count=0
    regardless of the filter and would not exercise this test's own mutation at all; a
    Monday-at-midnight claim makes week_start == ts exactly, so the join does fire). With ga's
    claim landing in its own bucket, wip_count == 1. If the filter were dropped, gz -- a distinct
    goal_id, same instant -- would rank its own rn=1, is_claim=1 row too, making wip_count == 2;
    this is the failure this test exists to catch."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','ga','2026-01-05T00:00:00','a1','claimed',1),"
        "('p1','gz','2026-01-05T00:00:00','a9','claimed',2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_7 ORDER BY week_start"))
    assert len(rows) == 1
    assert rows[0]["wip_count"] == 1
