# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.colors (issue #125, E4.S2, Task 1). See .sdlc/plans/125.md Decision 2
for the validator run and contrast figures these tests pin as regressions -- CI has no `node` or
CSS engine, so the validated palette and the resolved-hex contrast table are hardcoded here as the
durable proof, not re-derived at test time."""
import re

from insight.dash import render as dash_render
from insight.dash.colors import (
    ALL_PAIRS_CAP,
    CATEGORICAL,
    SEQUENTIAL_BLUE_DARK,
    SEQUENTIAL_BLUE_LIGHT,
    STATUS,
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
