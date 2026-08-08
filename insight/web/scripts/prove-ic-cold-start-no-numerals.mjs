// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #315 [E20.S4], .sdlc/plans/315.md Decisions D1/D2, Task 6 (done-when 4): /ic's own version
// of the cold-start guarantee prove-delivery-cold-start-no-numerals.mjs,
// prove-manager-cold-start-no-numerals.mjs and prove-leadership-cold-start-no-numerals.mjs already
// give their own pages -- see scripts/lib/cold-start-proof.mjs's own header for the full "why a
// cold-start proof at all" rationale (this file is a thin call site of that shared harness).
//
// PASSES `actor: "carol"` (D5) -- the one thing every other caller of runColdStartNoNumeralsProof
// omits -- because /ic's own page.tsx calls auth() itself and needs a session cookie minted under
// BOTH the plain and `__Secure-`-prefixed names to resolve an identity at all (see
// scripts/lib/cold-start-proof.mjs's own fetchRouteAs() comment). Without this, both sections
// would silently pass for the wrong reason: no identity resolved at all (the fail-closed branch),
// never "identity resolved, correctly hatched."
//
// Its "not vacuous" numeral floor is DERIVED from IC_PRIMARY_READOUT_IDS.length (compiled and
// imported live from src/lib/ic/curation.ts, the same tsc-scratch.mjs pattern
// prove-leadership-cold-start-no-numerals.mjs uses for its own curated list) PLUS the 5 fixed
// ActorReadout tiles (my queue, blocked on me, my parks, my gate verdicts given, my cost) --
// named in a comment below, per Task 6 step 12. Floor-on-floor sanity check (plan-review
// SHOULD-FIX, corrected from round 1): asserted on the CURATED LIST LENGTH directly
// (`icPrimaryReadoutIds.length >= 3`), the same shape leadership's own proof uses -- NOT on the
// derived total, which round 1 got wrong (a zero-length list derives a total of exactly 5, which
// fails a `>= 8` check the way round 1 asserted it, contradicting round 1's own stated rationale).
//
// EXECUTED NEGATIVE CONTROL, EXTENDED PER D1'S FIX (the whole point of this file, not merely of
// the shared harness): `assertPopulated` below does not just check that SOME numeral appears --
// it asserts each bespoke readout's numeral independently, so a readout whose OWN source table (or
// own predicate against a shared table) was never ingested stays hatched even though the actor has
// appeared and OTHER readouts are now live. `--populate-actor-rows` (seed-cold-start-store.py)
// writes ONE fact_event row (carol, kind='claimed') and ONE fact_handoff row (carol -> dave) --
// NEVER a fact_event row with kind='parked', and NEVER any fact_pr_review row at all. Verified
// live against the real seed script, not assumed: this means TWO of the four bespoke readouts stay
// genuinely hatched under this exact fixture, not one --
//   - "My queue" and "Blocked on me" go MEASURED: their own ever-ingested predicates
//     (`kind IN ('claimed','done','parked','failed')` and "any fact_handoff row exists",
//     respectively) are satisfied by the seeded rows, and carol has appeared.
//   - "My parks" stays HATCHED: `_park_ever_ingested`'s own predicate is the NARROWER
//     `kind = 'parked'` specifically (mirroring `_park_count`'s own WHERE clause per D1) -- the
//     seeded row's kind is 'claimed', not 'parked', so this predicate is never satisfied by this
//     fixture even though fact_event (the same physical table) has been written to. This is
//     D1's OWN point sharpened, not weakened: the gate tracks each readout's SPECIFIC query, not a
//     coarser "has this table ever been touched at all" signal -- two readouts reading the SAME
//     table can legitimately disagree about whether IT has been ingested for THEM.
//   - "My gate verdicts given" stays HATCHED: fact_pr_review is never written by this fixture at
//     all.
// This is a DELIBERATE, VERIFIED-LIVE deviation from this task's own literal draft note (which
// expected "My parks" to render a measured "0" here) -- run and observed directly against the real
// seed-cold-start-store.py and the real _park_ever_ingested predicate before being written this
// way, not assumed from the plan text. Asserting the two genuinely-hatched readouts (park AND
// verdicts) rather than only one is a STRICTER, not weaker, executed proof of D1's fix: under the
// rejected round-1 single `actor_ever_appeared` gate, BOTH of these assertions would have failed
// (both would have rendered a confident "0" instead).
import assert from "node:assert/strict";
import { copyFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { extractNumeralTexts, runColdStartNoNumeralsProof } from "./lib/cold-start-proof.mjs";
import { WEB as WEB_ROOT, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";

// ---- derive the numeral floor from the SAME list the page renders -------------------------------

function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022", lib: ["ES2022"], module: "ESNext", moduleResolution: "Bundler",
      strict: true, noEmit: false, outDir: "out", esModuleInterop: true, skipLibCheck: true,
      forceConsistentCasingInFileNames: true,
    },
    include: ["*.ts"],
  };
}

