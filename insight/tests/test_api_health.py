# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Health verdicts, and the four independent ways one is correctly refused.

A verdict is a stronger claim than a value, so almost every test here asserts SILENCE. The failure
mode this guards is not "a metric shows no colour"; it is a green edge on a number that does not
deserve one, which is the most damaging thing this panel can render.
"""
import pytest

pytest.importorskip("pydantic")

from insight.api import health  # noqa: E402

BASE = {"earlier": 100.0, "earlier_n": 30, "recent_n": 30}


def _verdict(mid=2, value=100.0, num=50, den=50, data_status=None, baseline=BASE, unit=None):
    return health.evaluate(mid, value, num, den, data_status=data_status, baseline=baseline, unit=unit)


# --------------------------------------------------------------------- the four refusals

def test_a_dark_metric_never_gets_a_verdict():
    """The most important line in this file. Metric 12's own guardrail says its interventions are
    undercounted; a green edge there would assert health about a number known to be wrong."""
    assert _verdict(mid=12, value=0.95, num=60, den=69, data_status="dark") is None


def test_a_metric_with_no_declared_direction_gets_no_verdict():
    """Guessing direction does not weaken a verdict, it INVERTS it -- a regression rendered green.
    id 99 is not in DIRECTIONS, so it must stay silent no matter how good the numbers look."""
    assert _verdict(mid=99) is None


def test_thin_coverage_gets_no_verdict():
    """Lead time is measured on 3 of 169 merges. A verdict there is a confident-sounding guess."""
    assert _verdict(mid=3, num=3, den=169) is None


def test_a_small_absolute_sample_gets_no_verdict_even_at_full_coverage():
    """2 of 2 is 100% coverage and still two observations. The share gate alone would pass it,
    which is exactly why there is an absolute floor as well."""
    assert _verdict(num=2, den=2) is None


def test_no_baseline_means_no_verdict():
    assert _verdict(baseline=None) is None


def test_too_few_goals_in_a_window_gets_no_verdict():
    """A median split always yields two halves; it does not make them worth comparing."""
    assert _verdict(baseline={"earlier": 100.0, "earlier_n": 3, "recent_n": 30}) is None
    assert _verdict(baseline={"earlier": 100.0, "earlier_n": 30, "recent_n": 3}) is None


def test_a_zero_earlier_value_gets_no_verdict():
    """There is no multiple of zero. Returning "infinitely worse" would be arithmetic, not a
    finding."""
    assert _verdict(baseline={"earlier": 0.0, "earlier_n": 30, "recent_n": 30}) is None


# --------------------------------------------------------------------- the verdicts themselves

def test_lower_is_better_holding_reads_healthy():
    v = _verdict(mid=2, value=105.0)          # +5%, inside the noise band
    assert v["verdict"] == "healthy" and v["basis"] == "baseline"


def test_lower_is_better_drifting_reads_watch():
    assert _verdict(mid=2, value=120.0)["verdict"] == "watch"


def test_lower_is_better_materially_worse_reads_breach():
    assert _verdict(mid=2, value=200.0)["verdict"] == "breach"


def test_direction_is_respected_not_assumed():
    """The same movement, opposite metrics. Cycle time rising is bad; autonomy rising is good. If
    direction were ignored, one of these two would be inverted -- and it would look plausible."""
    worse_duration = _verdict(mid=2, value=200.0, baseline=BASE)
    better_rate = health.evaluate(
        12, 200.0, 50, 50, data_status=None, baseline=BASE, unit=None,
    )
    assert worse_duration["verdict"] == "breach"
    assert better_rate["verdict"] == "healthy"


def test_a_higher_is_better_metric_falling_to_zero_is_a_breach():
    """Guards the division: `earlier / current` with current == 0 must not raise, and must not
    silently read as healthy."""
    assert health.evaluate(12, 0.0, 50, 50, baseline=BASE)["verdict"] == "breach"


# --------------------------------------------------------------------- registry invariants

def test_the_benchmark_table_is_empty_until_somebody_cites_a_source():
    """Ships empty on purpose. DORA's bands have moved between report editions and this module
    will not carry numbers from anyone's recollection -- a wrong band produces a confident verdict
    on a real metric, which is worse than no verdict at all."""
    assert health.BENCHMARK_BANDS == {}, (
        "a band was added: every entry must carry a `source` naming the report and edition, and "
        "the evaluator in health.evaluate() must be implemented rather than raising"
    )


def test_every_benchmark_entry_would_have_to_carry_a_source():
    """Runs against whatever is in the table, so it starts guarding the moment one is added."""
    for mid, band in health.BENCHMARK_BANDS.items():
        assert band.get("source"), f"metric {mid}'s benchmark band has no cited source"


def test_every_metric_with_a_direction_is_actually_wired():
    """A direction for a metric nobody measures is dead configuration that reads as coverage."""
    from insight.api.metrics import AGGREGATE_EXTRACTORS, VALUE_EXTRACTORS

    wired = set(VALUE_EXTRACTORS) | set(AGGREGATE_EXTRACTORS)
    for mid in health.DIRECTIONS:
        assert mid in wired, f"metric {mid} declares a direction but has no extractor"


def test_the_coverage_gate_thresholds_are_the_documented_ones():
    """These two numbers are the arbitrary choice in this design. Pinned so a change is a
    deliberate edit with a reason, not a quiet loosening that makes more cards go green."""
    assert health.COVERAGE_MIN_SHARE == 0.20
    assert health.COVERAGE_MIN_OBSERVATIONS == 10
