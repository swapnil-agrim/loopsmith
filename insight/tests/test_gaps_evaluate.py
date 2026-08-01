# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.gaps.evaluate (issue #116, E3.S1, Task 3; see .sdlc/plans/116.md Design
decision 4 for the full evaluate_rule precedence and Design decision 3 for the two reject
invariants this module and insight.gaps.header enforce, at different times).

Two halves, deliberately mixed in one file: pure unit tests of make_finding (no duckdb import,
no pytest.importorskip -- make_finding never touches a database) and live-conn tests of
evaluate_rule (pytest.importorskip("duckdb"), same conn/tmp_path fixture shape every existing
test_metric_*.py file uses). The importorskip call sits INSIDE the `conn` fixture below, not at
module level, precisely so a duckdb-less environment skips only the live-conn tests and still
runs the pure half -- a module-level importorskip would skip the whole file, including the
tests this module's own docstring promises need no duckdb at all."""
import pytest

from insight.gaps.evaluate import GapEvaluationError, evaluate_rule, make_finding


# --------------------------------------------------------------------------- pure unit tests
# No duckdb import, no pytest.importorskip -- make_finding never touches a database.


def test_a_finding_with_no_evidence_rows_raises():
    """The issue's own Task 3 done_when, pinned directly -- proven by calling make_finding
    directly, bypassing evaluate_rule entirely, with a deliberately contradictory input (Design
    decision 3): under evaluate_rule's own control flow this exact state is unreachable, so this
    test proves the guard exists and fires, not that a real rule can reach this state through
    normal use."""
    with pytest.raises(GapEvaluationError):
        make_finding(gap_class="Definition", metric="24", action="add a done_when",
                     severity="FAIL", evidence=[])


def test_a_finding_at_warn_with_no_evidence_also_raises():
    """The same shape at WARN, proving the guard is not special-cased to FAIL alone."""
    with pytest.raises(GapEvaluationError):
        make_finding(gap_class="Definition", metric="24", action="add a done_when",
                     severity="WARN", evidence=[])


def test_an_absent_finding_with_no_evidence_does_not_raise():
    """The BLOCKING fix's own counter-example, alongside the PASS one below, proving the
    guard's exclusion is specifically {"PASS", "ABSENT"}, not {"PASS"} alone."""
    finding = make_finding(gap_class="Definition", metric="24", action="add a done_when",
                            severity="ABSENT", evidence=[])
    assert finding == {"class": "Definition", "metric": "24", "action": "add a done_when",
                        "severity": "ABSENT", "evidence": []}


def test_a_pass_finding_with_no_evidence_does_not_raise():
    """The counter-example that proves the guard is specifically "WARN/FAIL + no evidence", not
    "any finding with no evidence"."""
    finding = make_finding(gap_class="Definition", metric="24", action="add a done_when",
                            severity="PASS", evidence=[])
    assert finding == {"class": "Definition", "metric": "24", "action": "add a done_when",
                        "severity": "PASS", "evidence": []}


def test_a_finding_with_evidence_and_a_non_pass_severity_does_not_raise():
    """The ordinary, correct case: one evidence row, severity="FAIL" -> returns normally, no
    raise."""
    finding = make_finding(gap_class="Definition", metric="24", action="add a done_when",
                            severity="FAIL", evidence=[{"project_id": "p1", "goal_id": "g1"}])
    assert finding == {"class": "Definition", "metric": "24", "action": "add a done_when",
                        "severity": "FAIL",
                        "evidence": [{"project_id": "p1", "goal_id": "g1"}]}


# --------------------------------------------------------------------------- live-conn tests
# pytest.importorskip("duckdb") lives inside the `conn` fixture below, not here, so the pure
# tests above never trigger it.

# All four tests share one rule dict, grounded in fact_goal's real done_when_present BOOLEAN
# column (insight/ingest/store.py -- used only as an accurate illustrative query, not as a
# shipped Definition-class rule; issue #116 ships none of the five gap classes).
RULE = {
    "class": "Definition", "metric": "24", "action": "add a done_when to the goal",
    "severity": "FAIL",
    "population": "SELECT count(*) FROM fact_goal",
    "query": "SELECT project_id, goal_id FROM fact_goal WHERE done_when_present = false "
             "ORDER BY goal_id",
}
RULE_WARN = {**RULE, "severity": "WARN"}


