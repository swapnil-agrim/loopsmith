# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.gaps.loader (issue #116, E3.S1, Task 1). pytest.importorskip("duckdb") is
NOT needed -- there is no duckdb anywhere in insight/gaps/loader.py (Design decision 7)."""
import pytest

from insight.gaps.loader import GapLoadError, load_gap_rules


def _write(path, text):
    path.write_text(text, encoding="utf-8")


_GOOD_1 = (
    "-- name: Missing done_when\n-- class: Definition\n-- metric: 24\n"
    "-- action: add a done_when to the goal\n-- severity: FAIL\n"
    "-- guardrail: pair with #5\n-- population: SELECT count(*) FROM fact_goal\n"
    "SELECT project_id, goal_id FROM fact_goal WHERE done_when_present = false\n"
)
_MISSING_GUARDRAIL = (
    "-- name: Bad\n-- class: Definition\n-- metric: 24\n"
    "-- action: do something\n-- severity: FAIL\n"
    "-- population: SELECT count(*) FROM fact_goal\nSELECT 1\n"
)
_MISSING_POPULATION = (
    "-- name: Bad\n-- class: Definition\n-- metric: 24\n"
    "-- action: do something\n-- severity: FAIL\n-- guardrail: z\nSELECT 1\n"
)
_NO_EVIDENCE_QUERY = (
    "-- name: Bad\n-- class: Definition\n-- metric: 24\n"
    "-- action: do something\n-- severity: FAIL\n-- guardrail: z\n"
    "-- population: SELECT count(*) FROM fact_goal\n"
    "-- TODO: write the query later\n"
)


def test_loads_a_conforming_rule_and_keeps_its_full_text_as_query(tmp_path):
    _write(tmp_path / "1.sql", _GOOD_1)
    registry = load_gap_rules(rules_dir=tmp_path)
    assert registry["1"]["class"] == "Definition"
    assert "-- name: Missing done_when" in registry["1"]["query"]
    assert "SELECT project_id, goal_id FROM fact_goal" in registry["1"]["query"]


def test_a_rule_missing_a_required_header_field_fails_the_loader(tmp_path):
    """Mirrors test_a_metric_missing_a_guardrail_header_fails_the_loader's "the issue's own
    done-when, verbatim" framing, adapted."""
    _write(tmp_path / "1.sql", _MISSING_GUARDRAIL)
    with pytest.raises(GapLoadError) as exc:
        load_gap_rules(rules_dir=tmp_path)
    assert "1.sql" in str(exc.value)
    assert "guardrail" in str(exc.value)


def test_a_rule_missing_population_is_rejected_at_load_time(tmp_path):
    """The load-time half of the BLOCKING fix, exercised through the loader, mirroring
    test_a_rule_with_no_evidence_query_is_rejected's own pattern directly below."""
    _write(tmp_path / "1.sql", _MISSING_POPULATION)
    with pytest.raises(GapLoadError) as exc:
        load_gap_rules(rules_dir=tmp_path)
    assert "1.sql" in str(exc.value)
    assert "population" in str(exc.value)


def test_a_rule_with_no_evidence_query_is_rejected(tmp_path):
    """The load-time invariant, exercised through the loader, not just parse_header in
    isolation."""
    _write(tmp_path / "1.sql", _NO_EVIDENCE_QUERY)
    with pytest.raises(GapLoadError) as exc:
        load_gap_rules(rules_dir=tmp_path)
    assert "1.sql" in str(exc.value)
    assert "evidence query" in str(exc.value)


def test_two_bad_rule_files_are_both_named_in_one_raised_error(tmp_path):
    """Mirrors the metrics precedent."""
    _write(tmp_path / "1.sql", _MISSING_GUARDRAIL)
    _write(tmp_path / "2.sql", _MISSING_POPULATION)
    with pytest.raises(GapLoadError) as exc:
        load_gap_rules(rules_dir=tmp_path)
    assert "1.sql" in str(exc.value) and "2.sql" in str(exc.value)


def test_a_bom_prefixed_rule_file_still_parses(tmp_path):
    """Mirrors the metrics precedent."""
    import codecs
    (tmp_path / "1.sql").write_bytes(codecs.BOM_UTF8 + _GOOD_1.encode("utf-8"))
    registry = load_gap_rules(rules_dir=tmp_path)
    assert registry["1"]["name"] == "Missing done_when"


def test_load_gap_rules_defaults_to_the_real_insight_gaps_directory():
    """Issue #117 landed Coverage's four rules; issue #118 added Definition's two; issue #120
    added Consistency's three; issue #121 adds Debt's one -- the fifth gap class to ship a rule
    (epic #115 itself is NOT closed by this story -- #209 and #210 remain open, see
    .sdlc/plans/121.md's own correction banner). This assertion is the intentional update #120's
    own version of this docstring predicted."""
    registry = load_gap_rules()
    expected_class = {
        "coverage_gate_absent": "Coverage",
        "coverage_verify_no_command": "Coverage",
        "coverage_review_missing": "Coverage",
        "coverage_degraded_collector": "Coverage",
        "definition_no_done_when": "Definition",
        "definition_no_plan_artifact": "Definition",
        "threshold_lead_time_breach": "Threshold",
        "consistency_ledger_done_pr_open": "Consistency",
        "consistency_verify_no_test_touched": "Consistency",
        "consistency_files_outside_plan": "Consistency",
        "debt_discovery_scan_rising": "Debt",
    }
    assert set(registry) == set(expected_class)
    for rule_id, rule in registry.items():
        assert rule["class"] == expected_class[rule_id]
