# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Drift test for insight.gaps.compare.compare_reports (issue #122, [E3.S7]; see
.sdlc/plans/122.md Design decision 6). Same technique as
insight/tests/test_gaps_severity_vocabulary.py: reads pipeline.py as TEXT, never imports it
(insight/ must never `import skills` -- tests/test_import_boundary.py, spec section 1.1 rule 1),
and asserts (whitespace-normalised) that its own compare_cards elif chain still reads the way
this hand-duplicated port assumes -- so a future edit to pipeline.py's own compare_cards breaks
THIS test loudly instead of leaving compare.py silently stale."""
import pathlib
import re

PIPELINE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "skills" / "sdlc-loop" / "scripts" / "pipeline.py"
)
COMPARE_PATH = pathlib.Path(__file__).resolve().parents[1] / "gaps" / "compare.py"

_WHITESPACE = re.compile(r"\s+")


def _normalize(text):
    return _WHITESPACE.sub("", text)


def test_pipeline_compare_cards_still_checks_still_failing_before_ordering():
    text = PIPELINE_PATH.read_text(encoding="utf-8")
    normalized = _normalize(text)
    still_failing_check = _normalize("if now == FAIL and before == FAIL:")
    regressed_check = _normalize("elif _ORDER.get(now, 1) > _ORDER.get(before, 1):")
    improved_check = _normalize("elif _ORDER.get(now, 1) < _ORDER.get(before, 1):")
    assert still_failing_check in normalized
    assert regressed_check in normalized
    assert improved_check in normalized
    assert normalized.index(still_failing_check) < normalized.index(regressed_check), (
        "pipeline.py's own still_failing check must textually precede the ordering elif -- if "
        "this ordering changed, insight.gaps.compare's own port needs to change with it"
    )


def test_compare_reports_port_mirrors_the_same_three_branches():
    normalized = _normalize(COMPARE_PATH.read_text(encoding="utf-8"))
    still_failing_check = _normalize('if now == "FAIL" and before == "FAIL":')
    regressed_check = _normalize("elif SEVERITY_ORDER.get(now, 1) > SEVERITY_ORDER.get(before, 1):")
    improved_check = _normalize("elif SEVERITY_ORDER.get(now, 1) < SEVERITY_ORDER.get(before, 1):")
    assert still_failing_check in normalized
    assert regressed_check in normalized
    assert improved_check in normalized
    assert normalized.index(still_failing_check) < normalized.index(regressed_check)
