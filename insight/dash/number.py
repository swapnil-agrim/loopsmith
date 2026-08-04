# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The number component (issue #263/D2; spec Sec.2). One function, `render_number`, renders ONE
numeric tile in exactly one of three states -- measured / empty_result / not_measured -- driven
by the metric's own reliability class and (when class-2) coverage denominator, extending #129's
build failure ("a class-2 metric with no coverage figure is a bug, not a number") from the page
into the component itself, per issue #263's own `done_when`.

Reuses, never re-derives: `not_measured_block` from `insight.dash.colors` (D1/#262's own
not-measured primitive, wholesale, unchanged) and `CoverageDenominatorMissing` +
`coverage_denominator_html` from `insight.dash.render` (#129's own coverage-column-presence check
and formatter, unchanged -- S3: this module never re-implements `extract_coverage`'s own
column-presence logic, it only takes that function's OUTPUT as an ingredient and enforces a
narrower, defensive invariant of its own; see `_derive_state`'s docstring below).

This module carries no CSS of its own (`_STYLE`) -- `.dash-number`/`.dash-number-label`/
`.dash-number-value`'s rules live in `insight.dash.colors.viz_css_vars()` (a global primitive,
same as `.data-state-not-measured` above them), not a page-specific block, since this component
is not itself a page (.sdlc/plans/263.md Decision 4)."""
import html

from insight.dash.colors import not_measured_block
from insight.dash.render import CoverageDenominatorMissing, coverage_denominator_html

_STATE_CSS_SUFFIX = {"measured": "measured", "empty_result": "empty-result",
                      "not_measured": "not-measured"}


def _derive_state(value, reliability_class, coverage):
    """Pure, side-effect-free except for the two raises -- called ONLY from render_number's
    not_measured=False branch (Decision 2's corrected contract: not_measured is an explicit
    caller flag, handled by render_number itself before this function is ever reached, never
    inferred here from value's nullity). The `reliability_class == 2 and coverage is None`
    refusal runs FIRST, UNCONDITIONALLY, before `value` is even inspected -- this is the #263
    plan-review blocking-defect fix: a class-2 call with a missing coverage denominator must
    raise regardless of whether `value` also happens to be None (insight/metrics/17.sql's real
    shape -- reliability_class 2, `cost_cents_per_landed_goal` NULL on every real row until #243
    closes, while class1_count/class2_count/total_count/coverage_pct are always well-defined --
    so `coverage=None` here means the caller forgot to call extract_coverage, not that the row was
    absent). Only once that refusal has had its chance to fire does `value` get inspected: `None`
    is now itself a caller error, not a state -- naming the real choice, coerce a legitimate
    empty-population NULL to 0, or have the caller pass not_measured=True instead. Tested directly
    (imported privately, matching this codebase's own convention of testing e.g.
    insight.dash.render._measured or insight.dash.charts._categorical_slots by direct import) as
    well as through render_number()'s own public behaviour."""
    if reliability_class == 2 and coverage is None:
        raise CoverageDenominatorMissing(
            f"render_number(): value {value!r} declares reliability_class 2 but no coverage "
            "denominator was supplied to render_number() -- a class-2 number with no coverage "
            "figure is a bug, not a number (spec Sec.2; .sdlc/plans/129.md Decision D8). Call "
            "insight.dash.render.extract_coverage() on the metric's own row and thread its result "
            "into render_number()'s coverage= argument. This check runs unconditionally, even "
            "when value is None (insight/metrics/17.sql's own shape) -- a missing coverage "
            "denominator is never excused by a missing value."
        )
    if value is None:
        raise ValueError(
            "render_number(): value is None but not_measured=True was not passed -- a "
            "legitimate empty-population aggregate (e.g. a NULLIF(count(*), 0)-derived NULL, "
            "insight/metrics/14.sql's own park_rate shape) must be coerced to 0 by the caller if "
            "it represents a measured zero; otherwise pass not_measured=True with "
            "explain_text/provenance to declare genuine absence explicitly. See "
            "insight.dash.manager._render_park_taxonomy's corrected call (issue #263 Out-of-"
            "scope) for the concrete coerce-vs-declare choice this forces."
        )
    return "empty_result" if value == 0 else "measured"


def render_number(label, value, reliability_class, coverage=None, population=None,
                   fmt=str, not_measured=False, explain_text=None, provenance=None):
    """Render ONE numeric tile in exactly one of three states -- measured / empty_result /
    not_measured. measured/empty_result are derived internally from `value` (never chosen by the
    caller); not_measured is an EXPLICIT caller-supplied flag, never inferred from `value`'s
    nullity (issue #263/D2 Decision 2, corrected after plan review -- see .sdlc/plans/263.md for
    the two real-metric proofs this fixes: insight/metrics/17.sql's dead-refusal case and
    insight/metrics/14.sql's NULLIF-null case). Whenever not_measured is NOT set, `value is None`
    is itself an error, not a silent state: the caller must either coerce a legitimate
    NULLIF/empty-population NULL to 0 (it IS a measured zero -- see
    insight.dash.manager._wip_row's own docstring, manager.py:89-99: "a real week existing with
    wip_count = 0 ... is a measured zero, not an absence") or pass not_measured=True with
    explain_text/provenance to declare genuine absence explicitly. Silence is what produced the
    original bug; see Decision 4 below and the corrected D5 migration example in Out-of-scope.

    label: str, the tile's label text (mirrors charts.render_stat_tile's own `label`).

    value: the metric's own raw numeric value, or None. Meaningful ONLY when not_measured is
    False (the default): a real zero MUST be passed as 0 (or 0.0); `None` is a caller error unless
    it is a legitimate NULLIF-derived empty-population NULL the caller has verified represents a
    measured zero, in which case the caller coerces it to 0 before calling. When not_measured is
    True, value MUST be None (anything else raises ValueError -- caller confusion, describing a
    real numeral as absent).

    reliability_class: 1 or 2, the metric's own declared class (insight.metrics.header's
    `reliability_class` field, same value load_metrics()/extract_coverage() already take). Ignored
    entirely when not_measured is True -- a not-measured tile never needs to know its class.

    coverage: the dict returned by insight.dash.render.extract_coverage(metric_id,
    reliability_class, row) for this same row, or None (extract_coverage already returns None for
    every class-1 metric, and for a class-2 metric whose row was None -- both legitimate). NEVER
    the raw row -- render_number does not re-derive extract_coverage's own column-presence check
    (S3); it takes that check's OUTPUT as an ingredient and enforces a narrower, DEFENSIVE
    invariant of its own (Decision 5): `reliability_class == 2 and coverage is None` ALWAYS raises
    CoverageDenominatorMissing when not_measured is False -- UNCONDITIONALLY, checked BEFORE value
    is even inspected for None, so a class-2 call with a missing coverage denominator can never
    escape the refusal by having a null value (the metric_17 shape above). Ignored when
    not_measured is True.

    population: int or None. The total row/item count `value` was measured against (e.g. 39 in
    spec Sec.2's own "0 of 39" example) -- rendered as "{value} of {population}" instead of a bare
    value when given. This is a PLAIN COUNT, unrelated to `coverage`'s four-column reliability
    shape, and a class-1 metric may supply it exactly as freely as a class-2 one (Decision 3) --
    see insight.dash.manager._render_park_taxonomy's own existing "{parked_terminal_count} of
    {terminal_count} terminal goal(s) parked" (manager.py:153) for the live precedent this
    generalizes. Raises ValueError if supplied together with not_measured=True (plan-review Finding
    3: a not-measured tile has no measured population to report -- a caller wanting a "0 of 39"
    hint almost certainly means empty_result, value=0 population=39, not not_measured; silently
    dropping a supplied argument is the "parameter that looks general but is special-cased" trap on
    a P0 API three blind call sites will build against, so this now raises symmetrically with the
    existing explain_text/provenance guard rather than being silently ignored).

    fmt: callable, value -> str, applied to `value` only when it is not None (e.g. `fmt=lambda v:
    f"{v:.0%}"` for a percentage tile). Defaults to `str`. Never applied to `population`, which
    always renders as a plain integer -- population is a row count, never a percentage.

    not_measured: bool, default False. The ONE explicit, caller-supplied signal for the
    not_measured state -- never inferred from value/coverage (this is the corrected contract:
    not-measured-ness is a judgment only the caller can make, per the two real-metric proofs
    above). When True: value MUST be None, population MUST be None, and explain_text/provenance
    are REQUIRED (both) -- any violation raises ValueError. reliability_class/coverage are not
    consulted at all when True. Renders exactly not_measured_block(explain_text, provenance)'s own
    output, unchanged.

    explain_text, provenance: only used, and both REQUIRED together, when not_measured=True --
    passed straight through to insight.dash.colors.not_measured_block(explain_text, provenance)
    unchanged. provenance names the missing writer (Task 3 of #263, e.g. "no writer \xb7
    fact_event.reason_class"). Supplying either one while not_measured is False raises ValueError
    (caller confusion -- describing a real numeral as if it were absent).

    Raises CoverageDenominatorMissing (reusing insight.dash.render's own exception class
    unchanged, S3) when not_measured is False, reliability_class == 2, and coverage is None --
    UNCONDITIONALLY, regardless of value's own nullity. Decision 5 has the full reasoning for why
    this check belongs here, not just in extract_coverage, and why it must never be gated behind a
    value-is-None check (that gating is exactly the blocking defect this section fixes).

    Raises ValueError when not_measured is False and value is None -- naming the real choice:
    coerce a legitimate empty-population aggregate to 0, or pass not_measured=True and declare
    genuine absence explicitly. This check runs AFTER the CoverageDenominatorMissing check above,
    so a class-2 call with both value=None and coverage=None still raises
    CoverageDenominatorMissing, not this ValueError -- the more specific, more actionable
    diagnosis wins (see the decision table below for every cell).

    Returns an HTML string: the tile markup (Decision 4) with coverage_denominator_html(coverage)
    concatenated directly after it, exactly like every existing render_stat_tile call site already
    does by hand (manager.py:147-151, leadership.py's own three call sites) -- this component does
    that concatenation FOR the caller instead of requiring it be remembered."""
    if not_measured:
        if value is not None:
            raise ValueError(
                "render_number(): not_measured=True requires value=None -- got a real value "
                f"({value!r}); this looks like caller confusion between describing a real "
                "numeral and declaring genuine absence"
            )
        if population is not None:
            raise ValueError(
                "render_number(): not_measured=True does not accept population -- there is no "
                "measured population to report for a genuinely absent instrument; if you meant "
                '"0 of N", this is an empty_result call (value=0, population=N), not not_measured'
            )
        if not explain_text or not provenance:
            raise ValueError(
                "render_number(): not_measured=True requires both explain_text and provenance -- "
                "spec Sec.2's mandatory provenance line naming the missing writer"
            )
        return not_measured_block(explain_text, provenance)

    if explain_text is not None or provenance is not None:
        raise ValueError(
            "render_number(): explain_text/provenance are only used when not_measured=True; got "
            f"not_measured=False with value={value!r} -- this looks like caller confusion "
            "between describing a real numeral and describing an absence"
        )
    state = _derive_state(value, reliability_class, coverage)  # may raise CoverageDenominatorMissing/ValueError
    value_text = f"{fmt(value)} of {population}" if population is not None else fmt(value)
    css_class = f"data-state-{_STATE_CSS_SUFFIX[state]}"
    tile = (
        f'<div class="dash-number {css_class}">'
        f'<div class="dash-number-label">{html.escape(str(label))}</div>'
        f'<div class="dash-number-value">{html.escape(value_text)}</div>'
        f'</div>'
    )
    return tile + coverage_denominator_html(coverage)
