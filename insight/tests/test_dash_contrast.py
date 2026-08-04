# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the WCAG contrast math and the CONTRAST_PAIRS registry (issue #262, D1, Steps 6-7).
See .sdlc/plans/262.md Design decision 9 for the full registry rebuild history -- these tests are
the MECHANICAL proof (S6): every ratio is recomputed from the live hex values every run, nothing
hardcoded, so a future colour or role change fails loudly instead of silently drifting."""
import pytest

from insight.dash.colors import (
    CONTRAST_PAIRS,
    _all_exported_color_tokens,
    _resolve,
    contrast_ratio,
    relative_luminance,
)

# --------------------------------------------------------------------------- WCAG math itself


def test_relative_luminance_of_black_and_white():
    assert relative_luminance("#000000") == 0.0
    assert relative_luminance("#ffffff") == 1.0


def test_contrast_ratio_black_on_white_is_21_to_1():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)


def test_contrast_ratio_is_order_independent():
    pairs = [("#000000", "#ffffff"), ("#2a78d6", "#fcfcfb"), ("#52514e", "#1a1a19")]
    for a, b in pairs:
        assert contrast_ratio(a, b) == contrast_ratio(b, a)


def test_contrast_ratio_matches_a_hand_computed_value():
    # #767676 on #ffffff, hand-computed: 0x76/255 = 0.46275; since that's > 0.03928, the linear
    # channel is ((0.46275 + 0.055) / 1.055) ** 2.4 ~= 0.18116 (all three channels equal, so
    # R=G=B, and relative luminance = 0.2126R + 0.7152G + 0.0722B = 0.18116 too). White's
    # relative luminance is exactly 1.0. contrast = (1.0 + 0.05) / (0.18116 + 0.05) ~= 4.54 --
    # the widely-cited "#767676 is right at the AA text floor on white" reference value.
    ratio = contrast_ratio("#767676", "#ffffff")
    assert ratio == pytest.approx(4.54, abs=0.01)


# --------------------------------------------------------------------------- the registry


def test_contrast_registry_references_only_real_tokens():
    referenced = {p["fg"] for p in CONTRAST_PAIRS} | {p["bg"] for p in CONTRAST_PAIRS}
    assert referenced <= _all_exported_color_tokens()


def test_every_exported_color_token_has_a_registered_contrast_pairing():
    referenced_as_fg = {p["fg"] for p in CONTRAST_PAIRS}
    # CHROME.surface itself is exempt -- a background is not a thing that needs contrast
    # against itself.
    exempt = {"CHROME.surface"}
    assert (_all_exported_color_tokens() - exempt) <= referenced_as_fg


def test_every_registered_pair_clears_its_floor_in_both_modes():
    for pair in CONTRAST_PAIRS:
        if pair["floor"] is None:
            continue  # exempt rows carry no floor to check -- see the next test
        for mode in ("light", "dark"):
            fg_hex, bg_hex = _resolve(pair["fg"], mode), _resolve(pair["bg"], mode)
            ratio = contrast_ratio(fg_hex, bg_hex)
            assert ratio >= pair["floor"], (
                f"{pair['fg']} on {pair['bg']} ({mode}): {ratio:.2f} < {pair['floor']} -- "
                f"{pair['why']}"
            )


def test_every_exempt_pair_names_its_second_channel():
    for pair in CONTRAST_PAIRS:
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
