# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""issue #303 [E17.S2] review fix 1(BLOCKING 1): a mechanical guard that every asset a future
story puts under insight/web/public/ -- referenced from insight/web/src/app/tokens.generated.css
via url(...), such as the self-hosted @font-face woff2 files THIS story added -- actually reaches
the deployed image, not just the local `next dev` server.

next.config.mjs's `output: "standalone"` DELIBERATELY EXCLUDES public/ from `.next/standalone/`
-- verified empirically against a real `next build`: the standalone output directory contains
only server.js, node_modules, and .next, nothing else (Next.js's own documented behaviour for
this output mode). insight/Dockerfile.web's runtime stage must therefore copy insight/web/public/
from the builder stage BY HAND, or every url(...) asset referenced from committed CSS 404s in the
one path this repo actually deploys through. CI's `web` job runs `docker build` but never
`docker run` + a request, so nothing else in the local or CI gate would ever catch this -- an
earlier version of this Dockerfile stage copied only `.next/standalone` and `.next/static` and
shipped exactly that regression (the self-hosted fonts silently fell back to a system face in the
deployed container, in the only path nothing exercises).

Derived from the REAL committed files under insight/web/public/ and the REAL url(...) references
in the REAL committed tokens.generated.css -- NOT a hardcoded list of filenames -- so a future
asset added under public/ is covered automatically, with no second commit to this test file
needed.

A missing insight/Dockerfile.web is a FAILURE here, never a skip: a guard that quietly stops
running the moment its target goes missing is the exact defect class issue #303 exists to
eliminate."""
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
INSIGHT = HERE.parent  # insight/ -- this file lives directly under insight/tests/ (issue #297),
                       # so every path here is derived from HERE, never from a repo root that
                       # would not exist once insight/ is extracted into a repo of its own.
WEB = INSIGHT / "web"
DOCKERFILE = INSIGHT / "Dockerfile.web"
PUBLIC_DIR = WEB / "public"
TOKENS_CSS = WEB / "src" / "app" / "tokens.generated.css"

#: Matches url(...) references inside CSS, with or without quotes -- deliberately plain regex,
#: not a CSS parser, matching this repo's own convention of avoiding a parser dependency for a
#: single mechanical check (see tests/test_web_prove_fonts_ci.py's docstring for the same
#: reasoning applied to ci.yml).
_URL_RE = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")


def _public_asset_urls():
    """Every url(...) reference in the REAL committed tokens.generated.css that resolves to a
    REAL file under insight/web/public/. Only site-root-absolute references ("/fonts/...") are
    candidates -- Next.js serves public/ at the site root, so url("/x") means
    insight/web/public/x on disk; a relative or data: url is never a public/ asset."""
    if not TOKENS_CSS.is_file():
        return []
    text = TOKENS_CSS.read_text(encoding="utf-8")
    hits = []
    for ref in _URL_RE.findall(text):
        if not ref.startswith("/"):
            continue
        candidate = PUBLIC_DIR / ref[1:]
        if candidate.is_file():
            hits.append(ref)
    return hits


def _runtime_stage_text():
    """Isolate insight/Dockerfile.web's runtime stage -- from its `FROM ... AS runtime` line to
    end of file (it is the last stage in the file; see the Dockerfile's own stage-divider
    comments). Raises via `assert` (never returns something that lets the caller silently treat
    a missing/malformed Dockerfile as "nothing to guard") if the file or the stage is absent."""
    assert DOCKERFILE.is_file(), (
        f"{DOCKERFILE} not found -- cannot verify that insight/web/public/ assets referenced by "
        "tokens.generated.css's url(...) rules reach the deployed image. A missing Dockerfile is "
        "a failure of this guard, not something to skip past."
    )
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = re.search(r"\n[Ff][Rr][Oo][Mm]\s+\S+\s+[Aa][Ss]\s+runtime\b(.*)\Z", text, re.S)
    assert m, f"{DOCKERFILE} has no `FROM ... AS runtime` stage -- cannot inspect what it COPYs"
    return m.group(1)


def test_generated_css_public_url_fixture_is_alive():
    """Sanity check on the fixture this whole guard depends on: if insight/web/public/ holds real
    files but tokens.generated.css's url(...) rules never resolve to any of them, either the
    fixture's shape changed or _public_asset_urls()'s own regex/resolution logic broke -- either
    way, test_dockerfile_copies_every_public_asset_referenced_by_css below would trivially pass
    with zero coverage, and this test exists so that failure mode is loud instead of silent."""
    real_files = [p for p in PUBLIC_DIR.rglob("*") if p.is_file()] if PUBLIC_DIR.is_dir() else []
    if not real_files:
        return  # nothing under public/ yet -- nothing for the fixture to be alive about
    assert _public_asset_urls(), (
        f"{PUBLIC_DIR} contains real files but tokens.generated.css's url(...) rules resolve to "
        "none of them -- either the CSS stopped referencing public/ assets (fine, but then this "
        "test's premise no longer holds and it should be revisited) or this test's own regex/"
        "resolution logic broke (not fine)"
    )


def test_dockerfile_copies_every_public_asset_referenced_by_css():
    urls = _public_asset_urls()
    if not urls:
        return  # nothing under insight/web/public/ is referenced by the generated CSS -- the
                # standalone-output public/ exclusion has nothing to bite on yet

    runtime = _runtime_stage_text()
    copies_public = re.search(r"^\s*COPY\b.*\bpublic\b", runtime, re.M)
    assert copies_public, (
        "insight/web/public/ contains asset(s) referenced by tokens.generated.css's url(...) "
        f"rules ({', '.join(sorted(urls))}), but insight/Dockerfile.web's runtime stage has no "
        "COPY line mentioning public/. next.config.mjs's `output: \"standalone\"` deliberately "
        "EXCLUDES public/ from `.next/standalone/` -- verified empirically, the standalone "
        "output holds only server.js, node_modules, and .next -- so without an explicit "
        "`COPY --from=<builder stage> <builder WORKDIR>/public ./public` in the runtime stage, "
        "these assets 404 in the deployed image even though every offline check (verify_web.py, "
        "`docker build`) stays green. Add that COPY line to the runtime stage, after the "
        "existing `.next/standalone` / `.next/static` copies."
    )
