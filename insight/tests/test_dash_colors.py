# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.colors (issue #125, E4.S2, Task 1). See .sdlc/plans/125.md Decision 2
for the validator run and contrast figures these tests pin as regressions -- CI has no `node` or
CSS engine, so the validated palette and the resolved-hex contrast table are hardcoded here as the
durable proof, not re-derived at test time."""
import re

import pytest

from insight.dash import render as dash_render
from insight.dash.colors import (
    ALL_PAIRS_CAP,
    CATEGORICAL,
    PANEL,
    PANEL_MIX,
    PANEL_SEQ,
    SEQUENTIAL_BLUE_DARK,
    SEQUENTIAL_BLUE_LIGHT,
    SPACE,
    STATUS,
    TYPE_SCALE,
    contrast_ratio,
    not_measured_block,
    not_measured_svg,
    panel_css_vars,
    status_mark,
    texture_defs,
    viz_css_vars,
)
from insight.gaps.severity import SEVERITY_ORDER

# The exact 8 light + 8 dark hexes the palette validator passed this session (.sdlc/plans/125.md
# Decision 2 / "Validator output"), in slot order.
_VALIDATED_LIGHT = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
_VALIDATED_DARK = [
    "#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767",
]

# The exact resolved-hex contrast figures from Decision 2's own table, bucket order 0..9.
_LIGHT_CONTRAST = [2.06, 2.44, 2.91, 3.54, 4.30, 5.26, 6.46, 7.89, 9.66, 11.64]
_DARK_CONTRAST = [2.15, 2.63, 3.23, 3.94, 4.79, 5.83, 6.96, 8.25, 9.74, 11.33]


def test_absent_is_not_a_fifth_status_hue():
    for mode in ("light", "dark"):
        other_status_hexes = {STATUS[s][mode] for s in ("PASS", "WARN", "FAIL")}
        assert STATUS["ABSENT"][mode] not in other_status_hexes
        categorical_hexes = {c[mode] for c in CATEGORICAL}
        assert STATUS["ABSENT"][mode] not in categorical_hexes


def test_status_keys_match_severity_order():
    assert set(STATUS) == set(SEVERITY_ORDER)


def test_categorical_hexes_pinned_to_the_validated_set():
    assert [c["light"] for c in CATEGORICAL] == _VALIDATED_LIGHT
    assert [c["dark"] for c in CATEGORICAL] == _VALIDATED_DARK
    assert ALL_PAIRS_CAP == 3


def test_sequential_ramps_are_pinned_and_monotonically_increase_in_contrast():
    pinned_light = [
        "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
        "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
    ]
    pinned_dark = [
        "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5",
        "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#b7d3f6",
    ]
    assert SEQUENTIAL_BLUE_LIGHT == pinned_light
    assert SEQUENTIAL_BLUE_DARK == pinned_dark
    assert len(_LIGHT_CONTRAST) == len(SEQUENTIAL_BLUE_LIGHT) == 10
    assert len(_DARK_CONTRAST) == len(SEQUENTIAL_BLUE_DARK) == 10
    for i in range(len(_LIGHT_CONTRAST) - 1):
        assert _LIGHT_CONTRAST[i] < _LIGHT_CONTRAST[i + 1], (
            "light sequential ramp must be monotonically increasing in contrast, bucket "
            f"{i} -> {i + 1}"
        )
        assert _DARK_CONTRAST[i] < _DARK_CONTRAST[i + 1], (
            "dark sequential ramp must be monotonically increasing in contrast, bucket "
            f"{i} -> {i + 1}"
        )


def test_viz_css_vars_declares_both_dark_scopes():
    css = viz_css_vars()
    assert "@media (prefers-color-scheme: dark)" in css
    assert '[data-theme="dark"]' in css


def test_viz_css_vars_gates_the_texture_overlay_off_by_default():
    css = viz_css_vars()
    assert ".dash-texture-a11y { opacity: 0; }" in css
    assert "@media (forced-colors: active), print" in css
    m = re.search(
        r"@media \(forced-colors: active\), print \{\s*\.dash-texture-a11y \{ opacity: 1; \}",
        css,
    )
    assert m, "texture overlay must flip to opacity: 1 only under forced-colors/print"


def test_viz_css_vars_declares_the_number_component_css():
    """Issue #263 (D2), Decision 4: the .dash-number component's CSS is a global primitive
    declared in viz_css_vars() (not a page-specific _STYLE block) -- .dash-number-value is the
    ONLY place font-variant-numeric is set for the tile's OWN `.dash-number-*` selectors,
    guaranteeing tabular-nums by construction for the value INSIDE the tile (Decision 4's own
    "guaranteed, not just applied once" argument). This is narrower than "the only numeral
    render_number ever emits": render_number also appends coverage_denominator_html()'s own
    `.coverage-denom` span AFTER the tile -- see
    test_viz_css_vars_declares_the_coverage_denominator_css below for that second, separately
    guaranteed rule (#263 PR-review finding 1 -- an earlier revision of this docstring implied
    .dash-number-value was the only tabular-nums rule this component needed, which was false: the
    coverage-denominator span render_number concatenates onto its own output carried NO CSS
    at all until that fix)."""
    css = viz_css_vars()
    assert ".dash-number-label {" in css
    assert ".dash-number-value {" in css
    m = re.search(r"\.dash-number-value \{[^}]*\}", css)
    assert m and "font-variant-numeric: tabular-nums" in m.group(0)


def test_viz_css_vars_declares_the_coverage_denominator_css():
    """#263 PR-review finding 1: coverage_denominator_html() (insight.dash.render) emits a
    `<span class="coverage-denom">` carrying its own numerals (the "62% (62 of 100 rows class-1,
    38 class-2)" shape) -- render_number (insight.dash.number) concatenates that span directly
    after its own tile, OUTSIDE `.dash-number-value`. Before this fix `.coverage-denom` had ZERO
    CSS anywhere in the codebase (repo-wide grep), so that numeral text was never guaranteed
    tabular even though it sits right next to a tabular-nums tile value. This is the durable
    regression guard: a rule for `.coverage-denom` itself, not routed through `.dash-number-value`
    (deliberately -- `.coverage-denom` is also used by insight.dash.render's own metric table and
    by manager.py/leadership.py's own call sites, none of which are inside a `.dash-number`
    tile)."""
    css = viz_css_vars()
    assert ".coverage-denom {" in css
    m = re.search(r"\.coverage-denom \{[^}]*\}", css)
    assert m and "font-variant-numeric: tabular-nums" in m.group(0)


def test_status_mark_absent_fill_is_a_css_var_and_texture_overlay_is_a_separate_element():
    out = status_mark("ABSENT", 10, 10)
    assert 'fill="var(--dash-status-absent)"' in out
    # The texture overlay is a SECOND, separately classed circle -- never the same element whose
    # only ABSENT-vs-not distinction is the texture fill.
    assert re.search(r'<circle[^>]*class="dash-texture-a11y"', out)
    base_circle = out.split("<circle", 2)[1]
    assert "dash-texture-a11y" not in base_circle.split("/>")[0]


def test_status_mark_never_emits_a_literal_hex():
    for status in STATUS:
        out = status_mark(status, 10, 10)
        assert "fill=\"#" not in out
        assert "stroke=\"#" not in out


def test_texture_defs_and_status_mark_pass_assert_self_contained():
    svg = f'<svg>{texture_defs()}{status_mark("ABSENT", 10, 10)}</svg>'
    html_text = f"<html><body>{svg}</body></html>"
    dash_render.assert_self_contained(html_text)  # must not raise


# --------------------------------------------------------------------------- issue #262 (D1):
# type scale, spacing scale, embedded fonts, and the not_measured primitive.


def test_viz_css_vars_declares_every_type_and_space_token():
    css = viz_css_vars()
    for k in TYPE_SCALE:
        assert f"--dash-text-{k}:" in css
    for k in SPACE:
        assert f"--dash-space-{k}:" in css


def test_viz_css_vars_declares_both_font_face_rules():
    css = viz_css_vars()
    assert css.count("@font-face") == 2
    assert css.count("data:font/woff2;base64,") == 2


def test_font_face_rules_are_outside_the_dark_mode_media_query():
    css = viz_css_vars()
    for block in _top_level_media_blocks(css):
        if block.startswith("@media (prefers-color-scheme: dark)"):
            assert "@font-face" not in block, (
                "fonts are mode-invariant -- embedding ~25KB of base64 twice per mode is waste"
            )
            return
    raise AssertionError("expected a @media (prefers-color-scheme: dark) block in viz_css_vars()")


def test_not_measured_block_label_never_renders_a_bare_numeral():
    # Scoped to the <p class="not-measured-label">...</p> substring specifically -- a legitimate
    # explain_text naming a date or row count (e.g. "no writer configured since 2026-01-01") must
    # not false-fail this test; the mechanical form of spec Sec.2's "never a numeral" is about the
    # labeled value a reader is meant to read as the measurement, not arbitrary prose elsewhere.
    out = not_measured_block("no writer configured since 2026-01-01", "no writer · fact_event.reason_class")
    label = re.search(r'<p class="not-measured-label">(.*?)</p>', out).group(1)
    assert not re.search(r"\d", label)
    assert label == "not measured"


def test_not_measured_block_always_includes_a_provenance_line():
    out = not_measured_block("explain", "no writer · fact_event.reason_class")
    provenance = re.search(r'<p class="not-measured-provenance">(.*?)</p>', out).group(1)
    assert provenance == "<code>no writer · fact_event.reason_class</code>"


def _top_level_media_blocks(css):
    """Extract every top-level `@media (...) { ... }` block from `css` by balanced-brace
    scanning -- a plain regex can't reliably find the matching close brace when the block's own
    body contains further nested `{ }` pairs (e.g. `.data-state-not-measured {...}` sitting right
    after a one-line `@media {...}` block with no newline between their closing braces)."""
    blocks = []
    i = 0
    while True:
        start = css.find("@media", i)
        if start == -1:
            break
        open_brace = css.index("{", start)
        depth = 0
        j = open_brace
        while True:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        blocks.append(css[start:j + 1])
        i = j + 1
    return blocks


def test_not_measured_texture_is_default_visible_not_gated():
    css = viz_css_vars()
    block_match = re.search(
        r"\.data-state-not-measured \{[^}]*background-image[^}]*\}", css, re.S,
    )
    assert block_match, "expected a .data-state-not-measured rule with a background-image"
    # Unlike .dash-texture-a11y (gated behind forced-colors/print), the not-measured hatch must
    # not appear inside ANY @media block.
    for block in _top_level_media_blocks(css):
        assert ".data-state-not-measured" not in block


@pytest.mark.parametrize("empty_provenance", ["", "   ", "\t\n"])
def test_not_measured_block_rejects_empty_provenance(empty_provenance):
    # PR review finding 5 on issue #262/D1: spec Sec.2 requires "a monospace provenance line
    # naming the missing writer" -- an empty/whitespace-only string satisfies the call signature
    # while rendering a hollow <code></code> that defeats the requirement. A real exception, not
    # a bare `assert` (which vanishes under `python -O`).
    with pytest.raises(ValueError):
        not_measured_block("explain", empty_provenance)


@pytest.mark.parametrize("empty_provenance", ["", "   ", "\t\n"])
def test_not_measured_svg_rejects_empty_provenance(empty_provenance):
    with pytest.raises(ValueError):
        not_measured_svg("explain", empty_provenance)


def test_not_measured_svg_and_not_measured_block_pass_assert_self_contained():
    block = not_measured_block("no instrument", "no writer · fact_event.reason_class")
    svg = not_measured_svg("no instrument", "no writer · fact_event.reason_class")
    html_text = f"<html><body>{block}<svg>{svg}</svg></body></html>"
    dash_render.assert_self_contained(html_text)  # must not raise


# --------------------------------------------------------------------------- issue #265 (D4),
# Step 1: PANEL_SEQ + panel_css_vars()'s new chart-role vars.


def test_panel_seq_is_pinned_and_ten_long():
    pinned = [
        "#141a1c", "#2e2a1d", "#48391f", "#624920", "#7c5822",
        "#976823", "#b17725", "#cb8726", "#e59628", "#ffa629",
    ]
    assert PANEL_SEQ == pinned
    assert len(PANEL_SEQ) == 10
    # Endpoints match the two named source tokens verbatim (Design 1: linear-interpolated
    # between these two, not an independently hand-picked ramp).
    assert PANEL_SEQ[0] == PANEL["raised"]
    assert PANEL_SEQ[-1] == PANEL["amber"]


def test_panel_seq_monotonically_increases_in_contrast_against_the_panel_ground():
    ratios = [contrast_ratio(hexv, PANEL["panel"]) for hexv in PANEL_SEQ]
    for i in range(len(ratios) - 1):
        assert ratios[i] < ratios[i + 1], (
            f"PANEL_SEQ must be monotonically increasing in contrast against PANEL['panel'], "
            f"bucket {i} -> {i + 1}: {ratios[i]:.2f} !< {ratios[i + 1]:.2f}"
        )


#: Every `--panel-*` var name any chart primitive under id_prefix="panel" could dereference
#: (colors.py Design 1's new-var table) -- the full set panel_css_vars() must emit, on top of the
#: PANEL/PANEL_ALPHA/PANEL_MIX/font vars it already emitted before issue #265.
_PANEL_CHART_ROLE_VARS = (
    ["--panel-ink:", "--panel-ink2:", "--panel-muted:", "--panel-baseline:", "--panel-gridline:",
     "--panel-surface:", "--panel-delta_good:", "--panel-status-fail:"]
    + [f"--panel-cat-{i}:" for i in range(len(PANEL_MIX))]
    + [f"--panel-seq-{i}:" for i in range(len(PANEL_SEQ))]
    + [f"--panel-text-{k}:" for k in TYPE_SCALE]
    + [f"--panel-space-{k}:" for k in SPACE]
    + ["--panel-radius-sm:", "--panel-border-hairline:"]
)


def test_panel_css_vars_declares_every_new_chart_role_var():
    css = panel_css_vars()
    missing = [name for name in _PANEL_CHART_ROLE_VARS if name not in css]
    assert not missing, f"panel_css_vars() never emits: {missing}"


def test_negative_control_proves_the_panel_css_vars_presence_check_has_teeth():
    """Not shipped code -- strips one var declaration from a copy of the emitted CSS and shows
    the exact same presence check above now fails, proving it isn't vacuously true."""
    css = panel_css_vars()
    assert "--panel-cat-3:" in css  # sanity: the var this control removes really is present
    mutated = css.replace("--panel-cat-3:", "--panel-cat-3-DELETED:")
    missing = [name for name in _PANEL_CHART_ROLE_VARS if name not in mutated]
    assert missing == ["--panel-cat-3:"], (
        "fixture regressed: mutating the CSS should make exactly one var name go missing"
    )


def test_panel_cat_has_seven_slots_one_fewer_than_categorical():
    # Design 1a: PANEL_MIX has 7 entries, CATEGORICAL has 8 -- the panel ground folds to "Other"
    # one area sooner (accepted tradeoff, plan §5).
    assert len(PANEL_MIX) == 7
    assert len(CATEGORICAL) == 8
