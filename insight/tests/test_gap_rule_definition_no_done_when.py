# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/definition_no_done_when.sql (issue #118, E3.S3, Task 1). "No
done_when" means bare absence of the done_when key, inherited from #102's own already-pinned
presence/truthiness semantics -- see .sdlc/plans/118.md Design decision 4, and
insight/tests/test_artifact_reader.py:72-86's
test_goal_missing_done_when_is_recorded_as_absent_not_defaulted /
test_goal_with_empty_done_when_is_present_not_absent, the upstream tests this rule reads off."""
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


def test_flags_goals_with_no_done_when_and_leaves_a_present_but_empty_one_alone(conn, registry):
    """g1 (p1, done_when_present=true) -- a real done_when, never flagged. g2 (p1,
    done_when_present=true) -- stands in for a goal whose done_when: "" was present-but-empty
    (Design decision 4's own pinning: fact_goal cannot distinguish this from g1, and this test
    proves neither is ever flagged). g3 (p1, done_when_present=false); g4 (p2,
    done_when_present=false).

    Hand-computed: population = count(*) = 4 (> 0). Evidence WHERE done_when_present = false
    matches g3 (p1) and g4 (p2); ordered by project_id, goal_id ('p1' < 'p2')."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, done_when_present) VALUES "
        "('p1', 'g1', true), ('p1', 'g2', true), ('p1', 'g3', false), ('p2', 'g4', false)"
    )
    finding = evaluate_rule(conn, registry["definition_no_done_when"])
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [
        {"project_id": "p1", "goal_id": "g3"},
        {"project_id": "p2", "goal_id": "g4"},
    ]
    assert set(finding["evidence"][0]) == {"project_id", "goal_id"}


def test_every_goal_with_a_done_when_is_pass(conn, registry):
    """Only g1/g2 inserted (both done_when_present=true): population 2 (> 0); evidence query
    returns 0 rows -> PASS, evidence=[]."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, done_when_present) VALUES "
        "('p1', 'g1', true), ('p1', 'g2', true)"
    )
    rule = registry["definition_no_done_when"]
    assert evaluate_rule(conn, rule) == {
        "class": "Definition", "metric": "done_when_coverage", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }
