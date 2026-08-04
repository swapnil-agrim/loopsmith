# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #263 (D2): the number component. `_derive_state`'s decision table tested directly
(private import, matching this codebase's own convention -- insight.dash.render._measured,
insight.dash.charts._categorical_slots), then `render_number`'s own public behaviour: one test
per rendered state (the issue's own "a test plants each state and asserts the rendered class"),
the two real-metric fixtures (metric_14's NULLIF-null shape, metric_17's coverage-missing shape,
.sdlc/plans/263.md Decision 2), the park-taxonomy negative fixture (S5, proving this component
does not reproduce manager.py:138's bug class), and the mutation-proof (Task 4's own explicit
requirement) that the class-2 refusal guard is load-bearing, not vacuously true."""
import inspect

import pytest

from insight.dash import number as number_module
from insight.dash.number import _derive_state, render_number
from insight.dash.render import CoverageDenominatorMissing, assert_self_contained


# --------------------------------------------------------------------------- _derive_state table

@pytest.mark.parametrize("value,reliability_class,coverage,expected", [
    (0, 1, None, "empty_result"),
    (0, 2, {"class1_count": 1, "class2_count": 0, "total_count": 1, "coverage_pct": 1.0}, "empty_result"),
    (5, 1, None, "measured"),
    (5, 2, {"class1_count": 1, "class2_count": 0, "total_count": 1, "coverage_pct": 1.0}, "measured"),
    (-2, 1, None, "measured"),  # a negative delta-shaped value is still "measured", not special-cased
])
def test_derive_state_matches_the_decision_table(value, reliability_class, coverage, expected):
    assert _derive_state(value, reliability_class, coverage) == expected


@pytest.mark.parametrize("value", [0, 5])
def test_derive_state_refuses_class_2_with_no_coverage(value):
    with pytest.raises(CoverageDenominatorMissing):
        _derive_state(value, 2, None)


def test_derive_state_refuses_class_2_with_no_coverage_even_when_value_is_none():
    """Plan-review blocking defect (metric_17 shape): insight/metrics/17.sql declares
    reliability_class 2 and has cost_cents_per_landed_goal unconditionally NULL on every real
    row until #243 closes, while class1_count/class2_count/total_count/coverage_pct are
    always well-defined (the GROUP BY only emits groups that exist). A caller who forgot to
    call extract_coverage for that row -- coverage=None -- must get the refusal REGARDLESS of
    value's own nullity; the old value-is-None-first ordering let this escape silently."""
    with pytest.raises(CoverageDenominatorMissing):
        _derive_state(None, 2, None)


def test_derive_state_raises_on_none_value_naming_the_real_choice():
    """value is None is no longer a state -- it is a caller error whenever not_measured wasn't
    declared (Decision 2, corrected). _derive_state itself is only ever called from
    render_number's not_measured=False branch, so this is the direct-import proof."""
    with pytest.raises(ValueError):
        _derive_state(None, 1, None)


def test_derive_state_raises_on_none_value_even_with_class_2_coverage_present():
    """A real coverage dict does not rescue a None value -- coverage and value are orthogonal
    (Decision 3); the caller still owes an explicit coerce-to-0 or not_measured=True."""
    coverage = {"class1_count": 1, "class2_count": 0, "total_count": 1, "coverage_pct": 1.0}
    with pytest.raises(ValueError):
        _derive_state(None, 2, coverage)


# --------------------------------------------------------------------------- render_number states

def test_render_number_measured_state_renders_its_own_class():
    out = render_number("WIP (open claims)", 5, 1)
    assert '<div class="dash-number data-state-measured">' in out
    assert '<div class="dash-number-value">5</div>' in out


def test_render_number_empty_result_state_renders_its_own_class_and_population():
    out = render_number("Reviewed", 0, 1, population=39)
    assert '<div class="dash-number data-state-empty-result">' in out
    assert '<div class="dash-number-value">0 of 39</div>' in out


def test_render_number_not_measured_state_renders_its_own_class_and_never_a_numeral():
    out = render_number(
        "Review cycles", None, 1, not_measured=True,
        explain_text="no ingest path exists for this data at all.",
        provenance="no writer \xb7 work.py _record/_save",
    )
    assert 'class="data-state-not-measured"' in out
    assert '<p class="not-measured-label">not measured</p>' in out
    import re
    label = re.search(r'<p class="not-measured-label">(.*?)</p>', out).group(1)
    assert not re.search(r'\d', label)  # never a numeral, spec Sec.2 verbatim


def test_render_number_class_2_with_coverage_appends_the_coverage_span():
    coverage = {"class1_count": 62, "class2_count": 38, "total_count": 100, "coverage_pct": 0.62}
    out = render_number("Prevented rework", 0, 2, coverage=coverage)
    assert 'class="coverage-denom"' in out
    assert "62%" in out and "62 of 100 rows class-1" in out and "38 class-2" in out


def test_render_number_refuses_a_class_2_value_with_no_coverage():
    with pytest.raises(CoverageDenominatorMissing):
        render_number("Prevented rework", 0, 2)


def test_render_number_rejects_explain_text_or_provenance_on_a_real_value():
    with pytest.raises(ValueError):
        render_number("WIP", 5, 1, provenance="should not be here")


# --------------------------------------------------------------------------- #263 PR-review
# finding 2: a malformed coverage dict must raise this component's own documented
# CoverageDenominatorMissing diagnostic, never an opaque KeyError from inside
# coverage_denominator_html().

def test_render_number_refuses_a_coverage_dict_missing_a_key():
    """The finding's own repro: a caller hand-builds `coverage` instead of calling
    extract_coverage() and forgets coverage_pct. Without the fix this reaches
    coverage_denominator_html() and raises a bare KeyError; the component must instead raise its
    own CoverageDenominatorMissing, naming the missing key."""
    coverage = {"class1_count": 5, "class2_count": 3, "total_count": 8}  # coverage_pct missing
    with pytest.raises(CoverageDenominatorMissing) as excinfo:
        render_number("Prevented rework", 0, 2, coverage=coverage)
    assert "coverage_pct" in str(excinfo.value)


def test_render_number_refuses_a_malformed_coverage_dict_even_at_reliability_class_1():
    """The check is not gated on reliability_class == 2 -- render_number appends
    coverage_denominator_html(coverage) unconditionally whenever coverage is not None, so a
    class-1 caller who mistakenly hands in a malformed coverage dict must be refused too."""
    coverage = {"class1_count": 5, "class2_count": 3, "total_count": 8}  # coverage_pct missing
    with pytest.raises(CoverageDenominatorMissing):
        render_number("Some class-1 metric", 5, 1, coverage=coverage)


def test_render_number_renders_a_present_but_none_coverage_pct_without_raising():
    """Presence-of-key, not truthiness (matching extract_coverage()'s own check): coverage_pct
    may legitimately be None (total_count == 0, via the SQL's own NULLIF guard) and must render
    "n/a", not raise."""
    coverage = {"class1_count": 0, "class2_count": 0, "total_count": 0, "coverage_pct": None}
    out = render_number("Prevented rework", 0, 2, coverage=coverage)
    assert 'class="coverage-denom"' in out
    assert "n/a" in out


# --------------------------------------------------------------------------- #263 PR-review
# finding 3: reliability_class must be validated, not silently take the class-1-shaped path.

@pytest.mark.parametrize("bad_class", [0, 3, -1, None, "2", True, False])
def test_render_number_rejects_an_invalid_reliability_class(bad_class):
    with pytest.raises(ValueError):
        render_number("x", 0, bad_class)


def test_render_number_accepts_reliability_class_1_and_2():
    """Negative control for the parametrized rejection above -- the two real, valid classes must
    still render normally."""
    assert "dash-number" in render_number("x", 5, 1)
    coverage = {"class1_count": 1, "class2_count": 0, "total_count": 1, "coverage_pct": 1.0}
    assert "dash-number" in render_number("x", 5, 2, coverage=coverage)


def test_render_number_not_measured_requires_both_explain_text_and_provenance():
    with pytest.raises(ValueError):
        render_number("Review cycles", None, 1, not_measured=True, explain_text="only one of the two")


def test_render_number_rejects_a_none_value_when_not_measured_was_not_declared():
    """The other half of Decision 2's corrected contract: value=None is now a caller error,
    not a silent not_measured inference, whenever not_measured wasn't explicitly set."""
    with pytest.raises(ValueError):
        render_number("Review cycles", None, 1)


def test_render_number_not_measured_rejects_a_real_value():
    with pytest.raises(ValueError):
        render_number("Review cycles", 0, 1, not_measured=True,
                       explain_text="x", provenance="no writer \xb7 y")


def test_render_number_not_measured_rejects_population():
    """Plan-review Finding 3: population supplied alongside not_measured must raise, mirroring
    the existing explain_text/provenance-on-a-real-value guard's own symmetry, rather than
    being silently dropped."""
    with pytest.raises(ValueError):
        render_number("Review cycles", None, 1, not_measured=True, population=39,
                       explain_text="x", provenance="no writer \xb7 y")


def test_render_number_output_passes_assert_self_contained():
    for out in (
        render_number("WIP", 5, 1),
        render_number("Reviewed", 0, 1, population=39),
        render_number("Review cycles", None, 1, not_measured=True,
                      explain_text="x", provenance="no writer \xb7 y"),
    ):
        assert_self_contained(out)  # does not raise


# --------------------------------------------------------------------------- S5: park-taxonomy
# negative fixture -- proves render_number does not reproduce manager.py:138's bug class

def test_a_row_with_a_real_zero_value_renders_empty_result_not_not_measured():
    """Mirrors the exact bug in insight.dash.manager._render_park_taxonomy (manager.py:138,
    `if not rate_row or not rate_row.get("terminal_count")`, D5's job to fix in that file):
    a row EXISTS with terminal_count == 0, a genuine measured zero over an empty terminal
    population, not a broken instrument (that function's own docstring, manager.py:132-136).
    render_number's state comes from `value == 0`, never from `value`'s own truthiness against
    falsy row-shapes, so this fixture -- value=0, population=0, NOT None -- must render
    empty_result."""
    out = render_number("Park rate", 0, 1, population=0)
    assert "data-state-empty-result" in out
    assert "data-state-not-measured" not in out


def test_render_number_coerces_the_real_metric_14_nullif_null_to_a_measured_zero(tmp_path):
    """The exact real shape this plan's own D5 migration example (Out-of-scope, below) must
    handle -- #263 plan-review blocking defect. An EMPTY store (today's real ingest state per
    insight/metrics/14.sql's own "DARK METRIC" guardrail) makes metric_14 emit exactly one
    row -- {parked_terminal_count: 0, terminal_count: 0, park_rate: None} -- via
    NULLIF(count(*), 0), NOT a hand-written 0. Feeding the RAW park_rate straight to
    render_number without coercion and without not_measured must raise (Decision 2's new
    value-is-None-without-not_measured guard); the corrected caller coerces it to 0.0 first (a
    genuine measured zero over an empty terminal population, manager.py's own docstring) and
    gets empty_result."""
    import duckdb as _duckdb
    from insight.ingest.store import ensure_schema
    from insight.metrics.loader import load_metrics
    from insight.metrics.testing import rows_as_dicts

    conn = _duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(conn)
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_14"))[0]
    assert row == {"parked_terminal_count": 0, "terminal_count": 0, "park_rate": None}

    with pytest.raises(ValueError):
        render_number("Park rate", row["park_rate"], 1, population=row["terminal_count"])

    coerced = row["park_rate"] if row["park_rate"] is not None else 0.0
    out = render_number("Park rate", coerced, 1, population=row["terminal_count"],
                         fmt=lambda v: f"{v:.0%}")
    assert "data-state-empty-result" in out
    assert "data-state-not-measured" not in out
    conn.close()


def test_render_number_refuses_the_real_metric_17_shape_even_though_value_is_none():
    """insight/metrics/17.sql (reliability_class 2): cost_cents_per_landed_goal is
    unconditionally NULL on every real row until #243 closes (17.sql's own guardrail), while
    class1_count/class2_count/total_count/coverage_pct are always well-defined (the GROUP BY
    only emits groups that exist). A call site that reads a metric_17 row and forgets to call
    extract_coverage -- coverage=None -- must get CoverageDenominatorMissing, not silently
    render not_measured, REGARDLESS of value's own nullity (#263 plan-review blocking
    defect)."""
    with pytest.raises(CoverageDenominatorMissing):
        render_number("Cost per landed goal", None, 2, coverage=None)


# --------------------------------------------------------------------------- mutation-proof
# (Task 4's own explicit requirement) -- the class-2 refusal guard is load-bearing, not
# vacuously true

_GUARD_LINE = "    if reliability_class == 2 and coverage is None:\n"


def _mutated_derive_state():
    src = inspect.getsource(number_module)
    assert _GUARD_LINE in src, "guard predicate moved or reworded -- update this literal match"
    mutated_src = src.replace(_GUARD_LINE, "    if False:  # MUTATED: guard predicate removed\n", 1)
    ns = {}
    exec(compile(mutated_src, "<mutated number.py>", "exec"), ns)
    return ns["_derive_state"]


def test_negative_control_proves_the_class_2_refusal_guard_has_teeth():
    mutated = _mutated_derive_state()
    # The SAME fixture that raises CoverageDenominatorMissing in the real _derive_state (Step 1's
    # test_derive_state_refuses_class_2_with_no_coverage) must NOT raise here -- it must instead
    # silently classify this as a plain "empty_result" numeral, proving the deleted guard line
    # was the only thing standing between this input and a wrong-looking rendered number.
    assert mutated(0, 2, None) == "empty_result"


def test_negative_control_proves_the_metric_17_fixture_depends_on_the_guard_not_the_value_check():
    """The blocking-defect-specific half of the mutation proof: with the guard deleted, the
    exact metric_17 fixture (value=None, reliability_class=2, coverage=None -- Step 1's
    test_derive_state_refuses_class_2_with_no_coverage_even_when_value_is_none) falls through
    to the SECOND check (`value is None` -> ValueError) instead of the guard's
    CoverageDenominatorMissing -- a different exception type, proving the guard, not the
    value-is-None check below it, is what the real fixture's raise depends on."""
    mutated = _mutated_derive_state()
    with pytest.raises(ValueError) as excinfo:
        mutated(None, 2, None)
    assert not isinstance(excinfo.value, CoverageDenominatorMissing)
