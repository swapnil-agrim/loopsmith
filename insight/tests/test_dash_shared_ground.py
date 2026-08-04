# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The shared-ground proving test for issue #264 (plan .sdlc/plans/264.md Sec 1.7, done_when 3):
every one of the five dash pages now sits on `instrument.page_open()`'s shared dark-ground frame
-- `panel_css_vars()`, never the old `shell.base_style()` light shell -- so this asserts, per
page, that a `panel_css_vars()` signature fragment (the `--panel-ground:` declaration) is PRESENT
and `base_style()`'s own signature fragment (`margin: var(--dash-space-7)`, its body rule's
literal margin declaration) is ABSENT. Falsifiable by construction: the negative control below
splices the old fragment back into one rendered page and proves the same assertion then fails --
a check that cannot fail against a deliberately broken input is not a check (mirrors this
codebase's own guardrail-test methodology, e.g. test_dash_manager_guardrail.py)."""
import datetime

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.dash.cross_functional import render_cross_functional_view  # noqa: E402
from insight.dash.ic import render_ic_view  # noqa: E402
from insight.dash.leadership import render_leadership_view  # noqa: E402
from insight.dash.manager import render_manager_view  # noqa: E402
from insight.dash.panel import render_panel  # noqa: E402

NOW = datetime.datetime(2026, 8, 1)

#: panel_css_vars()'s own signature fragment -- present on every page once it sits on the shared
#: instrument frame (colors.PANEL's "ground" token, emitted by every panel_css_vars() call).
_PANEL_GROUND_FRAGMENT = "--panel-ground:"

#: base_style()'s own signature fragment -- the old light shell's body rule's literal margin
#: declaration (insight/dash/shell.py). Must be gone from every migrated page's rendered output.
_OLD_SHELL_FRAGMENT = "margin: var(--dash-space-7)"


def _render_all_pages(conn_factory):
    """One fresh connection per page (mirrors __main__.py's own one-connection-per-page
    convention). `render_panel` returns the HTML string directly (not a `(html, summary)` pair
    like the other four render_*_view functions) -- mirrors test_dash_panel_absence.py's own
    `metrics_dir="insight/metrics"` convention."""
    pages = {}
    pages["panel"] = render_panel(conn_factory(), metrics_dir="insight/metrics", now=NOW)
    pages["manager"], _ = render_manager_view(conn_factory(), now=NOW)
    pages["leadership"], _ = render_leadership_view(conn_factory(), now=NOW)
    pages["ic"], _ = render_ic_view(conn_factory(), "alice", now=NOW)
    pages["cross-functional"], _ = render_cross_functional_view(conn_factory(), now=NOW)
    return pages


@pytest.fixture
def rendered_pages(tmp_path):
    def _make_conn():
        c = duckdb.connect(str(tmp_path / f"s-{_make_conn.n}.duckdb"))
        _make_conn.n += 1
        ensure_schema(c)
        return c

    _make_conn.n = 0
    return _render_all_pages(_make_conn)


def test_every_page_declares_the_panel_css_vars_ground_token(rendered_pages):
    for name, html_text in rendered_pages.items():
        assert _PANEL_GROUND_FRAGMENT in html_text, (
            f"{name}.html never declares {_PANEL_GROUND_FRAGMENT!r} -- not on the shared "
            "instrument frame (instrument.page_open() calls colors.panel_css_vars())"
        )


def test_no_page_carries_the_old_light_shells_signature_fragment(rendered_pages):
    for name, html_text in rendered_pages.items():
        assert _OLD_SHELL_FRAGMENT not in html_text, (
            f"{name}.html still carries {_OLD_SHELL_FRAGMENT!r} -- the old shell.base_style() "
            "light shell leaked back in"
        )


def test_negative_control_proves_the_old_shell_check_has_teeth(rendered_pages):
    """Not shipped code -- reinserts the old fragment into one rendered page's string and proves
    the SAME assertion used above now fails, so the positive check is falsifiable."""
    html_text = rendered_pages["manager"]
    assert _OLD_SHELL_FRAGMENT not in html_text  # sanity: passes today

    mutated = html_text.replace(
        "</style>", f"body {{ {_OLD_SHELL_FRAGMENT}; }}</style>", 1,
    )
    assert _OLD_SHELL_FRAGMENT in mutated, (
        "fixture regressed: negative control no longer lands inside the mutated page"
    )
    with pytest.raises(AssertionError):
        assert _OLD_SHELL_FRAGMENT not in mutated
