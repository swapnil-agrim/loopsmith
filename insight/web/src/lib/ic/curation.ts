// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #315 [E20.S4], .sdlc/plans/315.md Decision D3. The IC view's curated subset of the SAME
// catalog fetchDeliveryMetrics() already serves (mirrors .sdlc/plans/313.md/.sdlc/plans/314.md's
// manager/leadership precedent -- no new CLI action, no new SQL, no new API surface). A separate,
// importable constant (not inlined in page.tsx) so
// scripts/prove-ic-cold-start-no-numerals.mjs can derive its "not vacuous" floor from the SAME
// list the page renders, instead of a second, hand-typed magic number.
//
// The complete set of catalog ids whose .sql header carries `personas: ... IC ...` (grepped every
// insight/metrics/*.sql header directly, re-verifiable via
// `grep -l "personas:.*IC" insight/metrics/*.sql`):
//
//   id  name                                    class  resolves to
//   2   Cycle time                                1     measured
//   3   Lead time for change                      1     measured when non-null
//   4   Merge frequency                           1     absent_unbuilt (no VALUE_EXTRACTORS entry)
//   5   Change failure rate (proxy)              1 (proxy) measured
//   26  Verify reliability (current state)        1     absent_unbuilt (dark, no extractor)
//   32  Handoff response time                     1     absent_unbuilt (dark, no extractor)
//
// insight/api/metrics.py's VALUE_EXTRACTORS dict contains exactly {2, 3, 5, 12, 14, 16, 20} --
// 12/14/16/20 are not IC-tagged, and 4/26/32 have no entry, so they resolve `absent_unbuilt`
// structurally regardless of any fixture. No new extractor is added anywhere by this story (per
// #573 precedent) -- ids 4, 26, 32 stay `absent_unbuilt` under every fixture this story seeds.
//
// THE CLASS-2 HALF OF DONE-WHEN 2 IS HONESTLY VACUOUS FOR THIS PERSONA, NOT GAMED BY BORROWING AN
// OFF-PERSONA ID. Checked every class-2 id in the catalog (15, 16, 17, 18, 19, 22, 23, 27, 29) --
// none carry `personas: IC`. Leadership (#314) curated ids 23/27 for its OWN class-2 exercise, but
// verified those ARE `personas: manager, leadership`-tagged -- leadership only ever curated from
// its own persona's ids, per that file's own stated principle ("using each metric's own
// `personas:` header field... as the authoritative signal"). Borrowing id 23 or 27 onto this page
// would be displaying a metric that is not, by the codebase's own standing rule, an IC metric at
// all -- the exact thing leadership's own precedent establishes as *not* done. No class-2 catalog
// id is tagged for the IC persona, so done-when 2's class-2 clause is exercised zero times by IC's
// own catalog surface -- an honest fact about the catalog, not a gap this story papers over with a
// borrowed id. The general invariant itself ("a class-2 metric without a coverage denominator
// cannot be displayed") is already enforced structurally by the Metric/MeasuredMetric type
// contract and proven, catalog-wide, by prove-metric-contract-safety.mjs and
// prove-metric-view-behavior.mjs -- it does not need a second, per-persona proof to remain true.
export const IC_PRIMARY_READOUT_IDS: readonly number[] = [2, 3, 4, 5, 26, 32];
