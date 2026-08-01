# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #10, Aging WIP (issue #109). See #109 plan Design decision F for why the
fact_event/kind-vocabulary choice is an open question for #180, not a confirmed fact.

POST-PR-REVIEW BLOCKING FIX (round 3), DERIVED FROM skills/sdlc-loop/scripts/ledger.py's own
open_claims() at line 293 -- the spec's own named ground truth for this metric, opened only in
this round after two prior rounds (a review and a fix) each reasoned from the SQL's internal
logic instead. open_claims() is LAST-CLAIM-WINS: `held[goal] = (actor, ts)` OVERWRITES on every
claim. On a re-claim with no terminal event released in between (spec #35, lease contention),
the earlier claimant's hold is DROPPED, not doubled -- only the goal's single latest claim can be
its current open episode.

ROUND 2's test (removed here) asserted the opposite: that a lease-contended goal should surface
under BOTH actors, reasoning "each actor genuinely does hold their own open claim on it" as a
plausible-sounding but never-derived claim. Checked directly against open_claims() this round
(see the verification script run alongside this fix, not shipped here since insight/ may not
import skills/) -- that reasoning is empirically false. A pinning test written from an assumption
doesn't protect behaviour; it cements the assumption. This file now asserts the semantics
open_claims() actually implements.

VERIFIED against the real open_claims(), converting each fixture row to its {goal, kind, actor,
ts} shape, oldest-first:
    entries = [(gy,claimed,a1,2025-12-20), (gy,claimed,a2,2025-12-22), (ga,claimed,a1,2026-01-01),
               (gb,claimed,a2,2026-01-02), (ga,done,a1,2026-01-03), (gd,claimed,a1,2026-01-05),
               (ge,claimed,a1,2026-01-08), (ge,parked,a1,2026-01-08T12:00), (gc,claimed,a2,2026-01-10),
               (ge,claimed,a2,2026-01-15)]
    open_claims(entries) == {'gy': 'a2', 'gb': 'a2', 'gd': 'a1', 'gc': 'a2', 'ge': 'a2'}
i.e. a2's own claim on gy (12/22) OVERWROTE a1's (12/20) -- a1 no longer holds gy at all. a1's
only currently-open claim is gd (1/5); ga is closed (done); ge's first (a1) episode is dropped
by the same re-claim mechanism, not merely "closed" -- a2's re-claim (1/15) is what currently
holds it. Aging WIP (oldest open claim per actor) over this ground truth:
    a1 -> gd (claimed 2026-01-05) -- its only currently-held goal
    a2 -> gy (claimed 2025-12-22) -- the oldest of {gy, gb, gc, ge}, all currently held by a2
This is what the corrected 10.sql (last-claim-wins CTE, mirroring open_claims()'s overwrite
semantics, with a >= terminal comparison matching #7's own same-second-close fix) returns,
VERIFIED live.

AUDIT of every other test docstring in this story that asserts a behaviour is correct, done as
part of this round's fix (per the coordinator's explicit ask): test_metric_2_cycle_time.py's
skew and negative-duration claims are hand-arithmetic (rank-interpolation formula, independently
checkable, not reasoned from analogy) -- clean. test_metric_3/4/5/9's claims are plain arithmetic
over their own fixtures -- clean. test_metric_11_forecast.py's determinism and percentile claims
were cross-checked two independent ways (a pure-Python re-implementation outside DuckDB, and
exhaustive enumeration of all 256 draw-combinations) -- clean, ground-truth-derived, not
reasoned-by-analogy. test_metric_7_wip.py's claims (this round) are now also derived from
open_claims() directly, not just internal SQL reasoning -- see that file's own docstring. This
file's OWN round-2 docstring was the one exception found: reasoned from what "seemed like a
defensible read" rather than checked against the spec's named ground truth. No other docstring
in this story was found to share that shape."""
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


def test_metric_10_returns_each_actors_single_oldest_currently_held_claim(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["10"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_10 ORDER BY actor_id"))
    by_actor = {r["actor_id"]: r["goal_id"] for r in rows}
    # VERIFIED against the real open_claims() (see module docstring): a2's later claim on gy
    # (12/22) overwrites a1's earlier one (12/20) -- a1 no longer holds gy at all, so a1's oldest
    # (and only) open claim reverts to gd (1/5). a2's own open claims are {gy, gb, gc, ge}; gy
    # (12/22/2025) is the oldest of those, so it -- not gb -- is a2's answer.
    assert by_actor == {"a1": "gd", "a2": "gy"}
    assert len(rows) == 2  # exactly one row per actor holding at least one open claim


def test_metric_10_a_reclaim_drops_the_earlier_actors_hold_entirely(conn):
    """The coordinator's own minimal repro for BLOCKING (b), verified directly against
    open_claims(): a1 claims g, then claims h (a second, genuinely open goal of a1's own) --
    then a2 re-claims g with no terminal released in between. Truth (open_claims(entries) ==
    {'g': 'a2', 'h': 'a1'}): a2 EXCLUSIVELY holds g; a1's real open claim is h, which the OLD
    (round-2) SQL never surfaced at all because it only ever looked at each actor's own claim
    rows, never noticing a1's claim on g had been superseded by someone else's."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','h','2026-01-05T00:00:00','a1','claimed',1),"
        "('p1','g','2026-01-10T00:00:00','a2','claimed',1)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_10 ORDER BY actor_id"))
    by_actor = {r["actor_id"]: r["goal_id"] for r in rows}
    assert by_actor == {"a1": "h", "a2": "g"}
    assert len(rows) == 2


