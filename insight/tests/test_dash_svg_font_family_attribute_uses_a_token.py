# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Guard for the SVG `font-family="..."` ATTRIBUTE form (issue #265/D4, Design 8, dossier item i).

`test_dash_embedded_fonts_actually_apply.py` already guards `font-family:` CSS DECLARATIONS (rule
bodies), but its own docstring (L25-30) states plainly that it cannot see the inline-SVG
`font-family="..."` ATTRIBUTE form -- `=`, no leading `:`, no surrounding `{...}` rule body. That
gap is exactly where `panel.py`'s `_bars()`/`_strip()` shipped `font-family="PlexMono,monospace"`
seven times: a family no `@font-face` in the document declares (the real names are
`"Atkinson Hyperlegible"`/`"IBM Plex Mono"`), invisible to every existing content/hex/spacing scan
because it is neither a colour literal nor a bare-spacing value.

Method: scan every `insight/dash/*.py` file's raw SOURCE (not a rendered page -- a static,
per-file scan is a stronger and cheaper guard than a rendered-page scan, since it catches the
defect even in an SVG helper nothing currently calls) for a `font-family="..."` attribute whose
value is not `var(...)`. A literal family name or a bare CSS generic keyword written directly into
an SVG attribute never resolves to the embedded `@font-face` -- the correct form always routes
through the `--{id_prefix}-font-sans`/`--{id_prefix}-font-mono` custom properties, exactly as
`colors.py:424`'s own `not_measured_svg()` already does it.
"""
import pathlib
import re

_DASH_DIR = pathlib.Path(__file__).resolve().parent.parent / "dash"

#: font-family="..." or font-family='...' -- the SVG ATTRIBUTE form, distinct from the CSS
#: `font-family:` DECLARATION form test_dash_embedded_fonts_actually_apply.py already covers.
_FONT_FAMILY_ATTR = re.compile(r'''font-family\s*=\s*["']([^"']+)["']''')


def _non_var_font_family_attrs(source_text):
    """Every `font-family="..."` attribute value in `source_text` that is not a `var(...)`
    reference -- the defect shape this test guards against."""
    return [v for v in _FONT_FAMILY_ATTR.findall(source_text) if not v.strip().startswith("var(")]


def _dash_module_sources():
    return {p: p.read_text() for p in sorted(_DASH_DIR.glob("*.py"))}


def test_every_dash_module_has_at_least_one_font_family_attribute_to_scan():
    """Sanity floor -- without this, the main assertion below would pass vacuously if no module
    in insight/dash/ emitted this attribute form at all."""
    total = sum(
        len(_FONT_FAMILY_ATTR.findall(text)) for text in _dash_module_sources().values()
    )
    assert total > 0, "expected at least one font-family=\"...\" SVG attribute somewhere in insight/dash/"


def test_no_svg_font_family_attribute_is_a_literal_never_a_token():
    offenders = {}
    for path, text in _dash_module_sources().items():
        bad = _non_var_font_family_attrs(text)
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        f"font-family=\"...\" SVG attributes must reference a var(--{{id_prefix}}-font-*) token, "
        f"never a literal family name -- a family no @font-face declares falls back silently to "
        f"a system face (see test_dash_embedded_fonts_actually_apply.py's own docstring for the "
        f"CSS-declaration sibling of this exact defect class). Offenders: {offenders}"
    )


def test_negative_control_proves_the_font_family_attribute_check_has_teeth():
    """Not shipped code -- plants one bogus literal font-family attribute in a copy of a real
    dash module's source and shows the SAME check above now fails."""
    real_source = (_DASH_DIR / "colors.py").read_text()
    assert not _non_var_font_family_attrs(real_source), (
        "fixture regressed: colors.py itself should already be clean of this defect"
    )
    mutated = real_source + '\n# planted: <text font-family="PlexMono,monospace">x</text>\n'
    bad = _non_var_font_family_attrs(mutated)
    assert bad == ["PlexMono,monospace"], (
        "fixture regressed: negative control no longer lands as a detectable offender"
    )
