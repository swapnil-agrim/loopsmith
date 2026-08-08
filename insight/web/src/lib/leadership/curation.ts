// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #314 [E20.S3], .sdlc/plans/314.md Decision A. The leadership view's curated subset of the
// SAME catalog fetchDeliveryMetrics() already serves (mirrors .sdlc/plans/313.md §1's manager
// precedent -- no new CLI action, no new SQL, no new API surface). A separate, importable
// constant (not inlined in page.tsx) so scripts/prove-leadership-cold-start-no-numerals.mjs can
// derive its "not vacuous" floor from the SAME list the page renders, instead of a second,
// hand-typed magic number.
//
// Mapped to insight/dash/leadership.py:302-390's five panels, using each metric's own `personas:`
// header field (insight/metrics/*.sql) as the authoritative "is this leadership's metric" signal
// -- not eyeballed from catalog.py's flat id->label dict:
//
//   id  name                             panel                          class  resolves to
//   1   Throughput                       Speed (#1)                       1    absent_unbuilt
//   5   Change failure rate              Quality (#5)                     1    measured
//   9   Flow distribution                Impact (#9)                      1    absent_unbuilt
//   23  Gate catch rate by gate          -- (class-2 positive proof)      2    measured
//   27  Decision-gate denials            -- (class-2 negative proof)      2    absent_unbuilt
//   30  Debt inventory + trend           governance, leadership-tagged    1    absent_unbuilt
//   37  Ownership concentration          governance, leadership-tagged    1    absent_unbuilt
//   41  Portfolio table                  Portfolio (#41)                  1    absent_unbuilt
//   42  Adoption & flag correlation      leadership-tagged                1    absent_unbuilt
//
// Only 5 and 23 resolve `measured` -- the concrete, non-vacuous exercise of done-when 2: id 23 is
// a class-2 metric that legitimately carries a numeral + coverage denominator (the positive half),
// paired against id 27, an unwired class-2 metric that stays honestly absent (the negative half).
// Effectiveness has NO catalog id at all (leadership.py's own comment: "reads no table") -- there
// is structurally nothing to curate for it; inventing a fake id or a bespoke non-primitive tile to
// stand in for it would violate done-when 1 (no bespoke local styling) and is not attempted.
//
// EXCLUDED DELIBERATELY: id 13 (Interventions per goal) is leadership-tagged AND has a registered
// AGGREGATE_EXTRACTORS[13] -- it would render a real `measured` numeral if curated. Its own header
// (insight/metrics/13.sql) says `DATA_STATUS: DARK` -- "fact_goal.outcome and fact_event are 0/19
// and 0 populated respectively in this repo's own real ingest" -- a fixture-green number that is
// misleading in production, the exact ABSENT != PASS failure this product exists to prevent. That
// reason stands alone and is sufficient; panel-mapping (several curated ids above map to no
// leadership.py panel either) is NOT the criterion -- DARK-vs-honest-absence is.
export const LEADERSHIP_PRIMARY_READOUT_IDS: readonly number[] = [1, 5, 9, 23, 27, 30, 37, 41, 42];
