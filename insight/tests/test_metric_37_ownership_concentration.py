# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #37, Ownership concentration (issue #112, E2.S5, Task 6). "Bus factor" -- goals per
area per actor vs CODEOWNERS. Decision D: no CODEOWNERS reader is built (no such file exists in
this repo, dim_actor.areas has zero writer anywhere -- grep-confirmed) -- instead this view is
scoped to the actor<->area signal that DOES exist: fact_handoff.area x fact_handoff.to_actor, who
actually RECEIVES hand-offs in each area. This is a disclosed, named bias, not a silent
substitution: a bus factor computed from hand-off participation only sees actors who were ever
the RECIPIENT of a hand-off in that area -- a LOWER BOUND on true concentration risk, stated
explicitly in 37.sql's own guardrail.

Concentration measure: top actor's share of to_actor hand-offs into an area --
max(count per to_actor) / sum(count per to_actor). Thresholds (Plan's own calibration, spec
silent): share >= 0.75 -> FAIL, 0.5 <= share < 0.75 -> WARN, share < 0.5 -> PASS. ABSENT when
total_handoffs_in_area < 3 -- below that, "concentration" is not a structurally answerable
question (a single hand-off trivially "concentrates" 100% on whoever received it -- a fact about
volume, not risk), same false-zero-trap reasoning 30.sql's first-snapshot ABSENT already applies.

This is the ONE metric among the seven that carries PASS/WARN/FAIL/ABSENT + severity_rank
(Decision F) -- a bus-factor risk level is the one Layer-3 question shaped like a gate.

Fixture (37.jsonl), fact_handoff rows under project_id='p1' across four areas:
  - backend: to_actor='a1' x4, 'a2' x1 (5 total, share=0.8) -> FAIL.
  - frontend: to_actor='a1' x2, 'a2' x2 (4 total, share=0.5, exact boundary) -> WARN.
  - docs: to_actor='a3' x1 (1 total, below the volume floor) -> ABSENT.
  - platform: to_actor='a1' x2, 'a2' x2, 'a3' x2 (6 total, share=0.333) -> PASS (filler row,
    exercising the fourth status alongside the three the issue's minimum already requires).
  - issue=null (rows 17-18): the orphan-ack pair (BLOCKING-finding regression, post-review, issue
    #112 PR #190 cycle 2). Row 17 is a goal-only hand-off (area='ops', issue=None, never acked in
    its own row). Row 18 is its orphaned ack (issue=None, ack_ts/ack_state populated, area/
    to_actor/opened_ts all NULL) -- exactly what ledger_writer.py's _apply_handoff/_apply_ack
    produce for an issue-less record, since fact_handoff has no goal_id column to re-match the
    ack to its own hand-off. Before the issue IS NOT NULL fix, row 18 alone GROUP BY'd in
    per_actor into a phantom (area=NULL, to_actor=NULL) row that rolled up into per_area as a
    phantom area=NULL group -- ABSENT only because this fixture's total_handoffs_in_area (1) sits
    under the volume floor; at higher volume it would render a real PASS/WARN/FAIL status for an
    area that does not exist."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "37.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_37_computes_top_actor_share_and_status_per_area(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT area, top_actor_share, status FROM metric_37")
    )
    by_area = {r["area"]: r for r in rows}
    assert by_area["backend"]["status"] == "FAIL"
    assert by_area["backend"]["top_actor_share"] == pytest.approx(0.8)
    assert by_area["frontend"]["status"] == "WARN"
    assert by_area["frontend"]["top_actor_share"] == pytest.approx(0.5)
    assert by_area["docs"]["status"] == "ABSENT"
    assert by_area["platform"]["status"] == "PASS"
    assert by_area["platform"]["top_actor_share"] == pytest.approx(1 / 3)


def test_metric_37_renders_absent_not_a_false_pass_below_the_volume_floor(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT status FROM metric_37 WHERE area = 'docs'")
    )
    assert rows == [{"status": "ABSENT"}]
    assert all(r["status"] not in ("PASS", "FAIL") for r in rows)


def test_metric_37_excludes_the_phantom_null_area_group_from_an_orphaned_ack(conn):
    """BLOCKING-finding regression (post-review, issue #112 PR #190 cycle 2): fact_handoff has no
    goal_id column, so an issue-less hand-off's own later ack lands as a second, orphaned row
    (issue NULL, area/to_actor/opened_ts all NULL, only ack_ts/ack_state populated) rather than
    merging into the original. Fixture rows 17 and 18 add exactly that pair (a goal-only
    hand-off, area='ops', plus its orphaned ack). Before the issue IS NOT NULL fix, the orphaned
    ack row alone GROUP BY'd into its own phantom (area=NULL, to_actor=NULL) per_actor row, which
    rolled up into a phantom area=NULL per_area row -- a bus-factor status for an area that does
    not exist. Both the issue-less hand-off and its orphaned ack are excluded from this view
    entirely now -- the original four areas, unchanged."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT area FROM metric_37"))
    areas = {r["area"] for r in rows}
    assert None not in areas
    assert "ops" not in areas
    assert areas == {"backend", "frontend", "docs", "platform"}


def test_metric_37_declares_itself_dark(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["37"]["extra"]["data_status"] == "dark"
