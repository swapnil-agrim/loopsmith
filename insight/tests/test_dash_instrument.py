# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Smoke tests for insight.dash.instrument (issue #264, plan .sdlc/plans/264.md Step 2/4): one
case per primitive, proving each renders the markup shape its old panel.py call site relied on.
Not a re-test of panel.py's own content -- insight/tests/test_dash_panel_absence.py and the
manager content-pin (test_dash_manager.py) already pin the composed page output; this file only
pins each primitive's own contract in isolation."""
import re

import pytest

from insight.dash.instrument import (
    FRAME_CSS,
    NAV_ITEMS,
    alert,
    board,
    card,
    footer,
    masthead,
    page_close,
    page_open,
    persona_nav,
    readout,
    section_rule,
)


def test_masthead_renders_mark_title_and_meta():
    html_text = masthead("LoopSmith Insight", "Delivery", "built today")
    assert '<span class="mark">LoopSmith Insight</span>' in html_text
    assert "<h1>Delivery</h1>" in html_text
    assert '<span class="meta mono">built today</span>' in html_text


def test_section_rule_wraps_heading_in_h2():
    assert section_rule("Flow") == '<div class="rule"><h2>Flow</h2></div>'


def test_card_renders_title_subtitle_and_body():
    html_text = card("Title", "Subtitle", "<p>body</p>")
    assert '<div class="card">' in html_text
    assert "<h3>Title</h3>" in html_text
    assert '<div class="sub">Subtitle</div>' in html_text
    assert "<p>body</p>" in html_text


def test_readout_renders_a_value_with_unit_and_coverage():
    html_text = readout("Goals landed", 5, unit="goals", coverage="5/5", cls="C1")
    assert '<div class="val">5<span class="unit">goals</span></div>' in html_text
    assert '<div class="cov">5/5</div>' in html_text
    assert '<span class="cls">C1</span>' in html_text


def test_readout_absent_renders_no_sensor_and_never_a_numeral():
    html_text = readout("Goals landed", 999, coverage="999/999", absent_reason="nothing ingested")
    assert '<div class="val">NO SENSOR</div>' in html_text
    assert "999" not in html_text
    assert '<div class="cov">nothing ingested</div>' in html_text


def test_alert_default_kind_carries_no_class_suffix():
    html_text = alert("Title", "Detail")
    assert html_text == '<div class="alert"><div class="t">Title</div><div class="d">Detail</div></div>'


def test_alert_crit_and_void_kinds_add_the_expected_class():
    assert '<div class="alert crit">' in alert("T", "D", "crit")
    assert '<div class="alert void">' in alert("T", "D", "void")


def test_board_renders_one_band_and_grid_per_entry():
    bands = [("Flow", [(1, "Throughput", "live", "3 rows"), (2, "Cycle time", "dark", "NO DATA")])]
    html_text = board(bands)
    assert '<div class="band"><div class="bl">Flow</div>' in html_text
    assert '<div class="cell live">' in html_text
    assert '<div class="id">01</div>' in html_text
    assert '<div class="nm">Throughput</div>' in html_text
    assert '<div class="cell dark">' in html_text
    assert '<div class="n">NO DATA</div>' in html_text


def test_footer_wraps_body_html():
    assert footer("hello") == "<footer>\n  hello\n</footer>"


def test_frame_css_declares_no_literal_hex():
    """Belt-and-braces alongside test_dash_no_hex_outside_tokens.py, which already parametrizes
    over every insight/dash/*.py module including this one."""
    assert not re.findall(
        r'(?:fill|stroke|color|background(?:-color)?|border(?:-color)?)\s*[:=]\s*'
        r'["\']?#[0-9a-fA-F]{3,8}\b', FRAME_CSS, re.IGNORECASE,
    )


# --------------------------------------------------------------------------------------- nav/shell

def test_nav_items_cover_the_five_built_pages_with_bare_relative_hrefs():
    hrefs = [href for _, _, href in NAV_ITEMS]
    assert hrefs == [
        "panel.html", "manager.html", "leadership.html", "ic.html", "cross-functional.html",
    ]
    for href in hrefs:
        assert "/" not in href  # bare relative filename -- must work from file://


def test_persona_nav_marks_exactly_one_link_as_current():
    html_text = persona_nav("manager")
    assert html_text.count('aria-current="page"') == 1
    assert '<a href="manager.html" aria-current="page">Manager</a>' in html_text
    assert '<a href="panel.html">Delivery</a>' in html_text  # not current -- no aria-current


def test_persona_nav_raises_on_an_unknown_current_key():
    with pytest.raises(ValueError):
        persona_nav("nonexistent-page")


def test_page_open_emits_head_in_the_required_order_and_current_page_nav():
    html_text = page_open("My Title", current="ic", extra_css=".x{color:red}")
    charset_pos = html_text.index('<meta charset="utf-8">')
    viewport_pos = html_text.index('<meta name="viewport"')
    title_pos = html_text.index("<title>")
    style_pos = html_text.index("<style>")
    assert charset_pos < viewport_pos < title_pos < style_pos
    assert "<title>My Title</title>" in html_text
    assert 'data-theme="dark"' in html_text
    assert '<body class="viz-root"><div class="wrap">' in html_text
    assert ".x{color:red}" in html_text
    assert '<a href="ic.html" aria-current="page">IC</a>' in html_text


def test_page_open_escapes_the_title():
    html_text = page_open("<script>", current="panel")
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_page_close_closes_wrap_body_html():
    assert page_close() == "</div></body></html>"
