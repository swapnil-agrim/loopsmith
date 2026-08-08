// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #313 [E20.S2], .sdlc/plans/313.md Step 8 (done-when 4): "Cold-start test: empty fact
// tables, assert no readout renders a numeral" -- the manager page's own version of this
// guarantee. Structurally a copy of scripts/prove-delivery-cold-start-no-numerals.mjs (same
// getFreePort/startNext/fetchAs/digit-scan shape, same MANDATORY executed negative control
// discipline -- see that file's own header for why the control is executed, never asserted in
// prose), with two differences: it fetches /manager, and its "not vacuous" numeral floor is
// DERIVED from MANAGER_PRIMARY_READOUT_IDS.length (compiled and imported live from
// src/lib/manager/curation.ts, via the same scripts/lib/tsc-scratch.mjs helper
// prove-delivery-python-bridge-exit-codes.mjs already uses to compile a plain .ts source file)
// rather than a second, hand-typed magic number -- see .sdlc/plans/313.md §3 for why a derived
// floor alone is not the primary defense (the executed negative control is) and for the
// "floor on the floor" sanity check this script also runs.
//
// CI-ONLY, same family as prove:delivery-cold-start -- needs a real DuckDB store. Wired as
// `npm run prove:manager-cold-start`, in .github/workflows/ci.yml's `web` job, immediately after
// prove:delivery-cold-start.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { mintSessionToken, proofServerEnv, SESSION_COOKIE_NAME } from "./lib/proof-session.mjs";
import { WEB as WEB_ROOT, runTsc, runScenarioAsync } from "./lib/tsc-scratch.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const REPO_ROOT = path.resolve(WEB, "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

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

// ---- fixture ------------------------------------------------------------------------------

function seedFixture(scratchDir, filename, { populateMetric14 } = {}) {
  const dbPath = path.join(scratchDir, filename);
  const args = [path.join(WEB, "scripts", "lib", "seed-cold-start-store.py"), "--db", dbPath];
  if (populateMetric14) args.push("--populate-metric-14");
  const result = spawnSync("python3", args, { cwd: REPO_ROOT, encoding: "utf-8" });
  assert.equal(
    result.status, 0,
    `seed-cold-start-store.py must exit 0 (is duckdb installed? ci.yml's install step):\n` +
      `${result.stdout}\n${result.stderr}`,
  );
  return dbPath;
}

// ---- server lifecycle (identical pattern to prove-delivery-cold-start-no-numerals.mjs) -------

function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function waitForServer(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastErr;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status === 404) return;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`server did not become ready at ${url} within ${timeoutMs}ms: ${lastErr}`);
}

async function startNext(dbPath) {
  const port = await getFreePort();
  const proc = spawn(NEXT_BIN, ["start", "-p", String(port)], {
    cwd: WEB,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...proofServerEnv(), INSIGHT_DB_PATH: dbPath },
  });
  let out = "";
  proc.stdout.on("data", (d) => (out += d.toString()));
  proc.stderr.on("data", (d) => (out += d.toString()));
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForServer(`${baseUrl}/`);
  } catch (err) {
    proc.kill();
    throw new Error(`${err.message}\n-- next start output --\n${out}`);
  }
  return { proc, baseUrl };
}

async function fetchManagerAs(baseUrl, role) {
  const token = await mintSessionToken(role, "proof-user");
  return fetch(`${baseUrl}/manager`, {
    headers: { cookie: `${SESSION_COOKIE_NAME}=${token}` },
    redirect: "manual",
  });
}

// ---- the digit-scan check itself -------------------------------------------------------------

const DIGIT = /[0-9]/;
const NUMERAL_SLOT_RE = /data-testid="metric-numeral"[^>]*>([^<]*)</g;

function extractNumeralTexts(body) {
  return [...body.matchAll(NUMERAL_SLOT_RE)].map((m) => m[1]);
}

function makeAssertNoNumeralsAnywhere(floor) {
  return function assertNoNumeralsAnywhere(body) {
    const numerals = extractNumeralTexts(body);
    assert.ok(
      numerals.length >= floor,
      `expected at least ${floor} metric-numeral slots (MANAGER_PRIMARY_READOUT_IDS.length), ` +
      `found ${numerals.length} -- a check that finds nothing is not a check`,
    );
    for (const text of numerals) {
      assert.ok(!DIGIT.test(text), `a metric-numeral slot rendered a digit: ${JSON.stringify(text)}`);
    }
  };
}

async function main() {
  const t0 = Date.now();

  const managerPrimaryReadoutIds = await loadManagerPrimaryReadoutIds();
  // "Floor on the floor" (.sdlc/plans/313.md §3): if the curated id list ever shrank to near-
  // nothing, a derived floor alone would weaken to near-vacuous right along with it. Fails loudly
  // rather than silently degrading.
  assert.ok(
    managerPrimaryReadoutIds.length >= 5,
    "the curated id list shrank enough to weaken the cold-start floor to near-vacuous -- widen it " +
    "or update this minimum deliberately",
  );
  const assertNoNumeralsAnywhere = makeAssertNoNumeralsAnywhere(managerPrimaryReadoutIds.length);

  const scratchDir = path.join(REPO_ROOT, ".sdlc-proof-scratch-manager-cold-start");
  rmSync(scratchDir, { recursive: true, force: true });
  mkdirSync(scratchDir, { recursive: true });

  try {
    // ---- 1. THE MANDATORY FIXTURE: schema present, zero rows, no ingest ----------------------
    const coldDbPath = seedFixture(scratchDir, "cold.duckdb", { populateMetric14: false });
    let server = await startNext(coldDbPath);
    let coldBody;
    try {
      const res = await fetchManagerAs(server.baseUrl, "manager");
      assert.equal(
        res.status, 200,
        `manager on /manager (cold-start store) must succeed, got ${res.status}`,
      );
      coldBody = await res.text();
      assertNoNumeralsAnywhere(coldBody);
      console.log(
        "OK: cold-start /manager renders no numerals anywhere " +
        `(${extractNumeralTexts(coldBody).length} metric-numeral slots, all empty)`,
      );
    } finally {
      server.proc.kill();
    }

    // ---- 2. EXECUTED NEGATIVE CONTROL (mandatory, not prose) ----------------------------------
    const populatedDbPath = seedFixture(scratchDir, "populated.duckdb", { populateMetric14: true });
    server = await startNext(populatedDbPath);
    try {
      const res = await fetchManagerAs(server.baseUrl, "manager");
      assert.equal(
        res.status, 200,
        `manager on /manager (populated store) must succeed, got ${res.status}`,
      );
      const populatedBody = await res.text();
      assert.throws(
        () => assertNoNumeralsAnywhere(populatedBody),
        "NEGATIVE CONTROL FAILED: assertNoNumeralsAnywhere found nothing wrong with a page " +
          "rendered against a store where metric_14 legitimately has a value -- this check has " +
          "no teeth and would not catch a real regression of the literal-`0` bug class either",
      );
      console.log(
        "OK: negative control -- the same digit-scan check correctly FAILS once metric_14 is " +
        "populated (a real numeral legitimately appears on the page)",
      );
    } finally {
      server.proc.kill();
    }
  } finally {
    rmSync(scratchDir, { recursive: true, force: true });
  }

  console.log(`\nOK: prove-manager-cold-start-no-numerals (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-manager-cold-start-no-numerals");
  console.error(err);
  process.exit(1);
});
