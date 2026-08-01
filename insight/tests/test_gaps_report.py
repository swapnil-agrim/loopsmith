# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight/gaps/report.py (issue #122, [E3.S7], Task 1 / bundled base runner; see
.sdlc/plans/122.md)."""
import datetime
import json

import pytest

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
    # a fresh, empty store: every rule's population is 0 -> ABSENT for all ten.
    assert len(rule_ids) == 10
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
    schema's json_extract calls. The OTHER 7 rules must still evaluate and appear in findings."""
    conn.execute(
        "INSERT INTO fact_collector_pack (project_id, schema, collected_ts, raw_payload) "
        "VALUES ('p1', 'alignment-collect/v1', '2026-01-01', 'not valid json {{{')"
    )
    report = build_report(conn)
    errored_ids = {e["rule_id"] for e in report["errors"]}
    assert errored_ids == {
        "consistency_files_outside_plan", "consistency_verify_no_test_touched",
        "coverage_gate_absent",
    }
    found_ids = {f["rule_id"] for f in report["findings"]}
    assert len(found_ids) == 7  # the other 7 rules still ran, isolated from the 3 that crashed
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
