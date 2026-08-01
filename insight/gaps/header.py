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
triggers -- must be one of WARN/FAIL/ABSENT (VALID_TRIGGERED_SEVERITIES). PASS is deliberately
excluded: PASS is reserved for the zero-evidence case, computed at evaluation time
(insight/gaps/evaluate.py), never authored in a header. A rule statically declaring PASS as
"the severity when I find something" is a contradiction in terms.

THE LOAD-TIME REJECT INVARIANT (Design decision 3, issue #116's own done_when: "a rule with no
evidence query is rejected"): after the header-line loop below terminates at index `i`,
`lines[i:]` is "everything after the header" -- if that, stripped of blank lines and lines
whose stripped text starts with `--`, is empty, the file has nothing to execute as a query even
though it is non-empty in bytes (e.g. a header followed only by a trailing
`-- TODO: write the query later` comment). Caught here, before any query is ever executed --
deliberately not a bare "is the file non-empty" check."""
import re

REQUIRED_FIELDS = ("name", "class", "metric", "action", "severity", "guardrail", "population")
VALID_GAP_CLASSES = ("Coverage", "Definition", "Threshold", "Consistency", "Debt")
VALID_TRIGGERED_SEVERITIES = ("WARN", "FAIL", "ABSENT")

_FIELD_LINE = re.compile(r"^--\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


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
    missing = set(k for k in REQUIRED_FIELDS if not fields.get(k))

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

    # `/* ... */` is stripped as well as `--`, so a rule whose body is only a block comment
    # ("/* TODO: write the query later */") is rejected HERE, by name, rather than surviving to
    # evaluation time -- where conn.execute() on a comment-only statement returns None and
    # rows_as_dicts dies on `.description` with an AttributeError that names nothing useful.
    # Done_when says a rule with no evidence query is REJECTED; a confusing crash three layers
    # later is not a rejection.
    body_text = re.sub(r"/\*.*?\*/", " ", "\n".join(lines[i:]), flags=re.DOTALL)
    body = [
        line for line in body_text.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    if not body:
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
