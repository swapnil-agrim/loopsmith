// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// The manager view's curated subset of the SAME catalog fetchDeliveryMetrics() already serves
// (issue #313 [E20.S2] -- see .sdlc/plans/313.md §1 for why no new CLI action was added). A
// separate, importable constant (not inlined in page.tsx) so
// scripts/prove-manager-cold-start-no-numerals.mjs can derive its "not vacuous" floor from the
// SAME list the page renders, instead of a second, hand-typed magic number.
export const MANAGER_PRIMARY_READOUT_IDS: readonly number[] = [1, 7, 9, 10, 11, 14, 15, 16, 31, 32];
