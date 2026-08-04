# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Shared design tokens for every chart primitive in insight/dash/charts.py, and for the
migrated badge/dot CSS in insight/dash/render.py (issue #125, E4.S2, Task 1; see
.sdlc/plans/125.md Decision 2). Pure stdlib -- no `import duckdb`, same posture as
insight.gaps.severity.

NO LITERAL HEX IN ANY MARK (Blocking 2's structural fix, .sdlc/plans/125.md Decision 2). This
module is the ONLY place a hex string is ever written. Every `render_*` function in charts.py,
and the migrated `_STYLE` in render.py, reference a CSS custom property (`var(--dash-cat-0)`
etc.) declared by `viz_css_vars()` below -- never a baked hex -- because `insight dash` emits one
static HTML file with no server round-trip: a baked attribute cannot respond to
`@media`/`[data-theme]` no matter how correct this module's own values are. `viz_css_vars()`
declares a light-mode default plus BOTH a `@media (prefers-color-scheme: dark)` scope and a
`[data-theme="dark"]` scope for every token, so a manual light/dark toggle wins over the OS
preference in both directions.

ABSENT is not a status hue -- it means "not measured," never a fifth colour competing with
PASS/WARN/FAIL (Decision 5; see the module-level `assert` below, the durable drift guard).

SEQUENTIAL_BLUE is mode-aware (`_LIGHT`/`_DARK`, anchor flipped) -- baking the light ramp's hex
directly into a dark-mode render put the *oldest* claim (the one that matters most) at 1.46:1
contrast while the *youngest* sat at 5.83:1, exactly backwards (Blocking 2's repro). Both lists
walk the SAME 10 named steps from palette.md's 100-700 table in opposite directions -- no new hex
invented -- and both are independently pinned monotonically increasing in contrast against their
own surface by insight/tests/test_dash_colors.py.

The ABSENT texture overlay (the 45-degree hatch `status_mark()` layers on top of the ABSENT dot)
is genuinely OPT-IN, never on by default (Blocking 3's fix): `.dash-texture-a11y` is `opacity: 0`
by default and only flips to `opacity: 1` under `@media (forced-colors: active), print`.

---

issue #262 (D1) extends this module with the type scale, spacing scale, the `DATA_STATE`
vocabulary and its `not_measured_block()`/`not_measured_svg()` primitives, and a mechanical WCAG
contrast registry -- see .sdlc/plans/262.md for the full decision record, cited by Decision
number in the comments below rather than re-derived inline. `STATUS`/`CATEGORICAL`/
`SEQUENTIAL_BLUE_*`/`CHROME` above are UNCHANGED by this extension -- same keys, same values, same
drift guard. `DATA_STATE` is a second, disjoint vocabulary (lowercase keys vs. STATUS's uppercase
ones) describing *treatment*, not colour -- colour stays STATUS's one legitimate "colour signals
something" budget (Decision 7).

The two embedded WOFF2 font payloads live in the sibling `insight/dash/fonts.py`, imported below
and never re-exported raw -- `fonts.py` holds base64 *data*, every actual design decision (which
font, what size, what CSS variable name) stays here (Decision 2).
"""
import html
import re
import sys

from insight.dash.fonts import FONT_MONO_WOFF2_BASE64, FONT_SANS_WOFF2_BASE64
from insight.gaps.severity import SEVERITY_ORDER  # never re-derived; KeyError if it drifts

STATUS = {
    "PASS":   {"light": "#0ca30c", "dark": "#0ca30c", "icon": "+", "label": "PASS"},
    "WARN":   {"light": "#fab219", "dark": "#fab219", "icon": "!", "label": "WARN"},
    "FAIL":   {"light": "#d03b3b", "dark": "#d03b3b", "icon": "x", "label": "FAIL"},
    # ABSENT is NOT a status hue -- it means "not measured". Neutral ink, never a 5th competing
    # colour alongside PASS/WARN/FAIL. See module docstring. (Texture is a SEPARATE, opt-in
    # channel -- see status_mark() below, not baked into this token.)
    "ABSENT": {"light": "#898781", "dark": "#898781", "icon": "·", "label": "ABSENT"},
}
assert set(STATUS) == set(SEVERITY_ORDER)  # module-import-time drift guard

CATEGORICAL = [  # fixed order -- the CVD-safety mechanism itself, never reordered/cycled
    {"name": "blue",    "light": "#2a78d6", "dark": "#3987e5"},
    {"name": "orange",  "light": "#eb6834", "dark": "#d95926"},
    {"name": "aqua",    "light": "#1baf7a", "dark": "#199e70"},
    {"name": "yellow",  "light": "#eda100", "dark": "#c98500"},
    {"name": "magenta", "light": "#e87ba4", "dark": "#d55181"},
    {"name": "green",   "light": "#008300", "dark": "#008300"},
    {"name": "violet",  "light": "#4a3aa7", "dark": "#9085e9"},
    {"name": "red",     "light": "#e34948", "dark": "#e66767"},
]
ALL_PAIRS_CAP = 3  # scatter/CFD-lane forms; past this, fold to "Other" or facet (never a 4th)

# Ordinal/sequential magnitude ramp (aging-WIP age). BUCKET INDEX 0..9 is mode-agnostic (the
# same age maps to the same bucket in both modes) -- only the HEX per bucket differs per mode,
# because the sequential ramp's anchor flips in dark: on a light surface the receding/
# least-prominent end is LIGHT (near-white); on a dark surface the receding end must be DARK
# (near-black) instead, or the ranking a reader sees inverts. Both lists are the SAME 10 named
# steps, just walked in opposite directions -- no new hex invented.
SEQUENTIAL_BLUE_LIGHT = [  # bucket 0 (youngest, recedes) -> 9 (oldest, prominent); steps 250->700
    "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
    "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]
SEQUENTIAL_BLUE_DARK = [  # same bucket order; steps 600->150 -- anchor flipped, floor still met
    "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5",
    "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#b7d3f6",
]

CHROME = {
    "surface":     {"light": "#fcfcfb", "dark": "#1a1a19"},
    "ink":         {"light": "#0b0b0b", "dark": "#ffffff"},
    "ink2":        {"light": "#52514e", "dark": "#c3c2b7"},   # secondary -- body/ABSENT text
    "muted":       {"light": "#898781", "dark": "#898781"},   # axis ticks, glyphs -- NOT body text
    "gridline":    {"light": "#e1e0d9", "dark": "#2c2c2a"},
    "baseline":    {"light": "#c3c2b7", "dark": "#383835"},
    "delta_good":  {"light": "#006300", "dark": "#0ca30c"},   # dedicated success-text token
}

# --------------------------------------------------------------------------- type scale (D1,
# Decision 4): 8 steps anchored to the 8 pixel sizes actually rendered across insight/dash/*.py
# today, not a fresh geometric ideal -- see .sdlc/plans/262.md Decision 4 for the full per-token
# rationale, including the one deliberate 13px->body(14px) collapse.
TYPE_SCALE = {
    "micro":   "0.625rem",   # 10px -- SVG axis labels
    "caption": "0.6875rem",  # 11px -- SVG chart labels, status_mark()'s own text
    "small":   "0.75rem",    # 12px -- stat-tile label/delta, footer, gap-card meta
    "body":    "0.875rem",   # 14px -- shell body; also absorbs the 13px collapse
    "subhead": "1rem",       # 16px -- h3, .gap-card-what
    "head":    "1.125rem",   # 18px -- h2 (was 1.1rem/17.6px)
    "title":   "1.375rem",   # 22px -- h1 (was 1.4rem/22.4px)
    "display": "1.625rem",   # 26px -- .stat-tile-value (was 1.6rem/25.6px)
}

# --------------------------------------------------------------------------- spacing scale (D1,
# Decision 5): 7 steps, a 4px unit, covering every rem literal in use today exactly except two
# sub-2px roundings (both named, with their exact delta, at each call site in the migration).
SPACE = {
    1: "0.25rem",  # 4px
    2: "0.5rem",   # 8px
    3: "0.75rem",  # 12px
    4: "1rem",     # 16px
    5: "1.25rem",  # 20px
    6: "1.5rem",   # 24px
    7: "2rem",     # 32px
}

RADIUS_SM = "6px"        # the one border-radius value in use today, named not invented
BORDER_HAIRLINE = "1px"  # the one border-width value in use today

# ---------------------------------------------------------------- instrument panel (panel.py)
# A dark-ground ramp for `insight.dash.panel`, which composes an instrument rather than a
# document. Declared here, not in panel.py, because this module is the single source of colour
# truth for insight/dash/ -- a rule `test_dash_no_hex_outside_tokens.py` enforces, and which this
# palette was caught violating on its first run.
#
# Dark-only, deliberately, and NOT a second mode of the light chart palette above. Those tokens
# are tuned for ink-on-paper charts and have no dark ramp; forking them would mean overriding
# nearly every value. An instrument panel that flipped to a white ground would also lose the one
# thing the design depends on -- a lit gauge against a dark bezel.
#
# The `void-*` tokens are the load-bearing ones and are deliberately ACHROMATIC: pure neutrals
# with no hue at all. That is what stops an unmeasured cell from reading as a status colour on a
# greyscale print or to a colour-blind reader. Absence must differ from a reading in LIGHTNESS and
# TEXTURE, never in hue alone -- so never give these a tint, however subtle.
PANEL = {
    "ground": "#080b0c",   # page bezel
    "panel": "#0e1315",    # card face
    "raised": "#141a1c",   # board cell face
    "bone": "#e9e3d6",     # primary type; warm, not pure white, to cut halation on dark
    "dim": "#8b9599",      # secondary type
    "faint": "#5a6467",    # tertiary type / axis labels
    "amber": "#ffa629",    # the signal accent -- annunciator, p50 marker, bar fill
    "amber-deep": "#a8641a",  # bar-gradient foot
    "cyan": "#5ce0b0",     # a live reading
    "cyan-deep": "#2e8f6d",  # live-tick gradient foot
    "red": "#ff5f52",      # breach / p85 marker
    "void": "#171d1f",     # ABSENT fill -- achromatic, see above
    "void-ink": "#5a6467",  # ABSENT type -- achromatic, see above
}

# Event-mix stack colours, fixed order. Reuses the panel's own accents rather than importing the
# light CATEGORICAL ramp, whose mid-tones vanish against a #080b0c ground.
PANEL_MIX = ["#5ce0b0", "#ffa629", "#5aa9d6", "#ff5f52", "#b98ce0", "#d6b45a", "#7f8b8f"]

# issue #265 (D4), Design 1: the panel ground's own magnitude ramp for render_aging_wip's age
# bucket 0..9, sourced from colors.PANEL* (not CATEGORICAL/SEQUENTIAL_BLUE) so the panel ground
# has exactly one palette. Linear RGB interpolation between PANEL["raised"] (#141a1c, board-cell
# face -- the receding end, intentionally near-invisible against the panel ground, mirroring
# SEQUENTIAL_BLUE_DARK's own intentionally low-contrast floor) and PANEL["amber"] (#ffa629,
# panel.py's own established "bar fill" accent -- see _bars()'s amber/amber-deep gradient), 10
# steps. Computed once via plain linear interpolation (verified this session: contrast against
# PANEL["panel"] is 1.06, 1.31, 1.68, 2.22, 2.92, 3.85, 4.93, 6.26, 7.77, 9.57 -- strictly
# monotonically increasing, same shape as SEQUENTIAL_BLUE_DARK's own pinned ramp) and pinned as
# literals, not computed at import time -- matching this module's own static-token convention for
# SEQUENTIAL_BLUE_LIGHT/_DARK. The ONE new hex literal this plan introduces; derived math over two
# existing tokens, not an independent colour pick.
PANEL_SEQ = [
    "#141a1c", "#2e2a1d", "#48391f", "#624920", "#7c5822",
    "#976823", "#b17725", "#cb8726", "#e59628", "#ffa629",
]

# Hairlines and textures are expressed as alpha over the ground so they hold up on every card
# elevation without a per-surface value.
PANEL_ALPHA = {
    "rule": "rgba(233,227,214,.10)",
    "rule-hard": "rgba(233,227,214,.20)",
    "void-edge": "rgba(233,227,214,.16)",
    "grid": "rgba(233,227,214,.09)",
    "hatch": "rgba(233,227,214,.03)",
    "hatch-soft": "rgba(233,227,214,.028)",
    "grain": "rgba(233,227,214,.014)",   # the body's fine scanline texture
    "glow": "rgba(255,166,41,.07)",      # the masthead's warm bloom; the one tinted alpha
}


def panel_css_vars(prefix="panel"):
    """Emit the instrument palette as CSS custom properties, plus the two embedded @font-face
    rules. `insight.dash.panel` references `var(--panel-*)` for every colour it draws -- including
    inline-SVG `fill`/`stroke`, which resolve custom properties exactly as CSS does -- so the
    module itself contains no colour literal.

    issue #265 (D4), Design 1: also emits the chart-role vocabulary `insight.dash.charts`' five
    `render_*` primitives read when called with `id_prefix="panel"` -- `ink`/`ink2`/`muted`
    (text), `baseline`/`gridline` (hairlines), `surface` (cutout stroke), `delta_good`/
    `status-fail` (stat-tile direction glyphs), `cat-0..N-1` (categorical, sourced from
    PANEL_MIX -- one fewer slot than CATEGORICAL, see Design 1a), `seq-0..9` (magnitude ramp,
    PANEL_SEQ), plus the mode-invariant `text-*`/`space-*`/`radius-sm`/`border-hairline` scales
    verbatim from TYPE_SCALE/SPACE/RADIUS_SM/BORDER_HAIRLINE (unchanged from viz_css_vars()'s own
    invariant block). Every source is PANEL*/the mode-invariant scales -- never CATEGORICAL/
    SEQUENTIAL_BLUE/CHROME -- so the panel ground keeps exactly one palette (done_when 1)."""
    parts = [f"--{prefix}-{k}: {v};" for k, v in PANEL.items()]
    parts += [f"--{prefix}-{k}: {v};" for k, v in PANEL_ALPHA.items()]
    parts += [f"--{prefix}-mix-{i}: {c};" for i, c in enumerate(PANEL_MIX)]
    parts.append(f"--{prefix}-font-sans: {FONT_SANS_STACK};")
    parts.append(f"--{prefix}-font-mono: {FONT_MONO_STACK};")
    # --------------------------------------------------------------- chart-role layer (issue #265)
    parts.append(f"--{prefix}-ink: {PANEL['bone']};")
    parts.append(f"--{prefix}-ink2: {PANEL['dim']};")
    parts.append(f"--{prefix}-muted: {PANEL['faint']};")
    parts.append(f"--{prefix}-baseline: {PANEL_ALPHA['rule-hard']};")
    parts.append(f"--{prefix}-gridline: {PANEL_ALPHA['grid']};")
    parts.append(f"--{prefix}-surface: {PANEL['panel']};")
    parts.append(f"--{prefix}-delta_good: {PANEL['cyan']};")
    parts.append(f"--{prefix}-status-fail: {PANEL['red']};")
    parts += [f"--{prefix}-cat-{i}: {c};" for i, c in enumerate(PANEL_MIX)]
    parts += [f"--{prefix}-seq-{i}: {c};" for i, c in enumerate(PANEL_SEQ)]
    parts += [f"--{prefix}-text-{k}: {v};" for k, v in TYPE_SCALE.items()]
    parts += [f"--{prefix}-space-{k}: {v};" for k, v in SPACE.items()]
    parts.append(f"--{prefix}-radius-sm: {RADIUS_SM};")
    parts.append(f"--{prefix}-border-hairline: {BORDER_HAIRLINE};")
    return f""":root {{ color-scheme: dark; {" ".join(parts)} }}
@font-face {{ font-family: "Atkinson Hyperlegible"; font-style: normal; font-weight: 400;
  font-display: swap; src: url(data:font/woff2;base64,{FONT_SANS_WOFF2_BASE64}) format("woff2"); }}
@font-face {{ font-family: "IBM Plex Mono"; font-style: normal; font-weight: 400;
  font-display: swap; src: url(data:font/woff2;base64,{FONT_MONO_WOFF2_BASE64}) format("woff2"); }}
"""

# A distinctive humanist sans (Atkinson Hyperlegible) for prose/headings, a mono (IBM Plex Mono)
# for every number/identifier/timestamp/provenance line (spec Sec.3) -- explicitly NOT
# -apple-system as the final answer. Both embedded via @font-face in viz_css_vars() below,
# base64 payloads from the sibling fonts.py; the system-stack tail is the graceful-degradation
# fallback while the embedded face loads, never the intended steady state.
FONT_SANS_STACK = '"Atkinson Hyperlegible", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
FONT_MONO_STACK = '"IBM Plex Mono", ui-monospace, SFMono-Regular, Consolas, monospace'

# --------------------------------------------------------------------------- DATA_STATE (D1,
# Decision 7): a vocabulary of TREATMENT, not colour -- deliberately distinct in shape from
# STATUS (uppercase keys, a colour-per-key dict). measured/empty_result document an
# already-correct existing pattern (coverage_denominator_html(), the *_fmt_*_or_absent helpers);
# not_measured is the genuinely new primitive -- see not_measured_block()/not_measured_svg()
# below. No disjointness assert against STATUS: the two vocabularies' keys are disjoint by an
# unrelated, pre-existing uppercase/lowercase naming convention alone, so a guard here could
# never fire (Decision 7's own PLAN-REVIEW FIX).
DATA_STATE = {
    "measured": {
        "numeral": True,        # renders the real value as a plain numeral
        "border": "none",
        "texture": False,
    },
    "empty_result": {
        "numeral": True,        # ALSO a plain numeral -- "0" IS the measurement (spec Sec.2:
                                 # "0 of 39 ... visually a measurement, because it is one")
        "border": "none",
        "texture": False,
        "denominator": True,    # the ONE structural difference from "measured" -- always paired
                                 # with coverage_denominator_html()'s own denominator shape
    },
    "not_measured": {
        "numeral": False,       # NEVER a numeral (spec Sec.2, verbatim, twice)
        "border": "dashed",
        "texture": True,        # the NEW, default-visible hatch -- distinct from the existing
                                 # OPT-IN .dash-texture-a11y on STATUS["ABSENT"]
        "label": "not measured",
        "provenance": True,     # a mandatory monospace line naming the missing writer
    },
}


def viz_css_vars(prefix="dash"):
    """Emit ONE light-default + dark-scoped CSS custom-property block declaring a named var for
    EVERY token a chart mark can reference (status-*, cat-0..7, seq-0..9, chrome roles) --
    palette.md's documented @media+[data-theme] double-scope shape (:not()+:where() guards so a
    manual light toggle beats OS-dark). Also declares the ABSENT texture overlay's opt-in gate:
    `.dash-texture-a11y` is invisible by default and only shown under `forced-colors`/print --
    "never on by default," per anti-patterns.md/marks-and-anatomy.md. One call, shared by every
    chart's <style> and by render.py's migrated _STYLE (Task 7).

    issue #262 (D1) extends this with the type scale, spacing scale, radius/border-width, the two
    embedded font stacks, and the not-measured primitive's CSS -- all mode-INVARIANT (same value
    in light and dark), so declared once directly on `.viz-root`, outside `_vars(mode)` (Decision
    1). The two `@font-face` rules sit at the top level, outside the dark-mode media query
    entirely (fonts don't fork per mode -- duplicating ~25KB of base64 per mode would be pure
    waste, guarded by test_font_face_rules_are_outside_the_dark_mode_media_query).

    issue #263 (D2) adds `.coverage-denom` (the `<span class="coverage-denom">` shape
    `insight.dash.render.coverage_denominator_html()` emits and every numeral-bearing caller --
    `render.py`'s own metric table, `manager.py`, `leadership.py`, and now `insight.dash.number.
    render_number` -- concatenates directly after its own numeral markup, never inside it): a
    numeral-only `font-variant-numeric: tabular-nums` rule, matching `.dash-number-value`'s own,
    fixing a real gap this codebase shipped with -- `.coverage-denom` carried NO CSS at all before
    this (#263 PR-review finding 1), so the "62% (62 of 100 rows class-1, 38 class-2)" text it
    renders was never guaranteed tabular. `render_number` is the ONE call site that returns this
    span concatenated onto ITS OWN markup rather than a page hand-splicing it in, which is exactly
    why the gap was invisible until #263 gave it a dedicated component to audit."""
    def _vars(mode):
        parts = [f"--{prefix}-status-{k.lower()}: {v[mode]};" for k, v in STATUS.items()]
        parts += [f"--{prefix}-cat-{i}: {c[mode]};" for i, c in enumerate(CATEGORICAL)]
        seq = SEQUENTIAL_BLUE_LIGHT if mode == "light" else SEQUENTIAL_BLUE_DARK
        parts += [f"--{prefix}-seq-{i}: {hexv};" for i, hexv in enumerate(seq)]
        parts += [f"--{prefix}-{role}: {v[mode]};" for role, v in CHROME.items()]
        # TEXT ON A STATUS FILL DOES NOT FLIP, BECAUSE THE FILL DOES NOT FLIP. The status palette
        # is fixed across modes by design ("never themed"), so pairing it with a mode-flipping
        # foreground is what broke `warn`: white-on-#fab219 measures 1.83:1 in dark, while
        # black-on-#fab219 measures 10.73:1 in BOTH modes. pass/fail/absent happen to survive
        # var(--dash-surface) in both modes; warn is the one light-enough fill that does not, so
        # every status-filled surface uses this fixed ink instead of guessing per badge.
        parts.append(f"--{prefix}-on-status: {CHROME['ink']['light']};")
        return " ".join(parts)

    def _invariant_vars():
        parts = [f"--{prefix}-text-{k}: {v};" for k, v in TYPE_SCALE.items()]
        parts += [f"--{prefix}-space-{k}: {v};" for k, v in SPACE.items()]
        parts.append(f"--{prefix}-radius-sm: {RADIUS_SM};")
        parts.append(f"--{prefix}-border-hairline: {BORDER_HAIRLINE};")
        parts.append(f"--{prefix}-font-sans: {FONT_SANS_STACK};")
        parts.append(f"--{prefix}-font-mono: {FONT_MONO_STACK};")
        return " ".join(parts)

    return f"""
.viz-root {{ color-scheme: light; {_vars("light")} {_invariant_vars()} }}
@font-face {{ font-family: "Atkinson Hyperlegible"; font-style: normal; font-weight: 400;
  font-display: swap; src: url(data:font/woff2;base64,{FONT_SANS_WOFF2_BASE64}) format("woff2"); }}
@font-face {{ font-family: "IBM Plex Mono"; font-style: normal; font-weight: 400;
  font-display: swap; src: url(data:font/woff2;base64,{FONT_MONO_WOFF2_BASE64}) format("woff2"); }}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .viz-root {{ color-scheme: dark; {_vars("dark")} }}
}}
:root[data-theme="dark"] .viz-root {{ color-scheme: dark; {_vars("dark")} }}
.dash-texture-a11y {{ opacity: 0; }}
@media (forced-colors: active), print {{ .dash-texture-a11y {{ opacity: 1; }} }}
.data-state-not-measured {{
  border: var(--dash-border-hairline) dashed var(--dash-ink2);
  border-radius: var(--dash-radius-sm);
  background-image: repeating-linear-gradient(
    45deg, var(--dash-ink2) 0 1px, transparent 1px 6px);
  padding: var(--dash-space-3) var(--dash-space-4);
}}
.not-measured-label {{ font: 400 var(--dash-text-subhead) var(--dash-font-sans); margin: 0 0 var(--dash-space-1); color: var(--dash-ink); }}
.not-measured-provenance {{ font: var(--dash-text-caption) var(--dash-font-mono); margin: 0 0 var(--dash-space-1); color: var(--dash-ink2); }}
.not-measured-explain {{ font: var(--dash-text-small) var(--dash-font-sans); margin: 0; color: var(--dash-ink2); }}
.dash-number-label {{ font: var(--dash-text-small) var(--dash-font-sans); color: var(--dash-ink2); margin: 0 0 var(--dash-space-1); }}
.dash-number-value {{ font: 400 var(--dash-text-display) var(--dash-font-mono); font-variant-numeric: tabular-nums; color: var(--dash-ink); }}
.coverage-denom {{ font-variant-numeric: tabular-nums; }}
"""


def status_mark(status, cx, cy, r=4, id_prefix="dash"):
    """A status dot: fill=var(--{id_prefix}-status-{status}) (a CSS var, so it is mode-correct
    in both themes, never a baked hex) + adjacent icon+label text in ink2 -- colour is never the
    only channel here regardless of status. ABSENT gets ONE extra layered circle carrying the
    45-degree hatch texture, class="dash-texture-a11y" -- invisible (opacity:0) by default per
    Blocking 3, shown only under forced-colors/print (viz_css_vars' own gate above). ABSENT
    already carries three channels without the texture (the distinct muted hue, the '.' icon,
    the literal label) -- the texture is a fourth, genuinely-opt-in channel for a11y
    settings/print, not a default decoration layered on every render. Quoted attrs only."""
    c = STATUS[status]
    var = f"var(--{id_prefix}-status-{status.lower()})"
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{var}" stroke="{var}" stroke-width="1"/>']
    if status == "ABSENT":
        out.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#{id_prefix}-absent-hatch)" '
            f'class="dash-texture-a11y"/>'
        )
    out.append(
        f'<text x="{cx+r+4}" y="{cy+4}" font-size="var(--{id_prefix}-text-caption)" '
        f'fill="var(--{id_prefix}-ink2)">{html.escape(c["icon"])} {html.escape(c["label"])}</text>'
    )
    return "".join(out)


def texture_defs(id_prefix="dash"):
    """The ABSENT texture overlay's `<defs>` block: a 45-degree hatch `<pattern>`, referenced by
    `status_mark()`'s texture `<circle fill="url(#{id_prefix}-absent-hatch)">`. Callers that emit
    an ABSENT `status_mark()` inside an `<svg>` include this once, before the first mark that
    references it (`insight.dash.charts._absent_svg` does this for every primitive's empty-state
    branch). Visibility is controlled entirely by `viz_css_vars()`'s `.dash-texture-a11y` CSS gate
    -- this function only defines the pattern geometry, never toggles it on. Quoted attrs only,
    no `<image href>`, no inline `<script>` -- `assert_self_contained`-clean by construction."""
    return (
        f'<defs><pattern id="{id_prefix}-absent-hatch" width="4" height="4" '
        f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<line x1="0" y1="0" x2="0" y2="4" stroke="var(--{id_prefix}-ink2)" stroke-width="1"/>'
        f'</pattern></defs>'
    )


# --------------------------------------------------------------------------- not_measured
# primitive (D1, Decision 8): the genuinely new visual class -- hatched fill (DEFAULT-VISIBLE,
# never gated behind forced-colors/print the way STATUS["ABSENT"]'s .dash-texture-a11y is) +
# dashed border + the words "not measured" at body weight (NEVER a numeral) + a mandatory
# monospace provenance line naming the missing writer. Distinct from STATUS["ABSENT"] (a gate
# VERDICT, still used verbatim by cross_functional.py's gate matrix -- S4: that stays on STATUS,
# not this primitive). Border/hatch colour is CHROME.ink2, not CHROME.gridline: gridline is
# decorative-only (fails the 3:1 non-text floor on its own), but this border+hatch is the
# state's primary at-a-glance signal spec Sec.2's judged_when needs actually perceived, so it
# needs a token that clears 3:1 alone -- ink2 already does (see CONTRAST_PAIRS below).

def not_measured_block(explain_text, provenance):
    """The not_measured primitive (issue #262/D1; spec Sec.2's own "Decision"). Hatched fill
    (DEFAULT-VISIBLE -- .data-state-not-measured's background-image, never gated behind
    forced-colors/print the way STATUS['ABSENT']'s .dash-texture-a11y is, per S3) + dashed border
    + the words "not measured" at body weight (class="not-measured-label", NEVER a numeral) + a
    mandatory monospace provenance line naming the missing writer.

    Unlike status_mark()/texture_defs()/not_measured_svg(), this function takes no `id_prefix`:
    it emits only fixed class names (no `id="..."` attribute, no `var(--{id_prefix}-...)`
    reference anywhere in its output), so there is nothing here for a prefix to scope -- two
    `not_measured_block()`s on the same page cannot collide the way two `id`-bearing SVG
    fragments could. See PR review finding 4 on issue #262/D1: an earlier draft accepted and
    silently ignored an `id_prefix` parameter, a trap for a caller who could not tell it was a
    no-op without reading the source.

    Raises ValueError if `provenance` is empty or whitespace-only -- spec Sec.2 requires "a
    monospace provenance line naming the missing writer"; an empty string renders a hollow
    `<code></code>` that satisfies the call signature while defeating that requirement. A bare
    `assert` would vanish under `python -O`, so this is a real exception."""
    if not provenance or not provenance.strip():
        raise ValueError(
            "not_measured_block() requires a non-empty provenance naming the missing writer "
            "(spec Sec.2) -- got an empty/whitespace-only string"
        )
    return (
        f'<div class="data-state-not-measured">'
        f'<p class="not-measured-label">not measured</p>'
        f'<p class="not-measured-provenance"><code>{html.escape(provenance)}</code></p>'
        f'<p class="not-measured-explain">{html.escape(explain_text)}</p>'
        f'</div>'
    )


def not_measured_svg(explain_text, provenance, id_prefix="dash", w=480, h=100):
    """SVG-context sibling of not_measured_block(), for chart-shaped call sites (mirrors
    _absent_svg's own split in insight.dash.charts). Same hatch pattern id family as
    texture_defs(), but a SEPARATE pattern id (f"{id_prefix}-not-measured-hatch") and rendered
    always-on -- never gated behind .dash-texture-a11y.

    Raises ValueError if `provenance` is empty or whitespace-only -- see not_measured_block()'s
    docstring for why; the same requirement applies here (spec Sec.2)."""
    if not provenance or not provenance.strip():
        raise ValueError(
            "not_measured_svg() requires a non-empty provenance naming the missing writer "
            "(spec Sec.2) -- got an empty/whitespace-only string"
        )
    pid = f"{id_prefix}-not-measured-hatch"
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="not measured: {html.escape(explain_text)}">'
        f'<defs><pattern id="{pid}" width="4" height="4" patternUnits="userSpaceOnUse" '
        f'patternTransform="rotate(45)">'
        f'<line x1="0" y1="0" x2="0" y2="4" stroke="var(--{id_prefix}-ink2)" stroke-width="1"/>'
        f'</pattern></defs>'
        f'<rect x="1" y="1" width="{w-2}" height="{h-2}" fill="url(#{pid})" '
        f'stroke="var(--{id_prefix}-ink2)" stroke-width="1" stroke-dasharray="4 3"/>'
        f'<text x="12" y="24" font-family="var(--{id_prefix}-font-sans)" '
        f'font-size="var(--{id_prefix}-text-subhead)" fill="var(--{id_prefix}-ink)">not measured</text>'
        f'<text x="12" y="{h-14}" font-family="var(--{id_prefix}-font-mono)" '
        f'font-size="var(--{id_prefix}-text-caption)" fill="var(--{id_prefix}-ink2)">'
        f'{html.escape(provenance)}</text>'
        f'</svg>'
    )


# --------------------------------------------------------------------------- WCAG contrast math
# (D1, Decision 9): pure stdlib arithmetic, no colour library -- matching this module's own
# zero-dependency posture.

def _srgb_channel_to_linear(c8):
    c = c8 / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color):
    """WCAG 2.x relative luminance (https://www.w3.org/TR/WCAG21/#dfn-relative-luminance) of a
    "#rrggbb" string. Pure stdlib arithmetic -- no colour library, matching this module's own
    zero-dependency posture."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    R, G, B = (_srgb_channel_to_linear(v) for v in (r, g, b))
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def contrast_ratio(hex_a, hex_b):
    """WCAG 2.x contrast ratio (https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio) between two
    "#rrggbb" colours, order-independent, range [1, 21]."""
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# --------------------------------------------------------------------------- contrast registry
# (D1, Decision 9): an explicit, mechanically-checked pair registry. Rebuilt after an independent
# plan-review ran the WCAG formula above against the real, untouched hexes in this module and
# found 13 pair x mode combinations misclassified against a uniform floor rather than the
# rendered role each token actually plays -- see .sdlc/plans/262.md Decision 9's "PLAN-REVIEW FIX
# (Blocking 1)" for the full repro and root-cause analysis. Three tiers, per S6/S3 ("colour is
# never the only channel"): TEXT (4.5:1, WCAG SC 1.4.3), NON-TEXT/UI-BOUNDARY (3:1, WCAG SC
# 1.4.11), DECORATIVE/REDUNDANTLY-ENCODED (no floor, but a named second_channel is mandatory).
_SEQ_STEPS = len(SEQUENTIAL_BLUE_LIGHT)

CONTRAST_PAIRS = [
    # -- text: AA body-text floor, 4.5:1 (WCAG SC 1.4.3) --
    {"fg": "CHROME.ink", "bg": "CHROME.surface", "floor": 4.5, "why": "primary body text"},
    {"fg": "CHROME.ink2", "bg": "CHROME.surface", "floor": 4.5,
     "why": "secondary body text (ABSENT/not-measured explain + provenance sentences); also "
            "colours the not-measured primitive's border/hatch/SVG-rect-stroke -- a non-text use "
            "needing only 3:1, covered here with margin to spare (Decision 8 routes that colour "
            "through ink2, not gridline, exactly because gridline fails 3:1, see below)"},
    {"fg": "CHROME.on-status", "bg": "STATUS.WARN", "floor": 4.5,
     "why": "badge text on the .banner WARN fill (shell.py's .banner rule: "
            "background:var(--dash-status-warn); color:var(--dash-on-status)) -- the one status "
            "fill light enough to need checking, per colors.py:98-104"},
    # -- non-text / graphical-object floor, 3:1 (WCAG SC 1.4.11) --
    {"fg": "CHROME.muted", "bg": "CHROME.surface", "floor": 3.0,
     "why": "axis ticks / short glyph / sparkline stroke ONLY, never a sentence -- colors.py's "
            "own CHROME comment"},
    {"fg": "CHROME.delta_good", "bg": "CHROME.surface", "floor": 3.0,
     "why": "the 'good' direction-glyph (up/down triangle) fill beside a stat-tile delta -- a "
            "mark-level distinction per render_stat_tile()'s own docstring, which states this "
            "token is 'subject to the 3:1 floor, not the 4.5:1 text floor' verbatim; the delta "
            "VALUE itself always renders in plain ink text, never this colour"},
    {"fg": "STATUS.FAIL", "bg": "CHROME.surface", "floor": 3.0,
     "why": "the 'bad' direction-glyph fill beside a stat-tile delta (render_stat_tile(), same "
            "docstring as delta_good) -- FAIL's STRICTER role: unlike its status_mark() dot "
            "(decorative, see below), the glyph has no adjacent icon+label restating good/bad, "
            "only the plain numeral delta, which does not say whether that direction is good"},
    # -- decorative / redundantly-encoded: NO floor. Each row names the OTHER channel that carries
    # the same information (S3) -- an exemption without one is not granted, see
    # test_every_exempt_pair_names_its_second_channel in test_dash_contrast.py.
    {"fg": "STATUS.PASS", "bg": "CHROME.surface", "floor": None,
     "second_channel": "status_mark()'s adjacent icon '+' and text label 'PASS', always rendered "
                        "in the same call"},
    {"fg": "STATUS.WARN", "bg": "CHROME.surface", "floor": None,
     "second_channel": "status_mark()'s adjacent icon '!' and text label 'WARN', always rendered "
                        "in the same call (this is the row that fails 3:1 at 1.79 light if it is "
                        "ever mis-floored back to 3.0 -- see Blocking 1's repro above)"},
    {"fg": "STATUS.ABSENT", "bg": "CHROME.surface", "floor": None,
     "second_channel": "status_mark()'s adjacent icon (middle dot) and text label 'ABSENT', plus "
                        "the separately opt-in hatch texture under forced-colors/print"},
    {"fg": "CHROME.gridline", "bg": "CHROME.surface", "floor": None,
     "second_channel": "a low-emphasis divider only (stat-tile/gap-card/table-row-bottom "
                        "borders); the boundary is already conveyed by whitespace and DOM/table "
                        "structure. Deliberately NOT used for the not-measured primitive's own "
                        "border/hatch (Decision 8 uses CHROME.ink2 there instead), because THAT "
                        "boundary is the primary at-a-glance signal spec Sec.2's judged_when "
                        "requires and does need to be reliably perceived"},
    {"fg": "CHROME.baseline", "bg": "CHROME.surface", "floor": None,
     "second_channel": "chart axis/baseline lines and the .banner border; every chart that uses "
                        "this token direct-labels its own values in adjacent text (aging-WIP's "
                        "'actor -- Nd', burndown's p90/p10 labels, the handoff hub's 'all "
                        "hand-offs' label), and .banner's region is independently delineated by "
                        "its own WARN background fill"},
    *[{"fg": f"CATEGORICAL[{i}]", "bg": "CHROME.surface", "floor": None,
       "second_channel": "an adjacent legend swatch+text label (scatter/flow-lane legends) or a "
                          "direct node/edge text label (handoff graph) -- every categorical mark "
                          "in charts.py renders beside one; ALL_PAIRS_CAP/the shared legend "
                          "helper exist specifically so this is never colour-only"}
      for i in range(len(CATEGORICAL))],
    *[{"fg": f"SEQUENTIAL_BLUE[{i}]", "bg": "CHROME.surface", "floor": None,
       "second_channel": "render_aging_wip() direct-labels every bar with its actor and exact "
                          "age in days beside it ('colour is never the only channel', the "
                          "function's own docstring); render_aging_wip_table() is a plain-text "
                          "twin of the same data; ramp POSITION (pinned + monotonically "
                          "increasing in contrast by "
                          "test_sequential_ramps_are_pinned_and_monotonically_increase_in_"
                          "contrast, already in the suite) is the ordering cue, not raw contrast "
                          "against surface -- the low/receding end is intentionally low-contrast "
                          "by design, which is exactly why that existing test asserts only "
                          "monotonicity and not a floor"}
      for i in range(_SEQ_STEPS)],

    # -- issue #265 (D4) Design 2: the panel ground's own chart-role tokens. PANEL.ground/panel/
    # raised are pure backgrounds (never a foreground) -- exempted in
    # test_dash_contrast.py's own `exempt` set, exactly like CHROME.surface's existing exemption,
    # not listed here as rows (a background needs no contrast row against itself).
    {"fg": "PANEL.bone", "bg": "PANEL.panel", "floor": 4.5,
     "why": "primary chart text under id_prefix=\"panel\" (colors.py panel_css_vars()'s --panel-"
            "ink, sourced from PANEL['bone']) -- not_measured_svg's own 'not measured' label, "
            "direct bar/spoke labels"},
    {"fg": "PANEL.dim", "bg": "PANEL.panel", "floor": 4.5,
     "why": "secondary chart text (--panel-ink2, sourced from PANEL['dim']) -- explain/provenance "
            "sentences, axis labels"},
    {"fg": "PANEL.faint", "bg": "PANEL.panel", "floor": 3.0,
     "why": "tertiary/axis-tick glyph (--panel-muted, sourced from PANEL['faint']) -- clears at "
            "3.08, barely; flagged here so a future palette tweak that nudges this value down is "
            "caught by test_every_registered_pair_clears_its_floor_in_both_modes before it ships"},
    {"fg": "PANEL.cyan", "bg": "PANEL.panel", "floor": 3.0,
     "why": "stat-tile good-direction glyph (--panel-delta_good, sourced from PANEL['cyan']) -- "
            "11.35, comfortable margin"},
    {"fg": "PANEL.red", "bg": "PANEL.panel", "floor": 3.0,
     "why": "stat-tile bad-direction glyph (--panel-status-fail, sourced from PANEL['red']) -- "
            "6.24, comfortable margin"},
    {"fg": "PANEL.amber", "bg": "PANEL.panel", "floor": 3.0,
     "why": "bar-fill / signal accent, non-text mark (panel.py's own annunciator/p50 marker/bar "
            "fill, and PANEL_SEQ's own bright end) -- 9.57, comfortable margin"},
    {"fg": "PANEL.void-ink", "bg": "PANEL.panel", "floor": 3.0,
     "why": "the achromatic 'NO DATA'/'UNBUILT'/'NO SENSOR' short-label text instrument.py's own "
            "board/readouts already use -- clears at 3.08, barely, same margin note as "
            "PANEL.faint above (both happen to share the same hex today)"},
    {"fg": "PANEL.amber-deep", "bg": "PANEL.panel", "floor": None,
     "second_channel": "bar-gradient foot colour (panel._bars()'s linearGradient) -- never shown "
                        "as an isolated flat fill, always the receding end of a gradient whose "
                        "bright end (PANEL.amber) IS floored above"},
    {"fg": "PANEL.cyan-deep", "bg": "PANEL.panel", "floor": None,
     "second_channel": "live-tick gradient foot colour -- same reasoning as PANEL.amber-deep, "
                        "never an isolated flat fill"},
    {"fg": "PANEL.void", "bg": "PANEL.panel", "floor": None,
     "second_channel": "ABSENT fill -- always co-rendered with the dashed border + hatch + "
                        "'not measured'/'NO DATA' label, never colour alone, per PANEL's own "
                        "'not a status hue' docstring"},
    *[{"fg": f"PANEL_MIX[{i}]", "bg": "PANEL.panel", "floor": None,
       "second_channel": "an adjacent legend swatch or direct label, always rendered beside it -- "
                          "mirrors CATEGORICAL's own row, same reasoning, same ALL_PAIRS_CAP/"
                          "shared-legend mechanism, just PANEL-native colours"}
      for i in range(len(PANEL_MIX))],
    *[{"fg": f"PANEL_SEQ[{i}]", "bg": "PANEL.panel", "floor": None,
       "second_channel": "render_aging_wip direct-labels every bar; the table twin is a "
                          "plain-text equivalent; ramp POSITION, monotonically pinned by "
                          "test_panel_seq_monotonically_increases_in_contrast_against_the_panel_"
                          "ground, is the ordering cue -- verbatim the same reasoning "
                          "SEQUENTIAL_BLUE's own row above already states, because it is the same "
                          "design, just PANEL-native colours"}
      for i in range(len(PANEL_SEQ))],
]


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _is_hex(value):
    return isinstance(value, str) and bool(_HEX_RE.match(value))


def _is_light_dark_leaf(value):
    """True for a dict shaped like {"light": "#...", "dark": "#...", ...} -- the shape STATUS's
    and CHROME's per-key dicts, and CATEGORICAL's per-entry dicts, all share (extra keys like
    "icon"/"label"/"name" are ignored). This is the actual structural signature of "a colour
    token", not a naming convention."""
    return isinstance(value, dict) and _is_hex(value.get("light")) and _is_hex(value.get("dark"))


def _is_bare_hex_container(value):
    """True for a dict or list whose entries are ALL bare hex strings (not `{"light","dark"}`
    leaves) -- the shape PANEL (dict) and PANEL_MIX/PANEL_SEQ (lists) share. issue #265 (D4)
    Design 2, Route i: a second, dark-only/mode-invariant discovery shape, alongside
    `_is_light_dark_leaf()`'s light+dark shape -- extending discovery rather than giving
    PANEL/PANEL_MIX/PANEL_SEQ a synthetic light/dark leaf, which would contradict PANEL's own
    docstring rejecting a light mode."""
    if isinstance(value, dict):
        return bool(value) and all(_is_hex(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and all(_is_hex(v) for v in value)
    return False


def _all_exported_color_tokens():
    """Derived generically from this module's own top-level constants -- NOT a hand-enumerated
    list of the four containers that happened to exist when this was first written (PR review
    finding 1 on issue #262/D1: a hand-enumerated list is defeated by any NEW colour-bearing
    container, since it is simply never mentioned here). Structural shapes recognized, matching
    every colour container this module actually defines:

    1. A dict (STATUS, CHROME, or any future sibling) or a list (CATEGORICAL, or any future
       sibling) whose entries are `_is_light_dark_leaf()` dicts -> one token per entry, named
       "NAME.key" (dict) or "NAME[index]" (list).
    2. A `*_LIGHT`/`*_DARK` pair of same-length lists of bare hex strings (SEQUENTIAL_BLUE_LIGHT/
       _DARK, or any future sequential-ramp sibling) -- a genuinely different shape (no "light"/
       "dark" *keys* to sniff, since the two modes are two whole lists), so paired by name
       instead -> one token per index, named "BASE[index]".
    3. (issue #265, D4, Design 2, Route i) A dict or list whose entries are ALL bare hex strings,
       none of them `_is_light_dark_leaf()` dicts (PANEL, PANEL_MIX, PANEL_SEQ) -> one
       mode-invariant token per entry, same "NAME.key"/"NAME[index]" naming. Checked only for
       containers shape 1 didn't already claim (a dict of light/dark leaves is never also a bare
       hex container, since its values are dicts, not hex strings -- but stated as an explicit
       `elif` below so the two shapes can never double-count the same container).

    Precedence between shapes 2 and 3 (issue #265, D4, contrast-registry fix): a list that is
    itself one HALF of a shape-2 `*_LIGHT`/`*_DARK` pair (SEQUENTIAL_BLUE_LIGHT/_DARK) is
    structurally indistinguishable from a shape-3 bare-hex container (PANEL_MIX/PANEL_SEQ are
    also plain lists of bare hex) -- both are "a list of bare hex strings". Shape 2 takes
    precedence: such a list is discovered ONLY as the paired "BASE[index]" token (what
    CONTRAST_PAIRS actually registers for SEQUENTIAL_BLUE), never additionally under its own
    `*_LIGHT`/`*_DARK` name via shape 3 -- otherwise the same colours would be discovered twice
    under two different names, and test_every_exported_color_token_has_a_registered_contrast_
    pairing would report the `*_LIGHT`/`*_DARK` names as unregistered (they are never referenced
    as such in CONTRAST_PAIRS, since the pairing already covers them). The shape-2 pairing itself
    is computed first, below, so this precedence can be applied while shape 3 is being decided.

    Adding a brand-new top-level dict/list that matches any shape is picked up automatically, with
    no edit needed here -- that is what makes the registry mechanical rather than a hardcoded list
    that rots (see test_every_exported_color_token_has_a_registered_contrast_pairing, which fails
    loudly for any token this misses)."""
    mod = vars(sys.modules[__name__])

    # Shape 2's *_LIGHT/*_DARK halves, computed first so shape 3 (below) knows to skip them --
    # see the precedence note above.
    shape2_halves = set()
    for attr_name, value in mod.items():
        if not (attr_name.endswith("_LIGHT") and isinstance(value, list)):
            continue
        if not (value and all(_is_hex(v) for v in value)):
            continue
        base = attr_name[: -len("_LIGHT")]
        dark_value = mod.get(f"{base}_DARK")
        if isinstance(dark_value, list) and len(dark_value) == len(value):
            shape2_halves.add(attr_name)
            shape2_halves.add(f"{base}_DARK")

    names = set()
    for attr_name, value in mod.items():
        if attr_name.startswith("_") or not attr_name.isupper():
            continue
        if isinstance(value, dict):
            if any(_is_light_dark_leaf(v) for v in value.values()):
                names |= {f"{attr_name}.{k}" for k, v in value.items() if _is_light_dark_leaf(v)}
            elif _is_bare_hex_container(value):
                names |= {f"{attr_name}.{k}" for k in value}
        elif isinstance(value, list) and value:
            if all(_is_light_dark_leaf(v) for v in value):
                names |= {f"{attr_name}[{i}]" for i in range(len(value))}
            elif attr_name in shape2_halves:
                continue  # shape 2's own pairing (below) claims this list, not shape 3
            elif _is_bare_hex_container(value):
                names |= {f"{attr_name}[{i}]" for i in range(len(value))}
    for attr_name, value in mod.items():
        if not (attr_name.endswith("_LIGHT") and isinstance(value, list)):
            continue
        if not (value and all(_is_hex(v) for v in value)):
            continue
        base = attr_name[: -len("_LIGHT")]
        dark_value = mod.get(f"{base}_DARK")
        if isinstance(dark_value, list) and len(dark_value) == len(value):
            names |= {f"{base}[{i}]" for i in range(len(value))}
    names.add("CHROME.on-status")  # synthesized in viz_css_vars(), not a CHROME dict key
    return names


def _resolve(token_path, mode):
    """Resolve a CONTRAST_PAIRS token path (e.g. "CHROME.ink", "STATUS.WARN", "CATEGORICAL[2]",
    "SEQUENTIAL_BLUE[7]", "CHROME.on-status", "PANEL.bone", "PANEL_MIX[0]", "PANEL_SEQ[3]") to its
    hex string for the given mode ("light" or "dark"). Plumbing for the contrast-registry tests in
    test_dash_contrast.py.

    issue #265 (D4) Design 2: PANEL.*/PANEL_MIX[*]/PANEL_SEQ[*] return the SAME value regardless
    of `mode` -- exactly the pattern already used for "CHROME.on-status" above -- because PANEL is
    dark-only by design (its own docstring explicitly rejects a light mode)."""
    if token_path == "CHROME.on-status":
        return CHROME["ink"]["light"]  # fixed, never mode-flipped -- see viz_css_vars()'s comment
    if token_path.startswith("PANEL."):
        return PANEL[token_path.split(".", 1)[1]]  # mode-invariant, dark-only by design
    if token_path.startswith("PANEL_MIX["):
        return PANEL_MIX[int(token_path[len("PANEL_MIX["):-1])]  # mode-invariant
    if token_path.startswith("PANEL_SEQ["):
        return PANEL_SEQ[int(token_path[len("PANEL_SEQ["):-1])]  # mode-invariant
    if token_path.startswith("STATUS."):
        return STATUS[token_path.split(".", 1)[1]][mode]
    if token_path.startswith("CHROME."):
        return CHROME[token_path.split(".", 1)[1]][mode]
    if token_path.startswith("CATEGORICAL["):
        idx = int(token_path[len("CATEGORICAL["):-1])
        return CATEGORICAL[idx][mode]
    if token_path.startswith("SEQUENTIAL_BLUE["):
        idx = int(token_path[len("SEQUENTIAL_BLUE["):-1])
        seq = SEQUENTIAL_BLUE_LIGHT if mode == "light" else SEQUENTIAL_BLUE_DARK
        return seq[idx]
    raise KeyError(f"unresolvable contrast-registry token path: {token_path!r}")
