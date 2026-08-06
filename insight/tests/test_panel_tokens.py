# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for done-when 4 of issue #303: the instrument panel's absence material (`PANEL['void']`
/ `PANEL['void-ink']` and the hatch texture that overlays them) must be genuinely hueless AND
stay distinguishable in greyscale / to a colour-blind reader. See .sdlc/plans/303.md Decision f
for the full measurement and rebuild history -- these tests are the MECHANICAL proof (mirroring
insight/tests/test_dash_contrast.py's own structure and posture): every ratio/saturation is
recomputed from the live hex values every run, nothing hardcoded, so a future colour change fails
loudly instead of silently drifting.

Done-when 4 has TWO halves, and they are DIFFERENT properties:

* "genuinely hueless" -- an HSL-SATURATION check (lightness-invariant). Protanopia/deuteranopia/
  tritanopia collapse hue but preserve luminance, and so does greyscale printing -- a low-
  saturation neutral survives both. A raw channel-delta check (max(R,G,B) - min(R,G,B)) is
  lightness-dependent and would fight the exact lightness fix Decision f1 makes, which is why
  this file uses `colorsys.rgb_to_hls` instead (see Decision f's own worked example).
* "stays distinguishable ... in greyscale and to a colour-blind reader" -- a LUMINANCE property,
  i.e. WCAG contrast, via `colors.contrast_ratio`/`colors.relative_luminance` (already pure
  stdlib, already this module's own tool) against the new `PANEL_CONTRAST_PAIRS` registry. The
  original draft of this decision measured only saturation and would have shipped a green test
  over `void-ink`'s real, unreadable 2.80:1 -- the contrast half is what actually answers what
  done-when 4 asks.
"""
import colorsys
import re

import pytest

from insight.dash.colors import PANEL, PANEL_ALPHA, PANEL_CONTRAST_PAIRS, contrast_ratio, _panel_resolve

#: HSL saturation ceiling for "hueless". Current max among the tokens/composites checked here is
#: 14.8% (`void` itself); the least-saturated real PANEL signal colour is `cyan-deep` at 51.3%.
#: 20% sits with real margin on both sides -- see .sdlc/plans/303.md Decision f's own table.
_HUELESS_CEILING_PCT = 20.0

_RGBA_RE = re.compile(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)")


def _hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hsl_saturation_pct(hex_color):
    """HSL saturation (0-100), the lightness-invariant "how hueless is this" measure -- distinct
    from HSV/other colour-space saturations, and from a raw R/G/B channel delta (see module
    docstring)."""
    r, g, b = (c / 255.0 for c in _hex_to_rgb(hex_color))
    _h, _l, s = colorsys.rgb_to_hls(r, g, b)
    return s * 100.0


def _composite_over(rgba_str, base_hex):
    """Composite a PANEL_ALPHA `rgba(r,g,b,a)` string over an opaque "#rrggbb" base, returning
    the actual rendered "#rrggbb" a reader sees -- the hatch texture is never viewed as raw alpha,
    only as this composite, so this is what huelessness must be measured against, not the alpha
    channel's own (unrelated) RGB literal."""
    m = _RGBA_RE.match(rgba_str.replace(" ", ""))
    assert m, f"not a recognized rgba(...) string: {rgba_str!r}"
    r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
    br, bg, bb = _hex_to_rgb(base_hex)
    cr = round(r * a + br * (1 - a))
    cg = round(g * a + bg * (1 - a))
    cb = round(b * a + bb * (1 - a))
    return f"#{cr:02x}{cg:02x}{cb:02x}"


#: Every (label, hex) pair the huelessness floor applies to: the two flat PANEL tokens, plus the
#: hatch texture composited over every surface it is actually layered on in instrument.py
#: (`void`, `panel`, `raised` -- see instrument.py's `.ro.absent .val` / `.cell.dark .nm` rules).
def _hueless_targets():
    targets = [("PANEL.void", PANEL["void"]), ("PANEL.void-ink", PANEL["void-ink"])]
    for alpha_key in ("hatch", "hatch-soft"):
        for base_name in ("void", "panel", "raised"):
            composited = _composite_over(PANEL_ALPHA[alpha_key], PANEL[base_name])
            targets.append((f"PANEL_ALPHA.{alpha_key} over PANEL.{base_name}", composited))
    return targets


# --------------------------------------------------------------------------- huelessness


def test_absence_material_is_genuinely_hueless():
    for label, hex_color in _hueless_targets():
        sat = _hsl_saturation_pct(hex_color)
        assert sat <= _HUELESS_CEILING_PCT, (
            f"{label} ({hex_color}) has HSL saturation {sat:.1f}% > {_HUELESS_CEILING_PCT}% -- "
            "the absence material must read as achromatic (see colors.py's PANEL docstring: "
            "'Absence must differ from a reading in LIGHTNESS and TEXTURE, never in hue alone')"
        )


def test_negative_control_saturated_hatch_trips_the_huelessness_check():
    """Not shipped code -- mutates the hatch alpha to a visibly-tinted colour (a saturated cyan,
    PANEL['cyan']'s own RGB) and proves the SAME assertion used above now fails. Without this, a
    huelessness check that can never go red is not proof of anything."""
    saturated_hatch = "rgba(92,224,176,.30)"  # PANEL['cyan'] = #5ce0b0, strongly saturated
    composited = _composite_over(saturated_hatch, PANEL["void"])
    sat = _hsl_saturation_pct(composited)
    assert sat > _HUELESS_CEILING_PCT, (
        f"fixture regressed: the saturated negative-control hatch composited to {composited} "
        f"(HSL-S {sat:.1f}%), which no longer exceeds the {_HUELESS_CEILING_PCT}% ceiling -- "
        "pick a more saturated stand-in colour"
    )
    with pytest.raises(AssertionError):
        assert sat <= _HUELESS_CEILING_PCT


# --------------------------------------------------------------------------- distinguishability
# (contrast/luminance, via PANEL_CONTRAST_PAIRS)


def test_panel_contrast_registry_covers_the_two_load_bearing_pairs():
    referenced = {(p["fg"], p["bg"]) for p in PANEL_CONTRAST_PAIRS}
    assert ("PANEL.void-ink", "PANEL.void") in referenced
    assert ("PANEL.void", "PANEL.raised") in referenced


def test_every_registered_panel_pair_clears_its_floor():
    for pair in PANEL_CONTRAST_PAIRS:
        if pair["floor"] is None:
            continue  # exempt rows carry no floor to check -- see the next test
        fg_hex, bg_hex = _panel_resolve(pair["fg"]), _panel_resolve(pair["bg"])
        ratio = contrast_ratio(fg_hex, bg_hex)
        assert ratio >= pair["floor"], (
            f"{pair['fg']} on {pair['bg']}: {ratio:.2f} < {pair['floor']} -- {pair['why']}"
        )


def test_every_exempt_panel_pair_names_its_second_channel():
    for pair in PANEL_CONTRAST_PAIRS:
        if pair["floor"] is None:
            assert pair.get("second_channel"), (
                f"{pair['fg']} on {pair['bg']} claims no contrast floor but names no second "
                "channel -- an exemption without a stated reason is just an unchecked hole"
            )
        else:
            assert "second_channel" not in pair, (
                f"{pair['fg']} on {pair['bg']} has a floor AND a second_channel -- a floored "
                "row does not need an exemption reason, pick one classification"
            )


def test_panel_resolve_rejects_an_unrecognized_token_path():
    with pytest.raises(KeyError):
        _panel_resolve("CHROME.ink")  # a real CONTRAST_PAIRS path, but not a PANEL.* one


def test_negative_control_reverting_void_ink_trips_the_contrast_check():
    """Not shipped code -- reverts `void-ink` to its pre-#303 shipped value (#5a6467, measured at
    2.80:1 against `void` -- see .sdlc/plans/303.md Decision f) and proves the SAME 4.5:1
    assertion used above now fails. Without this, a floor that can never go red is not proof the
    fix in colors.py actually matters."""
    reverted_void_ink = "#5a6467"
    ratio = contrast_ratio(reverted_void_ink, PANEL["void"])
    assert ratio < 4.5, (
        f"fixture regressed: the reverted void-ink {reverted_void_ink} now measures {ratio:.2f} "
        "against PANEL['void'], which no longer fails the 4.5:1 floor -- has PANEL['void'] "
        "itself changed?"
    )
    with pytest.raises(AssertionError):
        assert ratio >= 4.5
