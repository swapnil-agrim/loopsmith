# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #16, Review-cycle distribution (issue #145, [E7.S2]). See .sdlc/plans/145.md and the
plan-review amendment folded into it (Amendment A): a malformed `dim_project.config_json` row
must degrade to a NULL cap for THAT project only, never crash the whole view -- 42.sql's
identical unguarded `json_extract(config_json, ...)` idiom is adjacent debt, not fixed here.

`>=` is the deliberate boundary, not `=`: `work.py`'s own `post_review()` parks a goal the
instant `cycles >= cap` first becomes true (verified by reading `work.py:536-542` this session),
so `goal_max_cycle > cap` is unreachable through the loop's own enforcement -- but a strict `=`
would hide a goal that somehow drifted past the cap, which is the worse case to hide. The
`g_above` fixture goal (cycle 4 against a cap of 3) exercises this defensively.

This view is functionally inert against any real, currently-ingested store (`ledger_writer.py`'s
`_write_event` never writes `cycle` -- #243): every test here bypasses the ledger/ingest
pipeline entirely and inserts directly into `fact_event`/`dim_project`, exactly like every other
metric test in this directory."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.render import render_dashboard  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "16.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_16_populated_fixture_computes_per_project_mass(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["16"]["reliability_class"] == 2

    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_16 ORDER BY project_id"))
    assert [r["project_id"] for r in rows] == ["p1", "p2", "p3"]
    by_project = {r["project_id"]: r for r in rows}

    # p1: cap=3, three goals -- g_below (max cycle 2, cap-1), g_at (max cycle 3, cap),
    # g_above (max cycle 4, cap+1, the defensive "drifted past" case) -- 2 of 3 at/over cap.
    p1 = by_project["p1"]
    assert p1["cap"] == 3
    assert p1["looped_goal_count"] == 3
    assert p1["goals_at_cap_count"] == 2
    assert p1["mass_at_cap_share"] == 0.6667
    assert p1["status"] == "FAIL"

    # p2: config_json present but `work.max_review_cycles` key missing -- cap is genuinely
    # unknown, never a fallback literal (Design decision 2).
    p2 = by_project["p2"]
    assert p2["cap"] is None
    assert p2["looped_goal_count"] == 1
    assert p2["goals_at_cap_count"] is None
    assert p2["mass_at_cap_share"] is None
    assert p2["status"] == "ABSENT"

    # p3: cap=5, one goal at cycle 1 -- nowhere near its own cap.
    p3 = by_project["p3"]
    assert p3["cap"] == 5
    assert p3["looped_goal_count"] == 1
    assert p3["goals_at_cap_count"] == 0
    assert p3["mass_at_cap_share"] == 0.0
    assert p3["status"] == "PASS"

    # render_dashboard must not raise CoverageDenominatorMissing for this class-2 metric.
    html_text, summary = render_dashboard(conn, "s.duckdb")
    assert summary["metric_count"] >= 28
    assert "16" in html_text or "Review-cycle distribution" in html_text


def test_metric_16_fires_only_at_the_cap_not_one_cycle_short(conn):
    """THE load-bearing test -- the done-when's own core claim: a goal one cycle short of its
    project's cap must not fire, and a goal exactly at the cap must."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"work\": {\"max_review_cycles\": 4}}')"
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p1', 'g_short', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 3, 2),"
        "('p1', 'g_exact', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 4, 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_16"))
    assert len(rows) == 1
    row = rows[0]
    assert row["cap"] == 4
    assert row["looped_goal_count"] == 2
    # Only g_exact (cycle == cap) fires -- g_short (cycle == cap - 1) does not.
    assert row["goals_at_cap_count"] == 1
    assert row["status"] == "FAIL"


def test_metric_16_null_cap_reads_absent_not_a_fallback_literal(conn):
    """No dim_project row AT ALL for a project that still has a qualifying post_review/cycle
    event -- the LEFT JOIN finds nothing, and cap must render NULL, never 0/0.0/a fallback like
    work.py's own code-default of 3."""
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('orphan', 'g1', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 3, 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_16"))
    assert len(rows) == 1
    row = rows[0]
    assert row["project_id"] == "orphan"
    assert row["cap"] is None
    assert row["goals_at_cap_count"] is None
    assert row["mass_at_cap_share"] is None
    assert row["status"] == "ABSENT"
    # Never a false PASS/FAIL hiding "I don't know the cap".
    assert row["status"] not in ("PASS", "FAIL")


def test_metric_16_reads_each_projects_own_cap_never_a_shared_literal(conn):
    """Two projects with DIFFERENT real caps, plus a colliding goal_id, so a bare-goal_id join
    (rather than one keyed on project_id too) would misattribute one project's goal into the
    other's cap-mass bucket."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p_low', '{\"work\": {\"max_review_cycles\": 2}}'),"
        "('p_high', '{\"work\": {\"max_review_cycles\": 7}}')"
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        # p_low's shared_g is at its own cap (2) -- must fire against 2, not 7.
        "('p_low', 'shared_g', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 2, 2),"
        # p_high's shared_g (same goal_id, different project) is at cycle 2 -- far below its
        # own cap of 7 -- must NOT fire, and must not borrow p_low's cap of 2 either.
        "('p_high', 'shared_g', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 2, 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_16 ORDER BY project_id"))
    assert [r["project_id"] for r in rows] == ["p_high", "p_low"]
    by_project = {r["project_id"]: r for r in rows}

    assert by_project["p_low"]["cap"] == 2
    assert by_project["p_low"]["goals_at_cap_count"] == 1
    assert by_project["p_low"]["status"] == "FAIL"

    assert by_project["p_high"]["cap"] == 7
    assert by_project["p_high"]["goals_at_cap_count"] == 0
    assert by_project["p_high"]["status"] == "PASS"


def test_metric_16_returns_zero_rows_over_a_fully_empty_store(conn):
    """No dimension table of expected projects to synthesize a phantom row from -- matching
    22.sql's own empty-store doctrine."""
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_16"))
    assert rows == []
    # render_dashboard must still not raise -- extract_coverage's own `row is None`
    # short-circuit applies.
    render_dashboard(conn, "s.duckdb")


def test_metric_16_coverage_denominator_reflects_the_post_review_population(conn):
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"work\": {\"max_review_cycles\": 3}}')"
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p1', 'g1', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 1, 1),"
        "('p1', 'g2', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 1, 2),"
        "('p1', 'g3', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 1, 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_16"))
    assert len(rows) == 1
    row = rows[0]
    assert row["class1_count"] == 1
    assert row["class2_count"] == 2
    assert row["total_count"] == 3
    assert row["coverage_pct"] == pytest.approx(1 / 3, abs=1e-4)


def test_metric_16_malformed_config_json_degrades_to_null_cap_for_that_project_only(conn):
    """AMENDMENT A's regression pin: a malformed config_json row must not crash the ENTIRE
    view. Before the json_valid() guard, `CAST(json_extract(config_json, ...) AS INTEGER)` over
    every dim_project row unconditionally raised InvalidInputException for every project the
    instant ANY one row held invalid JSON -- reproduced live by the plan reviewer. p_bad's own
    cap must degrade to NULL/ABSENT; p_valid's row must render completely unaffected.

    Deliberately does NOT call render_dashboard() here: 42.sql reads dim_project.config_json
    via the same unguarded json_extract_string idiom (grep-confirmed -- the only other metric
    that reads config_json at all) and WOULD raise over this same malformed row via the
    generic catalog's own full-registry render -- that is 42.sql's own, already-named adjacent
    debt (see this file's guardrail and .sdlc/plans/145.md's Out of scope), not a regression in
    metric_16 or something this story fixes. Querying metric_16 directly (below) is the correct,
    scoped proof that THIS view degrades per-project instead of crashing."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p_valid', '{\"work\": {\"max_review_cycles\": 3}}'),"
        "('p_bad', 'not json {{{')"
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p_valid', 'g1', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 3, 2),"
        "('p_bad', 'g2', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 1, 2)"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_16 ORDER BY project_id"))
    by_project = {r["project_id"]: r for r in rows}

    assert by_project["p_valid"]["cap"] == 3
    assert by_project["p_valid"]["goals_at_cap_count"] == 1
    assert by_project["p_valid"]["status"] == "FAIL"

    assert by_project["p_bad"]["cap"] is None
    assert by_project["p_bad"]["goals_at_cap_count"] is None
    assert by_project["p_bad"]["mass_at_cap_share"] is None
    assert by_project["p_bad"]["status"] == "ABSENT"


def test_metric_16_a_cap_too_large_for_an_integer_does_not_crash_the_view(conn):
    """json_valid() only proves the text IS JSON, not that the value fits an INTEGER. A
    syntactically perfect {"work": {"max_review_cycles": 999...9}} passed the guard and then
    raised ConversionException from CAST -- and not for that project alone: the WHOLE view, and
    render_dashboard over the whole catalog, went down with it, healthy siblings included. Same
    blast radius the json_valid guard was added to close, one layer further in. TRY_CAST closes
    it: the overflowing project degrades to a NULL cap / ABSENT, the healthy one still renders."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p_ok', '{\"work\": {\"max_review_cycles\": 3}}'),"
        "('p_huge', '{\"work\": {\"max_review_cycles\": 99999999999999999999}}')"
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p_ok', 'g1', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 3, 2),"
        "('p_huge', 'g2', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 1, 2)"
    )
    load_metrics(conn)
    rows = {r["project_id"]: r for r in rows_as_dicts(conn.execute("SELECT * FROM metric_16"))}
    assert rows["p_huge"]["cap"] is None
    assert rows["p_huge"]["status"] == "ABSENT"
    assert rows["p_ok"]["cap"] == 3                       # the healthy project is untouched
    assert rows["p_ok"]["status"] == "FAIL"               # g1 is at its own cap


