# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Structurally identical to #117/#118's own absent-invariant files (issue #119, [E3.S4]): a
Threshold rule must render ABSENT, never PASS, when there is nothing to check at all -- an empty
store, or a store with too little history to derive even one trailing baseline."""
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


def test_no_shipped_threshold_rule_renders_absent_as_pass_on_a_genuinely_empty_store(conn):
    registry = load_gap_rules()
    threshold_rules = {k: v for k, v in registry.items() if v["class"] == "Threshold"}
    assert len(threshold_rules) == 1, (
        "keep this count in sync with the real catalog if a Threshold rule is added or removed"
    )
    for rule_id, rule in threshold_rules.items():
        finding = evaluate_rule(conn, rule)
        assert finding["severity"] == "ABSENT"
        assert finding["evidence"] == []


def test_a_single_measured_merge_has_no_derivable_baseline_and_is_absent_not_pass(conn):
    """The realistic squash-merge-repo shape: exactly one measured merge ever has population 0
    (no PRIOR measured merge exists to derive a trailing baseline from) -- ABSENT, not PASS.
    Verified live this session."""
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, merge_ts, lead_time_seconds) "
        "VALUES ('p1', 's1', '2026-01-01', 500)"
    )
    registry = load_gap_rules()
    rule = registry["threshold_lead_time_breach"]
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Threshold", "metric": "3", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }
