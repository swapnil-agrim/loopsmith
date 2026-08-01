# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.gaps.header (issue #116, E3.S1, Task 1). No duckdb import needed -- pure
string/regex parsing, no import guard required."""
from insight.gaps.header import GapHeaderError, parse_header

_BASE_FIELDS = {
    "name": "-- name: Missing done_when\n",
    "class": "-- class: Definition\n",
    "metric": "-- metric: 24\n",
    "action": "-- action: add a done_when to the goal\n",
    "severity": "-- severity: FAIL\n",
    "guardrail": "-- guardrail: pair with #5 per spec Guardrails section\n",
    "population": "-- population: SELECT count(*) FROM fact_goal\n",
}


def _text(overrides=None, body="SELECT project_id, goal_id FROM fact_goal "
                                "WHERE done_when_present = false\n"):
    fields = dict(_BASE_FIELDS)
    if overrides:
        fields.update(overrides)
    return "".join(fields.values()) + body


def test_parses_all_seven_required_fields():
    header = parse_header(_text(), source="1.sql")
    assert header["name"] == "Missing done_when"
    assert header["class"] == "Definition"
    assert header["metric"] == "24"
    assert header["action"] == "add a done_when to the goal"
    assert header["severity"] == "FAIL"
    assert header["guardrail"] == "pair with #5 per spec Guardrails section"
    assert header["population"] == "SELECT count(*) FROM fact_goal"


def test_missing_each_required_field_raises():
    """Parametrized over all seven required fields -- mirrors
    test_missing_name_question_or_personas_each_raise's shape, extended to `population` (the
    plan-review BLOCKING fix's own required-field addition)."""
    for missing in _BASE_FIELDS:
        lines = "".join(v for k, v in _BASE_FIELDS.items() if k != missing) + "SELECT 1\n"
        try:
            parse_header(lines, source=f"missing_{missing}.sql")
            assert False, f"expected GapHeaderError for missing {missing}"
        except GapHeaderError as e:
            assert missing in str(e)


def test_missing_population_raises_naming_population():
    """A header with all six other fields present and population omitted -> GapHeaderError
    naming population explicitly, pinned as its own dedicated test (not left to the
    parametrized case alone) -- the exact test for the BLOCKING fix's own required-field
    addition."""
    overrides = {k: v for k, v in _BASE_FIELDS.items() if k != "population"}
    text = "".join(overrides.values()) + "SELECT 1\n"
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "population" in str(e)


