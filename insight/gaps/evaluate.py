# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Gap rule evaluation (issue #116, E3.S1, Task 3; see .sdlc/plans/116.md Design decision 4 for
the full "checked-and-clean vs never-checked" reasoning behind the three-way precedence below,
and Design decision 3 for how this module's runtime guard and insight.gaps.header's load-time
guard are two genuinely different mechanisms enforcing two different halves of issue #116's own
done_when.

No `import duckdb` -- `conn` is passed in already open, matching insight.metrics.testing's and
insight.metrics.loader's own convention. `rows_as_dicts` is imported directly from
insight.metrics.testing, not duplicated -- insight.gaps and insight.metrics are both product
code on the same side of the plugin/product boundary tests/test_import_boundary.py enforces, so
this is not a boundary crossing (see .sdlc/plans/116.md Global Constraints)."""
from insight.metrics.testing import rows_as_dicts


class GapEvaluationError(Exception):
    """Raised by make_finding when asked to construct a WARN or FAIL finding with zero evidence
    rows -- the runtime half of Design decision 3's two reject invariants, distinct from
    insight.gaps.header's load-time structural check. PASS and ABSENT are both legitimately
    evidence-free states (Decision 4's plan-review blocking fix widened the original guard,
    which excluded only PASS, to exclude ABSENT too) and are excluded from this guard."""


def evaluate_rule(conn, rule):
    """Run rule['population'] first to size the applicable population, then decide in this
    precedence: population 0 or NULL -> ABSENT, evidence=[] (no instrument; checked before the
    evidence query ever runs, and wins regardless of what that query would have returned -- see
    .sdlc/plans/116.md Design decision 4). population > 0 and the evidence query returns zero
    rows -> PASS, evidence=[]. Evidence query returns rows -> rule's own declared severity,
    those rows as evidence."""
    # fetchone() returns None -- not a row containing None -- when the population query yields
    # NO rows at all. The idiomatic `SELECT count(*) ...` always returns exactly one row even
    # against an empty table, so this cannot fire through the documented form; a non-aggregate
    # population query authored in #117-121 can. A rule whose population query names nothing is
    # the definition of "no instrument", so it degrades into the SAME ABSENT branch as a zero or
    # NULL count rather than crashing on a subscript.
    population_row = conn.execute(rule["population"]).fetchone()
    population = population_row[0] if population_row else 0
    if not population:
        return make_finding(gap_class=rule["class"], metric=rule["metric"],
                             action=rule["action"], severity="ABSENT", evidence=[])
    rows = rows_as_dicts(conn.execute(rule["query"]))
    if not rows:
        return make_finding(gap_class=rule["class"], metric=rule["metric"],
                             action=rule["action"], severity="PASS", evidence=[])
    return make_finding(gap_class=rule["class"], metric=rule["metric"],
                         action=rule["action"], severity=rule["severity"], evidence=rows)


def make_finding(*, gap_class, metric, action, severity, evidence):
    """The one enforcement point for Decision 3's second invariant -- revised for the ABSENT
    branch above: ABSENT is legitimately evidence-free (that is its entire meaning), so the
    guard excludes it explicitly, alongside PASS, rather than treating every non-PASS severity
    alike.

    The guard runs in BOTH directions (post-PR-review blocking fix). Excluding PASS and ABSENT
    from the empty-evidence check left the other half open: nothing stopped an ABSENT finding
    from carrying evidence rows, which is the same collapse read backwards -- a measured,
    evidenced finding wearing the token reserved for "never measured". ABSENT is no longer
    author-declarable (insight/gaps/header.py's VALID_TRIGGERED_SEVERITIES), so evaluate_rule
    cannot reach that state today; this guard is what keeps it unreachable for the five gap
    classes that will call make_finding directly."""
    if severity not in ("PASS", "ABSENT") and not evidence:
        raise GapEvaluationError(
            f"a finding at severity {severity!r} was constructed with zero evidence rows -- "
            "a WARN or FAIL finding must always be backed by at least one evidence row"
        )
    if severity in ("PASS", "ABSENT") and evidence:
        raise GapEvaluationError(
            f"a finding at severity {severity!r} was constructed WITH {len(evidence)} evidence "
            "row(s) -- PASS means the population was checked and clean, ABSENT means there was "
            "nothing to check at all; neither can carry evidence, and a consumer that saw one "
            "could not tell it from a genuinely never-measured finding (spec:534)"
        )
    return {"class": gap_class, "metric": metric, "action": action,
            "severity": severity, "evidence": evidence}
