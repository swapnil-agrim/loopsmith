# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/report.py (issue #122, [E3.S7], Task 1 / bundled base runner; see
.sdlc/plans/122.md)."""
import datetime
import json

import pytest

from insight.gaps.loader import load_gap_rules  # noqa: E402
from insight.gaps.report import build_report, json_default, render_report


@pytest.fixture
def conn(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from insight.ingest.store import ensure_schema

    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_report_carries_every_rule_including_pass_and_absent(conn):
    report = build_report(conn)
    assert report["schema"] == "insight-gaps-report/v1"
    rule_ids = {f["rule_id"] for f in report["findings"]}
    # A fresh, empty store: every rule's population is 0 -> ABSENT for ALL of them. The count is
    # derived from the registry, not hardcoded -- this test is about "every rule appears", and a
    # literal broke the moment #121 added an eleventh rule, which is not what it is checking.
    assert len(rule_ids) == len(load_gap_rules())
    assert {f["severity"] for f in report["findings"]} == {"ABSENT"}
    assert report["errors"] == []
    assert report["verdict"] == {
        "overall": "ABSENT", "clean": False, "failing": False, "errored": False,
    }


def test_a_warn_finding_carries_its_rule_id_and_full_evidence(conn):
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":3,\"files_changed_outside_any_plan\":[\"scratch.py\"],"
        "\"files_outside_plan_confidence\":\"low\"}}}')"
    )
    report = build_report(conn)
    finding = next(f for f in report["findings"] if f["rule_id"] == "consistency_files_outside_plan")
    assert finding["severity"] == "WARN"
    assert finding["evidence"][0]["files_changed_outside_any_plan"] == ["scratch.py"]
    assert report["verdict"] == {
        "overall": "WARN", "clean": False, "failing": False, "errored": False,
    }


def test_a_crashing_rule_is_isolated_never_aborts_the_run(conn):
    """Live-reproduced this session: malformed raw_payload crashes every rule sharing that
    schema's json_extract calls. Every OTHER rule must still evaluate and appear in findings --
    that isolation is the property, and it must not be stated as a count that a new rule breaks."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', 'not valid json {{{')"
    )
    report = build_report(conn)
    crashing = {
        "consistency_files_outside_plan", "consistency_verify_no_test_touched",
        "coverage_gate_absent",
    }
    errored_ids = {e["rule_id"] for e in report["errors"]}
    assert errored_ids == crashing
    found_ids = {f["rule_id"] for f in report["findings"]}
    # The non-crashing rules still ran, isolated from the ones that did. Derived, not literal:
    # the property is "a crash isolates to its own rule", not "the catalog has ten entries".
    assert len(found_ids) == len(load_gap_rules()) - len(crashing)
    assert report["verdict"]["errored"] is True


def test_verdict_failing_requires_a_real_fail_not_just_absent(conn):
    """A store with zero data renders every rule ABSENT, which ranks ABOVE PASS in SEVERITY_ORDER
    -- `failing` must stay False regardless (Design decision 4: FAIL is the only thing that
    fails the run, matching pipeline.py's own failing_stages, ABSENT-only never trips it)."""
    report = build_report(conn)
    assert report["verdict"]["overall"] == "ABSENT"
    assert report["verdict"]["failing"] is False


def test_json_round_trip_survives_a_real_datetime_evidence_value(conn):
    """Reproduces this story's own live prototype: json.dumps(report) raises TypeError without
    default=json_default, and round-trips cleanly with it."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":3,\"files_changed_outside_any_plan\":[\"scratch.py\"],"
        "\"files_outside_plan_confidence\":\"low\"}}}')"
    )
    report = build_report(conn)
    finding = next(f for f in report["findings"] if f["rule_id"] == "consistency_files_outside_plan")
    assert isinstance(finding["evidence"][0]["collected_ts"], datetime.datetime)
    with pytest.raises(TypeError):
        json.dumps(report)
    text = json.dumps(report, default=json_default)
    back = json.loads(text)
    back_finding = next(f for f in back["findings"] if f["rule_id"] == "consistency_files_outside_plan")
    assert back_finding["evidence"][0]["collected_ts"] == "2026-01-01T00:00:00"


def test_render_report_shows_action_and_evidence_only_for_warn_and_fail(conn):
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', "
        "'{\"schema\":\"alignment-collect/v1\",\"dimensions\":{\"d1\":"
        "{\"commits_with_source\":3,\"files_changed_outside_any_plan\":[\"scratch.py\"],"
        "\"files_outside_plan_confidence\":\"low\"}}}')"
    )
    report = build_report(conn)
    text = render_report(report)
    assert "consistency_files_outside_plan: WARN" in text
    assert "action: declare the touched files" in text
    assert "definition_no_done_when: ABSENT" in text
    # an ABSENT line must never print an "action:" continuation (evidence is empty by invariant)
    absent_line_index = text.index("definition_no_done_when")
    assert "action:" not in text[absent_line_index:absent_line_index + 80]


def _report(findings=(), errors=()):
    """A hand-built report -- render_report reads only these keys, never a live store."""
    return {"schema": "insight-gaps-report/v1", "findings": list(findings),
            "errors": list(errors),
            "verdict": {"overall": "ABSENT", "failing": False, "errored": bool(errors),
                        "clean": False}}


def test_render_report_never_hides_a_crashed_rule():
    """The guarantee two plan-review rounds blocked on: a rule that crashed is visible in the
    RENDERED output on every run, not only in the machine-readable delta. Nothing else covered
    this line, so a regression here would have gone silent."""
    out = render_report(_report(errors=[{"rule_id": "boom", "error": "InvalidInputException: x"}]))
    assert "! boom ERRORED: InvalidInputException: x" in out
    assert "(rule error(s) present)" in out


def test_render_report_shows_every_delta_bucket_by_its_own_name():
    """Each bucket renders as its own distinguishable line -- a still-failing recurrence must
    never be confused with a rule whose visibility was merely lost to a crash."""
    delta = {
        "regressed": [{"rule_id": "r1", "before": "PASS", "now": "FAIL"}],
        "improved": [{"rule_id": "r2", "before": "FAIL", "now": "PASS"}],
        "still_failing": [{"rule_id": "r3", "before": "FAIL", "now": "FAIL"}],
        "errored_in_current": [{"rule_id": "r4", "before": "WARN"}],
        "still_erroring": [{"rule_id": "r5"}],
        "recovered_from_error": [{"rule_id": "r6", "now": "PASS"}],
        "recurrence_count": 1,
    }
    out = render_report(_report(), delta)
    assert "REGRESSED r1: PASS -> FAIL" in out
    assert "STILL FAILING r3" in out and "route to the backlog" in out
    assert "ERRORED IN CURRENT RUN r4 (was WARN in the prior run)" in out
    assert "STILL ERRORING r5" in out
    assert "RECOVERED FROM ERROR r6 -> now PASS" in out
    # recurrence is still_failing ALONE -- the three error buckets never inflate it.
    assert "still-failing (recurrence)=1" in out