/** Compiles the real src/lib/ic/curation.ts with the local tsc and dynamic-import()s the emitted
 * output -- no React, no JSX, a plain constant array (mirrors
 * prove-leadership-cold-start-no-numerals.mjs's own loadLeadershipPrimaryReadoutIds()). */
async function loadIcPrimaryReadoutIds() {
  return runScenarioAsync(".ic-curation-proof-scratch-", async (dir) => {
    writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
    writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
    const src = path.join(WEB_ROOT, "src", "lib", "ic", "curation.ts");
    copyFileSync(src, path.join(dir, "curation.ts"));
    const { ok, output } = runTsc(dir);
    assert.ok(ok, `src/lib/ic/curation.ts must compile clean with the local tsc:\n${output}`);
    const emitted = path.join(dir, "out", "curation.js");
    const mod = await import(pathToFileURL(emitted).href);
    return mod.IC_PRIMARY_READOUT_IDS;
  });
}

// The 5 fixed ActorReadout tiles page.tsx always renders regardless of state: my queue, blocked
// on me, my parks, my gate verdicts given, my cost -- each carries data-testid="metric-numeral"
// (D2), so each counts toward the digit-scan floor even while hatched.
const FIXED_ACTOR_READOUT_COUNT = 5;

async function icFloor() {
  const icPrimaryReadoutIds = await loadIcPrimaryReadoutIds();
  // Floor-on-floor, corrected per plan-review SHOULD-FIX (round 2): asserted on the curated LIST
  // LENGTH directly (matching leadership's own precedent), not on the derived total -- see this
  // file's own header comment for why round 1's `>= 8`-on-the-total check contradicted its own
  // stated rationale.
  assert.ok(
    icPrimaryReadoutIds.length >= 3,
    "the curated id list shrank enough to weaken the cold-start floor to near-vacuous -- widen it " +
    "or update this minimum deliberately",
  );
  return icPrimaryReadoutIds.length + FIXED_ACTOR_READOUT_COUNT;
}

// ---- D1's executed asymmetry proof: each readout's own numeral, independently ------------------

function extractByReadoutId(body, readoutId) {
  const re = new RegExp(`data-readout-id="${readoutId}"[^>]*>([^<]*)<`);
  const m = body.match(re);
  assert.ok(m, `data-readout-id="${readoutId}" not found in the populated /ic response body`);
  return m[1];
}

/** seed-cold-start-store.py's own --populate-metric-5 seeds the SAME view
 * prove-leadership-cold-start-no-numerals.mjs's own negative control already establishes:
 * window_commit_count=20, repeated_revert_or_fixup_count=3, change_failure_rate=0.15 --
 * _change_failure_rate resolves that to value=0.15 (unit "ratio", so "15.0%" per
 * metric-view.ts's own formatRatio). Asserted directly against the real formatting function, not
 * hand-computed. --populate-actor-rows seeds carol's own real rows (a 'claimed' fact_event row,
 * and a carol -> dave fact_handoff row) -- see seed-cold-start-store.py's own docstring, the SAME
 * fixture prove-leadership-no-individual-grain-leak.mjs already relies on. */
