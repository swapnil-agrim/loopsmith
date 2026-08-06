// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md Step 1. Plain node:assert behavior proof for
// describeMetric() -- browser-free, sub-second, wired into `npm run test` alongside
// prove-metric-contract-safety.mjs (package.json's `test` script chains both).
//
// Proves done-when 3's logic half (fixText differs between the two absent states) and
// done-when 4's logic half (coverage text is always present for a measured metric, and always
// carries both the numerator and denominator).
//
// Imports the REAL src/lib/metric-view.ts directly -- no transpile step, no second toolchain.
// metric-view.ts uses only erasable TS syntax (type/interface declarations, no enums, no
// namespaces, no parameter properties), so Node's built-in type-stripping (unflagged as of the
// Node 22.x this repo targets -- confirmed live: this import runs with no `--experimental-*`
// flag) loads it as plain JS with the types stripped, not executed.
import assert from "node:assert/strict";
import { describeMetric } from "../src/lib/metric-view.ts";

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
