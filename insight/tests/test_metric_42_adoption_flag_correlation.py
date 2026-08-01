# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #42, Adoption & flag correlation (issue #113, E2.S6, Task 2). A per-project x per-flag
adoption TABLE (Decision 2), not a computed correlation coefficient -- long form, one row per
(project_id, flag_key), carrying that flag's extracted value alongside that project's own live
outcome aggregates (fact_merge_lead_time, fact_pr_check, fact_pr_review; fact_goal.outcome is
deliberately excluded, dark on all 19 rows in this repo's own real ingest).

Fixture (42.jsonl), three projects (exceeding the two-project minimum specifically to exercise
both NULL paths -- missing key vs. missing config -- side by side, per Decision 2a):
  - projA: a real config_json shape covering all eight flags (values read live from this repo's
    own .sdlc/config.json this session), plus outcome rows spanning both verdict vocabularies --
    2 fact_merge_lead_time rows (one measured, one lead_time_seconds=NULL), 3 fact_pr_check rows
    (SUCCESS, SUCCESS, FAILURE), 3 fact_pr_review rows: one native 'APPROVED', one
    loopsmith_comment 'approve' (lower-case, this project's own real convention), one
    loopsmith_comment 'block' (must not count toward the numerator).
  - projB: config_json missing the knowledge_graph key entirely (proves missing-key -> NULL, not
    'false'); zero rows in every outcome table (proves the "never scanned" NULL *_total_count
    path).
  - projC: config_json itself NULL (proves missing-config -> NULL for every flag); 1
    fact_pr_check row with conclusion=NULL (an in-progress check -- proves pr_check_total_count=0,
    a REAL zero because a row exists, distinct from projB's NULL "zero rows ever" shape)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "42.jsonl"

EXPECTED_PROJA_FLAGS = {
    "parallel.enabled": "false",
    "ledger.enabled": "true",
    "verify.enforce": "true",
    "work.require_review": "changes",
    "work.auto_merge": "protected",
    "gates.hard_plan_gate.enabled": "false",
    "review.independent": "true",
    "knowledge_graph.enabled": "false",
}


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_42_returns_eight_flag_rows_per_project(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT project_id FROM metric_42"))
    assert len(rows) == 24
    distinct = rows_as_dicts(conn.execute("SELECT DISTINCT project_id FROM metric_42"))
    assert {r["project_id"] for r in distinct} == {"projA", "projB", "projC"}


def test_metric_42_projA_flag_values_match_the_real_config_shape(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute(
            "SELECT flag_key, flag_value FROM metric_42 WHERE project_id = 'projA'"
        )
    )
    actual = {r["flag_key"]: r["flag_value"] for r in rows}
    assert actual == EXPECTED_PROJA_FLAGS
    # Both string-valued flags render their literal string, not a boolean-shaped value.
    assert actual["work.require_review"] == "changes"
    assert actual["work.auto_merge"] == "protected"


def test_metric_42_projB_missing_key_is_null_not_false(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute(
            "SELECT flag_value FROM metric_42 "
            "WHERE project_id = 'projB' AND flag_key = 'knowledge_graph.enabled'"
        )
    )[0]
    assert row["flag_value"] is None
    assert row["flag_value"] != "false"


def test_metric_42_projC_missing_config_is_null_for_every_flag(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT flag_value FROM metric_42 WHERE project_id = 'projC'")
    )
    assert len(rows) == 8
    assert all(r["flag_value"] is None for r in rows)


def test_metric_42_projA_outcome_aggregates_match_hand_computed_values(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute(
            "SELECT merge_total_count, merge_measured_count, pr_check_total_count, "
            "pr_check_pass_rate, pr_review_total_count, pr_review_approval_rate "
            "FROM metric_42 WHERE project_id = 'projA' LIMIT 1"
        )
    )[0]
    assert row["merge_total_count"] == 2
    assert row["merge_measured_count"] == 1
    assert row["pr_check_total_count"] == 3
    assert row["pr_check_pass_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert row["pr_review_total_count"] == 3
    # Numerator is 2, not 3: native 'APPROVED' + loopsmith_comment 'approve' both count;
    # loopsmith_comment 'block' does not.
    assert row["pr_review_approval_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_metric_42_projB_has_zero_rows_ever_not_a_false_zero_rate(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute(
            "SELECT merge_total_count, pr_check_total_count, pr_check_pass_rate, "
            "pr_review_total_count, pr_review_approval_rate "
            "FROM metric_42 WHERE project_id = 'projB' LIMIT 1"
        )
    )[0]
    assert row["merge_total_count"] is None
    assert row["pr_check_total_count"] is None
    assert row["pr_check_pass_rate"] is None
    assert row["pr_review_total_count"] is None
    assert row["pr_review_approval_rate"] is None


def test_metric_42_projC_pr_check_rows_exist_but_none_are_measured(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    row = rows_as_dicts(
        conn.execute(
            "SELECT pr_check_total_count, pr_check_pass_rate FROM metric_42 "
            "WHERE project_id = 'projC' LIMIT 1"
        )
    )[0]
    assert row["pr_check_total_count"] == 0
    assert row["pr_check_pass_rate"] is None


def test_metric_42_declares_itself_not_dark_or_proxy(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["42"]["extra"].get("data_status") is None
    assert registry["42"]["extra"].get("proxy") is None


def test_metric_42_every_row_has_a_non_null_project_id(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT project_id FROM metric_42"))
    assert all(r["project_id"] is not None for r in rows)
    assert len(rows) == 24
