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


def test_a_wellformed_d2_with_no_d1_at_all_is_pass_not_absent(conn, registry):
    """OUT-OF-SCOPE BEHAVIOR, PINNED (round-2 post-PR-review: named explicitly as "not in
    scope, do not fix, may disclose" -- pre-existing since the first commit, unreachable via
    the real writer, which always emits d1 and d2 paired). d2 is genuinely well-formed here,
    but d1 is missing ENTIRELY (no d1 key at all, not merely malformed). commits_with_source's
    own guard is deliberately `json_type(...) IS NULL OR json_type(...) IN ('UBIGINT',
    'BIGINT')` -- the IS NULL branch specifically ADMITS a missing commits_with_source (as
    opposed to a MALFORMED, present-but-wrong-typed one, which the same guard's second branch
    still excludes -- see test_commits_with_source_shaped_as_an_object_is_absent_not_a_crash).
    A missing commits_with_source therefore still contributes to population (via d2's own
    guards alone), and TRY_CAST(NULL) softens via COALESCE(...,0) to 0, so `0 > 0` is FALSE and
    the row never reaches evidence -> PASS. This test PINS that this specific out-of-scope
    behavior is unchanged by this round's crash-safety fix -- a future guard that tightened
    commits_with_source's own check to a bare `IN ('UBIGINT','BIGINT')` (dropping the IS NULL
    branch) would flip this to ABSENT and this test would catch it."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d2\":"
        "{\"tests_touched_with_source_pct\":0,\"test_command_known\":true}}}')"
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


def test_commits_with_source_shaped_as_an_object_is_absent_not_a_crash(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested). Round 1's fix guarded only the
    d1/d2 CONTAINER's own shape (json_type = 'OBJECT'); it did not guard the SIBLING SCALAR
    fields read out of a container that genuinely IS an object. d1 here is a well-formed object,
    but its own commits_with_source is itself a nested JSON object ({"x": 1}), not a number.
    CAST(json_extract(...) AS INTEGER) RAISES DuckDB's ConversionException on a real type
    mismatch (COALESCE never runs -- it only catches a NULL result, not an exception), and
    because this happens inside the WHERE of a single scan over fact_collector_pack, this one
    malformed row previously ABORTED THE ENTIRE evaluate_rule CALL rather than degrading to
    ABSENT -- reproduced live, pre-fix: `evaluate_rule` raised
    duckdb.duckdb.ConversionException: Failed to cast value to numerical: {"x":1}. FIXED by a
    per-field json_type(...) IN ('UBIGINT', 'BIGINT') guard on commits_with_source itself, in
    both population and evidence -- confirmed live (Key facts, this round): a JSON integer's own
    json_type is 'UBIGINT' for a non-negative value or 'BIGINT' for a negative one, never
    'OBJECT'/'ARRAY'/'VARCHAR'/'NULL' -- so a nested-object commits_with_source is excluded
    before the CAST that would otherwise crash ever runs. Population 0 -> ABSENT, no
    exception."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":{\"x\":1}},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_tests_touched_with_source_pct_shaped_as_an_object_is_absent_not_a_crash(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), the same class of crash as
    commits_with_source's own, on the OTHER integer-typed field this rule reads:
    tests_touched_with_source_pct as a nested object ({"x": 1}) rather than a number, inside a
    d2 that is otherwise a genuine, well-formed object. Pre-fix, CAST(json_extract(...) AS
    INTEGER) on this field raised the identical ConversionException, aborting the whole
    evaluate_rule call. FIXED by the same json_type(...) IN ('UBIGINT', 'BIGINT') per-field
    guard, applied to this field specifically. Population 0 -> ABSENT, no exception."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":{\"x\":1},"
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_test_command_known_shaped_as_an_object_is_absent_not_a_crash(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested). test_command_known as a nested
    JSON object ({"x": 1}) rather than a boolean, inside an otherwise well-formed d2.
    CAST(json_extract(...) AS BOOLEAN) also RAISES on a real type mismatch (confirmed live this
    round: it raises for a JSON object, a JSON array, and an arbitrary non-bool-parseable
    string alike -- it only succeeds for a real JSON boolean, an integer, or a
    bool-parseable string like "true"). FIXED by a json_type(...) = 'BOOLEAN' per-field guard,
    in both population and evidence -- confirmed live: json_type of a JSON boolean is the
    literal string 'BOOLEAN' for both true and false, never anything else, so this guard admits
    only real booleans through to the CAST. Population 0 -> ABSENT, no exception."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":{\"x\":1}}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_json_null_tests_touched_with_source_pct_is_absent_not_pass(conn, registry):
    """ROUND-2 HIGH FINDING (mutation-tested, undisclosed collapse one level deeper than round
    1's fix). d2 is a genuine, well-formed object, and test_command_known is a real boolean
    (true) -- but tests_touched_with_source_pct is a JSON `null` literal, not an integer.
    Pre-fix (round 1's guard alone): json_extract of a null-valued field returns the JSON text
    'null', CAST(... AS INTEGER) on that text returns SQL NULL cleanly (no crash, confirmed
    live), and `NULL = 0` is NULL, not TRUE -- so the evidence row silently dropped while
    population stayed > 0, rendering a false PASS: reproduced live, pre-fix, as
    {'severity': 'PASS', 'evidence': []}. This is the same 'unmeasured reads as fine' collapse
    round 1 fixed for the CONTAINER, one field deeper, and it was undisclosed before this round.
    FIXED by the same per-field json_type(...) IN ('UBIGINT', 'BIGINT') guard on
    tests_touched_with_source_pct added for finding 1 above -- confirmed live: json_type of a
    JSON null literal is the string 'NULL' (not SQL NULL, not UBIGINT/BIGINT), so `IN
    ('UBIGINT', 'BIGINT')` is FALSE, correctly excluding this row from population. Population
    0 -> ABSENT, not PASS."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":null,"
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    assert evaluate_rule(conn, rule) == {
        "class": "Consistency", "metric": "alignment_collect_d2", "action": rule["action"],
        "severity": "ABSENT", "evidence": [],
    }


def test_a_wellformed_row_survives_a_malformed_sibling_row_in_the_same_store(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), a MIXED-store crash-safety check:
    p1 is well-formed and WARN-worthy; p2 has a commits_with_source shaped as a nested object,
    the identical malformation as the single-row crash test above. Population (guards intact)
    correctly reads 1 (only p1 counts -- p2's commits_with_source is present-but-wrong-typed,
    excluded by the OR-guard's second branch). Because every field extraction in this rule now
    uses TRY_CAST (not bare CAST), p2's own malformed value never raises regardless of guard
    presence or absence -- TRY_CAST(json_extract(...) AS INTEGER) on a nested object safely
    returns SQL NULL, confirmed live, so this SPECIFIC mixed-store scenario no longer isolates
    the evidence-side guard's own necessity (see the SEPARATE coercion-leak tests below for
    that proof -- an OBJECT never coerces, but a numeric STRING or a BOOLEAN does, and THAT is
    the real risk TRY_CAST introduces and the evidence-side guards close). This test still pins
    the CRASH-SAFETY property directly: p1's own WARN finding must survive untouched alongside a
    malformed sibling row, with no exception raised."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}'), "
        "('p2', 'alignment-collect/v1', '2026-01-02', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":{\"x\":1}},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    population = conn.execute(rule["population"]).fetchone()
    assert population == (1,)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 5, "tests_touched_with_source_pct": 0, "test_command_known": True,
    }]


def test_a_coercible_test_command_known_int_does_not_leak_into_evidence(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), THE PROOF the evidence-side
    test_command_known guard is genuinely load-bearing, distinct from a crash-safety concern:
    TRY_CAST is PERMISSIVE, not merely crash-safe -- TRY_CAST(json_extract(...) AS BOOLEAN) on
    the JSON INTEGER 1 successfully coerces to TRUE (confirmed live this round;
    TRY_CAST(0 AS BOOLEAN) -> FALSE). p2's own test_command_known is the JSON integer 1 --
    json_type is 'UBIGINT', not 'BOOLEAN', so population's own guard correctly EXCLUDES p2
    (population reads 1, only p1). Reproduced live this round WITH the evidence-side guard
    REMOVED (population's own copy left intact): evidence still returned TWO rows --
    p2's own wrong-shaped test_command_known silently coerced to True and LEAKED into the
    output as a confirmed WARN, even though population correctly said p2 was never measurable.
    This is a worse defect than a crash: a wrong-shaped value silently masquerading as a
    real, confirmed measurement. FIXED by keeping json_type(...) = 'BOOLEAN' in the evidence
    WHERE too, which TRY_CAST's own permissiveness cannot substitute for."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}'), "
        "('p2', 'alignment-collect/v1', '2026-01-02', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":1}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    population = conn.execute(rule["population"]).fetchone()
    assert population == (1,)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 5, "tests_touched_with_source_pct": 0, "test_command_known": True,
    }]


