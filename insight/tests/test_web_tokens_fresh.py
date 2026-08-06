# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #303, Decision a. Freshness guard for insight/web/'s three generated design-token
artifacts (five files) -- exact structural mirror of test_openapi_schema_fresh.py: regenerates
every artifact IN MEMORY via insight.dash.generate_web_tokens and asserts byte-equality against
what is actually committed, naming the regenerate command on failure. A hand edit to
tokens.generated.css, or a colors.py token change that isn't followed by re-running the
generator, fails HERE, loudly, in the `insight` CI job -- not silently drifted from what the web
app actually ships.

Also carries the Should-fix 2(b) zero-browser, zero-network guard named in .sdlc/plans/303.md
Step 1: pure-Python declared-vs-embedded font-family matching, extending
insight/tests/test_dash_embedded_fonts_actually_apply.py's own pattern to the generated web CSS.
This is NOT a substitute for the real applied-font proof
(insight/web/scripts/prove-fonts-actually-apply.mjs, done-when 3) -- it is a declared-side check,
the exact class the issue rejects as insufficient on its own -- but it is free, it catches the
exact historical `'Atkinson'` vs `"Atkinson Hyperlegible"` mismatch shape, and it belongs in the
always-on gate regardless of where the browser check ends up running (per Step 1's own
instruction: "Add it regardless of what (a) finds")."""
import re

import insight.dash.generate_web_tokens as gwt

# --------------------------------------------------------------------------- main() itself


def test_main_writes_every_artifact_to_disk(tmp_path, monkeypatch):
    """Covers `main()` directly -- the freshness tests above only ever READ the real committed
    files, never call `main()` to produce them. Monkeypatches the module's own output-path
    attributes to a tmp dir, the same style test_openapi_schema_fresh.py's
    test_main_writes_build_schema_to_out() and test_verify_web.py's `_world()` both use, so this
    never touches the real committed insight/web/ files."""
    fonts_dir = tmp_path / "public" / "fonts"
    monkeypatch.setattr(gwt, "FONTS_DIR", fonts_dir)
    monkeypatch.setattr(gwt, "TOKENS_CSS_OUT", tmp_path / "tokens.generated.css")
    monkeypatch.setattr(gwt, "SANS_WOFF2_OUT", fonts_dir / "AtkinsonHyperlegible-Regular.woff2")
    monkeypatch.setattr(gwt, "MONO_WOFF2_OUT", fonts_dir / "IBMPlexMono-Regular.woff2")
    monkeypatch.setattr(gwt, "OFL_SANS_OUT", fonts_dir / "OFL-atkinson-hyperlegible.txt")
    monkeypatch.setattr(gwt, "OFL_MONO_OUT", fonts_dir / "OFL-ibm-plex-mono.txt")

    gwt.main()

    assert gwt.TOKENS_CSS_OUT.read_text(encoding="utf-8") == gwt.build_tokens_css()
    assert gwt.SANS_WOFF2_OUT.read_bytes() == gwt.build_sans_woff2_bytes()
    assert gwt.MONO_WOFF2_OUT.read_bytes() == gwt.build_mono_woff2_bytes()
    assert gwt.OFL_SANS_OUT.read_text(encoding="utf-8") == gwt.build_ofl_sans_text()
    assert gwt.OFL_MONO_OUT.read_text(encoding="utf-8") == gwt.build_ofl_mono_text()

#: The family name an `@font-face` block declares (double-quoted in this generator's output).
_FONT_FACE_FAMILY = re.compile(
    r"""@font-face\s*\{[^}]*?font-family\s*:\s*['"]([^'"]+)['"]""", re.DOTALL
)
#: The leading (highest-priority) quoted family name in a `--panel-font-sans`/`-mono` custom
#: property -- the name a browser tries FIRST, and therefore the one an `@font-face` must declare
#: for the embedded face to actually apply.
_ROOT_FONT_VAR = re.compile(r"""--panel-font-(?:sans|mono)\s*:\s*['"]([^'"]+)['"]""")


def _committed_text():
    return gwt.TOKENS_CSS_OUT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- freshness (Decision
# a): every generated artifact, byte-for-byte against what colors.py/fonts.py produce right now.


def test_tokens_generated_css_matches_colors_py():
    assert gwt.TOKENS_CSS_OUT.is_file(), (
        f"{gwt.TOKENS_CSS_OUT} does not exist -- run `python3 -m insight.dash.generate_web_tokens`"
    )
    committed = _committed_text()
    fresh = gwt.build_tokens_css()
    assert committed == fresh, (
        "insight/web/src/app/tokens.generated.css is stale -- it does not match what "
        "colors.web_tokens_css() (+ this generator's font-face/typography rules) produce from "
        "insight/dash/colors.py right now. Run `python3 -m insight.dash.generate_web_tokens` "
        "(from the repo root) to regenerate it, then commit the result."
    )


def test_sans_woff2_matches_fonts_py():
    assert gwt.SANS_WOFF2_OUT.is_file(), (
        f"{gwt.SANS_WOFF2_OUT} does not exist -- run `python3 -m insight.dash.generate_web_tokens`"
    )
    assert gwt.SANS_WOFF2_OUT.read_bytes() == gwt.build_sans_woff2_bytes(), (
        "insight/web/public/fonts/AtkinsonHyperlegible-Regular.woff2 is stale -- run "
        "`python3 -m insight.dash.generate_web_tokens`"
    )


def test_mono_woff2_matches_fonts_py():
    assert gwt.MONO_WOFF2_OUT.is_file(), (
        f"{gwt.MONO_WOFF2_OUT} does not exist -- run `python3 -m insight.dash.generate_web_tokens`"
    )
    assert gwt.MONO_WOFF2_OUT.read_bytes() == gwt.build_mono_woff2_bytes(), (
        "insight/web/public/fonts/IBMPlexMono-Regular.woff2 is stale -- run "
        "`python3 -m insight.dash.generate_web_tokens`"
    )


def test_ofl_licence_files_match_source():
    assert gwt.OFL_SANS_OUT.is_file() and gwt.OFL_MONO_OUT.is_file(), (
        "OFL licence files missing under insight/web/public/fonts/ -- run "
        "`python3 -m insight.dash.generate_web_tokens`"
    )
    assert gwt.OFL_SANS_OUT.read_text(encoding="utf-8") == gwt.build_ofl_sans_text()
    assert gwt.OFL_MONO_OUT.read_text(encoding="utf-8") == gwt.build_ofl_mono_text()


# --------------------------------------------------------------------------- done-when 2: no CDN


def test_no_cdn_font_reference_anywhere_in_the_generated_css():
    """Done-when 2's "self-hosted, no CDN" clause, closed mechanically rather than by inspection
    alone -- a one-line grep-style assertion per .sdlc/plans/303.md's own test plan."""
    text = _committed_text()
    for needle in ("fonts.googleapis.com", "fonts.gstatic.com", "use.typekit.net"):
        assert needle not in text, (
            f"{needle!r} found in tokens.generated.css -- fonts must be self-hosted under "
            "insight/web/public/fonts/, never loaded from a CDN"
        )


