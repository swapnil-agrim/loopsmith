# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/consistency_files_outside_plan.sql (issue #120, E3.S5, Task 1; see
.sdlc/plans/120.md Design decision 3). The evidence array column is a direct
CAST(json_extract(...) AS VARCHAR[]), proven live to return a native Python list with no
UNNEST needed."""
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


def test_flags_a_pack_with_source_commits_and_files_touched_outside_any_plan(conn, registry):
    """One fact_collector_pack row, schema='alignment-collect/v1', raw_payload='{"schema":
    "alignment-collect/v1","dimensions":{"d1":{"commits_with_source":3,
    "files_changed_outside_any_plan":["scratch.py","notes.md"],
    "files_outside_plan_confidence":"low"}}}'. Hand-computed, verified live this session:
    population 1 (d1 present); evidence 1 row."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":3,\"files_changed_outside_any_plan\":"
        "[\"scratch.py\",\"notes.md\"],\"files_outside_plan_confidence\":\"low\"}}}')"
    )
    finding = evaluate_rule(conn, registry["consistency_files_outside_plan"])
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 3,
        "files_changed_outside_any_plan": ["scratch.py", "notes.md"],
        "files_outside_plan_confidence": "low",
    }]
    assert isinstance(finding["evidence"][0]["files_changed_outside_any_plan"], list)


def test_an_empty_outside_plan_list_is_pass(conn, registry):
    """Same fixture, files_changed_outside_any_plan: []. Verified live: population 1, evidence
    0 rows -> PASS."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":3,\"files_changed_outside_any_plan\":[],"
        "\"files_outside_plan_confidence\":\"low\"}}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_zero_source_commits_does_not_fire(conn, registry):
    """POST-PRE-PR-REVIEW FIX (mutation-tested): commits_with_source: 0, PAIRED WITH A
    NON-EMPTY files_changed_outside_any_plan (["scratch.py"]), mirroring how the sibling
    test_gap_rule_consistency_verify_no_test_touched.py's own
    test_zero_source_commits_does_not_fire_even_at_zero_percent pairs commits_with_source: 0
    with tests_touched_with_source_pct: 0. The ORIGINAL fixture paired commits_with_source: 0
    with an EMPTY array -- json_array_length(...) > 0 alone already forces PASS for an empty
    array, so that fixture passed whether or not the commits_with_source > 0 gate existed at
    all (confirmed live: deleting that gate from the rule left this test, and the other three
    in this file, green). Pairing zero commits with a genuinely NON-EMPTY outside-plan array
    makes the commits_with_source > 0 gate the ONLY thing standing between this fixture and a
    WARN, so this test now actually proves that gate is load-bearing (confirmed live: restoring
    the deleted gate mutation makes this exact fixture FAIL, evidence == 1 row, not the
    asserted PASS/0 rows)."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":0,\"files_changed_outside_any_plan\":[\"scratch.py\"],"
        "\"files_outside_plan_confidence\":\"low\"}}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_a_pack_missing_d1_entirely_is_absent_not_pass(conn, registry):
    """raw_payload='{"schema":"alignment-collect/v1","dimensions":{}}'. Hand-computed, verified
    live: population 0 -> severity == ABSENT, evidence == []."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_d1_json_null_literal_is_absent_not_pass(conn, registry):
    """POST-PR-REVIEW BLOCKING FIX (PR #203, mutation-tested). json_extract(raw_payload,
    '$.dimensions.d1') IS NOT NULL -- the ORIGINAL population guard -- is TRUE for a d1 that is a
    JSON `null` literal (json_extract returns a non-SQL-NULL value for any PRESENT key regardless
    of type; only a genuinely MISSING key returns SQL NULL). Under that guard this fixture read
    population 1 while commits_with_source/files_changed_outside_any_plan extraction both
    returned NULL and were softened by COALESCE(...,0) into "nothing to flag", rendering a false
    PASS. json_type(raw_payload, '$.dimensions.d1') = 'OBJECT' correctly excludes it: json_type
    of a JSON null literal is the STRING 'NULL', not the guard's 'OBJECT', so population is
    0 -> ABSENT."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":null}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_d1_json_array_is_absent_not_pass(conn, registry):
    """POST-PR-REVIEW BLOCKING FIX (PR #203, mutation-tested). d1 is a JSON array ([1,2,3])
    rather than an object -- the reviewer's own live reproduction case. Under the ORIGINAL
    json_extract(...) IS NOT NULL guard this read population 1 while commits_with_source/
    files_changed_outside_any_plan extraction (a dotted path into an array) returned NULL and
    was excluded via COALESCE, rendering a false PASS. json_type(...) = 'OBJECT' correctly
    excludes an ARRAY-typed d1: population 0 -> ABSENT."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":[1,2,3]}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_d1_bare_scalar_is_absent_not_pass(conn, registry):
    """POST-PR-REVIEW BLOCKING FIX (PR #203, mutation-tested). d1 is a bare JSON string
    ("degraded") rather than an object -- json_type is 'VARCHAR', not the guard's 'OBJECT', the
    third of the three wrong-shape json_type values named in the review (NULL/ARRAY/VARCHAR,
    alongside the correct OBJECT and the missing-key None). Population 0 -> ABSENT, not a false
    PASS."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":\"degraded\"}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_wellformed_d1_with_files_changed_as_a_bare_string_is_absent_not_pass(conn, registry):
    """POST-PR-REVIEW BLOCKING FIX (PR #203, mutation-tested), the reviewer's own fourth live
    reproduction case and the reason this rule needs a SECOND, NESTED json_type guard, not just
    the top-level one: d1 itself IS a well-formed OBJECT here (commits_with_source: 5), so
    json_type(d1) = 'OBJECT' alone does NOT exclude this row -- but its own
    files_changed_outside_any_plan is a comma-joined STRING ("scratch.py,notes.md"), not a real
    array. Under the top-level-only guard this still read population 1 while
    json_array_length(...) on a VARCHAR returned NULL, softened by COALESCE(...,0) into "nothing
    to flag" -- the exact same collapse one level deeper. The nested
    json_type(raw_payload, '$.dimensions.d1.files_changed_outside_any_plan') = 'ARRAY' guard
    added to both population and evidence closes this: json_type of that field is 'VARCHAR' here,
    not 'ARRAY', so population is 0 -> ABSENT."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5,\"files_changed_outside_any_plan\":"
        "\"scratch.py,notes.md\"}}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }
