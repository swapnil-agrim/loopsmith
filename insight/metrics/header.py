# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The metric header grammar (issue #108, E2.S1) -- see .sdlc/plans/108.md Design decision A
for the full grammar rationale. Pure stdlib (re), no duckdb import: this module never touches
a database, only file text.

AUTHOR WARNING, FOR EVERY ONE OF THE 24 FILES THAT WILL IMITATE THIS FORMAT: each of the five
fields above is exactly ONE physical line, however long -- there is no continuation syntax (see
Design decision A in .sdlc/plans/108.md for why: an earlier draft's indentation-based
continuation rule silently absorbed an ordinary trailing comment into `guardrail` with no
error, and removing continuation entirely was the fix). Concretely: a second `--` line placed
directly below `guardrail` is NOT part of it, no matter how natural that looks to a human eye --
it silently terminates the header instead, and is left as ordinary file text. If a guardrail
needs to be long, keep it on one physical line, however long that line is; your editor will
wrap it visually on screen, but it is still one line to this parser.
"""
import re

REQUIRED_FIELDS = ("name", "question", "personas", "reliability_class", "guardrail")
VALID_RELIABILITY_CLASSES = (1, 2)

_FIELD_LINE = re.compile(r"^--\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


class HeaderError(ValueError):
    """Raised when a metric .sql file's header is missing a required field, a field fails
    validation, or a key is duplicated. Always carries the offending source path in its
    message (see Decision D -- this is a first-party bug, caught loudly, not degraded around)."""


def parse_header(text, source="<unknown>"):
    """Parse the leading `-- key: value` comment block of a metric .sql file's text.

    One field per physical line -- NO continuation syntax (revised after plan review
    BLOCKING-1: an earlier draft's indentation-based continuation rule silently absorbed an
    ordinary trailing SQL comment into the preceding field, with no error. Removing
    continuation entirely closes that ambiguity: the header terminates at the FIRST line that
    is not a `-- key: value` line, full stop -- blank, comment, or SQL, all alike.

    Returns a dict with the five required keys (personas as a list, reliability_class as an
    int) plus 'extra' (a dict of any additional -- key: value fields found, forward-compatible
    -- see Decision A; this is also where #110's `-- proxy: true` convention lands, per revised
    Decision B). Raises HeaderError, naming `source`, if any required field is absent or empty,
    if reliability_class is not an integer in {1, 2}, or if any key (required or not) appears
    more than once.
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
        raise HeaderError(
            f"{source}: duplicate header field(s): {', '.join(sorted(set(duplicates)))}"
        )
    missing = set(k for k in REQUIRED_FIELDS if not fields.get(k))

    # BLOCKING pre-PR review fix: validate the POST-SPLIT personas list, not the raw
    # string's truthiness. A raw value of pure commas/whitespace (`-- personas: ,,`) is a
    # non-empty string -- it survives the check above -- that then splits down to zero real
    # personas: a required PLURAL field silently validating as "present" while carrying
    # none. Treat that exactly like an absent line. (A single value containing a semicolon
    # instead of a comma, e.g. `manager; leadership`, is deliberately NOT rejected here --
    # see test_personas_with_a_semicolon_instead_of_a_comma_is_accepted_as_one_persona_by_design
    # for why guessing "likely mis-delimited" from punctuation was considered and declined.)
    personas = []
    if "personas" not in missing:
        personas = [p.strip() for p in fields["personas"].split(",") if p.strip()]
        if not personas:
            missing.add("personas")

    if missing:
        # Fold-in: distinguish "this key never appears anywhere in the file" from "this key
        # appears, but AFTER the header already terminated" (a blank line, an ordinary
        # comment, or the SQL body all end the header per Design decision A -- a field
        # below that point was never collected, but a human looking at the file sees it
        # sitting right there, and a plain "missing" message reads as flatly wrong to them).
        # The parser already has the information to tell the two apart -- it costs one more
        # scan of the lines the header itself never collected -- so the message now does.
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
                    "'-- key: value' line; see insight/metrics/header.py)"
                )
            else:
                parts.append(k)
        raise HeaderError(
            f"{source}: missing required header field(s): {', '.join(parts)}"
        )
    try:
        reliability_class = int(fields["reliability_class"])
    except ValueError:
        raise HeaderError(
            f"{source}: reliability_class must be an integer, "
            f"got {fields['reliability_class']!r}"
        )
    if reliability_class not in VALID_RELIABILITY_CLASSES:
        raise HeaderError(
            f"{source}: reliability_class must be one of {VALID_RELIABILITY_CLASSES}, "
            f"got {reliability_class}"
        )
    extra = {k: v for k, v in fields.items() if k not in REQUIRED_FIELDS}
    return {
        "name": fields["name"],
        "question": fields["question"],
        "personas": personas,
        "reliability_class": reliability_class,
        "guardrail": fields["guardrail"],
        "extra": extra,
    }
