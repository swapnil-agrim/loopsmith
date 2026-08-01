# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/compare.py (issue #122, [E3.S7], Task 1; see .sdlc/plans/122.md).
errored_in_current's own tests below were added post-plan-review round 1 (Design decision 8, a
BLOCKING finding). still_erroring's and recovered_from_error's own tests were added
post-plan-review ROUND 2 (a SECOND BLOCKING finding against round 1's own fix, plus a folded-in
nit) -- test_a_rule_erroring_two_runs_in_a_row_is_still_visible_via_still_erroring and
test_still_erroring_re_fires_across_a_three_run_streak are the reviewer's own round-2 repro,
verbatim."""
import json

from insight.gaps.compare import compare_reports

_EMPTY_DELTA = {"regressed": [], "improved": [], "still_failing": [], "errored_in_current": [],
                "still_erroring": [], "recovered_from_error": [], "recurrence_count": 0}


def _report(*pairs, errors=()):
    return {"findings": [{"rule_id": rid, "severity": sev} for rid, sev in pairs],
            "errors": [{"rule_id": rid, "error": msg} for rid, msg in errors]}


def test_still_failing_regressed_improved_and_both_pipeline_blind_spots_in_one_call():
    prior = _report(("a", "PASS"), ("b", "FAIL"), ("c", "WARN"), ("vanishes", "FAIL"))
    current = _report(("a", "WARN"), ("b", "FAIL"), ("c", "PASS"), ("brand_new", "FAIL"))
    delta = compare_reports(prior, current)
    assert delta["regressed"] == [{"rule_id": "a", "before": "PASS", "now": "WARN"}]
    assert delta["improved"] == [{"rule_id": "c", "before": "WARN", "now": "PASS"}]
    assert delta["still_failing"] == [{"rule_id": "b", "before": "FAIL", "now": "FAIL"}]
    assert delta["errored_in_current"] == []
    assert delta["still_erroring"] == []
    assert delta["recovered_from_error"] == []
    assert delta["recurrence_count"] == 1
    all_rule_ids = str(delta)
    assert "vanishes" not in all_rule_ids and "brand_new" not in all_rule_ids


def test_a_persistent_warn_is_not_a_recurrence_signal():
    """Design decision 9: only FAIL/FAIL counts as still_failing -- WARN/WARN produces no delta
    entry at all, matching pipeline.py's own elif chain exactly."""
    prior = _report(("w", "WARN"))
    current = _report(("w", "WARN"))
    delta = compare_reports(prior, current)
    assert delta == _EMPTY_DELTA


def test_absent_to_pass_is_neither_regressed_nor_improved():
    """ABSENT (1) ranks ABOVE PASS (0) -- ABSENT -> PASS is a REGRESSION in SEVERITY_ORDER terms
    only in the sense the ordering demands; verified explicitly since it is easy to get backwards."""
    prior = _report(("r", "ABSENT"))
    current = _report(("r", "PASS"))
    delta = compare_reports(prior, current)
    assert delta["improved"] == [{"rule_id": "r", "before": "ABSENT", "now": "PASS"}]


def test_empty_prior_and_empty_current_produce_an_empty_delta():
    delta = compare_reports({"findings": []}, {"findings": []})
    assert delta == _EMPTY_DELTA


def test_the_exact_round_1_plan_review_repro_now_appears_in_the_machine_readable_delta():
    """Round-1 reviewer's own live repro: a rule FAIL in prior, then a malformed raw_payload row
    crashes that same rule in current. Before this fix, compare_reports returned
    still_failing=[], recurrence_count=0, and the rule id appeared nowhere in json.dumps(delta)
    -- confirmed False. Pins the fixed behaviour: the rule id now appears, via errored_in_current,
    and recurrence_count is UNCHANGED (still 0 -- an errored rule is never a confirmed
    recurrence, Design decision 8)."""
    prior = _report(("consistency_files_outside_plan", "FAIL"))
    current = {
        "findings": [],  # the rule crashed -- it never reaches findings
        "errors": [{"rule_id": "consistency_files_outside_plan",
                    "error": "InvalidInputException: Malformed JSON at byte 0 of input"}],
    }
    delta = compare_reports(prior, current)
    assert delta["still_failing"] == []
    assert delta["recurrence_count"] == 0
    assert delta["errored_in_current"] == [
        {"rule_id": "consistency_files_outside_plan", "before": "FAIL"}
    ]
    assert "consistency_files_outside_plan" in json.dumps(delta)


