# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #120's own Task 3, phrased as the invariant it actually is (see .sdlc/plans/120.md
Task 3), mirroring test_gaps_coverage_absent_invariant.py/test_gaps_definition_absent_invariant.py:
on a store that has had ensure_schema run against it and NOTHING inserted, every real, shipped
Consistency rule's own population query reads 0, so evaluate_rule's first precedence branch
(#116 Design decision 4) must fire for every one of them: ABSENT, evidence=[]. Runs against
load_gap_rules()'s own real registry, not a hand-copied rule-id list, so a future Consistency
rule is covered by this same test with no edit needed here -- only the count assertion below
would need updating, and would fail loudly if it were forgotten."""
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


def test_no_shipped_consistency_rule_renders_absent_as_pass_on_a_genuinely_empty_store(conn):
    """Hand-computed, verified live this session against a real ensure_schema'd, zero-insert
    in-memory connection: consistency_ledger_done_pr_open's population is built entirely from
    fact_event/fact_goal/fact_pr_review/fact_pr_check, all empty on a fresh store -> 0.
    consistency_verify_no_test_touched/consistency_files_outside_plan's populations are both
    count(*) FROM fact_collector_pack WHERE ..., and fact_collector_pack is empty -> 0 for
    both. All three render {"severity": "ABSENT", "evidence": []}."""
    registry = load_gap_rules()
    consistency_rules = {k: v for k, v in registry.items() if v["class"] == "Consistency"}
    assert len(consistency_rules) == 3, (
        "this test's own invariant sweep must stay in sync with the real catalog -- update this "
        "count (and re-read whether the new rule's population is genuinely 0 on an empty store) "
        "if a Consistency rule was added or removed"
    )
    for rule_id, rule in consistency_rules.items():
        finding = evaluate_rule(conn, rule)
        assert finding["severity"] == "ABSENT", (
            f"{rule_id} rendered {finding['severity']!r} against a genuinely empty store -- "
            "should be ABSENT, and must never be PASS"
        )
        assert finding["evidence"] == []
