# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/coverage_verify_no_command.sql (issue #117, E3.S2, Task 1; see
.sdlc/plans/117.md Design decision 1). An exact reconstruction of the same two fields, in the
same precedence, skills/sdlc-loop/scripts/loop.py:172-179 itself consults before printing
NO-COMMAND and exiting 3 -- not a labelled proxy."""
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


def test_flags_goals_with_no_command_anywhere_and_leaves_covered_goals_alone(conn, registry):
    """p1 has a project-level verify.command fallback; p2's config has no verify key at all; p3
    has no dim_project row at all (LEFT JOIN gives NULL config_json, and
    json_extract_string(NULL, ...) is NULL per 42.sql's own documented behaviour).

    g1 (p1, verify_command=NULL) -- covered by p1's own project fallback. g2 (p1,
    verify_command='make test') -- covered by its own command. g3 (p2, verify_command=NULL) --
    no fallback, flagged. g4 (p3, verify_command=NULL) -- no dim_project row at all, flagged.

    Hand-computed: population = count(*) FROM fact_goal = 4 (> 0). Evidence, ordered by
    project_id, goal_id ('p2' < 'p3')."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"verify\":{\"command\":\"pytest\"}}'), ('p2', '{}')"
    )
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, verify_command) VALUES "
        "('p1', 'g1', NULL), ('p1', 'g2', 'make test'), "
        "('p2', 'g3', NULL), ('p3', 'g4', NULL)"
    )
    finding = evaluate_rule(conn, registry["coverage_verify_no_command"])
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [
        {"project_id": "p2", "goal_id": "g3"},
        {"project_id": "p3", "goal_id": "g4"},
    ]
    assert set(finding["evidence"][0]) == {"project_id", "goal_id"}


def test_every_goal_covered_is_pass(conn, registry):
    """Only g1/g2 inserted: population 2 (> 0); evidence query returns 0 rows -> PASS,
    evidence=[]."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"verify\":{\"command\":\"pytest\"}}')"
    )
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, verify_command) VALUES "
        "('p1', 'g1', NULL), ('p1', 'g2', 'make test')"
    )
    rule = registry["coverage_verify_no_command"]
    assert evaluate_rule(conn, rule) == {
        "class": "Coverage", "metric": "26", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }
