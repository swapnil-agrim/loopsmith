# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #122's own Task 3: 'test across two fixture runs', at the CLI level -- state N and N+1
built by hand (nothing under insight/ produces two comparable runs on its own today). Reuses the
exact FAIL fixture (coverage_review_missing) and WARN fixture (consistency_files_outside_plan)
live-verified in .sdlc/plans/122.md Tasks 1-2."""
import json

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "s.duckdb"
    conn = duckdb.connect(str(target))
    from insight.ingest.store import ensure_schema
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"work\":{\"require_review\":\"approval\"}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) VALUES "
        "('p1', 's1', 101, 'squash_pr'), ('p1', 's2', 102, 'squash_pr')"
    )
    conn.execute(
        "INSERT INTO fact_pr_check (project_id, pr_number, check_name, conclusion) VALUES "
        "('p1', 101, 'ci', 'success'), ('p1', 102, 'ci', 'success')"
    )
    conn.close()
    return target


def test_still_failing_recurrence_and_a_later_improvement_across_two_real_runs(db, tmp_path, capsys):
    from insight.__main__ import main

    # --- run N: both PRs unapproved -> FAIL, evidence 2 rows ---
    snap1 = tmp_path / "n.json"
    code = main(["gaps", "--db", str(db), "--json", str(snap1)])
    assert code == 1
    report_n = json.loads(snap1.read_text())
    fail_n = next(f for f in report_n["findings"] if f["rule_id"] == "coverage_review_missing")
    assert fail_n["severity"] == "FAIL"
    assert len(fail_n["evidence"]) == 2

    # --- run N+1: PR 101 gets approved, PR 102 does not -> still FAIL, evidence shrinks to 1 ---
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(str(db))
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict) "
        "VALUES ('p1', 101, 'gh', 'ev1', 'APPROVED')"
    )
    conn.close()
    capsys.readouterr()  # discard run N's own stdout before the next assertion
    snap2 = tmp_path / "n1.json"
    code = main(["gaps", "--db", str(db), "--compare", str(snap1), "--json", str(snap2)])
    assert code == 1  # STILL FAIL -- the recurrence signal, not fixed yet
    out = capsys.readouterr().out
    assert "STILL FAILING coverage_review_missing" in out
    assert "recurrence signal: route to the backlog" in out
    report_n1 = json.loads(snap2.read_text())
    assert report_n1["delta"]["still_failing"] == [
        {"rule_id": "coverage_review_missing", "before": "FAIL", "now": "FAIL"}
    ]
    assert report_n1["delta"]["recurrence_count"] == 1

    # --- run N+2: PR 102 also gets approved -> FAIL -> PASS, an "improved" transition ---
    conn = duckdb.connect(str(db))
    conn.execute(
        "INSERT INTO fact_pr_review (project_id, pr_number, source, event_id, verdict) "
        "VALUES ('p1', 102, 'gh', 'ev2', 'APPROVED')"
    )
    conn.close()
    code = main(["gaps", "--db", str(db), "--compare", str(snap2)])
    assert code == 0  # clean -- the recurrence is resolved
    out = capsys.readouterr().out
    assert "delta: regressed=0 improved=1 still-failing (recurrence)=0" in out
    assert "STILL FAILING" not in out
