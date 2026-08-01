# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Static comparison-literal guard for the Threshold class (issue #119, [E3.S4], Task 7). Pure
stdlib, reusing test_reliability_class_static_check.py/test_metrics_date_trunc_guard.py's own
_sql_body_only comment-stripper (duplicated, not imported).

WHAT THIS CHECKS: a numeric literal in COMPARISON-OPERAND position (immediately adjacent,
optional whitespace only, to <= >= <> != = < >), with exactly TWO allowances: (1) the literal 0
(an emptiness/existence check, e.g. COALESCE(x, 0) <= 0 -- verified against the real catalog this
session, unchanged since #117); (2) the literal K_CONSECUTIVE_BREACHES (imported from
insight.gaps.baseline, never re-hardcoded here), and ONLY when it is the right-hand operand of a
`>=` comparison against the specific, reserved identifier `breach_run_length` -- a count of
CONSECUTIVE BOOLEAN BREACH FLAGS, never the raw measured column or anything arithmetically
derived from it.

WHY THIS IS NOT A HOLE FOR MAGNITUDE-SMUGGLING, verified live against six cases this session
(.sdlc/plans/119.md Design decision 8): `pct >= 3` (same number, wrong identifier) is still
caught; `breach_run_length > 5` (wrong operator AND wrong number) is still caught;
`breach_run_length <= 3` (wrong operator) is still caught; `lead_time_seconds > 172800` (a real
smuggled magnitude, unrelated identifier) is still caught. Only an EXACT `breach_run_length >=
<K_CONSECUTIVE_BREACHES>` survives.

WHAT IS STRUCTURALLY OUT OF SCOPE: a number in function-argument position
(quantile_cont(x, 0.85)) or a window-frame bound (1 PRECEDING) is never adjacent to a comparison
operator, so it is never in this regex's scope at all -- see .sdlc/plans/119.md Design decision 9.

LIMITATION, stated explicitly, same posture as every prior round's own static guard: this is a
regex heuristic, not a SQL parser. A literal hidden behind a computed alias/CTE, an existence
check spelled with a literal other than bare 0, OR a future rule that renames an unrelated
magnitude column to `breach_run_length` to exploit this one narrow carve-out, would all evade
this test. Named, not claimed airtight -- Task 2's own hand-verified, live-executed tests are the
real, load-bearing proof that the shipped rule's criterion is genuinely a run length."""
import re
import pathlib

from insight.gaps.baseline import K_CONSECUTIVE_BREACHES

GAPS_DIR = pathlib.Path(__file__).parent.parent / "gaps"

_COMPARISON_OPERATOR = r"(?:<=|>=|<>|!=|=|<|>)"
_NUMBER = r"\d+(?:\.\d+)?"
_LITERAL_AS_OPERAND = re.compile(
    rf"(?:{_COMPARISON_OPERATOR})\s*({_NUMBER})\b|\b({_NUMBER})\s*(?:{_COMPARISON_OPERATOR})"
)
_RUN_LENGTH_COMPARISON = re.compile(
    rf"\bbreach_run_length\s*>=\s*({K_CONSECUTIVE_BREACHES})\b"
)


def _sql_body_only(text):
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def test_no_gap_rule_compares_against_a_hardcoded_numeric_magnitude():
    paths = sorted(GAPS_DIR.glob("*.sql"))
    assert paths, "insight/gaps/*.sql is empty"
    offenders = []
    for path in paths:
        body = _sql_body_only(path.read_text(encoding="utf-8-sig"))
        allowed_spans = [m.span() for m in _RUN_LENGTH_COMPARISON.finditer(body)]
        for match in _LITERAL_AS_OPERAND.finditer(body):
            number = match.group(1) or match.group(2)
            if number == "0":
                continue
            if any(start <= match.start() < end for start, end in allowed_spans):
                continue
            offenders.append((path.name, match.group(0)))
    assert offenders == [], (
        f"{offenders} -- each pair is a numeric literal used as a COMPARISON OPERAND in a gap "
        "rule's body. Allowed: 0 (emptiness/existence) and K_CONSECUTIVE_BREACHES compared with "
        ">= specifically against `breach_run_length` (a run length, not a magnitude -- issue "
        "#119's own done_when). Every other magnitude must be derived from the data itself, "
        "never hardcoded."
    )
