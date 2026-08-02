# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The gap rule header grammar (issue #116, E3.S1) -- see .sdlc/plans/116.md Design decisions
1-4 for the full rationale. Pure stdlib (re), no duckdb import: this module never touches a
database, only file text.

Same one-field-per-physical-line grammar as insight.metrics.header.parse_header, duplicated and
adapted here rather than imported -- REQUIRED_FIELDS differs (a gap rule's own five-tuple
{class, severity, evidence rows, metric moved, action} plus population, not a metric's
name/question/personas/reliability_class/guardrail), so the two modules stay independently
readable end to end rather than one importing field lists from the other. See
.sdlc/plans/116.md Design decision 2 for which fields exist and why, and Design decision 4 for
why `population` is required.

AUTHOR WARNING, carried over from insight.metrics.header verbatim: every field is exactly ONE
physical line, however long -- there is no continuation syntax (an earlier draft of the metrics
header's indentation-based continuation rule silently absorbed an ordinary trailing comment
into a field with no error; removing continuation entirely was the fix, and this grammar never
reintroduces it). A `population` field long enough to want a second line must instead name a
short `SELECT count(*)` against a named view (see .sdlc/plans/116.md Risks) -- it cannot span
lines.

TWO VALIDATIONS WITH NO METRICS-LAYER ANALOG (Design decision 2): `class` must be one of the
five spec-named gap classes (VALID_GAP_CLASSES); `severity` -- the level a rule reports IF it
triggers -- must be one of WARN/FAIL (VALID_TRIGGERED_SEVERITIES). PASS and ABSENT are BOTH
deliberately excluded: each names a state the ENGINE computes at evaluation time
(insight/gaps/evaluate.py), never a level an author picks. A rule statically declaring PASS as
"the severity when I find something" is a contradiction in terms, and a declarable ABSENT is
worse -- evaluate_rule returns whatever the header declared, so such a rule could emit an ABSENT
finding CARRYING evidence rows, which a consumer cannot tell from a genuinely never-measured one
(spec:534). See the comment on VALID_TRIGGERED_SEVERITIES below.

