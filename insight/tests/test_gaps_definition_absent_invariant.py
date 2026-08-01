# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #118's own Design decision 6, mirroring #117's test_gaps_coverage_absent_invariant.py
file-for-file: on a store that has had ensure_schema run against it and NOTHING inserted, every
real, shipped Definition rule's own population query reads 0, so evaluate_rule's first
precedence branch (#116 Design decision 4) must fire for every one of them: ABSENT,
evidence=[]. Runs against load_gap_rules()'s own real registry, not a hand-copied rule-id list,
so a future Definition rule is covered by this same test with no edit needed here -- only the
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


def test_no_shipped_definition_rule_renders_absent_as_pass_on_a_genuinely_empty_store(conn):
    """Hand-computed: both rules' population is SELECT count(*) FROM fact_goal; on a genuinely
    empty (post-ensure_schema, zero-insert) store, fact_goal has zero rows, so count(*) reads 0
    for both -- evaluate_rule's first precedence branch fires unconditionally for both,
    {"severity": "ABSENT", "evidence": []}."""
    registry = load_gap_rules()
    definition_rules = {k: v for k, v in registry.items() if v["class"] == "Definition"}
    assert len(definition_rules) == 2, (
        "this test's own invariant sweep must stay in sync with the real catalog -- update this "
        "count (and re-read whether the new rule's population is genuinely 0 on an empty store) "
        "if a Definition rule was added or removed"
    )
    for rule_id, rule in definition_rules.items():
        finding = evaluate_rule(conn, rule)
        assert finding["severity"] == "ABSENT", (
            f"{rule_id} rendered {finding['severity']!r} against a genuinely empty store -- "
            "should be ABSENT, and must never be PASS"
        )
        assert finding["evidence"] == []