function assertPopulated(body) {
  const numerals = extractNumeralTexts(body);
  assert.ok(
    numerals.includes("15.0%"),
    "metric 5 (Change failure rate) must render its numeral on the populated /ic page -- expected " +
    `"15.0%" among the rendered metric-numeral slots, found: ${JSON.stringify(numerals)}`,
  );

  // "My queue" -- carol's own 'claimed' row makes both my_queue_ever_ingested true (the table has
  // a matching row) AND her own open-claim count real: exactly 1.
  assert.equal(
    extractByReadoutId(body, "ic-my-queue-count"), "1",
    "carol's My queue numeral must render her one real open claim, not stay hatched",
  );

  // "Blocked on me" -- fact_handoff was populated (handoff_ever_ingested true) and carol has
  // appeared, so her real, zero-valued blocked-on-me count now renders as a numeral: she is the
  // seeded hand-off's from_actor, not its to_actor, so nothing is outstanding FOR her.
  assert.equal(
    extractByReadoutId(body, "ic-blocked-on-me-count"), "0",
    "carol's Blocked on me numeral must render a real, measured 0 (fact_handoff has been " +
    "ingested; nothing is outstanding TO her specifically), not stay hatched",
  );

  // THE asymmetry assertions (D1's fix, executed) -- TWO independent readouts must stay HATCHED
  // (empty numeral text) even though carol has appeared and her other readouts are now real
  // numerals, each for its own reason (see this file's own header comment for why there are two,
  // verified live against the real seed fixture rather than assumed):

  // "My parks": _park_ever_ingested's own predicate (kind = 'parked') is narrower than
  // _my_queue_kind_ever_ingested's (kind IN (...)) -- the ONE seeded fact_event row is
  // kind='claimed', which satisfies the latter but not the former, even though both read the same
  // physical table. A non-empty value here means the per-readout predicate has been coarsened back
  // toward "has fact_event ever been touched at all," losing exactly the precision D1 exists for.
  assert.equal(
    extractByReadoutId(body, "ic-park-count"), "",
    "My parks must stay hatched (empty numeral) -- the seeded fact_event row is kind='claimed', " +
    "not kind='parked', so park_ever_ingested's own narrower predicate is never satisfied by this " +
    "fixture even though fact_event (the same table my_queue reads) has been written to.",
  );

  // "My gate verdicts given": --populate-actor-rows never writes fact_pr_review at all, so
  // verdicts_ever_ingested stays false unconditionally under this fixture. A non-empty value here
  // means D1's per-table gate has regressed to round 1's single actor_ever_appeared gate.
  assert.equal(
    extractByReadoutId(body, "ic-verdicts-given-count"), "",
    "My gate verdicts given must stay hatched (empty numeral) -- fact_pr_review has never been " +
    "ingested for anyone in this fixture, even though carol has appeared and her other readouts " +
    "are now real numerals.",
  );

  console.log(
    "OK: D1's per-table asymmetry, executed -- My queue (1) and Blocked on me (0) render real " +
    "numerals while My parks and My gate verdicts given both stay hatched, each for its own " +
    "readout-specific reason (see this file's own header comment)",
  );
}

runColdStartNoNumeralsProof({
  proofName: "prove-ic-cold-start-no-numerals",
  route: "/ic",
  role: "ic",
  actor: "carol",
  populateFlags: ["--populate-metric-5", "--populate-actor-rows"],
  floor: icFloor,
  assertPopulated,
}).catch((err) => {
  console.error("FAIL: prove-ic-cold-start-no-numerals");
  console.error(err);
  process.exit(1);
});
