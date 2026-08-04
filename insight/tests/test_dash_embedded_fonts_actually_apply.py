# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Every embedded typeface a page pays for must be a typeface that page can actually use.

`colors.panel_css_vars()` / `colors.viz_css_vars()` embed two ~13 KB base64 WOFF2 faces per page
under the family names `"Atkinson Hyperlegible"` and `"IBM Plex Mono"` (issue #273). A rule that
asks for a family NO `@font-face` in the same document declares does not fall back loudly -- it
falls back silently to a system face, so the page still renders, still passes every content and
contrast assertion, and simply never shows the typeface it shipped. Nothing else in this suite
catches that: it is invisible to text/JSON/markup equality and to the hex-literal and
bare-spacing scans.

That is not hypothetical. `instrument.FRAME_CSS` was extracted from `panel.py` carrying
`font-family:'Atkinson',...` and `font-family:'PlexMono',...` -- names no `@font-face` defines.
On `panel.py` alone that was a latent, long-standing bug; but the four persona pages previously
took their body font from `shell.base_style()`'s `font-family: var(--dash-font-sans)`, which
resolves correctly, so routing them through the shared frame would have REGRESSED four working
pages to a system face. This test is the guard for that class of defect, on every page at once.

Method: pull every quoted family name out of every `font-family:` declaration in a rendered
page, and require each one to be declared by an `@font-face` in that same document. Unquoted
names (`monospace`, `ui-sans-serif`, `sans-serif`) are CSS generic/system keywords, not embedded
faces, and are correctly exempt. Falsifiable by construction -- the negative control below
splices one bogus quoted family into a real rendered page and proves the same assertion fails.
"""
import datetime
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.dash.cross_functional import render_cross_functional_view  # noqa: E402
from insight.dash.ic import render_ic_view  # noqa: E402
from insight.dash.leadership import render_leadership_view  # noqa: E402
from insight.dash.manager import render_manager_view  # noqa: E402
from insight.dash.panel import render_panel  # noqa: E402

NOW = datetime.datetime(2026, 8, 1)

#: A quoted family name inside a `font-family:` declaration -- single or double quoted. The
#: declaration value runs to the `;` or the closing `}` of the rule.
_FONT_FAMILY_DECL = re.compile(r"font-family\s*:\s*([^;}]+)")
_QUOTED_FAMILY = re.compile(r"""['"]([^'"]+)['"]""")

#: The family name an `@font-face` block declares (always double-quoted in colors.py).
_FONT_FACE_FAMILY = re.compile(
    r"""@font-face\s*\{[^}]*?font-family\s*:\s*['"]([^'"]+)['"]""", re.DOTALL
)


def _quoted_families_requested(html_text):
    """Every quoted family name any rule in this document asks for."""
    requested = set()
    for value in _FONT_FAMILY_DECL.findall(html_text):
        requested.update(_QUOTED_FAMILY.findall(value))
    return requested


def _families_embedded(html_text):
    """Every family name an @font-face in this document actually defines."""
    return set(_FONT_FACE_FAMILY.findall(html_text))


def _render_all_pages(conn_factory):
    """One fresh connection per page (mirrors __main__.py's own one-connection-per-page
    convention); `render_panel` returns the HTML string directly, the other four return a
    `(html, summary)` pair."""
    pages = {}
    pages["panel"] = render_panel(conn_factory(), metrics_dir="insight/metrics", now=NOW)
    pages["manager"], _ = render_manager_view(conn_factory(), now=NOW)
    pages["leadership"], _ = render_leadership_view(conn_factory(), now=NOW)
    pages["ic"], _ = render_ic_view(conn_factory(), "alice", now=NOW)
    pages["cross-functional"], _ = render_cross_functional_view(conn_factory(), now=NOW)
    return pages


@pytest.fixture
def rendered_pages(tmp_path):
    def _make_conn():
        c = duckdb.connect(str(tmp_path / f"s-{_make_conn.n}.duckdb"))
        _make_conn.n += 1
        ensure_schema(c)
        return c

    _make_conn.n = 0
    return _render_all_pages(_make_conn)


def test_every_page_embeds_at_least_one_face_and_asks_for_a_family(rendered_pages):
    """Sanity floor -- without this, the main assertion below would pass vacuously on a page
    that requested no quoted family at all."""
    for name, html_text in rendered_pages.items():
        assert _families_embedded(html_text), f"{name}.html embeds no @font-face at all"
        assert _quoted_families_requested(html_text), (
            f"{name}.html asks for no quoted font family -- the check below would be vacuous"
        )


def test_no_page_asks_for_a_font_family_it_never_embedded(rendered_pages):
    for name, html_text in rendered_pages.items():
        missing = _quoted_families_requested(html_text) - _families_embedded(html_text)
        assert not missing, (
            f"{name}.html asks for font families it never embeds: {sorted(missing)}. The page "
            f"embeds {sorted(_families_embedded(html_text))}. A misspelt family does not fail "
            "loudly -- it silently falls back to a system face, so the embedded typeface this "
            "page pays ~26 KB to ship never renders. Reference the tokens "
            "(var(--panel-font-sans) / var(--panel-font-mono)) rather than naming a family "
            "literally."
        )


def test_negative_control_proves_the_missing_family_check_has_teeth(rendered_pages):
    """Not shipped code -- splices one bogus quoted family into a real rendered page and proves
    the SAME assertion used above now fails."""
    html_text = rendered_pages["manager"]
    assert not _quoted_families_requested(html_text) - _families_embedded(html_text)

    mutated = html_text.replace(
        "</style>", "body { font-family:'PlexMono',monospace; }</style>", 1,
    )
    assert "PlexMono" in _quoted_families_requested(mutated), (
        "fixture regressed: negative control no longer lands inside the mutated page"
    )
    with pytest.raises(AssertionError):
        assert not _quoted_families_requested(mutated) - _families_embedded(mutated)
