# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Issue #262 (D1), Step 9: the per-file sibling of test_dash_shell.py's own
test_base_style_page_specific_rules_contain_no_bare_font_size_or_spacing_literal, one case per
page module that carries its own page-specific `_STYLE` CSS (render.py/manager.py/leadership.py/
ic.py) plus charts.py's own SVG `font-size="..."` attributes (that module has no `_STYLE` string
at all -- its "page-specific text" is the `<text font-size="...">` attributes emitted by its
render_* functions). Kept in one file rather than duplicated five times across five existing test
modules, so the allowlist documenting Design decision 5's named exceptions lives in one place --
test_dash_shell.py imports `_ALLOWLISTED_BARE_LITERALS`/`_BARE_SIZE_LITERAL`/
`_assert_no_bare_literal` from here rather than redefining them (PR review finding 3 on issue
#262/D1: an earlier draft's docstring claimed centralization while a second, independently
maintained copy lived in test_dash_shell.py)."""
import re

from insight.dash import ic, leadership, manager, render
from insight.dash.colors import viz_css_vars

#: Design decision 5's named exceptions, kept as literals on purpose (not a forced fit):
#: `10px` (shell.py's th/td horizontal padding, inherited via base_style() -- not re-declared by
#: any of these four page-specific _STYLE blocks, but harmless to allow here too), `10rem`
#: (`.stat-tile`'s min-width -- a component sizing constraint, not a spacing-between-elements
#: value; declared by ic.py/leadership.py/manager.py), `100%` (table width, a layout keyword, not
#: a spacing-scale value at all).
_ALLOWLISTED_BARE_LITERALS = ("10px", "10rem", "100%")

_BARE_SIZE_LITERAL = re.compile(r'\b\d+(?:\.\d+)?(?:px|rem|em)\b')


def _page_specific_style(module):
    return module._STYLE.replace(viz_css_vars(), "")


def _assert_no_bare_literal(text, label):
    # PR review finding 2 on issue #262/D1: matching literals FIRST and then filtering each
    # *matched* literal by exact equality is boundary-aware. The earlier approach pre-stripped
    # the haystack with plain `str.replace(allowed, "")`, which is not boundary-aware --
    # "210px".replace("10px", "") leaves "2", destroying the "px" the violation regex needs, so
    # a real "210px" violation silently vanished whenever an allowlisted literal ("10px") was one
    # of its substrings. Repro: "th, td { padding: 4px 210px; }" must report both "4px" and
    # "210px" -- test_a_literal_containing_an_allowlisted_substring_is_still_caught below pins it.
    matches = [
        m for m in _BARE_SIZE_LITERAL.findall(text) if m not in _ALLOWLISTED_BARE_LITERALS
    ]
    assert not matches, f"{label} hardcodes a font-size/spacing literal outside the token system: {matches}"


def test_render_style_contains_no_bare_font_size_or_spacing_literal():
    _assert_no_bare_literal(_page_specific_style(render), "render.py's _STYLE")


def test_manager_style_contains_no_bare_font_size_or_spacing_literal():
    _assert_no_bare_literal(_page_specific_style(manager), "manager.py's _STYLE")


def test_leadership_style_contains_no_bare_font_size_or_spacing_literal():
    _assert_no_bare_literal(_page_specific_style(leadership), "leadership.py's _STYLE")


def test_ic_style_contains_no_bare_font_size_or_spacing_literal():
    _assert_no_bare_literal(_page_specific_style(ic), "ic.py's _STYLE")


def test_charts_module_declares_no_bare_svg_font_size_attribute():
    """charts.py has no `_STYLE` string at all -- its page-specific text is the `font-size="..."`
    attribute on every `<text>` element its render_* functions emit. All seven should now read
    `font-size="var(--{id_prefix}-text-...)"`, never a bare digit. This intentionally does NOT
    scan the whole module for any bare px/rem/em literal -- charts.py's module-level pixel-
    geometry constants (`_ABSENT_W`, `_AGING_PAD_LEFT`, etc.) are Design decision 5's explicit
    out-of-scope exception (chart-primitive canvas geometry, not CSS typography/spacing), left to
    D4 to reconcile with the SPACE scale, if ever."""
    import insight.dash.charts as charts_module
    text = charts_module.__file__ and open(charts_module.__file__, encoding="utf-8").read()
    literal_font_size = re.findall(r'font-size="(\d[^"]*)"', text)
    assert not literal_font_size, (
        f"charts.py has a bare, un-tokenized SVG font-size attribute: {literal_font_size}"
    )


def test_a_literal_containing_an_allowlisted_substring_is_still_caught():
    """PR review finding 2 on issue #262/D1: the reviewer's repro. "210px" contains the
    allowlisted "10px" as a substring; a pre-strip-then-scan implementation destroys "210px"
    down to "2" before the violation regex ever sees it, so the real violation vanishes. This
    pins the fixed, boundary-aware behaviour: matched literals are filtered by exact equality
    against the allowlist, never by substring removal from the haystack."""
    css = "th, td { padding: 4px 210px; }"
    matches = [m for m in _BARE_SIZE_LITERAL.findall(css) if m not in _ALLOWLISTED_BARE_LITERALS]
    assert "210px" in matches
    assert "4px" in matches
