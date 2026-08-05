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
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { mkdtempSync, rmSync, writeFileSync, readFileSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import assert from "node:assert/strict";

import { generate } from "./generate-schema.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const TSC_BIN = path.join(WEB, "node_modules", ".bin", "tsc");
const SRC_API = path.join(WEB, "src", "lib", "api");
const OPENAPI_JSON = path.join(WEB, "openapi.json");
const FIXTURES = path.join(WEB, "fixtures");

// The same compiler options as the real insight/web/tsconfig.json, restated here rather than
// read from that file: each scratch scenario compiles a flat directory of its own (no src/
// nesting), so `include` must differ from the real file's; restating the rest keeps this script
// self-contained and immune to an unrelated tsconfig.json edit silently loosening what these
// scenarios prove.
function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022",
      lib: ["ES2022"],
      module: "ESNext",
      moduleResolution: "Bundler",
      strict: true,
      noEmit: true,
      esModuleInterop: true,
      skipLibCheck: true,
      forceConsistentCasingInFileNames: true,
    },
    include: ["*.ts"],
  };
}

/** Runs `tsc -p dir`, returning { ok, output } -- never throws on a nonzero exit. */
function runTsc(dir) {
  try {
    const output = execFileSync(TSC_BIN, ["-p", dir], { encoding: "utf-8" });
    return { ok: true, output };
  } catch (err) {
    // tsc writes diagnostics to stdout and exits nonzero; execFileSync throws with both
    // captured on the error object.
    return { ok: false, output: (err.stdout || "") + (err.stderr || "") };
  }
}

function mkScratch() {
  return mkdtempSync(path.join(tmpdir(), "insight-web-contract-proof-"));
}

function writeTsconfig(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
}

function runScenario(fn) {
  const dir = mkScratch();
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// --------------------------------------------------------------------------------- control

function control() {
  return runScenario((dir) => {
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
  return runScenario((dir) => {
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
  return runScenario((dir) => {
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

function main() {
  control();
  criterionThreeUnnarrowedValueFails();
  criterionTwoRenameBreaksMetricLabel();
  // TEMPORARY (issue #302, demo 3 of 4): a deliberately failing assertion, to watch the `web`
  // required check go red at `npm run test`. It survives typecheck and lint, so it proves the
  // `test` script specifically. Reverted in the next commit.
  assert.strictEqual(1, 2, "deliberate failure for issue #302's CI-red demonstration");
}

main();