def test_a_coercible_tests_touched_with_source_pct_string_does_not_leak_into_evidence(
    conn, registry
):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), the analogous coercion-leak proof
    for tests_touched_with_source_pct: TRY_CAST(json_extract(...) AS INTEGER) on the JSON
    STRING "0" successfully coerces to the integer 0 (confirmed live this round). p2's own
    tests_touched_with_source_pct is the string "0" -- json_type is 'VARCHAR', not
    'UBIGINT'/'BIGINT', so population's own guard correctly EXCLUDES p2 (population reads 1,
    only p1). Reproduced live this round WITH the evidence-side guard REMOVED: evidence
    returned p2 too, its wrong-shaped percentage silently coerced to 0 and matching the `= 0`
    trigger condition. FIXED by keeping json_type(...) IN ('UBIGINT', 'BIGINT') in the evidence
    WHERE too."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}'), "
        "('p2', 'alignment-collect/v1', '2026-01-02', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":\"0\","
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    population = conn.execute(rule["population"]).fetchone()
    assert population == (1,)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 5, "tests_touched_with_source_pct": 0, "test_command_known": True,
    }]


def test_a_coercible_commits_with_source_string_does_not_leak_into_evidence(conn, registry):
    """ROUND-2 POST-PR-REVIEW BLOCKING FIX (mutation-tested), the analogous coercion-leak proof
    for commits_with_source specifically (the field with the SOFTENED, "missing OR well-typed"
    population guard): TRY_CAST(json_extract(...) AS INTEGER) on the JSON STRING "5" coerces to
    the integer 5 (confirmed live this round). p2's own commits_with_source is the string "5"
    -- json_type is 'VARCHAR', which fails BOTH branches of the OR-guard (not NULL, not
    UBIGINT/BIGINT), so population correctly EXCLUDES p2 (population reads 1, only p1).
    Reproduced live this round WITH the evidence-side guard REMOVED: evidence returned p2 too,
    its wrong-shaped commits_with_source silently coerced to 5 and passing the `> 0` gate.
    FIXED by keeping the same OR-guard in the evidence WHERE too."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES "
        "('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":5},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}'), "
        "('p2', 'alignment-collect/v1', '2026-01-02', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":\"5\"},\"d2\":{\"tests_touched_with_source_pct\":0,"
        "\"test_command_known\":true}}}')"
    )
    rule = registry["consistency_verify_no_test_touched"]
    population = conn.execute(rule["population"]).fetchone()
    assert population == (1,)
    finding = evaluate_rule(conn, rule)
    assert finding["severity"] == "WARN"
    assert finding["evidence"] == [{
        "project_id": "p1", "collected_ts": datetime.datetime(2026, 1, 1),
        "commits_with_source": 5, "tests_touched_with_source_pct": 0, "test_command_known": True,
    }]