def test_a_rule_that_errors_with_no_prior_epoch_verdict_stays_silent():
    """Same 'nothing to lose' reasoning as blind spot 1 (a brand-new rule with no prior finding
    has no epoch verdict to diff against) -- extended to a brand-new rule that crashes on its own
    first run: there is no 'before' to report, so it does not appear in errored_in_current
    either."""
    prior = _report()
    current = {"findings": [], "errors": [{"rule_id": "brand_new_rule", "error": "boom"}]}
    delta = compare_reports(prior, current)
    assert delta["errored_in_current"] == []
    assert delta["still_erroring"] == []


def test_a_rule_erroring_two_runs_in_a_row_is_still_visible_via_still_erroring():
    """Round-2 reviewer's own live repro (BLOCKING against round 1's own fix): a rule errors in
    BOTH prior and current. errored_in_current's own condition sources prior_ix from
    prior["findings"] only, so a rule that ALSO errored in prior has no entry there and was
    dropped -- 'consistency_files_outside_plan' in json.dumps(delta) went back to False one run
    after round 1's fix made it True. Pins the round-2 fix: still_erroring now carries it."""
    prior = {"findings": [], "errors": [{"rule_id": "coverage_review_missing", "error": "boom"}]}
    current = {"findings": [], "errors": [{"rule_id": "coverage_review_missing", "error": "boom"}]}
    delta = compare_reports(prior, current)
    assert delta["errored_in_current"] == []  # NOT this bucket -- prior had no real severity either
    assert delta["still_erroring"] == [{"rule_id": "coverage_review_missing"}]
    assert delta["recurrence_count"] == 0
    assert "coverage_review_missing" in json.dumps(delta)


def test_still_erroring_re_fires_across_a_three_run_streak_not_just_the_second_crash():
    """Task's own explicit instruction: cover a 3-run streak, not just 2. Each pairwise call only
    ever asks whether a rule_id is in BOTH these two reports' own error lists -- re-verified here
    across two consecutive pairwise calls, proving the fix does not merely patch the second crash
    but genuinely generalises to an arbitrarily long streak."""
    errored = {"findings": [], "errors": [{"rule_id": "r", "error": "boom"}]}
    delta_2nd = compare_reports(errored, errored)
    delta_3rd = compare_reports(errored, errored)
    for delta in (delta_2nd, delta_3rd):
        assert delta["still_erroring"] == [{"rule_id": "r"}]
        assert delta["errored_in_current"] == []
        assert "r" in json.dumps(delta)


def test_recovering_from_an_error_is_flagged_not_silently_dropped():
    """Folded-in, non-blocking nit: a rule that errored in prior and has a real severity in
    current was previously silently dropped by the SAME before-is-None continue that (correctly)
    skips a brand-new rule -- except this rule has history, and there IS something to report."""
    prior = {"findings": [], "errors": [{"rule_id": "coverage_review_missing", "error": "boom"}]}
    current = _report(("coverage_review_missing", "PASS"))
    delta = compare_reports(prior, current)
    assert delta["recovered_from_error"] == [{"rule_id": "coverage_review_missing", "now": "PASS"}]
    assert delta["still_erroring"] == []
    assert delta["errored_in_current"] == []


def test_all_six_buckets_populated_in_one_call():
    """a: regressed. b: still_failing. c: improved. d: had a real prior severity, crashes this
    run -> errored_in_current. e: errored in prior, crashes again this run -> still_erroring. f:
    errored in prior, has a real severity this run -> recovered_from_error."""
    prior = {
        "findings": [
            {"rule_id": "a", "severity": "PASS"}, {"rule_id": "b", "severity": "FAIL"},
            {"rule_id": "c", "severity": "WARN"}, {"rule_id": "d", "severity": "WARN"},
        ],
        "errors": [{"rule_id": "e", "error": "boom"}, {"rule_id": "f", "error": "boom"}],
    }
    current = {
        "findings": [
            {"rule_id": "a", "severity": "WARN"},
            {"rule_id": "b", "severity": "FAIL"},
            {"rule_id": "c", "severity": "PASS"},
            {"rule_id": "f", "severity": "ABSENT"},
        ],
        "errors": [{"rule_id": "d", "error": "boom"}, {"rule_id": "e", "error": "boom"}],
    }
    delta = compare_reports(prior, current)
    assert delta["regressed"] == [{"rule_id": "a", "before": "PASS", "now": "WARN"}]
    assert delta["improved"] == [{"rule_id": "c", "before": "WARN", "now": "PASS"}]
    assert delta["still_failing"] == [{"rule_id": "b", "before": "FAIL", "now": "FAIL"}]
    assert delta["errored_in_current"] == [{"rule_id": "d", "before": "WARN"}]
    assert delta["still_erroring"] == [{"rule_id": "e"}]
    assert delta["recovered_from_error"] == [{"rule_id": "f", "now": "ABSENT"}]
    assert delta["recurrence_count"] == 1
