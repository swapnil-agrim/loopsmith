# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.shell (issue #127, E4.S4, Task 1). See .sdlc/plans/127.md Decision 4:
`base_style()` is the shared page-shell style extracted out of render.py/ic.py's own
near-duplicate `_STYLE` strings."""
import re

from insight.dash.colors import viz_css_vars
from insight.dash.shell import base_style

_HEX_ANYWHERE = re.compile(r'#[0-9a-fA-F]{3,6}\b')


def test_base_style_page_specific_rules_contain_no_literal_hex():
    """`viz_css_vars()` is colors.py's own token block and is, by that module's docstring, the
    ONE place a hex string is ever written -- excluded here, not re-litigated. The five rule
    groups `base_style()` itself contributes (body/h1,h2/table/th,td/.banner/footer) must
    reference only `var(--dash-...)` tokens, never a baked hex."""
    page_specific = base_style().replace(viz_css_vars(), "")
    assert not _HEX_ANYWHERE.search(page_specific)


def test_base_style_declares_both_dark_mode_scopes():
    style = base_style()
    assert "@media (prefers-color-scheme: dark)" in style
    assert '[data-theme="dark"]' in style


def test_base_style_carries_the_five_common_rule_groups():
    style = base_style()
    for selector in ("body {", "h1 {", "h2 {", "table {", "th, td {", ".banner {", "footer {"):
        assert selector in style


# --------------------------------------------------------------------------- issue #262 (D1),
# Step 9: a generalization of test_base_style_page_specific_rules_contain_no_literal_hex above --
# same "subtract viz_css_vars()'s own output" technique, scanning what remains for a bare
# font-size/spacing literal not already routed through a `var(--dash-...)` token. A small,
# explicit allowlist documents Design decision 5's named exceptions rather than silently
# special-casing them.

#: Design decision 5's named exceptions, kept as literals on purpose (not a forced fit):
#: `10px` (th/td horizontal padding, no scale neighbour within 2px), `10rem` (stat-tile
#: min-width, a component sizing constraint not a spacing-between-elements value), `100%`
#: (table width, a layout keyword/percentage, not a spacing-scale value at all).
_ALLOWLISTED_BARE_LITERALS = ("10px", "10rem", "100%")

_BARE_SIZE_LITERAL = re.compile(r'\b\d+(?:\.\d+)?(?:px|rem|em)\b')


def test_base_style_page_specific_rules_contain_no_bare_font_size_or_spacing_literal():
    page_specific = base_style().replace(viz_css_vars(), "")
    for allowed in _ALLOWLISTED_BARE_LITERALS:
        page_specific = page_specific.replace(allowed, "")
    matches = _BARE_SIZE_LITERAL.findall(page_specific)
    assert not matches, (
        f"base_style()'s page-specific rules hardcode a font-size/spacing literal not routed "
        f"through a var(--dash-...) token: {matches}"
    )
