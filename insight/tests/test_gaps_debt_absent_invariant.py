# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Structurally identical to #117/#118/#119's own absent-invariant files (issue #121, [E3.S6]): a
Debt rule must render ABSENT, never PASS, when there is nothing to check at all -- an empty
store, a store with only one snapshot ever (no trailing baseline derivable), or a store whose
only snapshot is adapter-degraded."""
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


def test_no_shipped_debt_rule_renders_absent_as_pass_on_a_genuinely_empty_store(conn):
    registry = load_gap_rules()
    debt_rules = {k: v for k, v in registry.items() if v["class"] == "Debt"}
    assert len(debt_rules) == 1, (
        "keep this count in sync with the real catalog if a Debt rule is added or removed"
    )
    for rule_id, rule in debt_rules.items():
        finding = evaluate_rule(conn, rule)
        assert finding["severity"] == "ABSENT"
        assert finding["evidence"] == []


def test_a_single_snapshot_has_no_derivable_baseline_and_is_absent_not_pass(conn):
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, degraded_adapter, "
        "raw_payload) VALUES ('p1', 'discovery-scan/v1', '2026-01-01', [], "
        "'{\"schema\":\"discovery-scan/v1\",\"candidates\":[{\"title\":\"a\"}]}')"
    )
    registry = load_gap_rules()
    rule = registry["debt_discovery_scan_rising"]
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Debt", "metric": "30", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_store_with_only_a_degraded_snapshot_is_absent_not_pass(conn):
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, degraded_adapter, "
        "raw_payload) VALUES ('p1', 'discovery-scan/v1', '2026-01-01', "
        "['adapter_exit_nonzero'], '{\"schema\":\"discovery-scan/v1\",\"candidates\":[]}')"
    )
    registry = load_gap_rules()
    rule = registry["debt_discovery_scan_rising"]
    finding = evaluate_rule(conn, rule)
    assert finding == {
        "class": "Debt", "metric": "30", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }
