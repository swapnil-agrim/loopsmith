// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #301 [E16.S3], .sdlc/plans/301.md Decision (c).
//
// Wired as `pretypecheck` -- npm auto-runs `pre<script>` before `<script>` -- so every
// `npm run typecheck` regenerates schema.d.ts INTO MEMORY (never touching the committed file)
// and diffs it byte-for-byte against what is actually on disk. This is the real enforcement for
// done-when 1 ("generated, not hand-edited"): a hand edit or a stale file (Pydantic model
// changed, `generate-schema.mjs` not re-run) fails the very next `npm run typecheck`, naming the
// exact fix command -- a header alone only documents intent, it does not fail a build.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { generate } from "./generate-schema.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const SCHEMA_PATH = path.join(WEB, "src", "lib", "api", "schema.d.ts");

const fresh = generate();
const committed = readFileSync(SCHEMA_PATH, "utf-8");

if (fresh !== committed) {
  console.error(
    `FAIL: ${path.relative(WEB, SCHEMA_PATH)} is stale -- it does not match what ` +
    "openapi-typescript generates from the committed insight/web/openapi.json right now. Run " +
    "`node scripts/generate-schema.mjs` (cwd insight/web/) to regenerate it, then commit the " +
    "result. If insight/web/openapi.json itself is stale, run " +
    "`python3 -m insight.api.export_openapi` from the repo root first.",
  );
  process.exit(1);
}

console.log("fresh");
