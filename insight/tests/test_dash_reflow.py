# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The reflow proving test for issue #264 (plan .sdlc/plans/264.md Sec 1.5, done_when 4): a
stand-in for actually measuring the five dash pages at 1440/1024/768px in a real browser, which
this repo has no headless-browser tooling to do.

**THIS IS PROXY VERIFICATION, NOT RENDERED-PIXEL VERIFICATION.** It does not open a browser, does
not measure a single computed pixel, and cannot see an actual visual overlap, a font that renders
wider than its box, or content that overflows for a reason no static CSS rule reveals (a very
long unbreakable token in a table cell, for instance). What it CAN and DOES catch, because these
are exactly the three shapes of bug that have actually caused page-level horizontal scroll in this
codebase's own dash pages before: (1) a fixed-px container wider than a narrow viewport (a bare
`width:`/`min-width:` in px >= 768, declared outside a `@media` guard, so it never shrinks); (2) a
nav bar that cannot wrap its links onto a second line at narrow widths (`.persona-nav` missing
`flex-wrap:wrap`); (3) a wide table with no scroll escape hatch of its own, forcing the whole page
to scroll sideways instead of just the table (leadership's portfolio table, cross-functional's
gate matrix -- both must sit inside a `.table-scroll` wrapper). Say so here and in the phase
report, per the plan's own instruction not to let the word "verified" imply a browser measured
this -- it did not.
"""
import datetime
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl  # noqa: E402
from insight.dash.cross_functional import render_cross_functional_view  # noqa: E402
from insight.dash.ic import render_ic_view  # noqa: E402
from insight.dash.leadership import render_leadership_view  # noqa: E402
from insight.dash.manager import render_manager_view  # noqa: E402
from insight.dash.panel import render_panel  # noqa: E402

NOW = datetime.datetime(2026, 8, 1)
FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"

#: Reuse panel's own existing breakpoints (plan Sec 1.5): 1440 renders the wide layout, 1024 and
#: 768 both trigger the shared <=1080px collapse -- these are the pixel widths this file's static
#: scan stands in for, not widths this file itself renders at.
_PX_WIDTHS_STOOD_IN_FOR = (1440, 1024, 768)

_BARE_WIDTH_PX = re.compile(r'(?<![-\w])(?:min-)?width\s*:\s*(\d+)px')


def _extract_style(html_text):
    m = re.search(r'<style>(.*?)</style>', html_text, re.DOTALL)
    assert m, "no <style> block found"
    return m.group(1)


def _strip_media_blocks(css):
    """Remove every `@media(...){...}` block (brace-depth aware, since a media block can hold
    more than one rule) -- what remains is exactly the unguarded, always-applied CSS the bare-
    width scan below cares about."""
    out = []
    i = 0
    while True:
        idx = css.find("@media", i)
        if idx == -1:
            out.append(css[i:])
            break
        out.append(css[i:idx])
        brace_start = css.index("{", idx)
        depth = 1
        j = brace_start + 1
        while depth > 0:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def _bare_wide_px_violations(css):
    """Every `width:`/`min-width:` px literal >= 768, found OUTSIDE any `@media` block --
    mirrors test_dash_no_bare_spacing_or_font_size.py's own match-then-filter technique."""
    unguarded = _strip_media_blocks(css)
    return [int(v) for v in _BARE_WIDTH_PX.findall(unguarded) if int(v) >= 768]


@pytest.fixture
def conn(tmp_path):
    def _make(name):
        c = duckdb.connect(str(tmp_path / f"{name}.duckdb"))
        ensure_schema(c)
        return c
    return _make


def _rendered_pages(conn_factory):
    leadership_conn = conn_factory("leadership")
    load_fixture_jsonl(leadership_conn, FIXTURES / "41.jsonl")  # gives the portfolio table rows
    cross_functional_conn = conn_factory("cross_functional")
    load_fixture_jsonl(cross_functional_conn, FIXTURES / "24.jsonl")  # gives the gate matrix rows

    pages = {
        "panel": render_panel(conn_factory("panel"), metrics_dir="insight/metrics", now=NOW),
        "manager": render_manager_view(conn_factory("manager"), now=NOW)[0],
        "leadership": render_leadership_view(leadership_conn, now=NOW)[0],
        "ic": render_ic_view(conn_factory("ic"), "alice", now=NOW)[0],
        "cross-functional": render_cross_functional_view(cross_functional_conn, now=NOW)[0],
    }
    return pages


