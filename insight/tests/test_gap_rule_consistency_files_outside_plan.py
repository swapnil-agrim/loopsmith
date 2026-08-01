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


def test_a_wellformed_files_changed_array_with_no_commits_with_source_at_all_is_pass(
    conn, registry
):
    """OUT-OF-SCOPE BEHAVIOR, PINNED, the analogous case for this rule to the sibling rule's own
    "well-formed d2, no d1 at all" note (round-2 post-PR-review: named explicitly as "not in
    scope, do not fix, may disclose" for consistency_verify_no_test_touched.sql; applied here
    for the identical reason since commits_with_source plays the SAME soft, secondary-gate role
    in both rules). d1 here IS a well-formed object with a real, non-empty
    files_changed_outside_any_plan array -- but commits_with_source itself is missing entirely
    from that object (not merely malformed). commits_with_source's own guard is deliberately
    `json_type(...) IS NULL OR json_type(...) IN ('UBIGINT', 'BIGINT')` -- the IS NULL branch
    specifically ADMITS a missing commits_with_source (as opposed to a MALFORMED,
    present-but-wrong-typed one, which the same guard's second branch still excludes -- see
    test_commits_with_source_shaped_as_an_object_is_absent_not_a_crash). A missing
    commits_with_source therefore still contributes to population (via the
    files_changed_outside_any_plan guard alone), and TRY_CAST(NULL) softens via COALESCE(...,0)
    to 0, so `0 > 0` is FALSE and the row never reaches evidence -> PASS."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"files_changed_outside_any_plan\":[\"a.py\"]}}}')"
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


def test_commits_with_source_shaped_as_an_object_is_absent_not_a_crash(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), the reviewer's own exact live
    crash reproduction, against the ORIGINAL bare-CAST design this round replaced. d1 here is a
    well-formed object and files_changed_outside_any_plan is a real, non-empty array -- but
    commits_with_source is itself a nested JSON object ({"x": 1}), not a number. Round 1's fix
    guarded only the d1 CONTAINER's own shape and the files_changed_outside_any_plan ARRAY's own
    shape; it did not guard this SIBLING SCALAR field, and used a bare CAST, which RAISES
    DuckDB's ConversionException on a real type mismatch (COALESCE never runs -- it only
    catches a NULL result, not an exception) -- reproduced live, pre-fix (this round):
    `evaluate_rule` raised duckdb.duckdb.ConversionException: Failed to cast value to
    numerical: {"x":1}. FIXED, this round, in two layers: (1) every extraction now uses
    TRY_CAST, which returns SQL NULL rather than raising for ANY type mismatch, closing the
    crash at its root regardless of guard presence (confirmed live: TRY_CAST(json_extract(...)
    AS INTEGER) on this exact object returns NULL, no exception); (2) commits_with_source ALSO
    carries its own population/evidence guard -- `json_type(...) IS NULL OR json_type(...) IN
    ('UBIGINT', 'BIGINT')` -- which specifically EXCLUDES a present-but-wrong-typed value like
    this one (ABSENT) while still ADMITTING a genuinely MISSING commits_with_source (an
    explicitly out-of-scope case pinned by
    test_a_wellformed_files_changed_array_with_no_commits_with_source_at_all_is_pass, below).
    Population 0 -> ABSENT here, no exception."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":{\"x\":1},\"files_changed_outside_any_plan\":"
        "[\"a.py\"]}}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d1", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_wellformed_row_survives_a_malformed_sibling_row_in_the_same_store(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), a MIXED-store crash-safety check:
    p1 is well-formed and WARN-worthy; p2 has the identical malformed (nested-object)
    commits_with_source as the single-row test above. Population (guards intact) correctly
    reads 1 (only p1 counts). Because every field extraction in this rule now uses TRY_CAST (not
    bare CAST), p2's own malformed value never raises regardless of guard presence or absence --
    confirmed live, this scenario no longer isolates the evidence-side guard's own necessity by
    itself (see test_a_coercible_commits_with_source_string_does_not_leak_into_evidence, below,
    for that proof -- an OBJECT never coerces via TRY_CAST, but a numeric STRING does, and THAT
    is the real risk the evidence-side guard closes). This test still pins the CRASH-SAFETY
    property directly: p1's own WARN finding must survive untouched alongside a malformed
    sibling row, with no exception raised."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":3,\"files_changed_outside_any_plan\":"
        "[\"scratch.py\",\"notes.md\"]}}}'), "
        "('p2', 'alignment-collect/v1', '2026-01-02', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":{\"x\":1},\"files_changed_outside_any_plan\":"
        "[\"a.py\"]}}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    population = conn.execute(rule["population"]).fetchone()
    assert population == (1,)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 3,
        "files_changed_outside_any_plan": ["scratch.py", "notes.md"],
        "files_outside_plan_confidence": None,
    }]


def test_a_coercible_commits_with_source_string_does_not_leak_into_evidence(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), THE PROOF the evidence-side
    commits_with_source guard is genuinely load-bearing, distinct from a crash-safety concern:
    TRY_CAST is PERMISSIVE, not merely crash-safe -- TRY_CAST(json_extract(...) AS INTEGER) on
    the JSON STRING "5" successfully coerces to the integer 5 (confirmed live this round). p2's
    own commits_with_source is the string "5" -- json_type is 'VARCHAR', which fails BOTH
    branches of the OR-guard (not NULL, not UBIGINT/BIGINT), so population correctly EXCLUDES
    p2 (population reads 1, only p1). Reproduced live this round WITH the evidence-side guard
    REMOVED (population's own copy left intact): evidence returned p2 too, its wrong-shaped
    commits_with_source silently coerced to 5 and passing the `> 0` gate -- a wrong-shaped value
    masquerading as a real, confirmed measurement, worse than a crash. FIXED by keeping the same
    OR-guard in the evidence WHERE too, which TRY_CAST's own permissiveness cannot substitute
    for."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":3,\"files_changed_outside_any_plan\":"
        "[\"scratch.py\",\"notes.md\"]}}}'), "
        "('p2', 'alignment-collect/v1', '2026-01-02', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":\"5\",\"files_changed_outside_any_plan\":"
        "[\"a.py\"]}}}')"
    )
    rule = registry["consistency_files_outside_plan"]
    population = conn.execute(rule["population"]).fetchone()
    assert population == (1,)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 3,
        "files_changed_outside_any_plan": ["scratch.py", "notes.md"],
        "files_outside_plan_confidence": None,
    }]