def test_metric_16_a_zero_cap_reads_absent_because_zero_disables_the_cap(conn):
    """A configured 0 does NOT mean "the cap is hit immediately" -- it means the cap is OFF.
    work.py:536,542 does `cap = int(... or 0)` then `over_cap = cap and cycles >= cap`, so a
    falsy 0 short-circuits and the loop never parks for the cap at all; reviews run unbounded.
    Reporting mass=1.0 / FAIL for such a project would be a confident alarm about a mechanism
    that is not running -- the exact "meaningful-looking but backwards" failure this metric
    exists to avoid. NULLIF mirrors work.py's own falsy-zero reading rather than inventing a
    second one."""
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p_zero', '{\"work\": {\"max_review_cycles\": 0}}')"
    )
    conn.execute(
        "INSERT INTO fact_event "
        "(project_id, goal_id, ts, kind, gate, verdict, cycle, reliability_class) VALUES "
        "('p_zero', 'g1', '2026-01-01T00:00:01', 'gate', 'post_review', 'block', 2, 2)"
    )
    load_metrics(conn)
    row = rows_as_dicts(conn.execute("SELECT * FROM metric_16"))[0]
    assert row["cap"] is None
    assert row["status"] == "ABSENT"
    assert row["goals_at_cap_count"] is None
    assert row["mass_at_cap_share"] is None