THE LOAD-TIME REJECT INVARIANT (Design decision 3, issue #116's own done_when: "a rule with no
evidence query is rejected"): after the header-line loop below terminates at index `i`,
`lines[i:]` is "everything after the header" -- if that, stripped of blank lines and lines
whose stripped text starts with `--`, is empty, the file has nothing to execute as a query even
though it is non-empty in bytes (e.g. a header followed only by a trailing
`-- TODO: write the query later` comment). Caught here, before any query is ever executed --
deliberately not a bare "is the file non-empty" check."""
import re

REQUIRED_FIELDS = ("name", "class", "metric", "action", "severity", "guardrail", "population")


def _has_printable(value):
    """True iff `value` contains at least one character that actually renders. Guards the
    required-field gate against values that are truthy and survive `strip()` but show as nothing:
    zero-width space, zero-width joiner, the bidi marks, and friends are format characters (Unicode
    category Cf), so `"\u200b".isspace()` is False and `"\u200b".strip()` returns it unchanged.
    `str.isprintable()` excludes exactly that category, which is the distinction wanted here."""
    if not isinstance(value, str):
        return bool(value)
    return any(ch.isprintable() and not ch.isspace() for ch in value)


VALID_GAP_CLASSES = ("Coverage", "Definition", "Threshold", "Consistency", "Debt")
# PASS and ABSENT are both COMPUTED at evaluation time and neither is author-declarable.
# POST-PR-REVIEW BLOCKING FIX: ABSENT used to be declarable here, and evaluate_rule's third
# branch returns whatever the header declared -- so a rule could declare `severity: ABSENT`,
# match real evidence rows, and emit an ABSENT finding CARRYING EVIDENCE. A consumer then cannot
# tell that measured, evidenced finding apart from a genuinely never-measured one by severity
# alone, which is spec:534's own forbidden collapse ("a gap engine that cannot tell
# checked-and-fine from never-checked is worse than none") one token over from PASS. The
# reasoning that excluded PASS applies to ABSENT word for word: it names a state the ENGINE
# determined, not a level an author chose. A rule whose finding is "these goals have no gate"
# declares WARN or FAIL -- those un-instrumented goals are its evidence, and the gap is real.
VALID_TRIGGERED_SEVERITIES = ("WARN", "FAIL")

_FIELD_LINE = re.compile(r"^--\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


def _strip_sql_comments(text, source):
    """Remove `--` line comments and `/* ... */` block comments, returning only executable text.

    POST-PR-REVIEW BLOCKING FIX: this replaces a single-pass `re.sub(r"/\\*.*?\\*/", ...)`, which
    is non-greedy and therefore NON-NESTING -- it stops at the FIRST `*/`. DuckDB's own grammar
    nests: `/* outer /* inner */ */` is one comment to DuckDB, but the regex stripped only
    through the inner `*/` and left trailing text behind, so a body that DuckDB reads as pure
    commentary looked non-empty here and was ACCEPTED as having an evidence query. It then
    crashed at evaluation with an AttributeError on None -- the exact symptom, and the exact
    root cause (a regex approximating a comment grammar), that the single-level fix had already
    been written to prevent. This scanner tracks nesting depth instead, so the reject boundary
    matches DuckDB's parser rather than an approximation of it.

    An UNTERMINATED `/*` is rejected here too, rather than being left to surface at evaluation as
    a duckdb.ParserException: a rule whose evidence query is swallowed by a comment that never
    closes has no evidence query, which is a load-time rejection by name.

    Single-quoted string literals AND double-quoted identifiers are honoured, so a `--` or `/*`
    inside either is not mistaken for a comment -- `WHERE note = 'a -- b'` and
    `SELECT 1 AS "col/*name"` are both real queries, not empty bodies. The double-quote half is a
    post-PR-review fix: without it an unmatched `/*` inside a quoted identifier read as a block
    comment that never closed, and a VALID query was REJECTED -- the mirror image of the two bugs
    this scanner replaced, and just as wrong.

    `$$`-style dollar-quoting is NOT tracked, and the concrete consequence is named here so a
    future rule author is not surprised by it: a body like `SELECT $$ /* unclosed $$` is valid
    DuckDB SQL and executes fine, but this scanner sees the bare `/*`, opens a comment that never
    closes, and REJECTS the file with "unterminated /* block comment". That is a false rejection,
    the same shape as the double-quote bug fixed above. It is left unhandled deliberately: no
    shipped rule or metric uses dollar-quoting anywhere, and the failure is a loud load-time
    error rather than a silent misclassification, so a tag-matching branch is worth writing only
    when a real rule needs one."""
    out = []
    i, n, depth = 0, len(text), 0
    while i < n:
        if depth:
            if text.startswith("/*", i):
                depth += 1
                i += 2
            elif text.startswith("*/", i):
                depth -= 1
                i += 2
            else:
                i += 1
            continue
        if text.startswith("/*", i):
            depth = 1
            i += 2
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
        elif text[i] in ("'", '"'):
            quote = text[i]
            doubled = quote * 2
            out.append(text[i])
            i += 1
            while i < n:
                out.append(text[i])
                # A doubled quote is an escaped one inside the literal, not the end of it.
                if text[i] == quote and not text.startswith(doubled, i):
                    i += 1
                    break
                i += 2 if text.startswith(doubled, i) else 1
        else:
            out.append(text[i])
            i += 1
    if depth:
        raise GapHeaderError(
            f"{source}: unterminated /* block comment in the body -- a rule whose evidence "
            "query is swallowed by a comment that never closes has no evidence query"
        )
    return "".join(out)


class GapHeaderError(ValueError):
    """Raised when a gap rule .sql file's header is missing a required field, a field fails
    validation, a key is duplicated, or the header parses but no evidence query follows it.
    Always carries the offending source path in its message -- same posture as
    insight.metrics.header.HeaderError (this is a first-party bug, caught loudly, not degraded
    around)."""


def parse_header(text, source="<unknown>"):
    """Same one-field-per-physical-line grammar as insight.metrics.header.parse_header,
    duplicated and adapted, not imported (its REQUIRED_FIELDS differ; see .sdlc/plans/116.md
    Decision 2 for which fields exist and why). Additionally rejects a header that parses but has
    no real evidence query following it (Decision 3).

    Returns a dict with the seven required keys (all plain strings) plus 'extra' (a dict of any
    additional -- key: value fields found, forward-compatible, same convention as
    insight.metrics.header's return dict). Raises GapHeaderError, naming `source`, if any
    required field is absent or empty, if any key (required or not) appears more than once, if
    `class` is not one of VALID_GAP_CLASSES, if `severity` is not one of
    VALID_TRIGGERED_SEVERITIES, or if no evidence query follows the header.
    """
    lines = text.splitlines()
    fields = {}
    duplicates = []
    i = 0
    while i < len(lines):
        m = _FIELD_LINE.match(lines[i])
        if not m:
            break
        key, value = m.group(1).lower(), m.group(2).strip()
        if key in fields:
            duplicates.append(key)
        fields[key] = value
        i += 1
    if duplicates:
        raise GapHeaderError(
            f"{source}: duplicate header field(s): {', '.join(sorted(set(duplicates)))}"
        )
    # `not fields.get(k)` alone treats a value that is only invisible characters as PRESENT --
    # `"​"` (zero-width space) is truthy and `str.strip()` does not remove it, since it is a
    # format character, not whitespace. A rule whose `name:` degraded to one of those (a paste from
    # a rich-text source is the realistic route) would load fine and then render a visually blank
    # gap card. Require at least one character that actually prints. Found by #134's PR review,
    # which reached this gate from the rendering side: the card's own four-part check had the
    # identical blind spot, and this is the root of it -- fix it once, where every rule loads.
    missing = set(k for k in REQUIRED_FIELDS if not _has_printable(fields.get(k)))

    if missing:
        # Same "displaced vs truly absent" distinction as insight.metrics.header.parse_header:
        # a field sitting after the header already terminated (a blank line, an ordinary
        # comment, or the SQL body all end the header) was never collected, but a human looking
        # at the file sees it sitting right there -- a plain "missing" message reads as wrong.
        displaced = {
            m.group(1).lower()
            for line in lines[i:]
            for m in (_FIELD_LINE.match(line),)
            if m
        }
        parts = []
        for k in REQUIRED_FIELDS:
            if k not in missing:
                continue
            if k in displaced:
                parts.append(
                    f"{k} (present later in the file, but after the header already "
                    "terminated -- the header ends at the first line that is not a "
                    "'-- key: value' line; see insight/gaps/header.py)"
                )
            else:
                parts.append(k)
        raise GapHeaderError(
            f"{source}: missing required header field(s): {', '.join(parts)}"
        )

    if fields["class"] not in VALID_GAP_CLASSES:
        raise GapHeaderError(
            f"{source}: class must be one of {VALID_GAP_CLASSES}, got {fields['class']!r}"
        )
    if fields["severity"] not in VALID_TRIGGERED_SEVERITIES:
        raise GapHeaderError(
            f"{source}: severity must be one of {VALID_TRIGGERED_SEVERITIES} (PASS is reserved "
            f"for the zero-evidence case, computed at evaluation time, never authored in a "
            f"header -- see .sdlc/plans/116.md Design decision 2), got {fields['severity']!r}"
        )

    # Comments are stripped so a rule whose body is only commentary ("/* TODO: write the query
    # later */") is rejected HERE, by name, rather than surviving to evaluation time -- where
    # conn.execute() on a comment-only statement returns None and rows_as_dicts dies on
    # `.description` with an AttributeError that names nothing useful. Done_when says a rule with
    # no evidence query is REJECTED; a confusing crash three layers later is not a rejection.
    body_text = _strip_sql_comments("\n".join(lines[i:]), source)
    if not body_text.strip():
        raise GapHeaderError(
            f"{source}: header parses but no evidence query follows it (empty body)"
        )

    extra = {k: v for k, v in fields.items() if k not in REQUIRED_FIELDS}
    return {
        "name": fields["name"],
        "class": fields["class"],
        "metric": fields["metric"],
        "action": fields["action"],
        "severity": fields["severity"],
        "guardrail": fields["guardrail"],
        "population": fields["population"],
        "extra": extra,
    }
