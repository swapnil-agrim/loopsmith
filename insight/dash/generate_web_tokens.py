# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Writes insight/web/'s generated design-token artifacts from insight/dash/colors.py's own
single source of truth (issue #303, Decision a) -- exact structural precedent:
insight/api/export_openapi.py (a Python module -> a committed frontend artifact, freshness
checked by a Python-side pytest, run by hand: `python3 -m insight.dash.generate_web_tokens`, from
the repo root, `insight` importable).

Writes three artifacts, five files:
- insight/web/src/app/tokens.generated.css -- colors.web_tokens_css()'s `:root` block (PANEL /
  PANEL_ALPHA / PANEL_MIX / TYPE_SCALE), plus the font custom properties, the two self-hosted
  `@font-face` rules (Decision b: real `public/` files referenced by URL, not `next/font/local`,
  not base64 -- see .sdlc/plans/303.md for why), and the `body`/`code,pre` font-family rules that
  make base typography actually use them (Decision c) -- a plain CSS rule, never a Tailwind
  utility class, so it is emitted regardless of what Tailwind's content scanner sees.
- insight/web/public/fonts/{AtkinsonHyperlegible,IBMPlexMono}-Regular.woff2 -- the SAME font
  bytes insight.dash.fonts already carries (base64, already OFL-licensed, already subsetted,
  already vetted), filed as real binary files instead of base64-embedded: one physical source of
  font bytes, two delivery mechanisms -- inlined for the offline artifact (panel_css_vars()),
  filed for the web app (this module).
- insight/web/public/fonts/OFL-{atkinson-hyperlegible,ibm-plex-mono}.txt -- copied unchanged from
  insight/dash/fonts/, alongside the files their licence covers.

insight/tests/test_web_tokens_fresh.py regenerates all three artifacts in memory and asserts
byte-equality against what's committed -- the freshness enforcement; this module only writes.
"""
import base64
import pathlib

from insight.dash.colors import FONT_MONO_STACK, FONT_SANS_STACK, web_tokens_css
from insight.dash.fonts import FONT_MONO_WOFF2_BASE64, FONT_SANS_WOFF2_BASE64

HERE = pathlib.Path(__file__).resolve().parent  # insight/dash/ -- this file lives directly here
INSIGHT = HERE.parent  # insight/
WEB = INSIGHT / "web"

TOKENS_CSS_OUT = WEB / "src" / "app" / "tokens.generated.css"
FONTS_DIR = WEB / "public" / "fonts"
SANS_WOFF2_OUT = FONTS_DIR / "AtkinsonHyperlegible-Regular.woff2"
MONO_WOFF2_OUT = FONTS_DIR / "IBMPlexMono-Regular.woff2"
OFL_SANS_OUT = FONTS_DIR / "OFL-atkinson-hyperlegible.txt"
OFL_MONO_OUT = FONTS_DIR / "OFL-ibm-plex-mono.txt"
_OFL_SANS_SRC = HERE / "fonts" / "OFL-atkinson-hyperlegible.txt"
_OFL_MONO_SRC = HERE / "fonts" / "OFL-ibm-plex-mono.txt"

_BANNER = (
    "/* GENERATED FILE -- DO NOT EDIT.\n"
    " * Produced by `python3 -m insight.dash.generate_web_tokens` from insight/dash/colors.py's\n"
    " * PANEL / PANEL_ALPHA / PANEL_MIX / TYPE_SCALE tokens -- the single source of truth, see\n"
    " * .sdlc/plans/303.md Decision a. insight/tests/test_web_tokens_fresh.py fails the `insight`\n"
    " * CI job if this file drifts from what colors.py generates -- re-run the command above and\n"
    " * commit the result; never hand-edit this file. (Not a BUSL header -- this is a `.css`\n"
    " * file, outside test_licence_boundary.py's `.py`/`.ts`/`.tsx` scope.)\n"
    " */\n"
)

_FONT_FACES = (
    '@font-face { font-family: "Atkinson Hyperlegible"; font-style: normal; font-weight: 400;\n'
    '  font-display: swap; src: url("/fonts/AtkinsonHyperlegible-Regular.woff2") format("woff2"); }\n'
    '@font-face { font-family: "IBM Plex Mono"; font-style: normal; font-weight: 400;\n'
    '  font-display: swap; src: url("/fonts/IBMPlexMono-Regular.woff2") format("woff2"); }\n'
)

#: Decision (c): base typography applied via plain CSS rules, never a Tailwind utility class --
#: a utility class is only emitted into the built CSS if referenced by Tailwind's content scan,
#: and layout.tsx/page.tsx carry no className at all today (verified directly against the code,
#: not assumed). A plain rule here is never a Tailwind directive, so PostCSS passes it through
#: untouched regardless of what gets scanned.
_BASE_TYPOGRAPHY = (
    "body { font-family: var(--panel-font-sans); }\n"
    "code, pre { font-family: var(--panel-font-mono); }\n"
)


def build_tokens_css():
    """The full generated CSS text -- pulled out as its own function so
    test_web_tokens_fresh.py can call it directly rather than re-deriving it, and so main() has
    exactly one thing to test: does it write what this function built (mirrors
    insight.api.export_openapi.build_schema()'s own split, the exact precedent this module
    follows)."""
    font_vars = (
        f":root {{ --panel-font-sans: {FONT_SANS_STACK}; --panel-font-mono: {FONT_MONO_STACK}; }}\n"
    )
    return _BANNER + web_tokens_css() + font_vars + _FONT_FACES + _BASE_TYPOGRAPHY


def build_sans_woff2_bytes():
    """The real Atkinson Hyperlegible WOFF2 bytes -- decoded from the SAME base64 constant
    insight.dash.fonts already carries, so there is exactly one physical source of font bytes."""
    return base64.b64decode(FONT_SANS_WOFF2_BASE64)


def build_mono_woff2_bytes():
    """The real IBM Plex Mono WOFF2 bytes -- decoded from the SAME base64 constant
    insight.dash.fonts already carries."""
    return base64.b64decode(FONT_MONO_WOFF2_BASE64)


def build_ofl_sans_text():
    return _OFL_SANS_SRC.read_text(encoding="utf-8")


def build_ofl_mono_text():
    return _OFL_MONO_SRC.read_text(encoding="utf-8")


def main():
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_CSS_OUT.write_text(build_tokens_css(), encoding="utf-8")
    SANS_WOFF2_OUT.write_bytes(build_sans_woff2_bytes())
    MONO_WOFF2_OUT.write_bytes(build_mono_woff2_bytes())
    OFL_SANS_OUT.write_text(build_ofl_sans_text(), encoding="utf-8")
    OFL_MONO_OUT.write_text(build_ofl_mono_text(), encoding="utf-8")


if __name__ == "__main__":
    main()
