# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Drift test for insight.gaps.severity.SEVERITY_ORDER (issue #116, E3.S1, Task 2; see
.sdlc/plans/116.md Design decision 5).

Issue #298 ([E15.S4]) converted this off the skills/-path AST-parsing technique described below
(now historical) onto the frozen, versioned insight/contract/vocabulary.json fixture: reading
skills/sdlc-loop/scripts/pipeline.py off disk meant this file (and its sibling
test_metric_severity_rank.py, test_metric_23_gate_catch_rate.py,
test_gaps_compare_mirrors_pipeline.py) skipped entirely in a standalone extraction of insight/
alone -- #297's retro named converting these four as this goal's own follow-up. Reading
insight/contract/vocabulary.json instead needs no skip guard: that fixture ships with insight/
itself, in every checkout shape.

(Historical note, kept for context: the previous mechanism read pipeline.py as TEXT and walked
its AST as data, never `import`ing it -- insight/ must never `import skills` as a Python package,
tests/test_import_boundary.py, spec section 1.1 rule 1 -- and duplicated that parser rather than
importing it from test_metric_severity_rank.py, because no test file in this repo imports
another's functions.)"""
import json
import pathlib

from insight.gaps import header
from insight.gaps.severity import SEVERITY_ORDER

VOCAB = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "contract" / "vocabulary.json")
    .read_text(encoding="utf-8")
)


def test_gaps_severity_order_matches_the_contract():
    """The drift test itself: the actual shipped constant, compared against
    insight/contract/vocabulary.json's own "severity_order" -- the insight-side sibling of
    tests/test_pipeline.py::test_severity_order_matches_the_contract on the engine side."""
    assert SEVERITY_ORDER == VOCAB["severity_order"]


def test_absent_ranks_below_warn():
    """Pins the one specific, easy-to-get-backwards ordering fact directly and independently of
    the full-dict equality check above."""
    assert SEVERITY_ORDER["ABSENT"] < SEVERITY_ORDER["WARN"]


def test_valid_triggered_severities_are_severity_order_minus_the_two_computed_states():
    """A structural check that insight.gaps.header's own vocabulary (Decision 2) and
    insight.gaps.severity's vocabulary (Decision 5) cannot silently diverge from each other now
    that they are two separate modules with two separate literal tuples.

    POST-PR-REVIEW BLOCKING FIX: the excluded set is now BOTH computed states, not PASS alone.
    ABSENT is what evaluate_rule returns when the population query finds nothing to measure; an
    author who could also declare it could emit an ABSENT finding carrying real evidence rows,
    collapsing "never measured" and "measured, and here is the proof" into one token."""
    assert set(header.VALID_TRIGGERED_SEVERITIES) == set(SEVERITY_ORDER) - {"PASS", "ABSENT"}
