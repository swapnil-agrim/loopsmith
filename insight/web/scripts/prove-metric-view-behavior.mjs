// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md Step 1. Plain node:assert behavior proof for
// describeMetric() -- browser-free, sub-second, wired into `npm run test` alongside
// prove-metric-contract-safety.mjs (package.json's `test` script chains both).
//
// Proves done-when 3's logic half (fixText differs between the two absent states) and
// done-when 4's logic half (coverage text is always present for a measured metric, and always
// carries both the numerator and denominator).
//
// Compiles the REAL src/lib/metric-view.ts (plus its type-only api/metric.ts + api/schema.d.ts
// dependency, copied in unmodified) with the LOCAL node_modules/.bin/tsc into a scratch dir, then
// dynamic-import()s the emitted JS and runs these assertions against THAT -- the real source,
// compiled, not a copy and not a re-implementation. This is the same "compile a scratch dir with
// the local tsc" pattern prove-metric-contract-safety.mjs already uses for its own scenarios;
// both scripts now share the machinery in ./lib/tsc-scratch.mjs.
//
// This replaces an earlier version of this file that imported metric-view.ts directly and relied
// on Node's UNFLAGGED TypeScript type-stripping. That was unsafe: unflagged stripping only
// shipped in Node 22.18.0 (2025-07-31) -- every Node 22.x before that requires
// `--experimental-strip-types`, and this repo pins no Node version anywhere (no `engines` field,
// no `.nvmrc`). Since this script is wired into `npm run test`, which is inside
// insight/verify_web.py's always-on CHECKS -- the repo-wide gate that runs in a FRESH worktree
// for EVERY goal in this repo, with `verify.enforce: true` -- a contributor, runner, or container
// on any pre-22.18 Node 22.x would hard-crash on the bare `.ts` import
// (`ERR_UNKNOWN_FILE_EXTENSION`) and park EVERY goal in the repo on that machine, for a reason
// unrelated to the goal being run. Compiling through the local `tsc` binary -- already required
// by `npm run typecheck`, guaranteed present after `npm ci` -- needs no type-stripping at all, so
// this now runs on any Node version that can already run this repo's toolchain.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { mkdirSync, writeFileSync, copyFileSync } from "node:fs";
import path from "node:path";

import { WEB, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";

const SRC_LIB = path.join(WEB, "src", "lib");
const SRC_API = path.join(SRC_LIB, "api");

// The same compiler options as the real insight/web/tsconfig.json, restated here for the same
// reason prove-metric-contract-safety.mjs's own scratchTsconfig() restates them: this scenario
// compiles a flat scratch directory of its own (no src/ nesting), and staying self-contained
// keeps it immune to an unrelated tsconfig.json edit silently loosening what this proves.
// `noEmit: false` + `outDir` is the one deliberate difference from that sibling function -- this
// scenario needs the compiled JS on disk to import(), not just a type-check.
function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022",
      lib: ["ES2022"],
      module: "ESNext",
      moduleResolution: "Bundler",
      strict: true,
      noEmit: false,
      outDir: "out",
      esModuleInterop: true,
      skipLibCheck: true,
      forceConsistentCasingInFileNames: true,
    },
    include: ["*.ts", "api/*.ts"],
  };
}

/** Compiles the real metric-view.ts (+ its type-only api/ dependency, needed only so `tsc` can
 * resolve the `import type { Metric }` -- that import is erased from the emitted JS regardless,
 * since it is declared `import type`) into `dir`. Returns the absolute path to the emitted
 * metric-view.js. A compile failure here (assert throws with tsc's own diagnostic output) IS a
 * real proof failure, not something to swallow -- describeMetric() cannot be behavior-tested if
 * the real source does not even compile. */
