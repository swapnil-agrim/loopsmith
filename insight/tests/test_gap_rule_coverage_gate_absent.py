# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/coverage_gate_absent.sql (issue #117, E3.S2, Task 1; see
.sdlc/plans/117.md Design decision 2). Duplicates 24.sql's own packs/gates CTEs inline
(41.sql's own precedent) rather than reading `metric_24` as a view -- load_gap_rules never
calls load_metrics (#116 Design decision 7), so this rule's evidence-row shape is proven
directly against evaluate_rule, the same way every other gap-rule test in this story is."""
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


def test_flags_a_pack_with_an_absent_gate_and_leaves_a_fully_measured_pack_alone(conn, registry):
    """Row A is fully measured on both gates (nonzero commits_with_source, nonzero
    window_commit_count). Row B has both denominators empty (commits_with_source=0,
    window_commit_count=0) -- both plan_gate and review_gate are absent for it.

    Hand-computed (plan's own worked example): population = count(*) WHERE schema =
    'alignment-collect/v1' = 2 (> 0). Row A: plan_gate denominator COALESCE(3,0)<=0 is false;
    review_gate denominator COALESCE(5,0)<=0 is false -- neither absent. Row B: plan_gate
    COALESCE(0,0)<=0 is true; review_gate COALESCE(0,0)<=0 is true -- both absent. Evidence
    ordered by project_id, collected_ts, gate ('plan_gate' < 'review_gate' lexically)."""
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, window_commit_count, raw_payload) VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', 5, "
        "'{\"dimensions\": {\"d1\": {\"commits_with_source\": 3}}}'), "
        "('p1', 'alignment-collect/v1', '2026-01-02', 0, "
        "'{\"dimensions\": {\"d1\": {\"commits_with_source\": 0}}}')"
    )
    finding = evaluate_rule(conn, registry["coverage_gate_absent"])
    assert finding["severity"] == "WARN"
    assert [
        {k: e[k] for k in ("project_id", "collected_ts", "gate")} for e in finding["evidence"]
    ] == [
        {"project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 2), "gate": "plan_gate"},
        {"project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 2), "gate": "review_gate"},
    ]
    assert set(finding["evidence"][0]) == {
        "project_id", "collected_ts", "gate", "degraded_collector", "degraded_adapter",
    }


def test_a_pack_with_no_absent_gate_is_pass(conn, registry):
    """Row A alone: population 1 (> 0); evidence query returns 0 rows (both denominators
    nonzero) -> PASS, evidence=[]."""
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, window_commit_count, raw_payload) VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', 5, "
        "'{\"dimensions\": {\"d1\": {\"commits_with_source\": 3}}}')"
    )
    rule = registry["coverage_gate_absent"]
    assert evaluate_rule(conn, rule) == {
        "class": "Coverage", "metric": "24", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }
