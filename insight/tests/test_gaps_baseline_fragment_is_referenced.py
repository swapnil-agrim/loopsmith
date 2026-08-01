# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Task 2/4's own done_when, mechanically enforced: every insight/gaps/threshold_*.sql file's
real, executable body must reference insight.gaps.baseline.TRAILING_P85_EXPR verbatim (its
`population` header field must too), and must compare `breach_run_length` against
K_CONSECUTIVE_BREACHES's own str() value with `>=` -- 'referencing it, never a literal'
(.sdlc/plans/119.md Design decision 7), so neither value can silently drift from its one named
source of truth.

WHY WHITESPACE IS STRIPPED ENTIRELY: see .sdlc/plans/119.md's own citation of this test's
round-1 rationale -- a harmless multi-line reflow of the CTE body must not read as a mismatch,
while a genuine token-level drift still must."""
import pathlib
import re

from insight.gaps.baseline import K_CONSECUTIVE_BREACHES, TRAILING_P85_EXPR
from insight.gaps.loader import load_gap_rules

GAPS_DIR = pathlib.Path(__file__).parent.parent / "gaps"

_WHITESPACE = re.compile(r"\s+")


def _sql_body_only(text):
    """Duplicated from test_reliability_class_static_check.py/test_metrics_date_trunc_guard.py."""
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def _normalize_whitespace(text):
    return _WHITESPACE.sub("", text)


def test_every_threshold_rule_references_the_baseline_fragment_and_run_length_constant():
    registry = load_gap_rules()
    threshold_rules = {k: v for k, v in registry.items() if v["class"] == "Threshold"}
    assert threshold_rules, (
        "no Threshold-class rule is registered -- this test would otherwise pass with zero "
        "real coverage"
    )
    normalized_p85 = _normalize_whitespace(TRAILING_P85_EXPR)
    run_length_comparison = _normalize_whitespace(
        f"breach_run_length>={K_CONSECUTIVE_BREACHES}"
    )
    body_p85_offenders = []
    body_run_length_offenders = []
    population_offenders = []
    for rule_id, rule in threshold_rules.items():
        body = _normalize_whitespace(_sql_body_only(rule["query"]))
        if normalized_p85 not in body:
            body_p85_offenders.append(rule_id)
        if run_length_comparison not in body:
            body_run_length_offenders.append(rule_id)
        population = _normalize_whitespace(rule["population"])
        if normalized_p85 not in population:
            population_offenders.append(rule_id)
    assert body_p85_offenders == [], (
        f"{body_p85_offenders} do not paste TRAILING_P85_EXPR (whitespace-normalised) into "
        "their own trailing-baseline CTE column"
    )
    assert body_run_length_offenders == [], (
        f"{body_run_length_offenders} do not compare breach_run_length >= "
        f"{K_CONSECUTIVE_BREACHES} (whitespace-normalised) -- the run-length literal may have "
        "drifted from insight.gaps.baseline.K_CONSECUTIVE_BREACHES"
    )
    assert population_offenders == [], (
        f"{population_offenders}'s own `population` header field does not reference "
        "TRAILING_P85_EXPR -- the second, separately hand-typed copy can drift too"
    )
