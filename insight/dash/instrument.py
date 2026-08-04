# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The shared instrument vocabulary (issue #264): the seven markup primitives and the generic
chrome CSS that `insight.dash.panel` originally built inline, plus (added in a later step of this
same goal) the page shell and persona nav shared by all five dash pages.

Why this module exists rather than staying inline in `panel.py`: `panel.py` proved that an
instrument-panel aesthetic -- not a stack of unstyled tables -- is what the product's central
claim (ABSENT != PASS) actually needs to read as designed. This module is that vocabulary made
reusable, so the other four persona pages can adopt the same dark-ground instrument frame without
each re-implementing masthead/section-rule/card/alert/board/footer markup from scratch.

`panel.py` keeps owning the 42-metric catalog and its own band membership (`CATALOG`, `BANDS`) --
`board()` here only lays out whatever band/cell data it is given, exactly like `readout()` and
`alert()` take already-escaped fragments from their call sites rather than owning any escaping
contract of their own.

A note on `FRAME_CSS`'s relationship to `panel.CSS`'s pre-#264 text: the rule GROUPS moved here
are reproduced byte-for-byte (no retokenized px/rem literals, no rewritten selectors). What does
change is document ORDER: `panel.py`'s original single `CSS` constant interleaved this module's
"generic chrome" rule groups with panel's own instrument furniture (`.instr`/`.ribbon`/`.tick`/
`.legend`/`.stack`/`.mixkey`) group-by-group down the file, including one shared
`@media(max-width:1080px)` block that mixed a moved selector (`.cols`, `.readouts`) with a
panel-only one (`.instr`). Splitting "generic" from "panel-only" necessarily splits that block in
two and regroups the rest -- there is no way to keep a single reusable `FRAME_CSS` constant AND
reproduce the exact original interleaving. The CSS RULES themselves are unchanged (same
selectors, same declarations, same values); only their order/grouping in the emitted `<style>`
text differs, which does not change how the page renders (none of the moved/kept selectors
conflict in specificity). See the phase report for the explicit before/after diff.
"""
import html

from insight.dash.colors import panel_css_vars


def _e(s):
    return html.escape(str(s))


# --------------------------------------------------------------------------------------------
# FRAME_CSS -- generic chrome moved byte-for-byte out of panel.py's original CSS (plan .sdlc/
# plans/264.md Sec 1.2). Every declaration below is reproduced unchanged from panel.py; only the
# module-level grouping/order differs from the pre-#264 panel.py (see module docstring).
# --------------------------------------------------------------------------------------------
FRAME_CSS = """
:root{ --s:8px }   /* spacing unit only -- every colour lives in colors.PANEL */
*{box-sizing:border-box}
body{
  margin:0; background:var(--panel-ground); color:var(--panel-bone);
  font-family:'Atkinson',ui-sans-serif,sans-serif; font-size:13px; line-height:1.5;
  -webkit-font-smoothing:antialiased;
  background-image:
    radial-gradient(ellipse 90% 60% at 50% -10%, var(--panel-glow), transparent 70%),
    repeating-linear-gradient(0deg, var(--panel-grain) 0 1px, transparent 1px 4px);
}
.mono{font-family:'PlexMono',ui-monospace,monospace; font-variant-numeric:tabular-nums}
.wrap{max-width:1500px; margin:0 auto; padding:calc(var(--s)*3) calc(var(--s)*4) calc(var(--s)*10)}

/* ---- masthead ------------------------------------------------------------------ */
.mast{display:flex; align-items:baseline; gap:calc(var(--s)*2); flex-wrap:wrap;
  padding-bottom:calc(var(--s)*2); border-bottom:1px solid var(--panel-rule-hard)}
.mark{font-family:'PlexMono',monospace; font-size:11px; letter-spacing:.34em;
  text-transform:uppercase; color:var(--panel-amber)}
.mast h1{font-size:26px; font-weight:400; margin:0; letter-spacing:-.015em}
.mast .meta{margin-left:auto; font-size:11px; color:var(--panel-faint); letter-spacing:.05em}

/* ---- section rule -------------------------------------------------------------- */
.rule{display:flex; align-items:center; gap:calc(var(--s)*1.5); margin:calc(var(--s)*4) 0 calc(var(--s)*1.5)}
.rule h2{font-size:11px; font-weight:400; letter-spacing:.28em; text-transform:uppercase;
  color:var(--panel-dim); margin:0; white-space:nowrap}
.rule:after{content:''; flex:1; height:1px; background:var(--panel-rule)}

/* ---- readouts ------------------------------------------------------------------ */
.readouts{display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--panel-rule);
  border:1px solid var(--panel-rule)}
