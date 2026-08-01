# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/definition_no_plan_artifact.sql (issue #118, E3.S3, Task 1). See the
rule's own guardrail and .sdlc/plans/118.md Design decision 3 for the naming-convention mismatch
this repo's own real data hits (plan_artifact_present checks .sdlc/plans/<goal-stem>.md exactly,
but this repo's plans are issue-numbered) -- these fixtures are synthetic, matching the sibling
coverage_verify_no_command tests' own convention, not real ingested data."""
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


def test_flags_goals_with_no_plan_artifact_and_leaves_a_covered_one_alone(conn, registry):
    """g1 (p1, plan_artifact_present=true) -- covered, never flagged. g2 (p1,
    plan_artifact_present=false); g3 (p2, plan_artifact_present=false).

    Hand-computed: population = count(*) = 3 (> 0). Evidence WHERE plan_artifact_present = false
    matches g2 (p1) and g3 (p2); ordered by project_id, goal_id."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, plan_artifact_present) VALUES "
        "('p1', 'g1', true), ('p1', 'g2', false), ('p2', 'g3', false)"
    )
    finding = evaluate_rule(conn, registry["definition_no_plan_artifact"])
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [
        {"project_id": "p1", "goal_id": "g2"},
        {"project_id": "p2", "goal_id": "g3"},
    ]
    assert set(finding["evidence"][0]) == {"project_id", "goal_id"}


def test_every_goal_with_a_plan_artifact_is_pass(conn, registry):
    """Only g1 inserted (plan_artifact_present=true): population 1 (> 0); evidence query returns
    0 rows -> PASS, evidence=[]."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, plan_artifact_present) VALUES "
        "('p1', 'g1', true)"
    )
    rule = registry["definition_no_plan_artifact"]
    assert evaluate_rule(conn, rule) == {
        "class": "Definition", "metric": "plan_artifact_coverage", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }
