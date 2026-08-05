# insight/web/

Next.js (App Router) + TypeScript application — the dashboard UI (design spec §4). No app lives
here yet; **E17.S1** authors it. What DOES live here, as of **E16.S3** (#301): the generated API
types (`src/lib/api/schema.d.ts`) and the npm scaffolding that regenerates and type-checks them,
wired into `insight/verify_web.py`'s four checks. See "What's here" below.

## The BUSL marker for `.ts`/`.tsx`

Every `.ts`/`.tsx` file here must open with the marker derived MECHANICALLY from
`insight/HEADER.txt`'s single `#` line — swap the leading `#` for `//`, nothing else changes:

    // SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.

Enforced by `tests/test_licence_boundary.py` (`_ts_marker`), derived from the one stored string,
never retyped a second time.

**Ordering: the marker comes FIRST, before any `"use client"` (or other) directive prologue.**
Verified against the actual checker (`_carries_marker`): marker-then-directive is accepted because
the marker sits on line 1; directive-then-marker is REJECTED, because unlike the `.py` side there
is no shebang/encoding-cookie allowance for `.ts`/`.tsx` that would let the checker look past a
leading string-literal statement. Putting the marker first is also syntactically safe for Next.js's
own directive-prologue detection, which only cares that `"use client"` is the first *statement* —
a leading line comment before it does not disqualify it. E17.S1 is the first story that has to get
this ordering right; there is no fixup once real `"use client"` files exist.

## What's here (E16.S3, #301) and what E17.S1 still owns

`package.json` + a committed `package-lock.json` arrived in this story, arming
`insight/verify_web.py` for real: every future goal's verify gate now runs `npm ci` plus all four
of `typecheck`/`lint`/`test`/`build`, in a fresh worktree, every time.

- **`typecheck`** is real: `tsc --noEmit` (strict), with a `pretypecheck` hook
  (`scripts/check-schema-fresh.mjs`) that regenerates `src/lib/api/schema.d.ts` from the committed
  `openapi.json` and diffs it byte-for-byte against what's on disk — the enforcement mechanism
  behind "generated, not hand-edited." Regenerate with `node scripts/generate-schema.mjs` (cwd
  `insight/web/`) after re-running `python3 -m insight.api.export_openapi` from the repo root.
- **`test`** is real: `scripts/prove-metric-contract-safety.mjs` is the mechanical proof that
  renaming a Pydantic field and regenerating breaks the frontend type-check (see
  `src/lib/api/metric.consumer.ts`'s `metricLabel`), plus the discriminated-union narrowing proof
  (`measuredValueOrNull`).
- **`lint`** and **`build`** are declared stubs — each echoes that it is a stub and exits 0. No
  ESLint config or Next.js app exists yet to lint or build.

`insight/tests/test_verify_web.py`'s machine-checked invariant — `package.json` must have a
sibling `package-lock.json` and declare every `CHECKS` name — now enforces for real rather than
vacuously.

**E17.S1** replaces the `lint`/`build` stubs with real ESLint/Next.js tooling, and authors the
actual app, without changing the `typecheck`/`test` contract this story built.