.ro{background:var(--panel-panel); padding:calc(var(--s)*2.5) calc(var(--s)*2.5) calc(var(--s)*2)}
.ro .lab{font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--panel-dim)}
.ro .val{font-family:'PlexMono',monospace; font-size:44px; line-height:1.05; letter-spacing:-.035em;
  margin-top:calc(var(--s)*1.5)}
.ro .unit{font-size:15px; color:var(--panel-faint); margin-left:4px}
.ro .cov{margin-top:var(--s); font-size:10.5px; color:var(--panel-faint);
  font-family:'PlexMono',monospace; border-top:1px solid var(--panel-rule); padding-top:6px}
.ro.absent .val{font-family:'PlexMono',monospace; font-size:19px; color:var(--panel-void-ink);
  letter-spacing:.08em}
.ro.absent{background:
  repeating-linear-gradient(45deg,var(--panel-hatch-soft) 0 3px,transparent 3px 7px), var(--panel-panel)}
.cls{float:right; font-family:'PlexMono',monospace; font-size:9.5px; letter-spacing:.1em;
  color:var(--panel-faint); border:1px solid var(--panel-rule-hard); border-radius:2px; padding:1px 5px}

/* ---- two-column body ----------------------------------------------------------- */
/* `stretch`, not `start`: the rail runs taller than the chart column, and with `start` the
   difference renders as a ragged void under the last chart. Stretching lets the two chart cards
   divide the full height evenly (`grid-auto-rows:1fr`) and the charts centre in the space they
   gain, so the section bottoms out on one flat line. */
.cols{display:grid; grid-template-columns:1fr 320px; gap:calc(var(--s)*3); align-items:stretch}
.colmain{display:grid; gap:calc(var(--s)*3); grid-auto-rows:1fr}
.colmain .card{display:flex; flex-direction:column}
.colmain .card svg{margin-top:auto; margin-bottom:auto}
.colrail{display:grid; gap:calc(var(--s)*3); align-content:start}
.card{background:var(--panel-panel); border:1px solid var(--panel-rule); border-radius:3px;
  padding:calc(var(--s)*2.5)}
.card h3{margin:0 0 calc(var(--s)*.5); font-size:12.5px; font-weight:400; letter-spacing:.02em}
.card .sub{font-size:10.5px; color:var(--panel-faint); margin-bottom:calc(var(--s)*2);
  font-family:'PlexMono',monospace}

/* ---- right rail ---------------------------------------------------------------- */
.alert{border-left:2px solid var(--panel-amber); padding:calc(var(--s)*1.25) 0 calc(var(--s)*1.25) calc(var(--s)*1.5);
  margin-bottom:calc(var(--s)*2)}
.alert.crit{border-left-color:var(--panel-red)}
.alert.void{border-left-color:var(--panel-void-ink); border-left-style:dashed}
.alert .t{font-size:12px}
.alert .d{font-size:10.5px; color:var(--panel-faint); margin-top:3px; font-family:'PlexMono',monospace}

/* ---- the board: all 42, lit or dark -------------------------------------------- */
.band{margin-bottom:calc(var(--s)*2.5)}
.band .bl{font-size:10px; letter-spacing:.2em; text-transform:uppercase; color:var(--panel-faint);
  margin-bottom:var(--s)}
/* Hairlines come from each cell's own 1px ring, NOT from a gap-coloured container background.
   With a container background, the unfilled tail of the last row renders as a phantom empty cell
   -- an artifact that reads, on a board whose entire job is showing what is and isn't measured, as
   an extra unlabelled dark metric. Adjacent rings overlap inside the 1px gap and resolve to a
   single hairline, so the grid still reads as ruled while staying responsive at any column count. */
.grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(148px,1fr)); gap:1px;
  background:transparent}
.cell{background:var(--panel-raised); padding:calc(var(--s)*1.25) calc(var(--s)*1.5); min-height:62px;
  display:flex; flex-direction:column; justify-content:space-between;
  box-shadow:0 0 0 1px var(--panel-rule)}
.cell .id{font-family:'PlexMono',monospace; font-size:9.5px; color:var(--panel-faint)}
.cell .nm{font-size:11.5px; line-height:1.3; margin-top:2px}
.cell .n{font-family:'PlexMono',monospace; font-size:10px; color:var(--panel-cyan); margin-top:5px}
.cell.dark, .cell.unbuilt{
  background:repeating-linear-gradient(45deg,var(--panel-hatch) 0 3px,transparent 3px 7px), var(--panel-void);
  color:var(--panel-void-ink)}
