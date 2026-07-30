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