# --------------------------------------------------------------------------- Should-fix 2(b):
# zero-browser, zero-network declared-vs-embedded font-family guard


def _embedded_families(text):
    return set(_FONT_FACE_FAMILY.findall(text))


def _requested_stack_leaders(text):
    return set(_ROOT_FONT_VAR.findall(text))


def test_every_font_stack_leader_is_declared_by_a_font_face():
    text = _committed_text()
    requested = _requested_stack_leaders(text)
    assert requested, "no --panel-font-sans/-mono custom property found -- check would be vacuous"
    embedded = _embedded_families(text)
    missing = requested - embedded
    assert not missing, (
        f"tokens.generated.css's font stack asks for {sorted(missing)} first, but no @font-face "
        f"in the same file declares that family (only {sorted(embedded)} are declared) -- this "
        "is the exact historical bug shape (issue #273/#303): a mismatched family name falls "
        "back silently to a system face, invisibly, on every page it ships on"
    )


def test_negative_control_mismatched_stack_leader_trips_the_font_family_guard():
    """Not shipped code -- splices the exact historical bug shape (`--panel-font-sans:
    'Atkinson', ...` instead of the correct `"Atkinson Hyperlegible"`) into a copy of the real
    generated CSS and proves the SAME assertion used above now fails. Without this, a check that
    can never go red is not proof of anything."""
    text = _committed_text()
    mutated = text.replace(
        '--panel-font-sans: "Atkinson Hyperlegible"', "--panel-font-sans: 'Atkinson'", 1,
    )
    assert mutated != text, "fixture regressed: substitution did not land"

    requested = _requested_stack_leaders(mutated)
    embedded = _embedded_families(mutated)
    assert "Atkinson" in requested, "negative control did not reproduce the mismatch shape"
    assert "Atkinson" not in embedded, (
        "fixture regressed: 'Atkinson' is now (accidentally) an embedded family"
    )
    missing = requested - embedded
    assert missing, "negative control failed to trip the guard -- 'Atkinson' resolved anyway"
