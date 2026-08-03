# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Static "no hardcoded multiplier constant" guard for issue #144's own done_when: "no industry
constant appears anywhere in the rule". Pure stdlib, no duckdb import -- same zero-dependency
text-scan shape as test_gaps_no_literal_thresholds.py / test_reliability_class_static_check.py.

SCOPE, NARROWED DELIBERATELY (plan-review amendment, verified by execution against ALL 25
pre-existing `insight/metrics/*.sql` files): the plan this test shipped from originally proposed
a repo-wide guard over every `insight/metrics/*.sql` file, matching this repo's own established
idiom for other metrics-wide static checks. Running that regex live against the real catalog
found it false-positives on TWO real files: `11.sql`'s `t.trial_id * 4 + w.week_no` (a Monte
Carlo hash-mixing constant inside a deterministic-shuffle formula, not a magnitude) and `41.sql`'s
`100.0 * count(...) FILTER (...) / NULLIF(...)` (a percent-of-total cast -- the same idiom family
as the already-allowed `* 1.0 /` ratio cast, just scaled to a 0-100 range instead of 0-1, which
the original carve-out's exact-adjacency-to-`1.0`-only shape did not cover). A repo-wide guard
that starts life broken on files it doesn't own is not a guard worth having, and this repo's own
callers keep naming the same failure mode for a growing allowlist ("an allowlist that grows is
the drift this repo keeps getting bitten by" -- issue #144's own plan-review amendment B): adding
`11.sql`'s `* 4` as an explicit, commented exception would be exactly that drift, one file at a
time. Preferring narrowing over allowlisting, this guard therefore scans ONLY
`insight/metrics/22.sql` -- issue #144's own done_when already reads "no industry constant
appears anywhere in **the rule**" (singular, this metric's own rule), not a repo-wide claim about
every other metric this story does not touch. `test_the_general_regex_would_false_positive_on_11_
and_41_without_narrowing` below proves the false positives are real, not asserted from memory --
the reason this file is scoped the way it is stays falsifiable, not just prose.

CARVE-OUT, EXTENDED (still needed even though 22.sql itself doesn't use the wider form, so a
future author who copies this helper against a different file does not reintroduce 41.sql's false
positive): a numeric literal immediately adjacent to `*` is allowed when its value is exactly
`1` (any trailing `.0`s) or `100` (any trailing `.0`s) -- the ratio-cast (`* 1.0 /`) and
percent-cast (`100.0 * ... /`) idioms both preserve the underlying ratio, they do not introduce an
external magnitude. Every other numeric literal adjacent to `*` is flagged.

LIMITATION, named in the same spirit as every other static guard in this codebase: a regex
heuristic over one file's comment-stripped body, not a SQL parser -- it cannot see a constant
smuggled through an intermediate CTE column literally named after a number, a differently-shaped
expression such as `POWER(x, 2)` (function-argument position, out of this regex's adjacency
scope), or a constant hidden in a Python helper (moot here: `insight/metrics/loader.py` has no
templating mechanism, every `.sql` file is opaque text to it, so `22.sql`'s multiplier is 100%
SQL by construction). Named, not solved -- `test_metric_22_prevented_rework.py`'s own
hand-computed fixture assertions are the real, load-bearing proof that the multiplier is actually
self-derived; this lint is an early-warning check on top."""
import pathlib
import re

METRICS_DIR = pathlib.Path(__file__).parent.parent / "metrics"

_NUMBER = r"\d+(?:\.\d+)?"
_MULTIPLIER_OPERAND = re.compile(rf"\*\s*({_NUMBER})\b|\b({_NUMBER})\s*\*")
_ALLOWED_RATIO_OR_PERCENT_CAST = (1.0, 100.0)


def _sql_body_only(text):
    """Strip SQL `-- ...` line comments so a file's own prose (including this guardrail's own
    mention of "* 4" above) can never satisfy or defeat the check. Duplicated from
    test_reliability_class_static_check.py / test_metrics_date_trunc_guard.py (this repo's
    no-test-imports-test convention)."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def _offending_multipliers(body):
    offenders = []
    for match in _MULTIPLIER_OPERAND.finditer(body):
        number = match.group(1) or match.group(2)
        if float(number) in _ALLOWED_RATIO_OR_PERCENT_CAST:
            continue
        offenders.append(match.group(0))
    return offenders


def test_metric_22_has_no_hardcoded_multiplier_constant():
    path = METRICS_DIR / "22.sql"
    assert path.exists(), "insight/metrics/22.sql is missing"
    body = _sql_body_only(path.read_text(encoding="utf-8-sig"))
    offenders = _offending_multipliers(body)
    assert offenders == [], (
        f"{offenders} -- 22.sql multiplies a real column by a bare numeric literal outside the "
        "`* 1.0 /` / `* 100.0 /` ratio-and-percent-cast idiom. Issue #144's done_when: no "
        "industry constant may appear anywhere in the rule -- the multiplier "
        "(avoided_cost_seconds) must be blocked_plan_review_count times the project's OWN "
        "measured cost_delta_seconds, nothing else."
    )


def test_the_multiplier_guard_actually_fires_on_a_synthetic_offender(tmp_path):
    """Mutation/negative-control proof: the check above matches zero literals in the real
    22.sql, so on its own it could be indistinguishable from a check that does nothing. Runs the
    same logic over synthetic snippets to prove it discriminates: a real hardcoded multiplier is
    flagged, the `* 1.0 /` ratio cast is not, and the `100.0 * ... /` percent cast is not
    (the exact form that broke the original repo-wide draft against 41.sql)."""
    offender = _sql_body_only(
        "SELECT blocked_plan_review_count * 2.7 AS avoided_cost_seconds FROM blocked\n"
    )
    assert _offending_multipliers(offender) == ["* 2.7"]

    ratio_cast = _sql_body_only(
        "SELECT ROUND(count(*) FILTER (WHERE x = 1) * 1.0 / NULLIF(count(*), 0), 4) AS pct\n"
    )
    assert _offending_multipliers(ratio_cast) == []

    percent_cast = _sql_body_only(
        "SELECT ROUND(100.0 * count(*) FILTER (WHERE pct >= 80) "
        "/ NULLIF(count(*), 0), 2) AS gate_coverage_pct\n"
    )
    assert _offending_multipliers(percent_cast) == []


def test_the_general_regex_would_false_positive_on_11_and_41_without_narrowing():
    """Documents WHY this guard is scoped to 22.sql only, verified by execution rather than
    asserted from memory: running the same multiplier-detection logic against the two real files
    the plan-review amendment named produces a real, non-empty offender list for each -- proving
    a repo-wide version of this guard would have broken on files this story does not own, and
    that narrowing (not an allowlist) was the correct fix.

    41.sql's own `100.0 * ...` is checked against the UN-extended carve-out (allowing only `1.0`,
    the shape the original plan proposed) -- it is flagged there, which is exactly why the
    extended `_ALLOWED_RATIO_OR_PERCENT_CAST` (allowing `100.0` too) exists above: without it,
    this guard's own logic would replay 41.sql's false positive the moment it was pointed at a
    file using the percent-cast idiom."""
    trial_body = _sql_body_only((METRICS_DIR / "11.sql").read_text(encoding="utf-8-sig"))
    assert _offending_multipliers(trial_body) == ["* 4"]

    portfolio_body = _sql_body_only((METRICS_DIR / "41.sql").read_text(encoding="utf-8-sig"))

    def _offending_with_only_the_ratio_cast_allowed(body):
        offenders = []
        for match in _MULTIPLIER_OPERAND.finditer(body):
            number = match.group(1) or match.group(2)
            if float(number) == 1.0:
                continue
            offenders.append(match.group(0))
        return offenders

    un_extended_offenders = _offending_with_only_the_ratio_cast_allowed(portfolio_body)
    assert "100.0 *" in un_extended_offenders
    # ...and the extended carve-out this file actually ships with clears it:
    assert _offending_multipliers(portfolio_body) == []
