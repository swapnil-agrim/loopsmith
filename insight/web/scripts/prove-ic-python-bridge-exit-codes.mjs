// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #310 [E19.S2], .sdlc/plans/310.md Task 3. Compiles the real
// src/lib/ic/pythonBridge.ts and drives it against the REAL `python3 -m insight web ic` -- no
// stub, no browser, no next build. Mirrors ../auth/pythonBridge.ts's own
// prove-python-bridge-exit-codes.mjs pattern (compile with the local tsc, dynamic-import the
// compiled output, exercise the real CLI) for the ic bridge instead of the credential bridge.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioInWebAsync } from "./lib/tsc-scratch.mjs";

function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022", lib: ["ES2022"], module: "ESNext", moduleResolution: "Bundler",
      strict: true, noEmit: false, outDir: "out", esModuleInterop: true, skipLibCheck: true,
      forceConsistentCasingInFileNames: true, types: ["node"],
    },
    include: ["*.ts"],
  };
}

function compileBridge(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  const src = path.join(WEB, "src", "lib", "ic", "pythonBridge.ts");
  writeFileSync(path.join(dir, "pythonBridge.ts"), readFileSync(src, "utf-8"));
  const { ok, output } = runTsc(dir);
  assert.ok(ok, `src/lib/ic/pythonBridge.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "pythonBridge.js");
}

// Repo root, two ".." above insight/web/ -- same computation this proof's own compiled module
// uses internally, needed here only to locate insight/web/scripts/lib/seed-ic-fixture.py and to
// spawn python3 with the same cwd the real bridge always uses.
const REPO_ROOT = path.resolve(WEB, "..", "..");

async function main() {
  const { fetchIcPayload, IcStoreUnavailableError } = await runScenarioInWebAsync(
    ".ic-python-bridge-proof-scratch-",
    async (dir) => {
      const emitted = compileBridge(dir);
      return import(pathToFileURL(emitted).href);
    },
  );

  // Scenario 1: a seeded alice/bob/carol fixture DB -> fetchIcPayload("alice") resolves with
  // alice's own data. Seeded via seed-ic-fixture.py (Task 6's own shared seeder), invoked once
  // here via INSIGHT_DB_PATH so this proof does not duplicate a fourth copy of the fixture rows.
  const fixtureDir = path.join(REPO_ROOT, ".sdlc-proof-scratch-ic-bridge");
  mkdirSync(fixtureDir, { recursive: true });
  const dbPath = path.join(fixtureDir, "s.duckdb");
  const { spawnSync } = await import("node:child_process");
  const seed = spawnSync(
    "python3",
    [path.join(WEB, "scripts", "lib", "seed-ic-fixture.py"), "--db", dbPath],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
  assert.equal(seed.status, 0, `seed-ic-fixture.py must exit 0:\n${seed.stdout}\n${seed.stderr}`);

  process.env.INSIGHT_DB_PATH = dbPath;
  try {
    const payload = await fetchIcPayload("alice");
    assert.equal(payload.actor_ever_appeared, true);
    assert.deepEqual(payload.my_queue.map((r) => r.goal_id), ["g-alice-1"]);
    console.log("OK: fetchIcPayload('alice') resolves with alice's own data from a real seeded store");

    // Scenario 2: a nonexistent DB path -> rejects with IcStoreUnavailableError, never resolves
    // with an empty-but-successful payload (ABSENT-!=-PASS).
    process.env.INSIGHT_DB_PATH = path.join(fixtureDir, "does-not-exist.duckdb");
    await assert.rejects(
      fetchIcPayload("alice"),
      (e) => e instanceof IcStoreUnavailableError,
      "a missing store must reject with IcStoreUnavailableError, never resolve with an empty payload",
    );
    console.log("OK: fetchIcPayload against a missing store rejects with IcStoreUnavailableError");
  } finally {
    delete process.env.INSIGHT_DB_PATH;
    rmSync(fixtureDir, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error("FAIL: prove-ic-python-bridge-exit-codes");
  console.error(err);
  process.exit(1);
});
