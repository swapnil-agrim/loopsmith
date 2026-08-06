# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #303 [E17.S2] review fix 2: insight/web/src/app/globals.css hand-mirrors every PANEL
colour token as a Tailwind `@theme` entry (`--color-panel-X: var(--panel-X);`), so
`bg-panel-ground`/`text-panel-bone`/etc. exist as real Tailwind utility classes for future stories
(E17.S4+) to reach for -- see globals.css's own module comment for why this block is inert (but not
broken) today. Nothing enforced that hand-written block against colors.py before this test: Tailwind
v4 tree-shakes any UNREFERENCED `--color-panel-*` value out of the built CSS (globals.css's own
comment), so a future key added to, removed from, or renamed in colors.py's PANEL/PANEL_ALPHA/
PANEL_MIX would desync from `@theme` with nothing going red -- undercutting done-when 1's
single-source-of-truth claim for this layer.

The expected token-name set is derived directly from colors.py's own PANEL/PANEL_ALPHA/PANEL_MIX
containers -- the SAME containers insight.dash.colors.web_tokens_css() walks to build
tokens.generated.css's `:root` block (insight/tests/test_web_tokens_fresh.py already pins that
generation step byte-for-byte) -- never a hand-typed list here that could itself rot.

Deliberately EXCLUDES TYPE_SCALE (`--panel-text-*`) and the two font custom properties
(`--panel-font-sans`/`-mono`): web_tokens_css() emits colour tokens (PANEL/PANEL_ALPHA/PANEL_MIX)
and TYPE_SCALE together as one `:root` string with no clean split exposed by the generator (see
its own source -- `parts` accumulates all four groups before `web_tokens_css()` returns), so this
test does not call it and slice the result; instead it derives the colour-only name set directly
from the three colour containers colors.py itself defines, which IS the clean split -- see
colors.py's own PANEL/PANEL_ALPHA/PANEL_MIX declarations. globals.css's `@theme` block is, by its
own comment, a colour-only mapping for Tailwind utility classes; type-scale/font values have no
Tailwind-utility use case here and are correctly absent from it."""
import pathlib
import re

from insight.dash.colors import PANEL, PANEL_ALPHA, PANEL_MIX

GLOBALS_CSS = pathlib.Path(__file__).resolve().parents[1] / "web" / "src" / "app" / "globals.css"

#: One line inside the `@theme { ... }` block: `--color-panel-X: var(--panel-Y);`
_THEME_LINE_RE = re.compile(r"--color-panel-([\w-]+):\s*var\(--panel-([\w-]+)\);")


def _expected_token_names():
    """The colour-only token-name set `@theme` is meant to cover -- PANEL + PANEL_ALPHA (bare
    keys) + PANEL_MIX (index-named "mix-0".."mix-N"), the exact naming scheme
    colors.web_tokens_css() uses for these same three containers. Never TYPE_SCALE, never the
    font custom properties -- see this module's own docstring for why those are out of scope."""
    names = set(PANEL) | set(PANEL_ALPHA)
    names |= {f"mix-{i}" for i in range(len(PANEL_MIX))}
    return names


def _theme_block():
    text = GLOBALS_CSS.read_text(encoding="utf-8")
    m = re.search(r"@theme\s*\{(.*?)\}", text, re.S)
    assert m, f"could not find an `@theme {{ ... }}` block in {GLOBALS_CSS}"
    return m.group(1)


def _theme_mapping():
    """{--color-panel-X name -> the var(--panel-Y) referent name} for every line in the @theme
    block, in source order, so a duplicate or malformed line is visible via dict overwrite/length
    mismatch against the raw line count."""
    block = _theme_block()
    lines = [line for line in block.splitlines() if line.strip()]
    mapping = {}
    for line in lines:
        m = _THEME_LINE_RE.search(line)
        assert m, (
            f"{GLOBALS_CSS}'s @theme block has a line that doesn't match the expected "
            f"`--color-panel-X: var(--panel-X);` shape: {line.strip()!r}"
        )
        mapping[m.group(1)] = m.group(2)
    assert len(mapping) == len(lines), (
        f"{GLOBALS_CSS}'s @theme block has a duplicate --color-panel-* declaration -- "
        f"{len(lines)} lines but only {len(mapping)} distinct names"
    )
    return mapping


def test_theme_block_covers_exactly_the_panel_colour_tokens_from_colors_py():
    expected = _expected_token_names()
    actual = set(_theme_mapping())
    missing = expected - actual
    extra = actual - expected
    assert not missing, (
        f"{GLOBALS_CSS}'s @theme block is missing Tailwind mappings for {sorted(missing)} -- "
        "colors.py's PANEL/PANEL_ALPHA/PANEL_MIX gained a key that globals.css's hand-written "
        "@theme block was never updated for. Add `--color-panel-X: var(--panel-X);` for each "
        "missing name."
    )
    assert not extra, (
        f"{GLOBALS_CSS}'s @theme block declares {sorted(extra)}, which no longer exist in "
        "colors.py's PANEL/PANEL_ALPHA/PANEL_MIX -- remove the stale `--color-panel-X` line(s), "
        "or colors.py's key was renamed and this line needs to follow it."
    )


def test_every_theme_line_references_the_matching_panel_custom_property():
    """Each `--color-panel-X: var(--panel-Y);` line must have X == Y -- the whole point of this
    block is to expose tokens.generated.css's own `--panel-X` custom properties as Tailwind
    utilities of the SAME name, never a renamed or mismatched one."""
    mapping = _theme_mapping()
    mismatched = {name: referent for name, referent in mapping.items() if name != referent}
    assert not mismatched, (
        f"{GLOBALS_CSS}'s @theme block has line(s) whose `--color-panel-X` name does not match "
        f"its `var(--panel-Y)` referent: {mismatched} -- each line must read "
        "`--color-panel-X: var(--panel-X);` with the same name on both sides"
    )


def test_negative_control_a_renamed_colors_py_key_would_trip_the_coverage_check():
    """Not shipped code -- proves the coverage check above is not vacuous by simulating exactly
    the desync scenario this test exists to catch: colors.py gains a key that globals.css's
    hand-written @theme block was never updated for."""
    expected = _expected_token_names() | {"a-brand-new-key-not-in-globals-css"}
    actual = set(_theme_mapping())
    assert expected - actual, "negative control did not reproduce a missing-key desync"
