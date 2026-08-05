// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #301 [E16.S3], .sdlc/plans/301.md Decision (c).
//
// generate(openapiJsonPath?) runs the LOCALLY installed openapi-typescript CLI binary
// (node_modules/.bin/openapi-typescript) against the committed insight/web/openapi.json and
// returns the generated text, with the BUSL marker prepended as literal line 1 -- before
// openapi-typescript's own "do not edit" banner comment. It never writes anything; that is
// check-schema-fresh.mjs's job (diff) and this file's own `main()` below (write), kept separate
// so check-schema-fresh.mjs can call generate() to get text to compare without touching disk.
//
// `node_modules/.bin/openapi-typescript` directly, never `npx`: npx can still hit the npm
// registry for a version check even when the package is already installed locally (verified
// during planning -- see .sdlc/plans/301.md Decision c and Risks). Once `npm ci` has populated
// node_modules/, this needs no network at all.
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const DEFAULT_OPENAPI_JSON = path.join(WEB, "openapi.json");
const SCHEMA_OUT = path.join(WEB, "src", "lib", "api", "schema.d.ts");
const OPENAPI_TYPESCRIPT_BIN = path.join(WEB, "node_modules", ".bin", "openapi-typescript");

// The same marker every other insight/ .ts/.tsx file carries, derived the same mechanical way
// tests/test_licence_boundary.py's `_ts_marker` derives it: swap HEADER.txt's leading `#` for
// `//`. Not imported from Python (no cross-language import exists) -- restated here, in the one
// place a generated .ts file's header text is assembled, and pinned against the Python source of
// truth by insight/tests/test_openapi_schema_fresh's sibling guard in
// tests/test_licence_boundary.py, which reads the real generated file on disk.
const BUSL_MARKER = "// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.";

/**
 * Runs openapi-typescript against `openapiJsonPath` (default: the committed insight/web/openapi.json)
 * and returns the generated TypeScript source as a string, with the BUSL marker prepended as line 1.
 * Never writes to disk -- see module docstring above.
 */
export function generate(openapiJsonPath = DEFAULT_OPENAPI_JSON) {
  const generated = execFileSync(
    OPENAPI_TYPESCRIPT_BIN,
    [openapiJsonPath],
    { encoding: "utf-8" },
  );
  return `${BUSL_MARKER}\n${generated}`;
}

function main() {
  const text = generate();
  fs.mkdirSync(path.dirname(SCHEMA_OUT), { recursive: true });
  fs.writeFileSync(SCHEMA_OUT, text);
}

// Only run when invoked directly (`node scripts/generate-schema.mjs`), not when imported by
// check-schema-fresh.mjs or prove-metric-contract-safety.mjs.
if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
