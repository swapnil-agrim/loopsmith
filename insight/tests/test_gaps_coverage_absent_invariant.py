# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #117's own Task 3, phrased as the invariant it actually is (see .sdlc/plans/117.md
Design decision 6): on a store that has had ensure_schema run against it and NOTHING inserted,
every real, shipped Coverage rule's own population query reads 0, so evaluate_rule's first
precedence branch (#116 Design decision 4) must fire for every one of them: ABSENT,
evidence=[]. Runs against load_gap_rules()'s own real registry, not a hand-copied rule-id list,
so a future Coverage rule is covered by this same test with no edit needed here -- only the
count assertion below would need updating, and would fail loudly if it were forgotten."""
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


def test_no_shipped_coverage_rule_renders_absent_as_pass_on_a_genuinely_empty_store(conn):
    """The issue's own Task 3 wording, phrased as the invariant it actually is: on a store that
    has ensure_schema run against it and NOTHING inserted, every real, shipped Coverage rule's
    OWN population query reads 0 -- proven per rule below -- so evaluate_rule's first
    precedence branch (Decision 4, #116) must fire for every one of them: ABSENT, evidence=[].
    NEVER PASS -- PASS would mean "checked, found clean", which is false on a store nothing was
    ever ingested into. Runs against load_gap_rules()'s own real registry, not a hand-copied
    rule-id list, so a future Coverage rule added by #117's own follow-up work is covered by
    this same test with no edit needed here -- only the count assertion below would need
    updating, and would fail loudly if it were forgotten.

    Hand-computed, per rule, why each population query reads 0 on a genuinely empty
    (post-ensure_schema, zero-insert) store: coverage_gate_absent's count(*) FROM
    fact_collector_pack WHERE schema = 'alignment-collect/v1' -- fact_collector_pack has zero
    rows -> 0. coverage_verify_no_command's count(*) FROM fact_goal -- fact_goal has zero rows
    -> 0. coverage_review_missing's count(*) over a derived table built from
    fact_merge_lead_time -- fact_merge_lead_time has zero rows, so the derived table's own
    FROM/JOIN produces zero rows before the EXISTS filters or the outer count(*) ever run,
    regardless of what dim_project/fact_pr_review/fact_pr_check themselves contain -> 0.
    coverage_degraded_collector's count(*) FROM fact_collector_pack -- same empty table as the
    first -> 0. All four are plain count(...) aggregates, so fetchone() returns exactly one row
    ((0,)) in every case, never None -- this test exercises the "population 0" branch
    specifically, not the separate "fetchone() returns no row at all" branch (already covered
    by #116's own
    test_evaluate_rule_returns_absent_when_the_population_query_returns_no_rows_at_all)."""
    registry = load_gap_rules()
    coverage_rules = {k: v for k, v in registry.items() if v["class"] == "Coverage"}
    assert len(coverage_rules) == 4, (
        "this test's own invariant sweep must stay in sync with the real catalog -- update this "
        "count (and re-read whether the new rule's population is genuinely 0 on an empty store) "
        "if a Coverage rule was added or removed"
    )
    for rule_id, rule in coverage_rules.items():
        finding = evaluate_rule(conn, rule)
        assert finding["severity"] == "ABSENT", (
            f"{rule_id} rendered {finding['severity']!r} against a genuinely empty store -- "
            "should be ABSENT, and must never be PASS (issue #117's own Task 3 done_when)"
        )
        assert finding["evidence"] == []
