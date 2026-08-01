# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/coverage_review_missing.sql (issue #117, E3.S2, Task 1; see
.sdlc/plans/117.md Design decision 3, including its own POST-PLAN-REVIEW CORRECTION paragraph).
The PR roster is fact_merge_lead_time.pr_number, not the dark fact_goal.pr -- and the
population is bounded by both gh's own ~50-PR fetch window (gh_reader.py:59's
_PR_FETCH_LIMIT) and by work.require_review == 'approval' exactly, never 'changes' or a
missing/off key (work.py:374-378's own three-way vocabulary)."""
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


def test_flags_a_merged_pr_with_no_approval_and_excludes_a_never_fetched_pr_and_a_colliding_pr_number_from_another_project(
    conn, registry
):
    """dim_project: p1/p5 are 'approval', p2 is 'off', p3's key is absent. fact_merge_lead_time:
    p1 has three merged PRs (101, 102, 103) but gh never fetched 103 -- no fact_pr_review/
    fact_pr_check row exists for (p1, 103) at all; p2 has 201; p3 has 301; p5 has 102 -- the
    SAME PR number as p1's 102, a different project. fact_pr_review: (p1,101,'APPROVED'),
    (p1,102,'CHANGES_REQUESTED'), (p5,102,'APPROVED').

    Hand-computed (plan's own worked example, verified live against a real in-memory DuckDB
    connection running these exact queries): require_review_projects = {p1, p5} (p2 is 'off',
    p3's key is absent -- both excluded). fetched_prs = {(p1,101), (p1,102), (p5,102)} -- (p1,103)
    has no row in either gh table, so it is not in this set. merged_prs = fact_merge_lead_time
    (JOIN require_review_projects) (JOIN fetched_prs) = {(p1,101), (p1,102), (p5,102)} --
    (p1,103) is excluded despite being a merged PR in an approval project, because gh never
    fetched it; p2/p3's rows are excluded because neither project is 'approval'. Population =
    count(*) over that distinct (project_id, pr_number) set = 3 (> 0) -- under the ORIGINAL,
    buggy count(DISTINCT pr_number) this would have collapsed (p1,102) and (p5,102) into one
    distinct 102 and undercounted to 2. approved_prs = {(p1,101), (p5,102)} ('APPROVED' on
    both; (p1,102)'s verdict is CHANGES_REQUESTED, not approving). Evidence = merged_prs LEFT
    JOIN approved_prs where unmatched = {(p1,102)} -- (p5,102)'s own approval is correctly
    attributed to p5 and does not mask p1's own unapproved 102, proving the (project_id,
    pr_number) grain, not just the JOIN key, is honoured end to end."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"work\":{\"require_review\":\"approval\"}}'), "
        "('p2', '{\"work\":{\"require_review\":\"off\"}}'), "
        "('p3', '{}'), "
        "('p5', '{\"work\":{\"require_review\":\"approval\"}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number) VALUES "
        "('p1', 's101', 101), ('p1', 's102', 102), ('p1', 's103', 103), "
        "('p2', 's201', 201), ('p3', 's301', 301), ('p5', 's501', 102)"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict) VALUES "
        "('p1', 101, 'gh', 'e1', 'APPROVED'), "
        "('p1', 102, 'gh', 'e2', 'CHANGES_REQUESTED'), "
        "('p5', 102, 'gh', 'e3', 'APPROVED')"
    )
    finding = evaluate_rule(conn, registry["coverage_review_missing"])
    assert finding["severity"] == "FAIL"
    assert finding["evidence"] == [{"project_id": "p1", "pr_number": 102}]
    assert set(finding["evidence"][0]) == {"project_id", "pr_number"}


def test_the_loopsmith_approve_marker_counts_as_approval(conn, registry):
    """A single PR (p1, 101), require_review='approval', fact_pr_review verdict='APPROVE' (the
    PR-comment marker spelling, not the native one -- this row also satisfies the fetched_prs
    membership check, since it is a real fact_pr_review row).

    Hand-computed: population 1 (p1 is 'approval', 101 is fetched), evidence 0 rows -> PASS,
    evidence=[] -- proves the second verdict spelling is honoured, not just the native one."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"work\":{\"require_review\":\"approval\"}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number) VALUES "
        "('p1', 's101', 101)"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict) VALUES "
        "('p1', 101, 'gh', 'e1', 'APPROVE')"
    )
    rule = registry["coverage_review_missing"]
    assert evaluate_rule(conn, rule) == {
        "class": "Coverage", "metric": "pr_review_coverage", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_off_missing_key_and_changes_all_contribute_nothing_to_population(conn, registry):
    """p2 is 'off', p3's key is absent, p4 is 'changes'. fact_merge_lead_time: p2 has 201, p3
    has 301, p4 has 102. fact_pr_review: (p4, 102, 'CHANGES_REQUESTED') -- a REAL, gh-fetched,
    UNAPPROVED PR in p4, deliberately included to prove 'changes' is excluded on its own policy
    meaning, not merely because no evidence exists for it.

    Hand-computed: require_review_projects (= 'approval') = {} -- p2 is 'off', p3's key is
    absent, p4 is 'changes' (a real, valid, non-'off' policy, still excluded because it is not
    'approval'). Population = 0 -> evaluate_rule returns the engine's own ABSENT -- severity ==
    'ABSENT', evidence == [], even though p4 genuinely has a merged, fetched, unapproved PR
    sitting right there. This is the case this repo's own real data hits today (its own
    require_review is 'changes') -- proof the fix does not quietly re-admit 'changes' through
    the back door: 'changes' mode never asked for an approval in the first place."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p2', '{\"work\":{\"require_review\":\"off\"}}'), "
        "('p3', '{}'), "
        "('p4', '{\"work\":{\"require_review\":\"changes\"}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number) VALUES "
        "('p2', 's201', 201), ('p3', 's301', 301), ('p4', 's401', 102)"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict) VALUES "
        "('p4', 102, 'gh', 'e1', 'CHANGES_REQUESTED')"
    )
    rule = registry["coverage_review_missing"]
    assert evaluate_rule(conn, rule) == {
        "class": "Coverage", "metric": "pr_review_coverage", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_require_review_true_and_mixed_case_approval_both_count_as_approval_mode(conn, registry):
    """PRE-PR REVIEW should-fix. config_json is stored VERBATIM at ingest with no normalisation,
    but work.py:315-321's review_mode() maps a bare `true` to approval outright and lowercases
    and strips every string value, and doctor.py's own _review_gate_state() agrees. A rule
    comparing `= 'approval'` exactly therefore read a project configured `require_review: true`
    or "Approval" as population 0 and rendered a permanent, silent ABSENT for a policy that is
    genuinely ACTIVE -- a false "nothing to check" rather than a false FAIL, but wrong in the
    same family, and on the one rule this story already had to correct once.

    p6 uses the JSON boolean `true`, p7 uses "Approval" with a capital and surrounding spaces.
    Hand-computed: both normalise into approval mode, so require_review_projects = {p6, p7};
    each has one merged PR that gh DID fetch (a fact_pr_review row exists) and neither carries
    an approving verdict, so population = 2 and both are evidence -> FAIL with two rows,
    ordered by project_id then pr_number."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p6', '{\"work\":{\"require_review\":true}}'), "
        "('p7', '{\"work\":{\"require_review\":\"  Approval  \"}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number) VALUES "
        "('p6', 's601', 601), ('p7', 's701', 701)"
    )
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict) VALUES "
        "('p6', 601, 'gh', 'e1', 'CHANGES_REQUESTED'), "
        "('p7', 701, 'gh', 'e2', 'COMMENTED')"
    )
    rule = registry["coverage_review_missing"]
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "FAIL"
    assert finding["evidence"] == [
        {"project_id": "p6", "pr_number": 601},
        {"project_id": "p7", "pr_number": 701},
    ]
