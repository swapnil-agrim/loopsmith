# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Drift test for insight.gaps.severity.SEVERITY_ORDER (issue #116, E3.S1, Task 2; see
.sdlc/plans/116.md Design decision 5).

WHY THIS TEST DOES NOT `import` pipeline.py, AND WHY IT DUPLICATES _pipeline_order() RATHER
THAN IMPORTING IT FROM insight/tests/test_metric_severity_rank.py: insight/ must never `import
skills` as a Python package (tests/test_import_boundary.py, spec section 1.1 rule 1) -- reading
a path is the allowed coupling, importing the package is not, so _pipeline_order() below reads
skills/sdlc-loop/scripts/pipeline.py as TEXT and walks its AST as data (no Import/ImportFrom node
is ever created), exactly like test_metric_severity_rank.py's own helper of the same name. It is
duplicated here, not imported, for two independent reasons (Design decision 5): (1)
tests/test_import_boundary.py's own module docstring states directly that no test file in this
repo imports another test file's functions -- there is no shared test-helper module anywhere in
insight/tests/, and importing from test_metric_severity_rank.py would be the first instance of
that; (2) _pipeline_order() is a small (~30-line), stable, self-contained AST walker with zero
dependency on anything metric-specific -- exactly the kind of "duplicate inline, name it" helper
.sdlc/plans/114.md's own Global Constraints already established for _sql_body_only."""
import ast
import pathlib

from insight.gaps import header
from insight.gaps.severity import SEVERITY_ORDER

PIPELINE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "skills" / "sdlc-loop" / "scripts" / "pipeline.py"
)


def _pipeline_order():
    """Read pipeline.py's PASS/WARN/FAIL/ABSENT + _ORDER off disk as TEXT/AST -- duplicated,
    not imported, from insight/tests/test_metric_severity_rank.py's own helper of the same name
    (see this file's module docstring for why)."""
    source = PIPELINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PIPELINE_PATH))
    names = {}
    order = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Tuple)
            and isinstance(node.value, ast.Tuple)
            and len(target.elts) == len(node.value.elts)
        ):
            for name_node, value_node in zip(target.elts, node.value.elts):
                if isinstance(name_node, ast.Name) and isinstance(value_node, ast.Constant):
                    names[name_node.id] = value_node.value
        elif (
            isinstance(target, ast.Name)
            and target.id == "_ORDER"
            and isinstance(node.value, ast.Dict)
        ):
            order = {}
            for key_node, value_node in zip(node.value.keys, node.value.values):
                assert isinstance(key_node, ast.Name) and isinstance(value_node, ast.Constant), (
                    "pipeline.py's _ORDER dict no longer uses bare Name keys / int Constant "
                    "values -- this parser's assumptions about its shape are stale"
                )
                order[names[key_node.id]] = value_node.value
    assert order is not None, (
        f"pipeline.py's _ORDER assignment was not found by AST-walking {PIPELINE_PATH} -- "
        "either the file moved or the assignment shape changed; this parser needs updating, "
        "not the hardcoded fallback this test deliberately does not have"
    )
    return order


def test_pipeline_order_parsed_off_disk_matches_the_known_vocabulary():
    """Sanity-checks the parser itself against the exact values re-read directly from
    pipeline.py this session -- if this fails, the parser above is broken, not
    insight.gaps.severity."""
    assert _pipeline_order() == {"PASS": 0, "ABSENT": 1, "WARN": 2, "FAIL": 3}


def test_gaps_severity_order_matches_pipelines_own_order():
    """The drift test itself: the actual shipped constant, compared against a live read of
    pipeline.py's real source, not a second hardcoded literal compared to a third."""
    assert SEVERITY_ORDER == _pipeline_order()


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
