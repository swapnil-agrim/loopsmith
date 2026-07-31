# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.metrics.header (issue #108, E2.S1). No duckdb import needed -- pure
string/regex parsing, no import guard required."""
from insight.metrics.header import HeaderError, parse_header


def test_parses_all_five_required_fields():
    text = (
        "-- name: Throughput\n"
        "-- question: Are we shipping?\n"
        "-- personas: manager, leadership\n"
        "-- reliability_class: 1\n"
        "-- guardrail: pair with #5 per spec Guardrails section\n"
        "SELECT 1\n"
    )
    header = parse_header(text, source="1.sql")
    assert header["name"] == "Throughput"
    assert header["question"] == "Are we shipping?"
    assert header["personas"] == ["manager", "leadership"]
    assert header["reliability_class"] == 1
    assert header["guardrail"] == "pair with #5 per spec Guardrails section"


def test_a_trailing_explanatory_comment_after_the_header_is_not_absorbed():
    """BLOCKING-1 regression, reproduced against the reviewer's own counter-example before the
    fix (indentation-based continuation swallowed this into guardrail with no error, no
    signal); continuation syntax is removed entirely (revised Design decision A) so this now
    just terminates the header cleanly and leaves the comment as ordinary file text."""
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n"
        "-- guardrail: pair with #5\n"
        "--  this line explains the query below, not part of guardrail\n"
        "SELECT 1\n"
    )
    header = parse_header(text, source="t.sql")
    assert header["guardrail"] == "pair with #5"


def test_missing_guardrail_raises_header_error():
    text = "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\nSELECT 1\n"
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        assert "guardrail" in str(e)
        assert "bad.sql" in str(e)


def test_missing_name_question_or_personas_each_raise():
    base = {
        "name": "-- name: X\n", "question": "-- question: Y?\n",
        "personas": "-- personas: manager\n", "reliability_class": "-- reliability_class: 1\n",
        "guardrail": "-- guardrail: z\n",
    }
    for missing in ("name", "question", "personas"):
        lines = "".join(v for k, v in base.items() if k != missing) + "SELECT 1\n"
        try:
            parse_header(lines, source=f"missing_{missing}.sql")
            assert False, f"expected HeaderError for missing {missing}"
        except HeaderError as e:
            assert missing in str(e)


def test_reliability_class_must_be_an_integer():
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n"
        "-- reliability_class: exact\n-- guardrail: z\nSELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        assert "reliability_class" in str(e)


def test_reliability_class_must_be_one_or_two():
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n"
        "-- reliability_class: 3\n-- guardrail: z\nSELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        assert "reliability_class" in str(e)


def test_personas_splits_on_commas_and_strips_whitespace():
    text = (
        "-- name: X\n-- question: Y?\n-- personas:  manager ,  leadership ,cross-functional\n"
        "-- reliability_class: 2\n-- guardrail: z\nSELECT 1\n"
    )
    header = parse_header(text, source="x.sql")
    assert header["personas"] == ["manager", "leadership", "cross-functional"]


def test_keys_are_case_insensitive():
    text = (
        "-- Name: X\n-- QUESTION: Y?\n-- Personas: manager\n"
        "-- Reliability_Class: 1\n-- Guardrail: z\nSELECT 1\n"
    )
    header = parse_header(text, source="x.sql")
    assert header["name"] == "X" and header["reliability_class"] == 1


def test_unknown_keys_are_preserved_not_fatal():
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n"
        "-- guardrail: z\n-- owner: swapnil\nSELECT 1\n"
    )
    header = parse_header(text, source="x.sql")
    assert header["extra"] == {"owner": "swapnil"}


def test_proxy_marker_lands_in_extra_not_a_special_field():
    """Non-blocking finding 1: the proxy convention is `-- proxy: true` in extra, not a
    guardrail-text-prefix convention. See revised Design decision B."""
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n"
        "-- guardrail: z\n-- proxy: true\nSELECT 1\n"
    )
    header = parse_header(text, source="x.sql")
    assert header["extra"] == {"proxy": "true"}


def test_duplicate_header_key_raises():
    """Non-blocking finding 4: a leftover copy-paste line must error, not silently last-win.
    Case-different duplicates are caught too -- the case-fold happens before the check."""
    text = (
        "-- name: X\n-- name: Y\n-- question: Y?\n-- personas: manager\n"
        "-- reliability_class: 1\n-- guardrail: z\nSELECT 1\n"
    )
    try:
        parse_header(text, source="dup.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        assert "duplicate" in str(e) and "name" in str(e)


def test_a_value_containing_a_colon_parses_correctly():
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n"
        "-- guardrail: ratio is: high, needs review\nSELECT 1\n"
    )
    header = parse_header(text, source="x.sql")
    assert header["guardrail"] == "ratio is: high, needs review"


def test_crlf_line_endings_are_handled():
    text = (
        "-- name: X\r\n-- question: Y?\r\n-- personas: manager\r\n"
        "-- reliability_class: 1\r\n-- guardrail: z\r\nSELECT 1\r\n"
    )
    header = parse_header(text, source="x.sql")
    assert header["name"] == "X"


def test_empty_field_value_is_treated_as_missing():
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n"
        "-- guardrail: \nSELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        assert "guardrail" in str(e)


def test_header_terminates_at_first_blank_line():
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n\n"
        "-- guardrail: this looks like a header line but is AFTER the blank terminator\n"
        "SELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError: guardrail is unreachable after the blank line"
    except HeaderError as e:
        assert "guardrail" in str(e)


def test_header_must_start_at_line_one():
    text = (
        "-- some other comment first\n"
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n"
        "-- guardrail: z\nSELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError:
        pass


def test_personas_of_only_separators_raises_header_error():
    """Pre-PR review BLOCK: the presence check tested the RAW string's truthiness, so
    `-- personas: ,,` (non-empty raw text) passed validation and silently produced
    personas == [] after the comma-split -- a required PLURAL field validating as present
    while carrying zero personas. Fixed by validating the POST-SPLIT list, not the raw
    string: a value that is pure commas/whitespace must be treated exactly like an absent
    line, not a legal empty list."""
    text = (
        "-- name: X\n-- question: Y?\n-- personas: ,, \n"
        "-- reliability_class: 1\n-- guardrail: z\nSELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        assert "personas" in str(e)


def test_personas_with_a_semicolon_instead_of_a_comma_is_accepted_as_one_persona_by_design():
    """Pre-PR review flagged this as a second symptom of the same raw-vs-post-split root
    cause and asked us to CONSIDER a clear rejection for a 'likely mis-delimited' value --
    considered and deliberately declined, recorded here rather than silently doing nothing.
    Reason: the grammar's delimiter is a comma (Design decision A); `manager; leadership` is
    a syntactically valid single persona name under that grammar, however unusual, and there
    is no reliable way to distinguish "the author meant two personas and mistyped the
    delimiter" from "the author really does have a persona named that" without guessing
    intent from punctuation -- exactly the class of heuristic Design decision A already
    rejected for continuation lines ("indentation is not a reliable signal of authorial
    intent"). A semicolon inside free text is no more reliable a signal. This test pins the
    current, deliberate behaviour so a future change to it is a visible, reviewed decision."""
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager; leadership\n"
        "-- reliability_class: 1\n-- guardrail: z\nSELECT 1\n"
    )
    header = parse_header(text, source="x.sql")
    assert header["personas"] == ["manager; leadership"]


def test_an_empty_segment_between_two_commas_raises_not_silently_dropped():
    """PR review BLOCK, distinct from the semicolon test directly above it -- read them
    together, not as an inconsistency. `manager; leadership` is accepted because a semicolon
    INSIDE free text is genuinely ambiguous (is it a typo for a delimiter, or a real value
    that happens to contain one?); rejecting it means guessing authorial intent from
    punctuation, exactly the heuristic Design decision A already deleted once. An EMPTY
    segment between two commas (`manager, , engineer`) has no such ambiguity: there is no
    interpretation of "comma-separated list" under which a zero-length entry is a real,
    intended persona. With no authorial intent to protect, the semicolon reasoning does not
    extend here, and the old code (only rejecting a WHOLLY empty post-split list) silently
    dropped the middle entry, leaving a required plural field quietly short by one --
    invisible to eye review, across every one of the 24 files that imitate this format."""
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager, , engineer\n"
        "-- reliability_class: 1\n-- guardrail: z\nSELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        assert "personas" in str(e)


def test_missing_field_message_distinguishes_absent_from_displaced_after_header_end():
    """Fold-in: a field displaced after the header already terminated (e.g. after a blank
    line, or after the SQL body starts) previously reported identically to a field that
    never appears anywhere in the file -- correct per the grammar, but a human looking at
    the file sees the field right there and the plain 'missing' message reads as wrong. The
    parser already knows the difference (it can keep scanning past the terminator purely to
    check), so the message now says so."""
    text = (
        "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\n\n"
        "-- guardrail: this is AFTER the blank terminator\n"
        "SELECT 1\n"
    )
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        msg = str(e)
        assert "guardrail" in msg
        assert "after the header" in msg or "outside the header" in msg, (
            f"message must distinguish displaced-but-present from truly absent, got: {msg!r}"
        )


def test_missing_field_message_does_not_claim_displacement_for_a_field_absent_everywhere():
    """The other half of the same fix: a field that truly never appears anywhere in the file
    must NOT be reported as merely displaced -- that would be misleading in the opposite
    direction."""
    text = "-- name: X\n-- question: Y?\n-- personas: manager\n-- reliability_class: 1\nSELECT 1\n"
    try:
        parse_header(text, source="bad.sql")
        assert False, "expected HeaderError"
    except HeaderError as e:
        msg = str(e)
        assert "guardrail" in msg
        assert "after the header" not in msg and "outside the header" not in msg, (
            f"a field absent everywhere must not be reported as merely displaced, got: {msg!r}"
        )
