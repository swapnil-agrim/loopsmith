# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Run-over-run comparison for a gap findings report (issue #122, [E3.S7]) -- classifies each
rule's finding regressed / improved / still-failing between two insight/gaps/report.py reports,
MIRRORING skills/sdlc-loop/scripts/pipeline.py's own compare_cards (read in full this session,
lines 114-136) as closely as this domain's own shape allows.

WHY THIS IS A HAND-DUPLICATED PORT, NOT AN IMPORT: insight/ must never `import skills` as a
Python package (insight/gaps/severity.py's own module docstring states the identical rule and
cites the same two sources -- tests/test_import_boundary.py, spec section 1.1 rule 1). This
module carries a hand-typed copy of compare_cards' own elif-chain semantics, backed by
insight/tests/test_gaps_compare_mirrors_pipeline.py's drift test, which reads pipeline.py as TEXT
and asserts (whitespace-normalised) that its three critical lines still read the way this port
assumes -- exactly insight/gaps/severity.py's own SEVERITY_ORDER technique, one file over.

IDENTITY IS `rule_id` (loader.py's own path.stem, enforced unique by the registry dict), A SINGLE
STRING, NOT pipeline.py's THREE-TUPLE (stage, direction, name) -- gap findings have no
stage/direction axes; this is a structural translation forced by the domain, not a semantic
improvement over the mirrored algorithm (.sdlc/plans/122.md Design decision 6). Evidence is NEVER
part of the identity key or the diff -- it differs every run by construction (a fresh
collected_ts at minimum) -- only `severity` (SEVERITY_ORDER-ranked) is compared, matching
pipeline.py's own signal `status` field exactly.

THE TWO PIPELINE.PY BLIND SPOTS, RULED ON EXPLICITLY (.sdlc/plans/122.md Design decisions 7/8 --
not silently improved, per the task brief's own instruction): (1) a rule_id in `current` but not
`prior` is silently `continue`d, same as pipeline.py's own "newly-instrumented lane" comment,
reused verbatim -- a rule shipped since the prior run has no epoch verdict to diff against. (2) a
rule_id in `prior` but not `current` -- e.g. a `.sql` file DELETED from the catalog between two
runs -- is NOT handled at all -- the loop only iterates `current`'s own keys, so it produces no
delta and no warning, reproducing pipeline.py's own disclosed gap rather than fixing it, because
"mirroring" is the done_when's own explicit word. THE SAME "DELIBERATE .SQL DELETION" REASONING
ALSO COVERS THE ONE CELL OF THIS 9-TRANSITION STATE SPACE THIS MODULE NEVER NAMES EXPLICITLY
ELSEWHERE: a rule_id that was ERRORING in `prior` (present in `prior["errors"]`, not
`prior["findings"]`) whose `.sql` file is then deleted before `current` runs leaves NO trace in
EITHER `still_erroring` (which requires the rule_id to still be present in `current["errors"]`)
or anywhere else in `delta` -- structurally identical to blind spot 2's own "vanished from the
catalog" shape, just starting from an errored epoch instead of a real severity one. Same ruling:
reproduced faithfully, not fixed, for the identical reason a deleted `.sql` file is a rare,
deliberate, source-controlled event, not a probable one.

A THIRD CASE, NOT INHERITED FROM pipeline.py, FIXED HERE (post-plan-review BLOCKING finding,
round 1, .sdlc/plans/122.md Design decision 8): a rule_id present in `prior`'s own `findings`
that ERRORS in `current` (insight/gaps/report.py's own per-rule try/except, Design decision 3)
also fails to reach `current`'s `findings` -- structurally identical to blind spot 2's own
"absent from current" shape, but semantically a different, much MORE likely event: a `.sql` file
being deleted from source control between two runs is rare and deliberate, while a malformed
`raw_payload`/`config_json` row crashing a rule is a disclosed, catalog-wide, unfixed, probable
condition (see insight/gaps/report.py's own module docstring, and .sdlc/plans/122.md's Research
grounding). `errored_in_current` closes it: every rule_id in `current["errors"]` that ALSO had a
real prior epoch verdict (appeared in `prior["findings"]`) is surfaced, with its own `before`
severity, as a delta bucket.

A FOURTH CASE, ROUND 2's OWN BLOCKING FINDING AGAINST ROUND 1's OWN FIX: `errored_in_current`'s
own condition sources `prior_ix` from `prior["findings"]` ONLY -- a rule_id that ALSO errored in
`prior` (not just `current`) has no entry there, so it is invisible to `errored_in_current` too,
reproducing the exact same "vanishes from the machine-readable delta" defect ONE RUN LATER, for a
condition (a SECOND consecutive crash) that is MORE probable than the first, not less, on an
append-only store where nothing self-heals a bad row. Live-reproduced this session across a real
THREE-run streak (.sdlc/plans/122.md Design decision 8's own "ROUND-2 BLOCKING FINDING" prose has
the full repro). `still_erroring` closes it: `rule_id` present in BOTH `current["errors"]` AND
`prior["errors"]`, entry shape `{"rule_id"}` only (neither run has a real severity to report).
`errored_in_current` and `still_erroring` are mutually exclusive by construction -- `build_report`
places each rule_id in exactly one of `findings`/`errors` per report, never both, so a crashing
`rule_id` in `current` matches exactly one of the two depending on which side of that disjoint
pair `prior` had it on. Re-verified live: `still_erroring` correctly re-fires at EVERY step of an
arbitrarily long streak (not merely the second crash), because each pairwise call only ever asks
"was this rule_id in both THESE TWO reports' own error lists," the same two-reports-in shape every
other bucket already has -- no new state, no schema change, no report-chain redesign.

A FIFTH CASE, THE SAME ROUND'S OWN NON-BLOCKING NIT, HANDLED RATHER THAN MERELY DISCLOSED: a
rule_id that errored in `prior` and has a REAL severity in `current` (recovered) was silently
dropped by the SAME `before is None: continue` branch that (correctly) skips a brand-new rule --
except this rule was not brand-new, and there IS something to report: it visibly, verifiably
works again. `recovered_from_error` closes it: `rule_id in prior["errors"]` and `rule_id` now has
a severity in `current["findings"]`, entry shape `{"rule_id", "now"}` (no `before` -- the crash
means no real prior severity survived to compare against; only that the rule is evaluable again
is known).

NONE of `errored_in_current`/`still_erroring`/`recovered_from_error` ever folds into
`still_failing` or affects `recurrence_count` (see `compare_reports`'s own docstring below for
why) -- each is its own axis, never a confirmed severity claim."""
from insight.gaps.severity import SEVERITY_ORDER


def compare_reports(prior, current):
    """Signal-level diff of two insight/gaps/report.py `build_report` payloads: regressed /
    improved / still_failing / errored_in_current / still_erroring / recovered_from_error, keyed
    on rule_id. still_failing (FAIL in both) is the recurrence signal routed to the backlog AS A
    FLAG, never a write -- see insight/__main__.py's gaps handler and render_report's own
    routing-note wording; nothing in this module or its caller writes a goal file
    (.sdlc/plans/122.md's own "flag, not write" research finding).

    The three error-related buckets NEVER count toward recurrence_count (.sdlc/plans/122.md
    Design decision 8): a rule that crashed in either or both runs has an unknown severity
    trajectory across the crash -- still-FAIL, newly-WARN, or silently fixed are all consistent
    with "the query raised before it could tell us" -- so treating any of them as a confirmed
    recurrence would assert a certainty the crash makes impossible to have. Each is reported as
    its own, separate axis: visibility lost, still lost, or regained -- never a severity claim."""
    def index(report):
        return {f["rule_id"]: f["severity"] for f in report.get("findings", [])}

    def error_ids(report):
        return {e["rule_id"] for e in report.get("errors", [])}

    prior_ix, current_ix = index(prior), index(current)
    prior_errored, current_errored = error_ids(prior), error_ids(current)

    delta = {"regressed": [], "improved": [], "still_failing": [],
             "errored_in_current": [], "still_erroring": [], "recovered_from_error": []}

    for rule_id, now in sorted(current_ix.items()):
        before = prior_ix.get(rule_id)
        if before is None:
            if rule_id in prior_errored:
                # Recovered: prior couldn't evaluate it at all, current has a real severity.
                delta["recovered_from_error"].append({"rule_id": rule_id, "now": now})
            continue                        # else: newly-instrumented rule, no epoch verdict yet
        row = {"rule_id": rule_id, "before": before, "now": now}
        if now == "FAIL" and before == "FAIL":
            delta["still_failing"].append(row)
        elif SEVERITY_ORDER.get(now, 1) > SEVERITY_ORDER.get(before, 1):
            delta["regressed"].append(row)
        elif SEVERITY_ORDER.get(now, 1) < SEVERITY_ORDER.get(before, 1):
            delta["improved"].append(row)

    # build_report puts each rule_id in exactly one of findings/errors per report, never both,
    # so prior_ix and prior_errored are disjoint -- each crashing rule_id below matches exactly
    # one of the next two branches, never neither (unless it has no prior history at all).
    for rule_id in sorted(current_errored):
        if rule_id in prior_ix:
            delta["errored_in_current"].append({"rule_id": rule_id, "before": prior_ix[rule_id]})
        elif rule_id in prior_errored:
            delta["still_erroring"].append({"rule_id": rule_id})
        # else: brand-new rule crashing on its own first run(s) -- no history to lose.

    delta["recurrence_count"] = len(delta["still_failing"])
    return delta
