# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/threshold_lead_time_breach.sql (issue #119, [E3.S4]; see
.sdlc/plans/119.md for the full design and the live-verified numbers each case below
reproduces)."""
import datetime

import pytest

from insight.gaps.evaluate import evaluate_rule
from insight.gaps.loader import load_gap_rules


@pytest.fixture
def conn(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


@pytest.fixture
def registry():
    return load_gap_rules()


@pytest.fixture
def rule(registry):
    return registry["threshold_lead_time_breach"]


def _insert(conn, rows):
    for project_id, merge_sha, merge_ts, lead_time_seconds in rows:
        conn.execute(
            "INSERT INTO fact_merge_lead_time (project_id, merge_sha, merge_ts, "
            "lead_time_seconds) VALUES (?, ?, ?, ?)",
            [project_id, merge_sha, merge_ts, lead_time_seconds],
        )


def test_a_single_10000x_spike_does_not_fire_never_on_a_single_crossing(conn, rule):
    """The reviewer's own round-2/3 fixture, re-run under k=3. Hand-computed and verified live
    this session: s3's own trailing_p85=100.0, 1000000 > 100.0 -> breach, but it is an ISOLATED
    breach (breach_run_length=1 < 3) -- s4/s5 do not breach either (baseline poisoned by s3, a
    named, accepted limitation, see Risks). This is the issue's own corrected done_when made
    literal: "never on a single crossing." PASS, not WARN -- a deliberate behaviour change from
    every prior round, not a regression."""
    _insert(conn, [
        ("p1", "s1", datetime.datetime(2026, 1, 1), 100),
        ("p1", "s2", datetime.datetime(2026, 1, 2), 100),
        ("p1", "s3", datetime.datetime(2026, 1, 3), 1000000),
        ("p1", "s4", datetime.datetime(2026, 1, 4), 105),
        ("p1", "s5", datetime.datetime(2026, 1, 5), 102),
    ])
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Threshold", "metric": "3", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_a_sustained_4x_step_up_fires_starting_at_the_first_regressed_merge(conn, rule):
    """Issue's own Task 6. Exact seeded values reused verbatim from the retired round-3 plan's
    own Task 2 test 5 (random.seed(7), Normal(100000,5000) s00-s14, Normal(400000,20000)
    s15-s24), deterministic, not regenerated. Hand-computed and verified live this session under
    k=3: severity WARN, evidence = s15 through s22 (8 consecutive breaching rows, one run) --
    s15 (the actual first regressed merge) is IN the evidence, not merely "eventually caught."
    s23 breaks the run (its own trailing baseline has absorbed the new regime); s24 breaches
    again but alone (run length 1, correctly excluded)."""
    _insert(conn, [
        ("p1", "s00", datetime.datetime(2026, 1, 2), 98720),
        ("p1", "s01", datetime.datetime(2026, 1, 3), 102557),
        ("p1", "s02", datetime.datetime(2026, 1, 4), 98869),
        ("p1", "s03", datetime.datetime(2026, 1, 5), 98424),
        ("p1", "s04", datetime.datetime(2026, 1, 6), 95349),
        ("p1", "s05", datetime.datetime(2026, 1, 7), 98933),
        ("p1", "s06", datetime.datetime(2026, 1, 8), 105559),
        ("p1", "s07", datetime.datetime(2026, 1, 9), 102120),
        ("p1", "s08", datetime.datetime(2026, 1, 10), 105184),
        ("p1", "s09", datetime.datetime(2026, 1, 11), 101244),
        ("p1", "s10", datetime.datetime(2026, 1, 12), 101973),
        ("p1", "s11", datetime.datetime(2026, 1, 13), 100926),
        ("p1", "s12", datetime.datetime(2026, 1, 14), 91669),
        ("p1", "s13", datetime.datetime(2026, 1, 15), 104276),
        ("p1", "s14", datetime.datetime(2026, 1, 16), 102531),
        ("p1", "s15", datetime.datetime(2026, 1, 17), 409976),
        ("p1", "s16", datetime.datetime(2026, 1, 18), 366172),
        ("p1", "s17", datetime.datetime(2026, 1, 19), 365122),
        ("p1", "s18", datetime.datetime(2026, 1, 20), 382207),
        ("p1", "s19", datetime.datetime(2026, 1, 21), 390636),
        ("p1", "s20", datetime.datetime(2026, 1, 22), 406108),
        ("p1", "s21", datetime.datetime(2026, 1, 23), 399081),
        ("p1", "s22", datetime.datetime(2026, 1, 24), 410419),
        ("p1", "s23", datetime.datetime(2026, 1, 25), 387155),
        ("p1", "s24", datetime.datetime(2026, 1, 26), 406174),
    ])
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    fired_shas = [e["merge_sha"] for e in finding["evidence"]]
    assert fired_shas == ["s15", "s16", "s17", "s18", "s19", "s20", "s21", "s22"], (
        "the rule must catch the regression starting at its FIRST point and report every "
        "breach in the run -- if s15 is missing, the rule cannot do the one job issue #119 "
        "asks of it regardless of specificity"
    )
    for e in finding["evidence"]:
        assert e["breach_run_length"] == 8
        assert e["lead_time_seconds"] > e["trailing_p85"]


def test_two_isolated_breaches_short_of_a_run_of_three_do_not_fire(conn, rule):
    """A run that only reaches length 2 must not fire -- 'never on a single crossing' extends to
    'never on two,' k literally means 3. Two merges tied on merge_ts are each judged against
    their own distinct trailing window (D4/ties, unaffected by k=3): s4's prior series is
    [100,100,100] (p85=100.0, 900>100.0 breach); s5's prior series is [100,100,100,900] (tie-break
    orders s4 first; p85=540.0 by quantile_cont's own interpolation, 900>540.0 breach) -- two
    consecutive breaches, run length 2, hand-computed and verified live: does not reach 3."""
    _insert(conn, [
        ("p1", "s1", datetime.datetime(2026, 1, 1), 100),
        ("p1", "s2", datetime.datetime(2026, 1, 2), 100),
        ("p1", "s3", datetime.datetime(2026, 1, 3), 100),
        ("p1", "s4", datetime.datetime(2026, 1, 4), 900),
        ("p1", "s5", datetime.datetime(2026, 1, 4), 900),
    ])
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Threshold", "metric": "3", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_a_run_that_reaches_exactly_three_fires_with_noisy_not_flat_values(conn, rule):
    """A run of exactly 3, with mild noise so the trailing p85 does not catch up to the new
    regime's own value before the third point (see .sdlc/plans/119.md's own named edge case: a
    PERFECTLY FLAT step-up can fail to reach a run of 3 at all, because the new regime's own
    trailing quantile converges on the new constant value fastest when there is no spread --
    verified live, a 900/900/900 step-up after 100/100/100 fires ZERO rows, not tested here as a
    positive case for that reason). Hand-computed and verified live: three prior merges at 100,
    then 910, 895, 905 -- all three breach, all three land in one run of length 3."""
    _insert(conn, [
        ("p1", "s1", datetime.datetime(2026, 1, 1), 100),
        ("p1", "s2", datetime.datetime(2026, 1, 2), 100),
        ("p1", "s3", datetime.datetime(2026, 1, 3), 100),
        ("p1", "s4", datetime.datetime(2026, 1, 4), 910),
        ("p1", "s5", datetime.datetime(2026, 1, 5), 895),
        ("p1", "s6", datetime.datetime(2026, 1, 6), 905),
    ])
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert [e["merge_sha"] for e in finding["evidence"]] == ["s4", "s5", "s6"]
    for e in finding["evidence"]:
        assert e["breach_run_length"] == 3


def test_a_project_with_no_run_of_three_and_a_clean_project_are_both_left_alone(conn, rule):
    """A stable seven-point flat series (population 6, no breach possible -- see Task 3's own
    fixture) alongside a second project's own isolated single breach (population-eligible, but
    run length 1) -- population counts both projects' evaluable rows; evidence is empty for
    both. Hand-computed and verified live."""
    _insert(conn, [
        ("p1", "s1", datetime.datetime(2026, 1, 1), 432000),
        ("p1", "s2", datetime.datetime(2026, 1, 3), 432000),
        ("p1", "s3", datetime.datetime(2026, 1, 5), 432000),
        ("p2", "t1", datetime.datetime(2026, 1, 1), 200),
        ("p2", "t2", datetime.datetime(2026, 1, 2), 200),
        ("p2", "t3", datetime.datetime(2026, 1, 3), 200),
        ("p2", "t4", datetime.datetime(2026, 1, 4), 500),
    ])
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Threshold", "metric": "3", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_two_projects_short_runs_never_concatenate_into_one_fabricated_run(conn, rule):
    """A run must not leak across projects. Two projects each hold a run of 2 -- short of k=3 --
    and their breaches are INTERLEAVED IN TIME (p1 d4, p2 d5, p1 d6) so that a run length counted
    without `PARTITION BY project_id` would splice them into a single run of 3 and fire. The
    existing two-project case above cannot catch that (neither project breaches more than once,
    so the spliced run is still short); this one is the mutation test for it. Verified live both
    ways: as shipped -> PASS; with run_lengths' partition reduced to `OVER (PARTITION BY grp)` ->
    WARN on a fabricated 3-row run spanning both projects."""
    _insert(conn, [
        ("p1", "a1", datetime.datetime(2026, 1, 1), 100),
        ("p2", "b1", datetime.datetime(2026, 1, 1), 100),
        ("p1", "a2", datetime.datetime(2026, 1, 2), 100),
        ("p2", "b2", datetime.datetime(2026, 1, 2), 100),
        ("p1", "a3", datetime.datetime(2026, 1, 3), 100),
        ("p2", "b3", datetime.datetime(2026, 1, 3), 100),
        ("p1", "a4", datetime.datetime(2026, 1, 4), 1000),
        ("p2", "b5", datetime.datetime(2026, 1, 5), 1000),
        ("p1", "a6", datetime.datetime(2026, 1, 6), 1000),
    ])
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "PASS"
    assert finding["evidence"] == []


def test_unmeasured_rows_never_occupy_a_slot_in_a_neighbours_trailing_window(conn, rule):
    """D2, unaffected by k=3: a NULL lead_time_seconds row (an unmeasured merge) contributes
    nothing to any window at all. Reusing the sustained-regression shape's own healthy prefix
    plus one unmeasured row dated after every measured row changes nothing about the result."""
    _insert(conn, [
        ("p1", "s1", datetime.datetime(2026, 1, 1), 100),
        ("p1", "s2", datetime.datetime(2026, 1, 2), 100),
        ("p1", "s3", datetime.datetime(2026, 1, 3), 100),
        ("p1", "s4", datetime.datetime(2026, 1, 4), 910),
        ("p1", "s5", datetime.datetime(2026, 1, 5), 895),
        ("p1", "s6", datetime.datetime(2026, 1, 6), 905),
    ])
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, merge_ts, "
        "lead_time_seconds) VALUES ('p1', 's7', '2026-01-07', NULL)"
    )
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert [e["merge_sha"] for e in finding["evidence"]] == ["s4", "s5", "s6"]
