# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Drift test for insight.gaps.compare.compare_reports (issue #122, [E3.S7]; see
.sdlc/plans/122.md Design decision 6).

Issue #298 ([E15.S4], Decision 4) replaced the skills/-path source-text scrape this file used to
carry (see git history for the removed test_pipeline_compare_cards_still_checks_
still_failing_before_ordering, which read pipeline.py as TEXT and skipped whenever skills/ was
absent) with a real BEHAVIORAL test of insight.gaps.compare.compare_reports itself. A source-text
scrape of pipeline.py is not a data-format contract any more than
tests/test_git_reader_velocity_parity.py's cross-repo comparison was (same class of problem,
Decision 4's own reasoning) -- the two implementations now each get their own literal-scenario
pin instead: tests/test_pipeline.py::test_compare_finds_recurrence_and_improvement (engine,
extended this same goal to also cover the regressed branch) and
test_compare_reports_classifies_regressed_improved_and_still_failing below (insight), both
independently proving the SAME three-way algorithm (documented in
insight/contract/README.md), never against each other's source text."""
import pathlib
import re

from insight.gaps.compare import compare_reports

COMPARE_PATH = pathlib.Path(__file__).resolve().parents[1] / "gaps" / "compare.py"

_WHITESPACE = re.compile(r"\s+")


def _normalize(text):
    return _WHITESPACE.sub("", text)


def test_compare_reports_classifies_regressed_improved_and_still_failing():
    prior = {"findings": [{"rule_id": "a", "severity": "FAIL"},
                           {"rule_id": "b", "severity": "PASS"},
                           {"rule_id": "c", "severity": "FAIL"}]}
    current = {"findings": [{"rule_id": "a", "severity": "FAIL"},
                             {"rule_id": "b", "severity": "FAIL"},
                             {"rule_id": "c", "severity": "PASS"}]}
    delta = compare_reports(prior, current)
    assert [r["rule_id"] for r in delta["still_failing"]] == ["a"]
    assert [r["rule_id"] for r in delta["regressed"]] == ["b"]
    assert [r["rule_id"] for r in delta["improved"]] == ["c"]


def test_compare_reports_port_mirrors_the_same_three_branches():
    normalized = _normalize(COMPARE_PATH.read_text(encoding="utf-8"))
    still_failing_check = _normalize('if now == "FAIL" and before == "FAIL":')
    regressed_check = _normalize("elif SEVERITY_ORDER.get(now, 1) > SEVERITY_ORDER.get(before, 1):")
    improved_check = _normalize("elif SEVERITY_ORDER.get(now, 1) < SEVERITY_ORDER.get(before, 1):")
    assert still_failing_check in normalized
    assert regressed_check in normalized
    assert improved_check in normalized
    assert normalized.index(still_failing_check) < normalized.index(regressed_check)