def test_class_must_be_one_of_the_five_named_classes():
    try:
        parse_header(_text({"class": "-- class: Bogus\n"}), source="bad.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "class" in str(e)


def test_severity_must_be_warn_or_fail():
    """POST-PR-REVIEW BLOCKING FIX: ABSENT moved from the accepted list to the rejected one.
    evaluate_rule's third branch returns whatever the header declared, so a declarable ABSENT
    let a rule match real evidence rows and emit an ABSENT finding CARRYING EVIDENCE -- a
    measured, evidenced finding wearing the token reserved for "never measured", which a
    consumer cannot tell apart from a genuinely un-instrumented one (spec:534). PASS and ABSENT
    are both states the ENGINE computes, never levels an author picks."""
    for sev in ("WARN", "FAIL"):
        header = parse_header(_text({"severity": f"-- severity: {sev}\n"}), source="ok.sql")
        assert header["severity"] == sev
    for bad in ("PASS", "ABSENT", "Bogus"):
        try:
            parse_header(_text({"severity": f"-- severity: {bad}\n"}), source="bad.sql")
            assert False, f"expected GapHeaderError for severity {bad!r}"
        except GapHeaderError as e:
            assert "severity" in str(e)


def test_a_header_with_no_query_following_it_raises():
    """Header block only, nothing after it (a trailing -- TODO comment line only) ->
    GapHeaderError mentioning "evidence query". This is the load-time half of the issue's own
    done_when, pinned directly."""
    text = "".join(_BASE_FIELDS.values()) + "-- TODO: write the query later\n"
    try:
        parse_header(text, source="empty.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "evidence query" in str(e)


def test_a_header_with_only_a_blank_line_after_it_also_raises():
    """The body-emptiness check strips blank lines too, not only comment lines."""
    text = "".join(_BASE_FIELDS.values()) + "\n\n"
    try:
        parse_header(text, source="empty.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "evidence query" in str(e)


def test_duplicate_field_raises():
    """Mirrors the metrics precedent exactly."""
    text = "-- name: X\n" + "".join(_BASE_FIELDS.values()) + "SELECT 1\n"
    try:
        parse_header(text, source="dup.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "duplicate" in str(e) and "name" in str(e)


def test_a_body_that_is_only_a_block_comment_also_raises():
    """PRE-PR REVIEW should-fix: the body-emptiness check stripped only `--` lines, so a body
    consisting solely of a `/* ... */` block comment PARSED FINE and the failure was deferred to
    evaluation, where conn.execute() on a comment-only statement returns None and rows_as_dicts
    dies on `.description` with an AttributeError naming nothing useful. Done_when says a rule
    with no evidence query is REJECTED; a confusing crash three layers later is not a rejection.
    Multi-line, because the strip is DOTALL and a single-line version would pass even with a
    line-oriented implementation."""
    text = "".join(_BASE_FIELDS.values()) + "/* TODO: write the\n   query later */\n"
    try:
        parse_header(text, source="blockcomment.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "evidence query" in str(e)


def test_a_real_query_carrying_an_inline_block_comment_still_parses():
    """The other side of the strip above: stripping `/* ... */` must not swallow a REAL query
    that merely annotates itself. Pins that the rejection above is about an EMPTY body, not
    about block comments being present."""
    text = "".join(_BASE_FIELDS.values()) + "SELECT /* the goals */ goal_id FROM fact_goal\n"
    rule = parse_header(text, source="annotated.sql")
    assert rule["class"] == "Definition"


def test_a_nested_block_comment_body_also_raises():
    """POST-PR-REVIEW BLOCKING FIX. The previous strip was a non-greedy regex, so it stopped at
    the FIRST `*/`. DuckDB's grammar NESTS: `/* outer /* inner */ */` is one comment to DuckDB,
    but the regex stripped only through the inner `*/` and left trailing text, so a body DuckDB
    reads as pure commentary looked non-empty and was ACCEPTED as having an evidence query --
    then died at evaluation on `None.description`. Same symptom and same root cause as the
    single-level bug already fixed once; nesting reopened it."""
    text = "".join(_BASE_FIELDS.values()) + "/* outer /* inner */ SELECT 1 FROM fact_goal */\n"
    try:
        parse_header(text, source="nested.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "evidence query" in str(e)


def test_an_unterminated_block_comment_raises_at_load_time():
    """An unterminated `/*` swallows the rest of the file, so the rule has no evidence query --
    a load-time rejection by name, not a duckdb.ParserException three layers later."""
    text = "".join(_BASE_FIELDS.values()) + "/* TODO SELECT project_id FROM fact_goal\n"
    try:
        parse_header(text, source="unterminated.sql")
        assert False, "expected GapHeaderError"
    except GapHeaderError as e:
        assert "unterminated" in str(e)


def test_comment_markers_inside_a_string_literal_are_not_comments():
    """The other direction: the stripper must not eat a REAL query because a literal happens to
    contain `--` or `/*`. A rule wrongly rejected is as broken as one wrongly accepted."""
    body = "SELECT goal_id FROM fact_goal WHERE note = 'a -- b /* c'\n"
    rule = parse_header("".join(_BASE_FIELDS.values()) + body, source="literal.sql")
    assert rule["class"] == "Definition"


def test_a_comment_marker_inside_a_double_quoted_identifier_is_not_a_comment():
    """POST-PR-REVIEW should-fix: the scanner honoured single-quoted literals but not
    double-quoted IDENTIFIERS, so an unmatched `/*` inside one read as a block comment that never
    closed and a VALID query was REJECTED -- the mirror image of the two bugs this scanner was
    written to fix, and just as wrong. Verified against real DuckDB: `SELECT 1 AS "col/*name"`
    executes fine."""
    body = 'SELECT 1 AS "col/*name", goal_id FROM fact_goal\n'
    rule = parse_header("".join(_BASE_FIELDS.values()) + body, source="ident.sql")
    assert rule["class"] == "Definition"
