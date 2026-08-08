// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #313 [E20.S2], .sdlc/plans/313.md Step 8 (done-when 4): "Cold-start test: empty fact
// tables, assert no readout renders a numeral" -- the manager page's own version of this
// guarantee. Structurally a copy of scripts/prove-delivery-cold-start-no-numerals.mjs (same
// getFreePort/startNext/fetchAs/digit-scan shape, same MANDATORY executed negative control
// discipline -- see scripts/lib/cold-start-proof.mjs's own header for why the control is
// executed, never asserted in prose), with two differences: it fetches /manager, and its "not
// vacuous" numeral floor is DERIVED from MANAGER_PRIMARY_READOUT_IDS.length (compiled and
// imported live from src/lib/manager/curation.ts, via the same scripts/lib/tsc-scratch.mjs helper
// prove-delivery-python-bridge-exit-codes.mjs already uses to compile a plain .ts source file)
// rather than a second, hand-typed magic number -- see .sdlc/plans/313.md §3 for why a derived
// floor alone is not the primary defense (the executed negative control is) and for the
// "floor on the floor" sanity check this script also runs.
//
// CI-ONLY, same family as prove:delivery-cold-start -- needs a real DuckDB store. Wired as
// `npm run prove:manager-cold-start`, in .github/workflows/ci.yml's `web` job, immediately after
// prove:delivery-cold-start.
//
// issue #314 [E20.S3], .sdlc/plans/314.md Decision B / Step 3: this file's own server-lifecycle/
// digit-scan/two-section machinery was extracted into scripts/lib/cold-start-proof.mjs (the same
// extraction Step 2 applied to prove-delivery-cold-start-no-numerals.mjs). This file keeps its
// own derive-and-sanity-check logic (loadManagerPrimaryReadoutIds + the ">= 5" floor-on-floor
// check) exactly as before, just relocated into the harness's own `floor` thunk parameter.
import assert from "node:assert/strict";
import { copyFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { runColdStartNoNumeralsProof } from "./lib/cold-start-proof.mjs";
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

/** Compiles the real src/lib/manager/curation.ts with the local tsc and dynamic-import()s the
 * emitted output -- no React, no JSX, a plain constant array, trivial to compile (mirrors
 * prove-delivery-python-bridge-exit-codes.mjs's own compileBridge(), minus the paths-mapped
 * import that file needs and this one doesn't -- curation.ts has zero imports). */
async function loadManagerPrimaryReadoutIds() {
  return runScenarioAsync(".manager-curation-proof-scratch-", async (dir) => {
    writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
    writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
    const src = path.join(WEB_ROOT, "src", "lib", "manager", "curation.ts");
    copyFileSync(src, path.join(dir, "curation.ts"));
    const { ok, output } = runTsc(dir);
    assert.ok(ok, `src/lib/manager/curation.ts must compile clean with the local tsc:\n${output}`);
    const emitted = path.join(dir, "out", "curation.js");
    const mod = await import(pathToFileURL(emitted).href);
    return mod.MANAGER_PRIMARY_READOUT_IDS;
  });
}

/** The harness's own `floor` thunk: derives the numeral floor live from
 * MANAGER_PRIMARY_READOUT_IDS.length, then runs the "floor on the floor" sanity check
 * (.sdlc/plans/313.md §3) -- if the curated id list ever shrank to near-nothing, a derived floor
 * alone would weaken to near-vacuous right along with it. Fails loudly rather than silently
 * degrading. */
async function managerFloor() {
  const managerPrimaryReadoutIds = await loadManagerPrimaryReadoutIds();
  assert.ok(
    managerPrimaryReadoutIds.length >= 5,
    "the curated id list shrank enough to weaken the cold-start floor to near-vacuous -- widen it " +
    "or update this minimum deliberately",
  );
  return managerPrimaryReadoutIds.length;
}

runColdStartNoNumeralsProof({
  proofName: "prove-manager-cold-start-no-numerals",
  route: "/manager",
  role: "manager",
  populateFlags: ["--populate-metric-14"],
  floor: managerFloor,
}).catch((err) => {
  console.error("FAIL: prove-manager-cold-start-no-numerals");
  console.error(err);
  process.exit(1);
});
