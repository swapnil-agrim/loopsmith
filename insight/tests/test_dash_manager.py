# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.manager (issue #127, E4.S4, Tasks 4-5): one test per panel's LIVE
branch (a synthetic non-zero fixture) and one per ABSENT branch (an empty fixture), per
.sdlc/plans/127.md Decision 2 -- three distinct absence flavors (measured absence, zero writers,
no ingest path) must never collapse into one generic sentence, so each panel's own distinguishing
string is asserted explicitly rather than a shared generic check reused across panels.

The individual-grain GUARDRAIL itself (the panel-scoped leak check + its negative control) is
NOT here -- see insight/tests/test_dash_manager_guardrail.py, this story's Task 7 and its own
proving test."""
import datetime
import json
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.dash.render import assert_self_contained  # noqa: E402
from insight.dash.manager import (  # noqa: E402
    _park_rate_row,
    _reason_class_measured_count,
    _render_park_taxonomy,
    _render_review_cycle_distribution,
    _wip_row,
    render_manager_view,
)

NOW = datetime.datetime(2026, 8, 1)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _sections(html_text):
    return dict(re.findall(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', html_text, re.DOTALL))


# --------------------------------------------------------------------------- page shell (Task 5)

def test_render_manager_view_has_exactly_the_five_expected_sections(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    sections = _sections(html_text)
    assert set(sections) == {
        "panel-burndown", "panel-wip-aging", "panel-handoff-graph",
        "panel-park-taxonomy", "panel-review-cycles",
    }

    # ------------------------------------------------------------------------------------- #264
    # Content-identical pin (plan .sdlc/plans/264.md Sec 1.6): frozen against the CURRENT,
    # unmodified code on a fixed empty-store fixture + fixed NOW, so a later refactor of the page
    # shell / instrument primitives (issue #264) cannot silently change manager.html's rendered
    # content. Strict equality, so it is falsifiable by construction -- no separate negative
    # control needed.
    h2_texts = re.findall(r"<h2>(.*?)</h2>", html_text)
    assert set(h2_texts) == {
        "Burndown + Monte Carlo band", "WIP and aging", "Handoff graph (by area)",
        "Park taxonomy", "Review-cycle distribution",
    }

    payload_text = re.search(
        r'<script type="application/json" id="insight-manager-data">(.*?)</script>',
        html_text, re.DOTALL,
    ).group(1)
    assert json.loads(payload_text) == {
        "generated_at": "2026-08-01T00:00:00",
        "burndown": {"weeks": 0, "p10_total": None, "p90_total": None},
        "wip": None,
        "aging_wip_count": 0,
        "handoff_by_area": [],
        "park_rate": {
            "parked_terminal_count": 0, "terminal_count": 0, "park_rate": None,
        },
        "reason_class_measured_count": 0,
    }

    footer_text = re.search(r"<footer>(.*?)</footer>", html_text, re.DOTALL).group(1)
    assert footer_text == (
        "Self-contained: no network fetch, no external script/style/font reference. Data is\n"
        "inlined above. No individual-grain metric appears on this page except aging WIP "
        "(panel-wip-aging)\nand hand-off response (not rendered here) -- see this module's own "
        "docstring."
    )


def test_render_manager_view_passes_assert_self_contained(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    assert_self_contained(html_text)  # must not raise


def test_render_manager_view_title_names_manager_view(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    assert "<title>LoopSmith Insight -- Manager view</title>" in html_text


def test_render_manager_view_never_imports_ic_or_actor_modules():
    """Decision 5: the manager view is team-wide, never actor-resolution-gated. An AST-level
    import check (not a source-text grep, which would also match this module's own docstring
    naming what it deliberately does NOT import) -- pins the STRUCTURE, not only today's observed
    output."""
    import ast
    import inspect

    import insight.dash.manager as manager_mod

    source = inspect.getsource(manager_mod)
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(m.endswith(("dash.ic", "dash.actor")) for m in imported), imported
    assert "actor" not in inspect.signature(render_manager_view).parameters


# --------------------------------------------------------------------------- panel-burndown

def test_burndown_panel_absent_on_a_store_with_no_claimed_ts(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-burndown"]
    assert "fact_goal.terminal_ts not populated yet" in panel


def test_burndown_panel_live_once_claimed_ts_and_terminal_ts_populate(conn):
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, claimed_ts, terminal_ts) VALUES "
        "('p1', 'g1', 'done', '2026-01-01T00:00:00', '2026-01-08T00:00:00'), "
        "('p1', 'g2', 'done', '2026-01-01T00:00:00', '2026-01-15T00:00:00')"
    )
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-burndown"]
    assert "fact_goal.terminal_ts not populated yet" not in panel
    assert "remaining (actual)" in panel  # render_burndown_with_mc_band's own live-branch label


# --------------------------------------------------------------------------- panel-wip-aging

def test_wip_aging_panel_absent_on_a_store_with_no_events(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-wip-aging"]
    assert "no claimed/done/parked/failed event has ever been measured" in panel
    assert "no open claims measured" in panel


def test_wip_aging_panel_live_with_one_open_claim(conn):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g-dana-1', ?, 'dana', 'claimed', 1)",
        [NOW - datetime.timedelta(days=3)],
    )
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-wip-aging"]
    assert "WIP (open claims)" in panel
    assert "dana" in panel
    assert "3d" in panel


def test_wip_row_returns_the_shape_exactly_week_start_and_wip_count(conn):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g1', ?, 'dana', 'claimed', 1)", [NOW],
    )
    load_metrics(conn)
    row = _wip_row(conn)
    assert set(row) == {"week_start", "wip_count"}


# --------------------------------------------------------------------------- panel-handoff-graph

def test_handoff_graph_panel_absent_when_fact_handoff_is_empty(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-handoff-graph"]
    assert "fact_handoff has no rows with a linked issue" in panel


def test_handoff_graph_panel_live_with_one_area(conn):
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES ('p1', 'dana', 'erin', 'insight', 401, 'p1', ?)", [NOW],
    )
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-handoff-graph"]
    assert "insight (1)" in panel
    assert "Handoff volume by area" in panel


# --------------------------------------------------------------------------- panel-park-taxonomy

def test_park_taxonomy_panel_renders_two_distinct_absent_sentences_on_an_empty_store(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-park-taxonomy"]
    assert "no terminal goals recorded yet" in panel
    assert "zero writers anywhere in the ingest path" in panel
    # the two sentences are genuinely different claims, not the same text reused twice
    assert panel.count("no terminal goals recorded yet") == 1
    assert panel.count("zero writers anywhere in the ingest path") == 1


def test_park_rate_sub_panel_live_with_terminal_goals(conn):
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome) VALUES "
        "('p1', 'g1', 'done'), ('p1', 'g2', 'failed')"
    )
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g2', ?, 'dana', 'parked', 1)", [NOW],
    )
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-park-taxonomy"]
    assert "no terminal goals recorded yet" not in panel
    assert "Park rate" in panel
    assert "1 of 2 terminal" in panel
    # taxonomy sub-panel is still absent -- no reason_class writer in this fixture
    assert "zero writers anywhere in the ingest path" in panel


def test_park_taxonomy_sub_panel_live_once_reason_class_is_populated(conn):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reason_class, "
        "reliability_class) VALUES ('p1', 'g1', ?, 'dana', 'parked', 'blocked_on_review', 1)",
        [NOW],
    )
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-park-taxonomy"]
    assert "zero writers anywhere in the ingest path" not in panel
    assert "1" in panel and "reason_class" in panel


def test_reason_class_measured_count_ignores_reliability_class_2(conn):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reason_class, "
        "reliability_class) VALUES ('p1', 'g1', ?, 'dana', 'parked', 'blocked_on_review', 2)",
        [NOW],
    )
    assert _reason_class_measured_count(conn) == 0


def test_park_rate_row_reads_metric_14s_own_columns_directly(conn):
    load_metrics(conn)
    row = _park_rate_row(conn)
    assert set(row) == {"parked_terminal_count", "terminal_count", "park_rate"}


def test_park_rate_row_returns_none_when_metric_14_has_no_row():
    """metric_14 is a bare aggregate with no GROUP BY, so it always returns exactly one row in
    practice -- this defensive branch mirrors charts._mc_band's own identical precedent for
    metric_11, exercised the same way: a stub connection, not a real one."""
    class _StubCursor:
        description = [("parked_terminal_count",), ("terminal_count",), ("park_rate",)]

        def fetchone(self):
            return None

    class _StubConn:
        def execute(self, sql):
            return _StubCursor()

    assert _park_rate_row(_StubConn()) is None


# --------------------------------------------------------------------------- panel-review-cycles

def test_review_cycles_panel_always_renders_the_strongest_absent_flavor(conn):
    """No live branch exists for this panel (Decision 2/Risk 4) -- it renders the identical
    'no ingest path' sentence regardless of what else is in the store."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome) VALUES ('p1', 'g1', 'done')"
    )
    html_text, _ = render_manager_view(conn, now=NOW)
    panel = _sections(html_text)["panel-review-cycles"]
    assert "no ingest path exists for this data at all" in panel
    assert "review_cycles" in panel


def test_render_review_cycle_distribution_takes_no_arguments():
    out = _render_review_cycle_distribution()
    assert "no ingest path exists for this data at all" in out


# --------------------------------------------------------------------------- three absence flavors are textually distinct

def test_the_three_absence_flavors_use_three_different_phrases_never_the_same_generic_text(conn):
    html_text, _ = render_manager_view(conn, now=NOW)
    burndown_absent = "fact_goal.terminal_ts not populated yet"
    taxonomy_absent = "zero writers anywhere in the ingest path"
    review_absent = "no ingest path exists for this data at all"
    assert burndown_absent != taxonomy_absent != review_absent != burndown_absent
    assert burndown_absent in html_text
    assert taxonomy_absent in html_text
    assert review_absent in html_text
