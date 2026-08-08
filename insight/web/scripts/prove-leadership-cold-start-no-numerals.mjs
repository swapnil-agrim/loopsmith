// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #314 [E20.S3], .sdlc/plans/314.md Step 9 (done-when 4): the leadership page's own version
// of the cold-start guarantee prove-delivery-cold-start-no-numerals.mjs and
// prove-manager-cold-start-no-numerals.mjs already give their own pages -- see
// scripts/lib/cold-start-proof.mjs's own header for the full "why a cold-start proof at all"
// rationale (this file is a thin call site of that shared harness, written directly against it
// since leadership never existed as a standalone copy -- Decision B).
//
// Its "not vacuous" numeral floor is DERIVED from LEADERSHIP_PRIMARY_READOUT_IDS.length (compiled
// and imported live from src/lib/leadership/curation.ts, the same tsc-scratch.mjs pattern
// prove-manager-cold-start-no-numerals.mjs uses for its own curated list), plus the same "floor on
// the floor" sanity check (.sdlc/plans/313.md §3): if the curated id list ever shrank to
// near-nothing, a derived floor alone would weaken to near-vacuous right along with it.
//
// Populates BOTH metric_5 and metric_23 for its negative control (Step 8's plan-review amendment)
// -- metric_5 (Change failure rate) is a plain VALUE_EXTRACTORS[5] reading, metric_23 (Gate catch
// rate by gate) is the class-2 AGGREGATE_EXTRACTORS[23] reading Decision A calls "the concrete,
// non-vacuous exercise of done-when 2". The extra `assertPopulated` hook below is what actually
// demonstrates done-when 2's positive half: metric 23 must render BOTH its numeral AND its
// coverage denominator once populated -- the `absent_unbuilt` id 27 is its negative half and needs
// no seeding (an unwired class-2 metric with no store cannot become measured no matter what is
// seeded).
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

/** Compiles the real src/lib/leadership/curation.ts with the local tsc and dynamic-import()s the
 * emitted output -- no React, no JSX, a plain constant array, trivial to compile (mirrors
 * prove-manager-cold-start-no-numerals.mjs's own loadManagerPrimaryReadoutIds()). */
async function loadLeadershipPrimaryReadoutIds() {
  return runScenarioAsync(".leadership-curation-proof-scratch-", async (dir) => {
    writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
    writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
    const src = path.join(WEB_ROOT, "src", "lib", "leadership", "curation.ts");
    copyFileSync(src, path.join(dir, "curation.ts"));
    const { ok, output } = runTsc(dir);
    assert.ok(ok, `src/lib/leadership/curation.ts must compile clean with the local tsc:\n${output}`);
    const emitted = path.join(dir, "out", "curation.js");
    const mod = await import(pathToFileURL(emitted).href);
    return mod.LEADERSHIP_PRIMARY_READOUT_IDS;
  });
}

async function leadershipFloor() {
  const leadershipPrimaryReadoutIds = await loadLeadershipPrimaryReadoutIds();
  assert.ok(
    leadershipPrimaryReadoutIds.length >= 5,
    "the curated id list shrank enough to weaken the cold-start floor to near-vacuous -- widen it " +
    "or update this minimum deliberately",
  );
  return leadershipPrimaryReadoutIds.length;
}

// ---- Decision A's positive half: metric 23 must render measured, WITH its coverage denominator --

const COVERAGE_SLOT_RE = /data-testid="metric-coverage"[^>]*>([^<]*)</g;

function extractCoverageTexts(body) {
  return [...body.matchAll(COVERAGE_SLOT_RE)].map((m) => m[1]);
}

/** seed-cold-start-store.py's own --populate-metric-23 seeds gate_event_count=10, catch_count=4
 * -- _gate_catch_rate (insight/api/metrics.py:120-129) resolves that to value=0.4 (unit "ratio",
 * so "40.0%" per metric-view.ts's own formatRatio), coverage numerator=4, denominator=10 (so
 * coverageText "4/10" per describeMetric()). Asserted directly against the real formatting
 * functions, not hand-computed, so this proof cannot silently drift from either. */
function assertMetric23MeasuredWithCoverage(body) {
  const numerals = extractNumeralTexts(body);
  assert.ok(
    numerals.includes("40.0%"),
    "metric 23 (Gate catch rate by gate) must render its numeral on the populated leadership " +
    `page -- expected "40.0%" among the rendered metric-numeral slots, found: ${JSON.stringify(numerals)}`,
  );
  const coverages = extractCoverageTexts(body);
  assert.ok(
    coverages.includes("4/10"),
    "metric 23 (Gate catch rate by gate) must render its coverage denominator on the populated " +
    `leadership page -- expected "4/10" among the rendered metric-coverage slots, found: ${JSON.stringify(coverages)}`,
  );
}

runColdStartNoNumeralsProof({
  proofName: "prove-leadership-cold-start-no-numerals",
  route: "/leadership",
  role: "leadership",
  populateFlags: ["--populate-metric-5", "--populate-metric-23"],
  floor: leadershipFloor,
  assertPopulated: assertMetric23MeasuredWithCoverage,
}).catch((err) => {
  console.error("FAIL: prove-leadership-cold-start-no-numerals");
  console.error(err);
  process.exit(1);
});