function compileMetricView(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  // Node needs an explicit "type": "module" here: insight/web/package.json itself has none
  // (defaults to CommonJS), and this scratch dir is rooted under the OS tmpdir (mkScratch()),
  // outside that tree entirely, so nothing else tells Node the emitted `export function` syntax
  // is ESM.
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));

  mkdirSync(path.join(dir, "api"));
  copyFileSync(path.join(SRC_API, "schema.d.ts"), path.join(dir, "api", "schema.d.ts"));
  copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "api", "metric.ts"));
  copyFileSync(path.join(SRC_LIB, "metric-view.ts"), path.join(dir, "metric-view.ts"));

  const { ok, output } = runTsc(dir);
  assert.ok(ok, `metric-view.ts must compile clean with the local tsc:\n${output}`);

  return path.join(dir, "out", "metric-view.js");
}

async function main() {
  const { describeMetric } = await runScenarioAsync("insight-web-view-behavior-proof-", async (dir) => {
    const emitted = compileMetricView(dir);
    // Awaited HERE, inside the scenario callback -- runScenarioAsync only tears down `dir` after
    // this callback's returned promise settles, so the compiled file is guaranteed to still be on
    // disk for the entire dynamic import().
    return import(pathToFileURL(emitted).href);
  });

  // ---- fixtures, one per state -----------------------------------------------------------------

  const measured = {
    id: 1,
    label: "Autonomy rate",
    reliabilityClass: 2,
    state: "measured",
    value: 0.82,
    coverage: { numerator: 41, denominator: 50 },
  };

  const absentNoData = {
    id: 2,
    label: "Cycle time",
    reliabilityClass: 1,
    state: "absent_no_data",
    reason: "metric_2 has no value yet",
  };

  const absentUnbuilt = {
    id: 3,
    label: "Escape rate",
    reliabilityClass: 2,
    state: "absent_unbuilt",
    reason: "no escape_rate.sql exists yet -- only a code change can build this metric",
  };

  // ---- measured -------------------------------------------------------------------------------

  {
    const d = describeMetric(measured);
    assert.equal(d.numeral, "0.82", `measured: numeral must be the formatted value, got ${d.numeral}`);
    assert.ok(d.coverageText !== null, "measured: coverageText must not be null");
    assert.ok(d.coverageText.includes("41"), `measured: coverageText must include the numerator, got ${d.coverageText}`);
    assert.ok(d.coverageText.includes("50"), `measured: coverageText must include the denominator, got ${d.coverageText}`);
    assert.equal(d.reasonText, null, "measured: reasonText must be null -- no absence fields on a measured metric");
    assert.equal(d.fixText, null, "measured: fixText must be null -- no absence fields on a measured metric");
    console.log("OK: describeMetric(measured) -- numeral + coverage present, no absence fields");
  }

  // ---- absent_no_data ---------------------------------------------------------------------------

  {
    const d = describeMetric(absentNoData);
    assert.equal(d.numeral, null, "absent_no_data: numeral must be null -- no numeral for an absent state");
    assert.equal(d.reasonText, absentNoData.reason, "absent_no_data: reasonText must equal the fixture's reason");
    assert.ok(d.fixText, "absent_no_data: fixText must be non-empty -- it must name what would fix it");
    console.log("OK: describeMetric(absent_no_data) -- no numeral, reason + fix text present");
  }

  // ---- absent_unbuilt -----------------------------------------------------------------------------

  {
    const d = describeMetric(absentUnbuilt);
    assert.equal(d.numeral, null, "absent_unbuilt: numeral must be null -- no numeral for an absent state");
    assert.equal(d.reasonText, absentUnbuilt.reason, "absent_unbuilt: reasonText must equal the fixture's reason");
    assert.ok(d.fixText, "absent_unbuilt: fixText must be non-empty -- it must name what would fix it");

    // The assertion that most directly targets done-when 3's logic half: the two absent states
    // must not share the same "what fixes it" copy, or the reader cannot tell them apart by text
    // alone.
    const dNoData = describeMetric(absentNoData);
    assert.notEqual(
      d.fixText,
      dNoData.fixText,
      "absent_unbuilt's fixText must differ from absent_no_data's -- done-when 3 requires each " +
      "absent state to name its OWN fix, not share generic absence copy",
    );
    console.log("OK: describeMetric(absent_unbuilt) -- fixText differs from absent_no_data's");
  }

  console.log("\nOK: prove-metric-view-behavior");
}

main();
