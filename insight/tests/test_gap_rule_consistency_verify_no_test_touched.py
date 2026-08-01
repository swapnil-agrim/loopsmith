# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/consistency_verify_no_test_touched.sql (issue #120, E3.S5, Task 1;
see .sdlc/plans/120.md Design decision 2). Population is scoped to packs that actually carry a
d2 block, not merely the right schema -- a bare schema-only filter was proven live to be a
false-PASS trap (Key facts item 3)."""
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


def test_flags_a_pack_with_source_commits_a_known_verify_command_and_zero_tests_touched(
    conn, registry
):
    """One fact_collector_pack row, schema='alignment-collect/v1', raw_payload carries d1 with
    commits_with_source=5 and d2 with tests_touched_with_source_pct=0,
    test_command_known=true. Hand-computed, verified live this session: population 1 (d2
    present); evidence 1 row."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}')"
    )
    finding = evaluate_rule(conn, registry["consistency_verify_no_test_touched"])
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 5, "tests_touched_with_source_pct": 0, "test_command_known": True,
    }]


def test_a_nonzero_test_touch_percentage_is_pass(conn, registry):
    """Same fixture, tests_touched_with_source_pct: 40. Verified live: population 1, evidence 0
    rows -> PASS."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":40,"
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_test_command_unknown_does_not_fire_even_at_zero_percent(conn, registry):
    """Same fixture, test_command_known: false, tests_touched_with_source_pct: 0. Verified
    live: population 1 (d2 present), evidence 0 rows -> PASS -- a pack the project itself
    flagged as having no known test command (which also degrades with no_test_command, per a
    different rule) must not additionally read as a test-touch violation."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":false}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_zero_source_commits_does_not_fire_even_at_zero_percent(conn, registry):
    """commits_with_source: 0, tests_touched_with_source_pct: 0, test_command_known: true.
    Verified live: population 1, evidence 0 rows -> PASS -- the trivial 0% alignment-collect.sh
    itself emits when there is nothing to measure is not a real disagreement."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":0},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "PASS", "evidence": [],
    }


def test_a_pack_missing_d2_entirely_is_absent_not_pass(conn, registry):
    """One fact_collector_pack row, schema='alignment-collect/v1', raw_payload='{"schema":
    "alignment-collect/v1","dimensions":{}}'. Hand-computed, verified live: population 0 (no
    pack carries a d2 block) -> ABSENT -- proving the false-PASS trap is actually closed, not
    merely described."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_d2_json_null_literal_is_absent_not_pass(conn, registry):
    """POST-PR-REVIEW BLOCKING FIX (PR #203, mutation-tested). json_extract(raw_payload,
    '$.dimensions.d2') IS NOT NULL -- the ORIGINAL population guard -- is TRUE for a d2 that is a
    JSON `null` literal (json_extract returns a non-SQL-NULL value for any PRESENT key regardless
    of type; only a genuinely MISSING key returns SQL NULL). Under that guard this fixture read
    population 1 while every d2 field extraction below it returned NULL and was softened by
    CAST(...)/COALESCE into "nothing to flag", rendering a false PASS. json_type(raw_payload,
    '$.dimensions.d2') = 'OBJECT' correctly excludes it: json_type of a JSON null literal is the
    STRING 'NULL', not the guard's 'OBJECT', so population is 0 -> ABSENT."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":null}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_d2_json_array_is_absent_not_pass(conn, registry):
    """POST-PR-REVIEW BLOCKING FIX (PR #203, mutation-tested). d2 is a JSON array ([1,2,3])
    rather than an object -- the reviewer's own live reproduction case. Under the ORIGINAL
    json_extract(...) IS NOT NULL guard this read population 1 while
    test_command_known/tests_touched_with_source_pct extraction (a dotted path into an array)
    returned NULL and was excluded via CAST(...) IS TRUE / COALESCE, rendering a false PASS.
    json_type(...) = 'OBJECT' correctly excludes an ARRAY-typed d2: population 0 -> ABSENT."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":[1,2,3]}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_d2_bare_scalar_is_absent_not_pass(conn, registry):
    """POST-PR-REVIEW BLOCKING FIX (PR #203, mutation-tested). d2 is a bare JSON string
    ("degraded") rather than an object -- json_type is 'VARCHAR', not the guard's 'OBJECT', the
    third of the three wrong-shape json_type values named in the review (NULL/ARRAY/VARCHAR,
    alongside the correct OBJECT and the missing-key None). Population 0 -> ABSENT, not a false
    PASS."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":\"degraded\"}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }
