# Notes on `insight/LICENSE`

These notes are deliberately **not** in `LICENSE` itself: BUSL 1.1 covenant 4 commits the licensor
"not to modify this License in any other way", and appending a section to the licence text is in
tension with that. They live here instead.

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
standard way to make it unmissable; tracked as a follow-up on the foundation epic.
