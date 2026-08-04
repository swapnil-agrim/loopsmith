# insight/web/

Next.js (App Router) + TypeScript application — the dashboard UI (design spec §4). Nothing lives
here yet; **E17.S1** authors the app and wires `insight/verify_web.py`'s four checks
(typecheck/lint/test/build).

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

## `package.json` must NOT appear before E17.S1

Its mere existence arms `insight/verify_web.py`: it starts running `npm ci` (which hard-fails with
`EUSAGE` without a committed `package-lock.json`) and then requires all four of
`typecheck`/`lint`/`test`/`build` to exist as npm scripts and pass, inside every future goal's
verify gate, in a fresh worktree. Landing `package.json` here without a committed lockfile and all
four scripts would park every subsequent goal on an unrelated failure. E17.S1 lands both together.

This is not just prose: `insight/tests/test_verify_web.py` carries a machine-checked invariant — IF
`insight/web/package.json` exists THEN it must have a sibling `package-lock.json` and its `scripts`
must declare every name `insight/verify_web.py`'s `CHECKS` requires (read from that module, never
retyped). Today the invariant is vacuously true (the file does not exist); the day E17.S1 commits
it, the guard starts enforcing for real and nothing here has to change or be deleted.
