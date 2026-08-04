# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #265 (D4) Steps 7-9: a single prefix-leak regression proving manager.py/leadership.py/
ic.py's own chart-primitive CALL SITES actually switched to `id_prefix="panel"` -- not just that
the capability exists (test_dash_charts.py's own panel-prefix-dispatch tests already prove that
mechanically), but that these THREE PAGES actually wire it in. Before this issue, none of these
three files' own test suites pinned a single `panel`/`dash`/`id_prefix` literal (grepped this
session) -- ic.py in particular shipped with NO prefix-leak guard at all (independent plan-review
Finding 4: the original plan wording covered only manager.py/leadership.py). Each page gets its
own live-data proof (whichever --panel-cat-/--panel-seq- roles that page's own charts can actually
produce) plus one shared negative control proving the check has teeth."""
import datetime

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.dash.ic import render_ic_view  # noqa: E402
from insight.dash.leadership import render_leadership_view  # noqa: E402
from insight.dash.manager import render_manager_view  # noqa: E402

NOW = datetime.datetime(2026, 8, 1)

#: The old light/dash CHART-MARK vocabulary -- must never appear as an actual mark REFERENCE (a
#: `var(--dash-...)` USE) on any of these three panel-ground pages once Steps 7-9 land. Checked as
#: `var(--dash-...` (the reference form), never the bare `--dash-...` substring, because
#: viz_css_vars() legitimately still DECLARES `--dash-cat-0: ...;` etc in the <style> block
#: (harmless, unused token definitions these pages still emit for whatever else in their own
#: _STYLE legitimately wants the light/dash system) -- only a mark that actually REFERENCES one
#: is a real leak.
_OLD_VOCAB_MARK_REFS = ("var(--dash-cat-", "var(--dash-seq-", "var(--dash-status-absent)")


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _assert_no_old_vocab_mark_leak(html_text):
    for needle in _OLD_VOCAB_MARK_REFS:
        assert needle not in html_text, f"{needle!r} leaked into rendered output"


def test_manager_view_chart_marks_are_all_panel_prefixed_never_dash(conn):
    """manager.py can produce BOTH categorical (handoff-by-area) and sequential (aging-wip) marks
    -- a live fixture for each proves both actually switched, not just the absence branches."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g1', ?, 'dana', 'claimed', 1)", [NOW - datetime.timedelta(days=3)],
    )
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, to_actor, area, issue, priority, "
        "opened_ts) VALUES ('p1', 'dana', 'erin', 'insight', 401, 'p1', ?)", [NOW],
    )
    html_text, _ = render_manager_view(conn, now=NOW)
    assert "var(--panel-seq-" in html_text  # render_aging_wip's live bars
    assert "var(--panel-cat-" in html_text  # render_handoff_graph_by_area's live spoke
    _assert_no_old_vocab_mark_leak(html_text)


def test_ic_view_chart_marks_are_all_panel_prefixed_never_dash(conn):
    """ic.py has no categorical chart on this page (no handoff graph here) -- only render_aging_
    wip's sequential ramp is live-exercisable. This is the page independent plan-review Finding 4
    flagged as having NO prior panel/dash guard at all."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES ('p1', 'g1', ?, 'dana', 'claimed', 1)", [NOW - datetime.timedelta(days=2)],
    )
    html_text, _ = render_ic_view(conn, actor="dana", now=NOW)
    assert "var(--panel-seq-" in html_text  # render_aging_wip's live bars (my queue)
    _assert_no_old_vocab_mark_leak(html_text)


def test_leadership_view_absence_uses_panel_material_never_dash_status_absent(conn):
    """leadership.py's own charts.py calls (render_stat_tile x2, never with delta/trend anywhere
    on this page; _absent_line) never draw a categorical/sequential MARK at all -- there is
    nothing live-cat/seq-shaped for this page to assert positively. What DOES change is the
    ABSENT vocabulary: every clause is absent on an empty store, so the panel-material dispatch
    (colors.not_measured_block, class="data-state-not-measured") is this page's whole proof --
    before Step 8, this same empty-store render used the old STATUS["ABSENT"] shell instead
    (var(--dash-status-absent), caught by the negative-vocab assertion below)."""
    html_text, _ = render_leadership_view(conn, now=NOW)
    assert 'class="data-state-not-measured"' in html_text
    _assert_no_old_vocab_mark_leak(html_text)


def test_negative_control_proves_the_old_vocab_mark_leak_check_has_teeth():
    """Not shipped code -- proves `_assert_no_old_vocab_mark_leak` is falsifiable."""
    clean = '<rect fill="var(--panel-seq-3)"/><rect fill="var(--panel-cat-0)"/>'
    _assert_no_old_vocab_mark_leak(clean)  # sanity: passes on real-shaped clean markup

    leaked = '<rect fill="var(--panel-seq-3)"/><rect fill="var(--dash-seq-3)"/>'
    with pytest.raises(AssertionError, match="leaked into rendered output"):
        _assert_no_old_vocab_mark_leak(leaked)