@pytest.fixture
def rendered_pages(conn):
    return _rendered_pages(conn)


# --------------------------------------------------------------------------- (a) nav can wrap

def test_persona_nav_carries_flex_wrap_on_every_page(rendered_pages):
    for name, html_text in rendered_pages.items():
        style = _extract_style(html_text)
        m = re.search(r'\.persona-nav\{([^}]*)\}', style)
        assert m, f"{name}.html's <style> has no .persona-nav rule"
        assert "flex-wrap:wrap" in m.group(1), (
            f"{name}.html's .persona-nav does not carry flex-wrap:wrap -- five nav links would "
            "force horizontal overflow at 768px instead of wrapping to a second line"
        )


# --------------------------------------------------------------------------- (b) wide tables scroll themselves

def test_leadership_portfolio_table_sits_inside_the_table_scroll_wrapper(rendered_pages):
    html_text = rendered_pages["leadership"]
    assert '<div class="table-scroll"><table>' in html_text, (
        "leadership's portfolio table is not wrapped in .table-scroll -- a wide table would force "
        "page-level horizontal scroll instead of scrolling itself"
    )


def test_cross_functional_gate_matrix_sits_inside_the_table_scroll_wrapper(rendered_pages):
    html_text = rendered_pages["cross-functional"]
    assert '<div class="table-scroll"><table>' in html_text, (
        "cross-functional's gate matrix is not wrapped in .table-scroll -- a wide table would "
        "force page-level horizontal scroll instead of scrolling itself"
    )


def test_table_scroll_wrapper_is_never_applied_directly_to_the_table_element(rendered_pages):
    """Plan Sec 1.5: the guard belongs on a WRAPPER, never on <table> itself -- overflow-x:auto on
    a display:table box behaves inconsistently across engines. The wrapper class itself must
    carry the guard (`.table-scroll{overflow-x:auto}`); no BARE `table{...overflow-x...}` rule
    (the guard applied straight to the element) may exist anywhere."""
    for name, html_text in rendered_pages.items():
        style = _extract_style(html_text)
        assert re.search(r'\.table-scroll\{[^}]*overflow-x', style), (
            f"{name}.html's .table-scroll wrapper class does not itself carry overflow-x"
        )
        bare_table_rule = re.search(r'(?<![.\w])table\{([^}]*)\}', style)
        if bare_table_rule:
            assert "overflow-x" not in bare_table_rule.group(1), (
                f"{name}.html applies overflow-x directly to a bare <table> rule"
            )


# --------------------------------------------------------------------------- (c) no unguarded wide fixed box

def test_no_page_declares_a_bare_wide_width_outside_a_media_guard(rendered_pages):
    for name, html_text in rendered_pages.items():
        style = _extract_style(html_text)
        violations = _bare_wide_px_violations(style)
        assert not violations, (
            f"{name}.html declares a bare width/min-width >= 768px outside any @media block: "
            f"{violations} -- this is exactly the shape of fixed-px container that forces "
            "page-level horizontal scroll on a narrow viewport"
        )


# --------------------------------------------------------------------------- negative control

def test_negative_control_proves_the_bare_wide_width_scan_has_teeth(rendered_pages):
    """Not shipped code -- splices a deliberately too-wide, unguarded `.wrap{min-width:1600px}`
    into a copy of one page's <style> text and proves the SAME scan used above now fails."""
    style = _extract_style(rendered_pages["panel"])
    assert not _bare_wide_px_violations(style)  # sanity: passes today

    mutated = style + "\n.wrap{min-width:1600px}\n"
    violations = _bare_wide_px_violations(mutated)
    assert 1600 in violations, (
        "fixture regressed: negative control's bare min-width no longer lands outside every "
        "@media block"
    )
