# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/consistency_ledger_done_pr_open.sql (issue #120, E3.S5, Task 1; see
.sdlc/plans/120.md Design decision 1). The merge signal is fact_pr_review/fact_pr_check's own
pr_merged_ts (gh-sourced), never fact_merge_lead_time; the join runs through fact_goal.pr, which
has zero writers under insight/ingest/ today -- so this rule renders ABSENT on this repo's own
real data, and that is correct, not a defect (see the rule's own guardrail)."""
import datetime

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


def test_flags_a_done_goal_whose_linked_pr_has_no_merge_timestamp(conn, registry):
    """fact_goal (p1, g1, pr=501); fact_event (p1, g1, ts='2026-01-01', kind='done');
    fact_pr_review (p1, 501, 'gh', 'e1', verdict='APPROVED', pr_merged_ts=NULL).

    Hand-computed, verified live this session: population 1 (goal done, pr set, 501 fetched by
    gh); evidence 1 row."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, pr) VALUES ('p1', 'g1', 501)"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind) VALUES "
        "('p1', 'g1', '2026-01-01', 'done')"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict, "
        "pr_merged_ts) VALUES ('p1', 501, 'gh', 'e1', 'APPROVED', NULL)"
    )
    finding = evaluate_rule(conn, registry["consistency_ledger_done_pr_open"])
    assert finding["severity"] == "FAIL"
    assert finding["evidence"] == [{
        "project_id": "p1", "goal_id": "g1",
        "ledger_done_ts": datetime.datetime(2026, 1, 1), "pr_number": 501, "pr_merged_ts": None,
    }]
    assert set(finding["evidence"][0]) == {
        "project_id", "goal_id", "ledger_done_ts", "pr_number", "pr_merged_ts",
    }


def test_a_merged_pr_is_pass(conn, registry):
    """Same fixture, but fact_pr_review.pr_merged_ts = '2026-01-02'. Hand-computed, verified
    live: population 1, evidence 0 rows -> PASS."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, pr) VALUES ('p1', 'g1', 501)"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind) VALUES "
        "('p1', 'g1', '2026-01-01', 'done')"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict, "
        "pr_merged_ts) VALUES ('p1', 501, 'gh', 'e1', 'APPROVED', '2026-01-02')"
    )
    rule = registry["consistency_ledger_done_pr_open"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "ledger_done_pr_open", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_a_done_goal_whose_pr_gh_never_fetched_is_excluded_from_population(conn, registry):
    """fact_goal (p1, g1, pr=999); fact_event (p1, g1, ts='2026-01-01', kind='done'); no
    fact_pr_review/fact_pr_check row for (p1, 999) at all. Hand-computed, verified live:
    population 0 -> severity == ABSENT, evidence == [] -- proving a never-fetched PR does not
    silently read as 'confirmed open'."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, pr) VALUES ('p1', 'g1', 999)"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind) VALUES "
        "('p1', 'g1', '2026-01-01', 'done')"
    )
    rule = registry["consistency_ledger_done_pr_open"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "ledger_done_pr_open", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_two_projects_sharing_a_pr_number_are_kept_apart(conn, registry):
    """fact_goal (p1, g1, pr=102), (p5, g5, pr=102); both fact_event ... kind='done';
    fact_pr_review (p1, 102, ..., verdict='APPROVED', pr_merged_ts=NULL), (p5, 102, ...,
    verdict='APPROVED', pr_merged_ts='2026-01-02'). Hand-computed, verified live: population 2;
    evidence exactly one row for p1 -- p5's own merged 102 neither masks nor is masked by p1's
    own open 102."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, pr) VALUES "
        "('p1', 'g1', 102), ('p5', 'g5', 102)"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind) VALUES "
        "('p1', 'g1', '2026-01-01', 'done'), ('p5', 'g5', '2026-01-01', 'done')"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict, "
        "pr_merged_ts) VALUES "
        "('p1', 102, 'gh', 'e1', 'APPROVED', NULL), "
        "('p5', 102, 'gh', 'e2', 'APPROVED', '2026-01-02')"
    )
    finding = evaluate_rule(conn, registry["consistency_ledger_done_pr_open"])
    assert finding["severity"] == "FAIL"
    assert finding["evidence"] == [{
        "project_id": "p1", "goal_id": "g1",
        "ledger_done_ts": datetime.datetime(2026, 1, 1), "pr_number": 102, "pr_merged_ts": None,
    }]


def test_a_goal_with_more_than_one_done_event_contributes_one_evidence_row(conn, registry):
    """fact_goal (p1, g1, pr=501); fact_event two rows, kind='done', ts='2026-01-01' and
    ts='2026-01-03'; fact_pr_review (p1, 501, ..., pr_merged_ts=NULL). Hand-computed, verified
    live: population 1 (one distinct (project_id, goal_id)); evidence exactly one row,
    ledger_done_ts == 2026-01-01 (the MIN, the earlier event) -- not two rows, proving the
    aggregation fix holds."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, pr) VALUES ('p1', 'g1', 501)"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, kind) VALUES "
        "('p1', 'g1', '2026-01-01', 'done'), ('p1', 'g1', '2026-01-03', 'done')"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict, "
        "pr_merged_ts) VALUES ('p1', 501, 'gh', 'e1', 'APPROVED', NULL)"
    )
    finding = evaluate_rule(conn, registry["consistency_ledger_done_pr_open"])
    assert len(finding["evidence"]) == 1
    assert finding["evidence"][0]["ledger_done_ts"] == datetime.datetime(2026, 1, 1)