@pytest.fixture
def conn(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_evaluate_rule_returns_absent_when_the_population_query_returns_zero(conn):
    """Should-fix 2, and the direct proof of Decision 4's first precedence branch:
    ensure_schema only, NO INSERT AT ALL, so fact_goal is genuinely empty. Hand-computed:
    SELECT count(*) FROM fact_goal is 0 -- branch 1 of the precedence fires.

    Against an empty table, RULE["query"] would ALSO return zero rows, so this one test cannot
    by itself distinguish "population checked first" from "evidence checked first, happened to
    also be empty" -- that distinction is what evaluate_rule's unconditional population-first
    check (Decision 4) exists to make true by construction, not something a single test's data
    alone can force."""
    assert evaluate_rule(conn, RULE) == {
        "class": "Definition", "metric": "24", "action": "add a done_when to the goal",
        "severity": "ABSENT", "evidence": [],
    }


def test_evaluate_rule_downgrades_to_pass_when_the_query_returns_zero_rows(conn):
    """Hand-computed: SELECT count(*) FROM fact_goal is 2 (population > 0, branch 1 does not
    fire -- this is the test that distinguishes this case from the ABSENT test directly above:
    same zero evidence rows, different population). The query's WHERE done_when_present = false
    then matches 0 of 2 rows. Note the header's own declared "FAIL" is overridden to "PASS" by
    the zero-row case, proving Decision 2's "severity is computed, not static" claim directly
    rather than by assertion alone."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, done_when_present) VALUES "
        "('p1', 'g1', true), ('p1', 'g2', true)"
    )
    assert evaluate_rule(conn, RULE) == {
        "class": "Definition", "metric": "24", "action": "add a done_when to the goal",
        "severity": "PASS", "evidence": [],
    }


def test_evaluate_rule_returns_the_headers_declared_fail_severity_when_rows_exist(conn):
    """Hand-computed: population is 3 (> 0). g1 (true) is excluded; g2 and g3 (false) both
    match -- exactly 2 of 3 rows. ORDER BY goal_id in RULE["query"] makes the evidence order
    exact."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, done_when_present) VALUES "
        "('p1', 'g1', true), ('p1', 'g2', false), ('p1', 'g3', false)"
    )
    finding = evaluate_rule(conn, RULE)
    assert finding["severity"] == "FAIL"  # RULE's own header value, unmodified
    assert finding["evidence"] == [
        {"project_id": "p1", "goal_id": "g2"},
        {"project_id": "p1", "goal_id": "g3"},
    ]


def test_evaluate_rule_returns_the_headers_declared_warn_severity_when_rows_exist(conn):
    """Should-fix 2: the identical three-row insert as the FAIL test directly above, evaluated
    against RULE_WARN instead of RULE. Hand-computed: population is 3 (> 0, unchanged from
    above); the same g2/g3 pair matches (unchanged from above) -- only RULE_WARN["severity"]
    differs."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, done_when_present) VALUES "
        "('p1', 'g1', true), ('p1', 'g2', false), ('p1', 'g3', false)"
    )
    finding = evaluate_rule(conn, RULE_WARN)
    assert finding["severity"] == "WARN"  # RULE_WARN's own header value, unmodified
    assert finding["evidence"] == [
        {"project_id": "p1", "goal_id": "g2"},
        {"project_id": "p1", "goal_id": "g3"},
    ]


def test_evaluate_rule_returns_absent_when_the_population_query_returns_no_rows_at_all(conn):
    """PRE-PR REVIEW should-fix: `.fetchone()` returns None -- not a row containing None -- when
    the population query yields NO ROWS, and the original `.fetchone()[0]` crashed with
    `TypeError: 'NoneType' object is not subscriptable`. The idiomatic `SELECT count(*)` always
    returns exactly one row even against an empty table, so this cannot fire through the
    documented form; a non-aggregate population query authored in #117-121 can.

    A population query that names NOTHING is the definition of "no instrument", so it belongs in
    the same ABSENT branch as a zero or NULL count -- not in a crash, and emphatically not in
    PASS. Hand-computed: fact_goal is empty and the WHERE matches nothing regardless, so the
    query returns 0 rows and fetchone() is None."""
    rule = {**RULE, "population": "SELECT project_id FROM fact_goal WHERE project_id = 'nope'"}
    assert evaluate_rule(conn, rule) == {
        "class": "Definition", "metric": "24", "action": "add a done_when to the goal",
        "severity": "ABSENT", "evidence": [],
    }


def test_an_absent_finding_carrying_evidence_raises():
    """POST-PR-REVIEW BLOCKING FIX, the guard's other direction. Excluding PASS and ABSENT from
    the empty-evidence check left the converse open: nothing stopped an ABSENT finding from
    carrying evidence rows -- a measured, evidenced finding wearing the token reserved for
    "never measured", indistinguishable by severity alone from a genuinely un-instrumented one
    (spec:534). ABSENT is no longer author-declarable, so evaluate_rule cannot reach this state;
    the guard is what keeps it unreachable for the five gap classes that will call make_finding
    directly."""
    try:
        make_finding(gap_class="Coverage", metric="24", action="add the gate",
                     severity="ABSENT", evidence=[{"goal_id": "g1"}])
        assert False, "expected GapEvaluationError"
    except GapEvaluationError as e:
        assert "ABSENT" in str(e)


def test_a_pass_finding_carrying_evidence_raises():
    """Same guard, the PASS half: PASS means the population was checked and came back clean, so
    evidence rows contradict it outright."""
    try:
        make_finding(gap_class="Definition", metric="24", action="add a done_when",
                     severity="PASS", evidence=[{"goal_id": "g1"}])
        assert False, "expected GapEvaluationError"
    except GapEvaluationError as e:
        assert "PASS" in str(e)