def test_metric_10_a_same_second_claim_then_done_is_not_a_currently_held_claim(conn):
    """Same whole-second-timestamp fix as metric_7's round-3 repro (ledger.py's _epoch() parses
    %Y-%m-%dT%H:%M:%SZ -- no sub-second resolution): a claim released by a terminal event at the
    IDENTICAL timestamp must not appear as an open claim for its actor."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','gq','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','gq','2026-01-01T00:00:00','a1','done',1)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_10"))
    assert rows == []


def test_metric_10_the_same_actor_holding_claims_in_two_projects_returns_both(conn):
    """PRECONDITION BUG FIX (issue #105). current_holder already carries project_id (it is
    SELECTed out of latest_event two CTEs up), but the final ranked window used to
    `PARTITION BY actor_id` alone, with no project_id anywhere in its PARTITION BY or output
    list. An actor holding an open claim in TWO DIFFERENT projects therefore collapsed onto
    ONE row globally -- whichever of the two had the smaller claimed_ts -- and the other
    project's claim vanished from the result silently (not wrong, just gone; nothing in the
    query shape would have hinted a row was missing). This was inert only because fact_event
    carried zero rows in every real run before this story's ledger writer (#105/#180) started
    filling it -- a single actor working the same identity across two separate LoopSmith
    projects is exactly this ingest path's own normal shape (project_id is derived per
    project_root, actor_id is the ledger's own `actor`, and nothing ties the two together).

    Fix: PARTITION BY (project_id, actor_id) and include project_id in the SELECT. Two
    projects, one shared actor, one open claim in each -- both survive as two rows.

    MUTATION-PROVEN: reverting the fix (PARTITION BY actor_id alone, project_id dropped from
    the SELECT) makes this test fail -- p2's row disappears entirely (len(rows) == 1, not 2)
    because 2026-01-01 (p1) sorts before 2026-01-05 (p2) globally. See the story's own report
    for the exact revert/run/restore transcript."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g1','2026-01-01T00:00:00','a1','claimed',1),"
        "('p2','g2','2026-01-05T00:00:00','a1','claimed',1)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_10 ORDER BY project_id"))
    by_project = {r["project_id"]: (r["actor_id"], r["goal_id"]) for r in rows}
    assert by_project == {"p1": ("a1", "g1"), "p2": ("a1", "g2")}
    assert len(rows) == 2  # BOTH survive -- the bug this fixes silently dropped one


def test_metric_10_excludes_a_class_2_claim(conn):
    """Reliability-class enforcement (#114, spec line 563: "a NOW metric must not read any
    reliability_class=2 row"). g1's class-1 claim (a1) must surface; g2's class-2 claim (a9)
    must never surface a row for a9 at all."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) VALUES "
        "('p1','g1','2026-01-01T00:00:00','a1','claimed',1),"
        "('p1','g2','2026-01-02T00:00:00','a9','claimed',2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_10"))
    assert len(rows) == 1
    assert rows[0]["actor_id"] == "a1"
    assert rows[0]["goal_id"] == "g1"
