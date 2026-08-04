# Notes on `insight/LICENSE`

These notes are deliberately **not** in `LICENSE` itself. BUSL 1.1 covenant 4 commits the licensor
"not to modify this License in any other way", and appending a free-text section after the Notice
is in tension with that. Filling in the **Parameters** block is different — that block exists to be
filled in, and covenants 2 and 3 explicitly direct the licensor to supply those values — so naming
the repository, the Additional Use Grant, and a licensing contact there is conforming use, not
modification. Commentary that is not a parameter lives here.

The licensing contact rides on the `Licensor` value rather than a new `Licensing Contact:` label,
so the Parameters block keeps exactly the five slots BUSL defines. Inventing a sixth label would be
added text, which is the thing covenant 4 speaks to — the same argument that moved these notes out
of the licence in the first place.

## The five parameters are a product decision, not legal advice

`Licensor`, `Licensed Work`, `Additional Use Grant`, `Change Date`, and `Change License` are the
author's stated intent, recorded as a product decision. They have **not been reviewed by a lawyer.**
See section 10, question 0 of
[the design spec](../docs/superpowers/specs/2026-07-30-loopsmith-insight-data-platform-design.md).
Finalising the wording is a one-time legal pass and does not block engineering work.

## Why the Additional Use Grant is phrased as a proviso

Covenant 2 requires the Additional Use Grant to impose no *additional* restriction beyond the
Terms. A grant written as a standalone prohibition ("You may not ...") reads as restricting rights
granted elsewhere in the Terms - which explicitly permit redistribution and non-production use. So
the grant is written as a single permissive sentence with the limit inside it.

## Root `LICENSE` carries no carve-out

The repository root `LICENSE` grants MIT over "the Software" without excluding `insight/`. The
carve-out is stated in the root `README.md` and in `insight/LICENSE`. A root `NOTICE` file is the
standard way to make it unmissable. Tracked as **issue #162**, which also has to relax
`test_plugin_licence_is_still_mit` — as written, that assertion would reject the very carve-out
sentence the fix needs.

## Third-party fonts embedded in `insight/dash/` (2026-08-04, issue #262)

`insight/dash/fonts.py` embeds two subsetted, base64-encoded WOFF2 font payloads, referenced via
`@font-face { src: url(data:font/woff2;base64,...) }` inside `insight.dash.colors.viz_css_vars()`
— the design spec's "distinctive humanist sans for prose/headings, a mono for every number,
identifier, timestamp and provenance line," embedded rather than linked because every `insight
dash --out` page is a single, independently-portable, self-contained HTML file with no server
round-trip (`render.py`'s own `assert_self_contained()`).

Both faces are licensed under the **SIL Open Font License, Version 1.1** — a copy of the OFL 1.1
text ships alongside the embedding module at `insight/dash/fonts/OFL-atkinson-hyperlegible.txt`
and `insight/dash/fonts/OFL-ibm-plex-mono.txt` respectively, as OFL 1.1's own Requirement clause
directs ("Copies of the OFL... must be distributed with any Font Software that includes the
Reserved Font Name(s)"). Neither face's own licence text or copyright notice was altered.

- **Atkinson Hyperlegible** (sans, prose/headings) — Copyright 2020 Braille Institute of America,
  Inc. Fetched from the canonical upstream release,
  <https://github.com/googlefonts/atkinson-hyperlegible> (Google Fonts' own unmodified mirror of
  the Braille Institute's release).
- **IBM Plex Mono** (mono, numbers/identifiers/timestamps/provenance lines) — Copyright 2017 IBM
  Corp. with Reserved Font Name "Plex". Fetched from IBM's own
  <https://github.com/IBM/plex> release `@ibm/plex-mono@2.5.0`.

**Subsetting performed** (build-time only, never at runtime): both faces reduced to their Regular
weight, Basic Latin + Latin-1 Supplement (`U+0000-00FF` — the only range `insight/dash/*.py`'s own
`html.escape()`'d, plain-ASCII-source output ever emits), WOFF2 flavor, via `fonttools`'
`pyftsubset`. No italic, no bold weight embedded — headings and stat-tile values use the browser's
synthetic/faux bold instead, an accepted v1 simplification. `fonttools` and its `brotli` WOFF2
extension are implement-time-only tools; neither is a runtime dependency of `insight/` and neither
appears in `insight/pyproject.toml`'s `dependencies`, which stays `duckdb~=1.4` only.
