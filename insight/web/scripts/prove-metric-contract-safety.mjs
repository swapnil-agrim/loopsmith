// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #301 [E16.S3], .sdlc/plans/301.md Decision (e). The real `npm run test` -- the mechanical
// proof for done-when 2 and 3, per the issue's own "this is the story's actual proof."
//
// Three scenarios, each compiling a scratch directory with `tsc -p <dir>` (the LOCAL
// node_modules/.bin/tsc, never npx -- see generate-schema.mjs's docstring for why):
//
//   CONTROL     -- the real, unmodified src/lib/api/ files compile clean. Without this, a
//                  broken harness that always reports failure would also "pass" criteria 2 and
//                  3 -- this is what stops that.
//   CRITERION 3 -- the negative case: reading `metric.value` with no `state` narrowing fails
//                  TS2339 (two of the three union members carry no `value` field at all).
//   CRITERION 2 -- renames the wire property `label` -> `title` directly in an in-memory copy of
//                  the committed openapi.json (the exact JSON Schema mutation shape a Pydantic
//                  field rename produces for an un-aliased field -- see
//                  insight/tests/test_openapi_schema_fresh.py::test_label_field_has_no_wire_alias,
//                  which pins that `label` really has no alias, keeping this proxy honest),
//                  regenerates schema.d.ts from that mutation, and asserts the SAME, UNMODIFIED
//                  metric.consumer.ts::metricLabel() now fails TS2339 against it.
//
// Network-free: all three scenarios use only the already-`npm ci`'d local `openapi-typescript`/
// `tsc` binaries and the committed openapi.json -- 3 `tsc` subprocess calls total, sub-second.
import { writeFileSync, readFileSync, copyFileSync } from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

import { generate } from "./generate-schema.mjs";
import { WEB, runTsc, runScenario, runScenarioInWeb } from "./lib/tsc-scratch.mjs";

const SRC_API = path.join(WEB, "src", "lib", "api");
const OPENAPI_JSON = path.join(WEB, "openapi.json");
const FIXTURES = path.join(WEB, "fixtures");

// The same compiler options as the real insight/web/tsconfig.json, restated here rather than
// read from that file: each scratch scenario compiles a flat directory of its own (no src/
// nesting), so `include` must differ from the real file's; restating the rest keeps this script
// self-contained and immune to an unrelated tsconfig.json edit silently loosening what these
// scenarios prove.
// issue #304 [E17.S3], .sdlc/plans/304.md Decision (b): `"jsx": "react-jsx"` and the `*.tsx`
// include pattern are new here, added to this SHARED function rather than forked into a second
// one. Both are inert for the three original .ts-only scenarios above (no .tsx file is ever
// written into their scratch dirs, so the extra include pattern matches nothing there, and the
// `jsx` option only affects files that actually contain JSX syntax).
function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022",
      lib: ["ES2022", "DOM"],
      module: "ESNext",
      moduleResolution: "Bundler",
      jsx: "react-jsx",
      strict: true,
      noEmit: true,
      esModuleInterop: true,
      skipLibCheck: true,
      forceConsistentCasingInFileNames: true,
    },
    include: ["*.ts", "*.tsx"],
  };
}

// runTsc/runScenario/runScenarioInWeb now live in ./lib/tsc-scratch.mjs -- issue #304 [E17.S3],
// factored out so prove-metric-view-behavior.mjs can reuse the same "compile a scratch dir with
// the local tsc" machinery instead of Node's unflagged TypeScript type-stripping. Behaviour is
// unchanged: runScenario()/runScenarioInWeb() below still tear down their dir in a `finally`,
// and mkScratchInWeb()'s dirs are still rooted under insight/web/ (gitignored -- see
// insight/web/.gitignore's `.contract-proof-scratch-*/` entry) for the same @types/react
// upward-node_modules-walk reason the comment above used to explain here.

function writeTsconfig(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
}