.cell.dark .nm, .cell.unbuilt .nm{color:var(--panel-void-ink)}
.cell.dark .n, .cell.unbuilt .n{color:var(--panel-void-ink); letter-spacing:.09em}
.cell.unbuilt{opacity:.72}

footer{margin-top:calc(var(--s)*5); padding-top:calc(var(--s)*2); border-top:1px solid var(--panel-rule);
  font-size:10.5px; color:var(--panel-faint); font-family:'PlexMono',monospace}

@media (max-width:1080px){
  .cols{grid-template-columns:1fr}
  .readouts{grid-template-columns:repeat(2,1fr)}
}
@media (max-width:620px){ .readouts{grid-template-columns:1fr} .wrap{padding:calc(var(--s)*2)} }

/* ---- persona nav (issue #264, NEW) ---------------------------------------------- */
/* `flex-wrap:wrap` is what stops five nav links forcing horizontal overflow at 768px -- see
   test_dash_reflow.py, a later step of this same goal. */
.persona-nav{display:flex; flex-wrap:wrap; gap:calc(var(--s)*2.5); margin-bottom:calc(var(--s)*3);
  padding-bottom:calc(var(--s)*2); border-bottom:1px solid var(--panel-rule-hard)}
.persona-nav a{font-family:'PlexMono',monospace; font-size:11px; letter-spacing:.08em;
  text-transform:uppercase; color:var(--panel-faint); text-decoration:none;
  padding-bottom:4px; border-bottom:2px solid transparent}
.persona-nav a[aria-current="page"]{color:var(--panel-bone); border-bottom-color:var(--panel-amber)}

/* ---- generic content (issue #264, NEW) ------------------------------------------ */
/* The four persona pages stop calling shell.base_style() once migrated onto this shared frame,
   so their unstyled <h1>/<h2>/<table>/.banner/<code> need a minimal legible-on-dark-ground
   treatment here -- kept intentionally small, not a redesign of those pages' own content. */
h1{font-size:26px; font-weight:400; margin:0 0 calc(var(--s)*2); letter-spacing:-.015em}
h2{font-size:16px; font-weight:400; margin:calc(var(--s)*4) 0 calc(var(--s)*1.5)}
table{border-collapse:collapse; width:100%}
th, td{padding:var(--s) calc(var(--s)*1.5); border-bottom:1px solid var(--panel-rule);
  text-align:left; font-size:12.5px}
th{color:var(--panel-dim); font-weight:400; text-transform:uppercase; letter-spacing:.08em;
  font-size:10.5px}
.banner{background:var(--panel-panel); border:1px solid var(--panel-rule); border-radius:3px;
  padding:calc(var(--s)*2) calc(var(--s)*2.5); margin-bottom:calc(var(--s)*3)}
code{font-family:'PlexMono',monospace; font-size:.9em; color:var(--panel-cyan)}

/* ---- table-overflow guard (issue #264, NEW) -------------------------------------- */
/* Applied to a WRAPPER, never to <table> itself: `overflow-x:auto` on a `display:table` box
   behaves inconsistently across engines (plan .sdlc/plans/264.md Sec 1.5). A wide table (e.g.
   leadership's portfolio table, cross-functional's gate matrix) scrolls itself instead of
   forcing page-level horizontal scroll. */
.table-scroll{overflow-x:auto}
.table-scroll table{min-width:100%}
"""


# --------------------------------------------------------------------------------------------
# The seven markup primitives, carved out of panel.py's inline f-string markup. Each reproduces
# the exact HTML text its old call site built -- no new escaping contract (callers pass already-
# escaped fragments, exactly as panel.py's own call sites already did before this move).
# --------------------------------------------------------------------------------------------

def masthead(mark, title, meta_html=""):
    """The page banner: a small caps mark, the page's `<h1>`, and a right-aligned meta line."""
    return (f'<div class="mast">\n'
            f'  <span class="mark">{mark}</span>\n'
            f'  <h1>{title}</h1>\n'
            f'  <span class="meta mono">{meta_html}</span>\n'
            f'</div>')


def section_rule(heading):
    """A `<h2>` with a trailing hairline that runs to the edge of `.wrap`."""
    return f'<div class="rule"><h2>{heading}</h2></div>'


def card(title, subtitle, body_html):
    """A bordered content block: `<h3>` title, a small mono subtitle, then arbitrary body markup
    (an SVG, a stack of alerts, a proportion bar -- whatever the caller already built)."""
    return (f'<div class="card">\n'
            f'      <h3>{title}</h3>\n'
            f'      <div class="sub">{subtitle}</div>\n'
            f'      {body_html}\n'
            f'    </div>')


