# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #118's own Task 3, literally: one SYNTHETIC "complete" goal (.sdlc/plans/118.md Design
decision 3 -- this repo's own real data cannot demonstrate this case, since
plan_artifact_present is 0/19 here), run through both new Definition rules at once -- a single
shared row complete in every dimension either new rule reads, proving neither one fires on it
simultaneously. A bonus assertion, named as such here and not required by the issue's literal
three tasks, also runs the same row through the already-shipped coverage_verify_no_command
(Design decision 1's "met by reference" clause), demonstrating a goal genuinely complete in all
three spec:527 senses passes all three checks, not just the two this story ships."""
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


def test_a_complete_goal_passes_both_new_definition_rules(conn):
    """Hand-computed: population = count(*) FROM fact_goal = 1 (> 0) for both rules. Evidence
    WHERE done_when_present = false matches 0 rows (g1 is true); evidence WHERE
    plan_artifact_present = false matches 0 rows (g1 is true) -- both PASS, evidence=[]."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, done_when_present, plan_artifact_present, "
        "verify_command) VALUES ('p1', 'g1', true, true, 'pytest -q')"
    )
    registry = load_gap_rules()
    assert evaluate_rule(conn, registry["definition_no_done_when"]) == {
        "class": "Definition", "metric": "done_when_coverage",
        "action": registry["definition_no_done_when"]["action"],
        "severity": "PASS", "evidence": [],
    }
    assert evaluate_rule(conn, registry["definition_no_plan_artifact"]) == {
        "class": "Definition", "metric": "plan_artifact_coverage",
        "action": registry["definition_no_plan_artifact"]["action"],
        "severity": "PASS", "evidence": [],
    }
    # Bonus, not required by the issue's own three tasks: the same row's verify_command also
    # makes it PASS under the already-shipped coverage_verify_no_command (Design decision 1).
    assert evaluate_rule(conn, registry["coverage_verify_no_command"])["severity"] == "PASS"
