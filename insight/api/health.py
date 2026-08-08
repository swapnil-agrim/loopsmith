# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Health verdicts: turning a measured value into healthy / watch / breach, or into nothing.

"Or into nothing" is the load-bearing half. A verdict is a STRONGER claim than a value, so it gets
stricter rules than a value does, not looser ones. Four independent conditions must all hold before
this module will say anything at all:

  1. a declared BASIS -- a cited benchmark band, or the metric's own trailing history
  2. a declared DIRECTION -- whether higher or lower is better, never guessed
  3. enough COVERAGE -- a verdict on four observations out of 118 is a confident-sounding guess
  4. the metric is not DARK -- its own author flagged the underlying data as unverified

Any one missing yields `None`, which the panel renders as no accent at all. That is why the design
does not degenerate into a wall of green: most metrics legitimately fail at least one condition,
and the honest output is silence.

WHY THE BENCHMARK TABLE SHIPS EMPTY. DORA's published bands have moved between report editions,
and this module will not carry numbers transcribed from anybody's recollection -- a wrong band is
worse than no band, because it produces a confident verdict on a real metric. `BENCHMARK_BANDS`
therefore starts empty, every future entry must carry a `source` naming the report and edition it
came from, and a test rejects any entry without one. Until somebody fills it from a citation they
have actually checked, every verdict here comes from the trailing baseline instead.
"""

# ---------------------------------------------------------------- direction

# Whether a rise is good or bad. There is no default and no inference: guessing wrong does not
# weaken a verdict, it INVERTS it, turning a regression into a green edge. A metric absent from
# this map can never receive a verdict, which is the safe direction to fail in.
DIRECTIONS = {
    2: "lower_is_better",    # cycle time
    3: "lower_is_better",    # lead time for change
    5: "lower_is_better",    # change failure rate
    12: "higher_is_better",  # autonomy rate
    13: "lower_is_better",   # interventions per goal
    14: "lower_is_better",   # park rate
    16: "lower_is_better",   # share of goals hitting the review-cycle cap
    20: "lower_is_better",   # rework ratio
    23: "higher_is_better",  # gate catch rate -- catching more, earlier, is the point of a gate
    35: "lower_is_better",   # lease contention
}

# ---------------------------------------------------------------- benchmarks

# id -> {"bands": [...], "source": "<report and edition>"}. EMPTY BY DESIGN -- see the module
# docstring. Populating it requires a citation somebody has checked, not a recollection.
BENCHMARK_BANDS = {}

# ---------------------------------------------------------------- gates

# THE ARBITRARY NUMBERS, named and justified rather than buried inline.
#
# Coverage: lead time is currently measured on 3 of 169 merges. Calling that "elite" would be
# exactly the failure this product exists to prevent, so a verdict requires both a fifth of the
# population AND an absolute floor of ten observations -- a 100% ratio over two samples is not a
# finding. Deliberately conservative: withholding a verdict costs a little, publishing a wrong one
# costs the product's credibility.
COVERAGE_MIN_SHARE = 0.20
COVERAGE_MIN_OBSERVATIONS = 10

# Baseline: how far the recent window must move before it counts as a real change rather than
# noise. A 10% swing on a few dozen goals is not signal; a 50% regression is. These are judgement
# calls, not statistics -- a proper rank test would be better and is the obvious upgrade path if
# anyone starts acting on marginal verdicts.
# ponytail: multiplier bands, not a Mann-Whitney U -- upgrade if marginal verdicts start mattering.
BASELINE_WATCH_MULTIPLE = 1.10
BASELINE_BREACH_MULTIPLE = 1.50

# Each window needs enough goals to be worth comparing. Below this the split is two anecdotes.
BASELINE_MIN_PER_WINDOW = 8


def _coverage_is_sufficient(numerator, denominator):
    """Coverage gate. `numerator` is what was measured, `denominator` the population it came from."""
    if not denominator or denominator <= 0:
        return False
    if numerator < COVERAGE_MIN_OBSERVATIONS:
        return False
    return (numerator / denominator) >= COVERAGE_MIN_SHARE


def _format_ratio_or_number(value, unit):
    if unit == "ratio":
        return "%.1f%%" % (value * 100)
    if unit == "seconds":
        if value < 5400:
            return "%.0fm" % (value / 60)
        return "%.1fh" % (value / 3600)
    return "%g" % value


def evaluate_baseline(mid, current, baseline, unit=None):
    """Compare a metric's current value against its own earlier window.

    `baseline` is `{"earlier": float, "earlier_n": int, "recent_n": int}` or None. Returns a
    verdict dict or None -- None whenever the comparison cannot be made honestly, which includes
    an undeclared direction, too few goals in either window, and a zero earlier value (nothing to
    express a change as a multiple OF).
    """
    direction = DIRECTIONS.get(mid)
    if direction is None or baseline is None:
        return None
    earlier = baseline.get("earlier")
    if earlier is None or earlier <= 0:
        return None
    if baseline.get("earlier_n", 0) < BASELINE_MIN_PER_WINDOW:
        return None
    if baseline.get("recent_n", 0) < BASELINE_MIN_PER_WINDOW:
        return None

    # Express the move as "how many times the earlier value", in the WORSENING direction, so one
    # set of thresholds serves both directions without a second pair of constants to keep in sync.
    if direction == "lower_is_better":
        worsening = current / earlier
    else:
        # A rise is good here, so the worsening multiple is the inverse. Guard the zero: a current
        # value of zero on a higher-is-better metric is maximally bad, not undefined.
        worsening = (earlier / current) if current > 0 else float("inf")

    was = _format_ratio_or_number(earlier, unit)
    if worsening >= BASELINE_BREACH_MULTIPLE:
        verdict = "breach"
        note = "materially worse than its earlier window (%s)" % was
    elif worsening >= BASELINE_WATCH_MULTIPLE:
        verdict = "watch"
        note = "drifting from its earlier window (%s)" % was
    else:
        verdict = "healthy"
        note = "holding against its earlier window (%s)" % was

    return {
        "verdict": verdict,
        "basis": "baseline",
        "explanation": "%s, %d earlier vs %d recent goals" % (note, baseline["earlier_n"], baseline["recent_n"]),
        "source": None,
    }


def evaluate(mid, value, numerator, denominator, data_status=None, baseline=None, unit=None):
    """The whole gate. Returns a verdict dict, or None meaning "no verdict" -- never a neutral one.

    Order matters only for the explanation a caller might log; every condition is independent and
    any single failure yields None.
    """
    # A dark metric never gets a verdict, no matter how good the number looks. Its own author
    # flagged the underlying data as unverified, and a green edge on unverified data is the single
    # most damaging thing this panel could render.
    if data_status == "dark":
        return None
    if mid not in DIRECTIONS:
        return None
    if not _coverage_is_sufficient(numerator, denominator):
        return None

    band = BENCHMARK_BANDS.get(mid)
    if band is not None:
        # Reserved for when the table is filled from a citation. Deliberately not implemented as a
        # guess: reaching here today is impossible, and a future implementer should write it
        # against the real band shape rather than inherit a placeholder.
        raise NotImplementedError(
            "BENCHMARK_BANDS has an entry for metric %d but no evaluator has been written for it. "
            "Implement the band comparison against the cited edition's real thresholds." % mid
        )

    return evaluate_baseline(mid, value, baseline, unit=unit)
