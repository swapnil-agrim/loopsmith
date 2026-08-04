# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.dash.fonts (issue #262, D1, Step 1). See .sdlc/plans/262.md Design decision
6: both embedded WOFF2 payloads are build-time-produced base64 data, never fetched/generated at
runtime -- these tests are cheap sanity checks that the embedded bytes are real, valid WOFF2, and
that the combined page-weight cost stays bounded."""
import base64

from insight.dash.fonts import FONT_MONO_WOFF2_BASE64, FONT_SANS_WOFF2_BASE64

# 150,000 base64 chars (~110KB of actual font bytes after decoding) -- a realistic
# subsetted-single-weight-Latin-only WOFF2 pair for these two faces should land far under this;
# a result anywhere near it is itself a signal something about the subsetting step went wrong
# (e.g. forgetting to subset, or embedding all weights/italics). See Design decision 6.
_PAGE_WEIGHT_CEILING = 150_000


def test_embedded_font_payloads_stay_under_the_page_weight_ceiling():
    assert len(FONT_SANS_WOFF2_BASE64) + len(FONT_MONO_WOFF2_BASE64) < _PAGE_WEIGHT_CEILING


def test_both_constants_are_valid_base64():
    # Must round-trip through base64.b64decode without raising.
    base64.b64decode(FONT_SANS_WOFF2_BASE64, validate=True)
    base64.b64decode(FONT_MONO_WOFF2_BASE64, validate=True)


def test_both_constants_decode_to_a_woff2_header():
    # The WOFF2 magic number is the ASCII bytes "wOF2" in the first 4 bytes of the file.
    assert base64.b64decode(FONT_SANS_WOFF2_BASE64)[:4] == b"wOF2"
    assert base64.b64decode(FONT_MONO_WOFF2_BASE64)[:4] == b"wOF2"


def test_both_constants_are_non_trivially_sized():
    """A cheap floor, not just a ceiling: catches an accidentally-empty or truncated constant
    that would still pass the three checks above (an empty string round-trips through b64decode
    to b"", which is neither over the ceiling nor a WOFF2 header -- but the header check above
    already rejects that; this test instead guards against a suspiciously tiny, near-empty but
    still WOFF2-headered payload, e.g. a font subset to zero glyphs)."""
    assert len(FONT_SANS_WOFF2_BASE64) > 1000
    assert len(FONT_MONO_WOFF2_BASE64) > 1000
