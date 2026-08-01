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
"""
import html

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


def viz_css_vars(prefix="dash"):
    """Emit ONE light-default + dark-scoped CSS custom-property block declaring a named var for
    EVERY token a chart mark can reference (status-*, cat-0..7, seq-0..9, chrome roles) --
    palette.md's documented @media+[data-theme] double-scope shape (:not()+:where() guards so a
    manual light toggle beats OS-dark). Also declares the ABSENT texture overlay's opt-in gate:
    `.dash-texture-a11y` is invisible by default and only shown under `forced-colors`/print --
    "never on by default," per anti-patterns.md/marks-and-anatomy.md. One call, shared by every
    chart's <style> and by render.py's migrated _STYLE (Task 7)."""
    def _vars(mode):
        parts = [f"--{prefix}-status-{k.lower()}: {v[mode]};" for k, v in STATUS.items()]
        parts += [f"--{prefix}-cat-{i}: {c[mode]};" for i, c in enumerate(CATEGORICAL)]
        seq = SEQUENTIAL_BLUE_LIGHT if mode == "light" else SEQUENTIAL_BLUE_DARK
        parts += [f"--{prefix}-seq-{i}: {hexv};" for i, hexv in enumerate(seq)]
        parts += [f"--{prefix}-{role}: {v[mode]};" for role, v in CHROME.items()]
        return " ".join(parts)
    return f"""
.viz-root {{ color-scheme: light; {_vars("light")} }}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .viz-root {{ color-scheme: dark; {_vars("dark")} }}
}}
:root[data-theme="dark"] .viz-root {{ color-scheme: dark; {_vars("dark")} }}
.dash-texture-a11y {{ opacity: 0; }}
@media (forced-colors: active), print {{ .dash-texture-a11y {{ opacity: 1; }} }}
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
        f'<text x="{cx+r+4}" y="{cy+4}" font-size="11" fill="var(--{id_prefix}-ink2)">'
        f'{html.escape(c["icon"])} {html.escape(c["label"])}</text>'
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