// --------------------------------------------------------------------------------- control

function control() {
  return runScenario("insight-web-contract-proof-", (dir) => {
    writeTsconfig(dir);
    copyFileSync(path.join(SRC_API, "schema.d.ts"), path.join(dir, "schema.d.ts"));
    copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "metric.ts"));
    copyFileSync(path.join(SRC_API, "metric.consumer.ts"), path.join(dir, "metric.consumer.ts"));
    const { ok, output } = runTsc(dir);
    assert.ok(ok, `control: the real, unmodified src/lib/api/ files must compile clean:\n${output}`);
    console.log("OK: control (real src/lib/api/ compiles clean)");
  });
}

// --------------------------------------------------------------------------------- criterion 3

function criterionThreeUnnarrowedValueFails() {
  return runScenario("insight-web-contract-proof-", (dir) => {
    writeTsconfig(dir);
    copyFileSync(path.join(SRC_API, "schema.d.ts"), path.join(dir, "schema.d.ts"));
    copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "metric.ts"));
    copyFileSync(
      path.join(FIXTURES, "unnarrowed-value-access.ts.fixture"),
      path.join(dir, "unnarrowed-value-access.ts"),
    );
    const { ok, output } = runTsc(dir);
    assert.ok(
      !ok && output.includes("TS2339"),
      `criterion 3: reading metric.value with no 'state' narrowing must fail tsc --noEmit with ` +
      `TS2339, got:\n${output}`,
    );
    console.log("OK: criterion 3 (unnarrowed .value access fails TS2339)");
  });
}

// --------------------------------------------------------------------------------- criterion 2

/** Deep-renames the wire property `label` -> `title` on the three Metric schemas -- the exact
 * shape a Pydantic field rename produces for an un-aliased field (both the `properties` key and
 * the matching `required` array entry move together). */
function renameLabelToTitleInSchema(openapiDoc) {
  const schemas = openapiDoc.components.schemas;
  for (const name of ["MeasuredMetric", "AbsentNoDataMetric", "AbsentUnbuiltMetric"]) {
    const schema = schemas[name];
    schema.properties.title = schema.properties.label;
    delete schema.properties.label;
    schema.required = schema.required.map((f) => (f === "label" ? "title" : f));
  }
  return openapiDoc;
}

function criterionTwoRenameBreaksMetricLabel() {
  return runScenario("insight-web-contract-proof-", (dir) => {
    writeTsconfig(dir);

    const mutated = renameLabelToTitleInSchema(JSON.parse(readFileSync(OPENAPI_JSON, "utf-8")));
    const scratchOpenapiJson = path.join(dir, "openapi.json");
    writeFileSync(scratchOpenapiJson, JSON.stringify(mutated));

    writeFileSync(path.join(dir, "schema.d.ts"), generate(scratchOpenapiJson));
    // metric.ts and metric.consumer.ts are copied UNMODIFIED -- the whole point of the proof is
    // that neither file changes, only the schema underneath them does.
    copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "metric.ts"));
    copyFileSync(path.join(SRC_API, "metric.consumer.ts"), path.join(dir, "metric.consumer.ts"));

    const { ok, output } = runTsc(dir);
    assert.ok(
      !ok && output.includes("TS2339"),
      "criterion 2: renaming the wire property label -> title and regenerating must break " +
      `metric.consumer.ts::metricLabel() with TS2339, got:\n${output}`,
    );
    assert.ok(
      output.includes("metric.consumer.ts"),
      `criterion 2: the TS2339 failure must be reported against metric.consumer.ts, got:\n${output}`,
    );
    console.log("OK: criterion 2 (renaming label -> title and regenerating breaks metricLabel())");
  });
}

// --------------------------------------------------------------------------------- done-when 4

