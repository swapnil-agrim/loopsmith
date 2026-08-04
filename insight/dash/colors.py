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
    waste, guarded by test_font_face_rules_are_outside_the_dark_mode_media_query)."""
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

def not_measured_block(explain_text, provenance, id_prefix="dash"):
    """The not_measured primitive (issue #262/D1; spec Sec.2's own "Decision"). Hatched fill
    (DEFAULT-VISIBLE -- .data-state-not-measured's background-image, never gated behind
    forced-colors/print the way STATUS['ABSENT']'s .dash-texture-a11y is, per S3) + dashed border
    + the words "not measured" at body weight (class="not-measured-label", NEVER a numeral) + a
    mandatory monospace provenance line naming the missing writer."""
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
    always-on -- never gated behind .dash-texture-a11y."""
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
]


def _all_exported_color_tokens():
    names = set()
    names |= {f"STATUS.{k}" for k in STATUS}
    names |= {f"CATEGORICAL[{i}]" for i in range(len(CATEGORICAL))}
    names |= {f"SEQUENTIAL_BLUE[{i}]" for i in range(_SEQ_STEPS)}
    names |= {f"CHROME.{k}" for k in CHROME}
    names |= {"CHROME.on-status"}  # synthesized in viz_css_vars(), not a CHROME dict key
    return names


def _resolve(token_path, mode):
    """Resolve a CONTRAST_PAIRS token path (e.g. "CHROME.ink", "STATUS.WARN", "CATEGORICAL[2]",
    "SEQUENTIAL_BLUE[7]", "CHROME.on-status") to its hex string for the given mode ("light" or
    "dark"). Plumbing for the contrast-registry tests in test_dash_contrast.py."""
    if token_path == "CHROME.on-status":
        return CHROME["ink"]["light"]  # fixed, never mode-flipped -- see viz_css_vars()'s comment
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
