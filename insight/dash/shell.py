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
    page ends with). No page-specific selector belongs here -- see this module's own docstring."""
    return f"""
{viz_css_vars()}
body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 2rem; color: var(--dash-ink); background: var(--dash-surface); }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 4px 10px; border-bottom: 1px solid var(--dash-gridline);
          font-size: 13px; }}
.banner {{ background: var(--dash-status-warn); border: 1px solid var(--dash-baseline);
          padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1.5rem; color: var(--dash-on-status); }}
footer {{ margin-top: 2rem; font-size: 12px; color: var(--dash-muted); }}
"""
