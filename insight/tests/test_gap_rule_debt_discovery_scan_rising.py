# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/debt_discovery_scan_rising.sql (issue #121, [E3.S6]; see
.sdlc/plans/121.md for the full design and the live-verified numbers each case below
reproduces)."""
import datetime
import pathlib

import pytest

from insight.gaps.evaluate import evaluate_rule
from insight.gaps.loader import load_gap_rules

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "121_debt_rising_and_flat.jsonl"


@pytest.fixture
def conn(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


@pytest.fixture
def rule():
    return load_gap_rules()["debt_discovery_scan_rising"]


def _insert(conn, project_id, counts, start=datetime.date(2026, 1, 1)):
    for i, c in enumerate(counts):
        ts = datetime.datetime.combine(start + datetime.timedelta(days=i), datetime.time())
        payload = ('{"schema":"discovery-scan/v1","candidates":[' +
                   ",".join('{"title":"x"}' for _ in range(c)) + "]}")
        conn.execute(
            "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, "
            "degraded_adapter, raw_payload) VALUES (?, 'discovery-scan/v1', ?, [], ?)",
            [project_id, ts, payload],
        )


def test_task_3_fixture_fires_on_the_rising_project_and_leaves_the_flat_one_alone(conn, rule):
    from insight.metrics.testing import load_fixture_jsonl

    load_fixture_jsonl(conn, FIXTURE)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    project_ids = {e["project_id"] for e in finding["evidence"]}
    assert project_ids == {"projRising"}, (
        "projFlat must contribute zero evidence rows -- a flat, healthy series must never fire"
    )
    assert len(finding["evidence"]) == 3
    for e in finding["evidence"]:
        assert e["candidate_count"] == 40
        assert e["breach_run_length"] == 3


def test_a_sustained_step_up_fires_starting_at_the_first_regressed_snapshot(conn, rule):
    """Hand-computed and verified live this session: trailing_p85 of six 20s is 20.0, so the
    7th snapshot (the first 40) already breaches; run reaches length 3 by the 9th snapshot and
    then stops (the trailing baseline itself has absorbed the new regime by the 10th) -- the
    same accepted, named limitation Threshold's own rule discloses."""
    _insert(conn, "p1", [20] * 6 + [40] * 4)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert [e["candidate_count"] for e in finding["evidence"]] == [40, 40, 40]
    assert len(finding["evidence"]) == 3


def test_a_single_one_off_spike_does_not_fire(conn, rule):
    """Never on a single crossing -- spec section 7's own corrected property 2, verified here for
    Debt the same way #119 verified it for Threshold."""
    _insert(conn, "p1", [20] * 6 + [200] + [20] * 3)
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Debt", "metric": "30", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_a_degraded_snapshot_is_skipped_not_treated_as_a_zero_count(conn, rule):
    """Mirrors metric 30's own IGNORE NULLS proof: a degraded pack's candidate_count is NULLed
    before any comparison, so it neither breaks the run nor poisons the trailing baseline with a
    fabricated zero. A degraded snapshot is spliced into the MIDDLE of the same 6-flat-then-4x40
    leading shape already proven (below) to produce a run of exactly 3 -- 3 flats, a degraded
    snapshot, then 3 more flats (6 real flat measurements total), then the rise. Verified live
    this session: the resulting trailing_p85 sequence at the three breaching rows
    (20.0, 21.999999999999993, 39.0) is BYTE-IDENTICAL to the ungapped 6-then-4 case's own
    sequence -- direct proof the degraded row contributes nothing at all to the window, not even
    a low value that would have shifted the quantile."""
    _insert(conn, "p1", [20, 20, 20], start=datetime.date(2026, 1, 1))
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, degraded_adapter, "
        "raw_payload) VALUES ('p1', 'discovery-scan/v1', '2026-01-04', "
        "['adapter_exit_nonzero'], '{\"schema\":\"discovery-scan/v1\",\"candidates\":[]}')"
    )
    _insert(conn, "p1", [20, 20, 20], start=datetime.date(2026, 1, 5))
    _insert(conn, "p1", [40, 40, 40, 40], start=datetime.date(2026, 1, 8))
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert len(finding["evidence"]) == 3
    for e in finding["evidence"]:
        assert e["candidate_count"] == 40


def test_two_projects_interleaved_never_splice_into_one_fabricated_run(conn, rule):
    """PARTITION BY project_id, mutation-style proof mirroring
    test_two_projects_short_runs_never_concatenate_into_one_fabricated_run in the Threshold
    suite: projA holds the same 6-flat-then-4x40 shape already proven to produce a genuine run of
    3, projB stays flat at the same 10 dates, and every insert is interleaved day-by-day (projA's
    day N lands immediately before projB's day N) so a window with no PARTITION BY would see the
    two projects' rows interleaved in ORDER BY collected_ts. Reproduces this plan's own
    'Multi-project partition safety' live output verbatim: WARN, evidence at
    2026-01-07/08/09, candidate_count 40, projA only -- projB never appears."""
    dates = [datetime.date(2026, 1, d) for d in range(1, 11)]
    counts_a = [20, 20, 20, 20, 20, 20, 40, 40, 40, 40]
    counts_b = [20] * 10
    for d, ca, cb in zip(dates, counts_a, counts_b):
        _insert(conn, "projA", [ca], start=d)
        _insert(conn, "projB", [cb], start=d)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert {e["project_id"] for e in finding["evidence"]} == {"projA"}
    assert len(finding["evidence"]) == 3
