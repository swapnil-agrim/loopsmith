# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Shared dashboard page-shell style (issue #127, E4.S4, Task 1; see .sdlc/plans/127.md
Decision 4). `insight.dash.render._STYLE` and `insight.dash.ic._STYLE` were near-duplicates --
both opened with `viz_css_vars()` then the identical `body`/`h1,h2`/`table`/`th,td`/`.banner`/
`footer` rule bodies, verified byte-identical by diffing the two strings directly. A third
near-copy for `insight/dash/manager.py` was the concrete trigger for extracting this module,
rather than repeating the pattern a third time.

`base_style()` returns exactly those five common rule-groups plus `viz_css_vars()` -- nothing
page-specific. Each page module builds its own `_STYLE` as
`f"{base_style()}\\n<page-specific rules>"`. This changes rule ORDER relative to the pre-#127
`_STYLE` strings (the shared base now precedes page-specific rules, rather than being
interleaved exactly as before) but not the selector SET, and no test in the suite depends on
`_STYLE`'s literal ordering -- verified live this session: the selector set reconstructed from
`base_style()` + each page's own extra rules is identical to the pre-migration shipped strings
for both `render.py` and `ic.py` (symmetric difference empty for both).

Kept out of `insight.dash.colors` (tokens only, by that module's own docstring) and out of
`insight.dash.charts` (chart primitives, not page shells) -- a new, single-purpose module keeps
the same boundary `.sdlc/plans/126.md` Decision 7 already drew for `ic.py`.
"""
from insight.dash.colors import viz_css_vars


def base_style():
    """The five CSS rule-groups every dash page shares, plus `viz_css_vars()` -- `body` text/
    background, `h1`/`h2` sizing, `table`/`th,td` layout, `.banner` (the cold-start/warning
    banner every page can render), and `footer` (the self-contained-page disclosure line every
    page ends with). No page-specific selector belongs here -- see this module's own docstring.

    issue #262 (D1): every font-size/spacing/border/radius literal below is retokenized onto
    `insight.dash.colors`' type/spacing scale (Migration inventory) -- a CSS-VALUE substitution
    only, no selector added/removed/renamed except the new `code {...}` rule (Decision 10's
    zero-markup-change mono win: every existing `<code>` usage across the codebase gets the mono
    treatment for free). `10px` (th/td horizontal padding) is a NAMED exception, kept literal
    (Decision 5) -- no scale step lands within 2px of it either direction."""
    return f"""
{viz_css_vars()}
body {{ font-family: var(--dash-font-sans); font-size: var(--dash-text-body); line-height: 1.5;
       margin: var(--dash-space-7); color: var(--dash-ink); background: var(--dash-surface); }}
h1 {{ font-size: var(--dash-text-title); }}
h2 {{ font-size: var(--dash-text-head); margin-top: var(--dash-space-7); }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: var(--dash-space-1) 10px;
          border-bottom: var(--dash-border-hairline) solid var(--dash-gridline);
          font-size: var(--dash-text-body); }}
.banner {{ background: var(--dash-status-warn); border: var(--dash-border-hairline) solid var(--dash-baseline);
          padding: var(--dash-space-3) var(--dash-space-4); border-radius: var(--dash-radius-sm);
          margin-bottom: var(--dash-space-6); color: var(--dash-on-status); }}
footer {{ margin-top: var(--dash-space-7); font-size: var(--dash-text-small); color: var(--dash-muted); }}
code {{ font-family: var(--dash-font-mono); font-variant-numeric: tabular-nums; }}
"""