/** issue #304 [E17.S3], .sdlc/plans/304.md Step 2 / Decision (e). A `MeasuredMetric` object
 * literal missing `coverage` (a required, non-optional member for every reliability class) must
 * fail `tsc`: "cannot be rendered" reduces to "cannot be constructed". */
function measuredMetricMissingCoverageFails() {
  return runScenario("insight-web-contract-proof-", (dir) => {
    writeTsconfig(dir);
    copyFileSync(path.join(SRC_API, "schema.d.ts"), path.join(dir, "schema.d.ts"));
    copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "metric.ts"));
    copyFileSync(
      path.join(FIXTURES, "measured-metric-missing-coverage.ts.fixture"),
      path.join(dir, "measured-metric-missing-coverage.ts"),
    );
    const { ok, output } = runTsc(dir);
    assert.ok(
      !ok && output.includes("TS2741"),
      "done-when 4: a MeasuredMetric literal omitting 'coverage' must fail tsc --noEmit with " +
      `TS2741 (missing required property), got:\n${output}`,
    );
    console.log("OK: done-when 4 (MeasuredMetric literal without coverage fails TS2741)");
  });
}

// --------------------------------------------------------------------------------- done-when 2

/** issue #304 [E17.S3], .sdlc/plans/304.md Step 4. Done-when 2's literal ask: "a component
 * reaching metric.value" -- not a plain function (criterion 3 above already covers that) --
 * "fails tsc" without `state` narrowing. Uses the new JSX-capable scratch path
 * (mkScratchInWeb()/scratchTsconfig()'s `jsx`/`*.tsx` additions, Decision (b)). */
function componentUnnarrowedValueAccessFails() {
  return runScenarioInWeb(".contract-proof-scratch-", (dir) => {
    writeTsconfig(dir);
    copyFileSync(path.join(SRC_API, "schema.d.ts"), path.join(dir, "schema.d.ts"));
    copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "metric.ts"));
    copyFileSync(
      path.join(FIXTURES, "unnarrowed-value-access-component.tsx.fixture"),
      path.join(dir, "unnarrowed-value-access-component.tsx"),
    );
    const { ok, output } = runTsc(dir);
    assert.ok(
      !ok && output.includes("TS2339"),
      "done-when 2: a component reading props.metric.value with no 'state' narrowing must fail " +
      `tsc --noEmit with TS2339, got:\n${output}`,
    );
    console.log("OK: done-when 2 (unnarrowed component .value access fails TS2339)");
  });
}

/** Positive control for the new JSX-capable scratch path (mirrors the top-level `control()`
 * scenario's purpose, applied to the machinery `componentUnnarrowedValueAccessFails()` newly
 * depends on): a component that DOES narrow `state` before reading `.value` must compile clean.
 * Without this, "the unnarrowed fixture fails" would be indistinguishable from "the scratch dir
 * can't resolve react types at all and everything fails" -- see .sdlc/plans/304.md Step 4. */
function componentNarrowedValueAccessCompiles() {
  return runScenarioInWeb(".contract-proof-scratch-", (dir) => {
    writeTsconfig(dir);
    copyFileSync(path.join(SRC_API, "schema.d.ts"), path.join(dir, "schema.d.ts"));
    copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "metric.ts"));
    copyFileSync(
      path.join(FIXTURES, "narrowed-value-access-component.tsx.fixture"),
      path.join(dir, "narrowed-value-access-component.tsx"),
    );
    const { ok, output } = runTsc(dir);
    assert.ok(
      ok,
      "positive control: a component that narrows 'state' before reading .value must compile " +
      `clean under the new JSX-capable scratch path, got:\n${output}`,
    );
    console.log("OK: positive control (narrowed component .value access compiles clean)");
  });
}

function main() {
  control();
  criterionThreeUnnarrowedValueFails();
  criterionTwoRenameBreaksMetricLabel();
  measuredMetricMissingCoverageFails();
  componentNarrowedValueAccessCompiles();
  componentUnnarrowedValueAccessFails();
}

main();