def readout(label, value, unit=None, coverage=None, cls=None, absent_reason=None):
    """One primary readout. `absent_reason` and `value` are mutually exclusive by construction --
    an absent readout renders the reason where the numeral would go, so there is no numeral to
    misread. Moved verbatim from `panel.py`'s own `_readout` (issue #264)."""
    badge = f'<span class="cls">{_e(cls)}</span>' if cls else ""
    if absent_reason is not None:
        return (f'<div class="ro absent"><div class="lab">{badge}{_e(label)}</div>'
                f'<div class="val">NO SENSOR</div>'
                f'<div class="cov">{_e(absent_reason)}</div></div>')
    u = f'<span class="unit">{_e(unit)}</span>' if unit else ""
    cov = f'<div class="cov">{_e(coverage)}</div>' if coverage else ""
    return (f'<div class="ro"><div class="lab">{badge}{_e(label)}</div>'
            f'<div class="val">{_e(value)}{u}</div>{cov}</div>')


def alert(title_html, detail_html, kind=None):
    """A right-rail attention item. `kind` is one of `None` (informational), `"crit"` (red), or
    `"void"` (dashed, absence-flavoured) -- matching panel.py's own three call sites."""
    cls = f" {kind}" if kind else ""
    return (f'<div class="alert{cls}"><div class="t">{title_html}</div>'
            f'<div class="d">{detail_html}</div></div>')


def board(bands):
    """All of a page's metrics, banded by subject. Generalized from panel.py's original
    `_board(d)`: this function only lays out `.band`/`.grid`/`.cell` markup -- the caller supplies
    `bands` as `[(band_name, [(cell_id, cell_name, state, note), ...]), ...]`, with `band_name` /
    `cell_name` / `note` already escaped exactly like every other primitive here. `panel.py` keeps
    owning the 42-metric catalog and band membership; only the rendering moved."""
    out = []
    for name, cells in bands:
        cell_html = "".join(
            f'<div class="cell {state}"><div><div class="id">{cid:02d}</div>'
            f'<div class="nm">{cname}</div></div>'
            f'<div class="n">{note}</div></div>'
            for cid, cname, state, note in cells
        )
        out.append(f'<div class="band"><div class="bl">{name}</div>'
                   f'<div class="grid">{cell_html}</div></div>')
    return "".join(out)


def footer(body_html):
    """The page's closing `<footer>`. `body_html` carries its own embedded line-continuation
    indentation exactly like panel.py's original literal footer text did."""
    return f'<footer>\n  {body_html}\n</footer>'


# --------------------------------------------------------------------------------------------
# The page shell and persona nav (issue #264, plan Sec 1.1/1.4) -- shared by all five dash pages.
# --------------------------------------------------------------------------------------------

#: (key, label, href). `href` is a bare relative filename (not an absolute path) so the built
#: pages work when opened directly from `file://`, matching every other self-contained-page
#: guarantee in this codebase (see `insight.dash.render.assert_self_contained`).
NAV_ITEMS = (
    ("panel", "Delivery", "panel.html"),
    ("manager", "Manager", "manager.html"),
    ("leadership", "Leadership", "leadership.html"),
    ("ic", "IC", "ic.html"),
    ("cross-functional", "Cross-functional", "cross-functional.html"),
)


def persona_nav(current):
    """One `<a>` per `NAV_ITEMS`, the current page's own link marked `aria-current="page"` so
    assistive tech and `[aria-current="page"]` styling both pick it out. Raises on an unknown
    `current` key rather than silently rendering a nav with no page marked current."""
    keys = [key for key, _, _ in NAV_ITEMS]
    if current not in keys:
        raise ValueError(f"persona_nav: unknown current page {current!r}, expected one of {keys}")
    links = []
    for key, label, href in NAV_ITEMS:
        current_attr = ' aria-current="page"' if key == current else ""
        links.append(f'<a href="{href}"{current_attr}>{label}</a>')
    return f'<nav class="persona-nav">{"".join(links)}</nav>'


def page_open(title, current, extra_css=""):
    """Doctype through the opening `<div class="wrap">` plus the persona nav -- every dash page's
    shared head/shell. `title`/`current`/`extra_css` are PRESENTATIONAL ARGS ONLY (all `str`),
    never a data value: this is what keeps the shared shell from becoming a shared data path
    (done_when 6). Head order is fixed: `<meta charset="utf-8">`, the viewport meta, `<title>`,
    then `<style>`."""
    css = panel_css_vars() + FRAME_CSS + extra_css
    return f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>{css}</style></head>
<body class="viz-root"><div class="wrap">
{persona_nav(current)}
"""


def page_close():
    """The mirror of `page_open()` -- closes `.wrap`, `<body>`, `<html>`."""
    return "</div></body></html>"
