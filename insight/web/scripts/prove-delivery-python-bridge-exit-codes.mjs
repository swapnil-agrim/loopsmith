// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] Goal B, Task B2. Compiles the real src/lib/delivery/pythonBridge.ts and
// drives it against the REAL `python3 -m insight web delivery` -- no stub, no browser, no next
// build. Mirrors prove-ic-python-bridge-exit-codes.mjs's own pattern (compile with the local tsc,
// dynamic-import the compiled output, exercise the real CLI) for the delivery bridge instead of
// the IC one.
//
// ONE STRUCTURAL DIFFERENCE FROM THAT FILE'S OWN SCRATCH COMPILE: pythonBridge.ts here imports
// the shared `Metric` union type via the `@/lib/api/metric` path alias (../ic/pythonBridge.ts
// defines its own payload type inline and has no such import). The scratch tsconfig below adds a
// `paths` mapping for exactly that one specifier, and schema.d.ts/metric.ts are copied alongside
// pythonBridge.ts into the same scratch dir -- mirrors prove-role-route-matrix.mjs Part B's own
// `paths`-mapped stub technique (compileProxy()), applied to a REAL file (metric.ts), not a stub.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, rmSync, writeFileSync, copyFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioInWebAsync } from "./lib/tsc-scratch.mjs";

const SRC_API = path.join(WEB, "src", "lib", "api");

function scratchTsconfig() {
  return {
    compilerOptions: {
      target: "ES2022", lib: ["ES2022"], module: "ESNext", moduleResolution: "Bundler",
      strict: true, noEmit: false, outDir: "out", esModuleInterop: true, skipLibCheck: true,
      forceConsistentCasingInFileNames: true, types: ["node"],
      baseUrl: ".",
      paths: { "@/lib/api/metric": ["./metric"] },
    },
    include: ["*.ts"],
  };
}

function compileBridge(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  copyFileSync(path.join(SRC_API, "schema.d.ts"), path.join(dir, "schema.d.ts"));
  copyFileSync(path.join(SRC_API, "metric.ts"), path.join(dir, "metric.ts"));
  const src = path.join(WEB, "src", "lib", "delivery", "pythonBridge.ts");
  writeFileSync(path.join(dir, "pythonBridge.ts"), readFileSync(src, "utf-8"));
  const { ok, output } = runTsc(dir);
  assert.ok(ok, `src/lib/delivery/pythonBridge.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "pythonBridge.js");
}

// Repo root, two ".." above insight/web/ -- same computation this proof's own compiled module
// uses internally, needed here only to locate insight/web/scripts/lib/seed-ic-fixture.py-style
// fixtures and to spawn python3 with the same cwd the real bridge always uses.
const REPO_ROOT = path.resolve(WEB, "..", "..");

async function main() {
  const { fetchDeliveryMetrics, DeliveryBridgeUnavailableError } = await runScenarioInWebAsync(
    ".delivery-python-bridge-proof-scratch-",
    async (dir) => {
      const emitted = compileBridge(dir);
      return import(pathToFileURL(emitted).href);
    },
  );

  const fixtureDir = path.join(REPO_ROOT, ".sdlc-proof-scratch-delivery-bridge");
  rmSync(fixtureDir, { recursive: true, force: true });
  mkdirSync(fixtureDir, { recursive: true });

  try {
    // Scenario 1: a nonexistent DB path -> resolves (not rejects!) with all 42 catalog entries,
    // every one absent -- the deliberate divergence from ../ic/pythonBridge.ts's
    // IcStoreUnavailableError (see this file's header comment / .sdlc/plans/312.md §3a point 2).
    // ABSENT-!=-PASS is upheld a different way here: not by rejecting, but by every entry
    // carrying no `value`/`coverage` at all.
    process.env.INSIGHT_DB_PATH = path.join(fixtureDir, "does-not-exist.duckdb");
    const missingStoreMetrics = await fetchDeliveryMetrics();
    assert.equal(missingStoreMetrics.length, 42, "a missing store must still return all 42 catalog entries");
    assert.ok(
      missingStoreMetrics.every((m) => m.state !== "measured"),
      "a missing store must degrade every metric to an absent state, never a fabricated 'measured' one",
    );
    console.log("OK: fetchDeliveryMetrics() against a missing store resolves with all 42 entries, all absent (no rejection)");

    // Scenario 2: a populated store (metric_12 seeded, the one catalog id with a registered
    // VALUE_EXTRACTOR) -> id 12 resolves measured, everything else stays absent. Uses the same
    // fixture shape insight/tests/test_api_metrics_route.py and test_cli_web_delivery.py already
    // use, seeded here via a small inline Python one-liner (no new shared seeder needed -- unlike
    // seed-ic-fixture.py's alice/bob/carol rows, this is one CREATE VIEW statement).
    const dbPath = path.join(fixtureDir, "s.duckdb");
    const { spawnSync } = await import("node:child_process");
    const seed = spawnSync(
      "python3",
      [
        "-c",
        "import sys, duckdb\n" +
        "from insight.ingest.store import ensure_schema\n" +
        "conn = duckdb.connect(sys.argv[1])\n" +
        "ensure_schema(conn)\n" +
        "conn.execute(\"CREATE VIEW metric_12 AS SELECT 3 AS a, 4 AS b, 0.75 AS c\")\n" +
        "conn.close()\n",
        dbPath,
      ],
      { cwd: REPO_ROOT, encoding: "utf-8" },
    );
    assert.equal(seed.status, 0, `inline seed must exit 0 (is duckdb installed?):\n${seed.stdout}\n${seed.stderr}`);

    process.env.INSIGHT_DB_PATH = dbPath;
    const populatedMetrics = await fetchDeliveryMetrics();
    assert.equal(populatedMetrics.length, 42);
    const byId = Object.fromEntries(populatedMetrics.map((m) => [m.id, m]));
    assert.equal(byId[12].state, "measured");
    assert.equal(byId[12].value, 0.75);
    assert.deepEqual(byId[12].coverage, { numerator: 3, denominator: 4 });
    const others = populatedMetrics.filter((m) => m.id !== 12);
    assert.ok(others.every((m) => m.state !== "measured"), "only metric id 12 has a registered extractor -- every other id must stay absent");
    console.log("OK: fetchDeliveryMetrics() against a populated store resolves id 12 as measured, all 41 others absent");

    // Scenario 3: an unrecognized failure -> rejects with DeliveryBridgeUnavailableError, never
    // silently resolves with an empty or partial array. Unlike scenario 1 (a path that does not
    // EXIST, which `open_store_read_only` catches and normalizes to a clean FileNotFoundError,
    // §3a point 2's deliberate all-absent success), this points `--db` at a path that DOES exist
    // but is not a valid DuckDB file -- `path.is_file()` is true, so the FileNotFoundError branch
    // is never taken, and `duckdb.connect(..., read_only=True)` on a corrupt file raises an
    // uncaught duckdb exception, so the CLI exits nonzero with a traceback on stderr.
    const corruptPath = path.join(fixtureDir, "not-a-duckdb-file.duckdb");
    writeFileSync(corruptPath, "this is not a duckdb file\n");
    process.env.INSIGHT_DB_PATH = corruptPath;
    await assert.rejects(
      fetchDeliveryMetrics(),
      (e) => e instanceof DeliveryBridgeUnavailableError,
      "an unrecognized nonzero exit must reject with DeliveryBridgeUnavailableError, never resolve",
    );
    console.log("OK: fetchDeliveryMetrics() against a corrupt (non-DuckDB) file rejects with DeliveryBridgeUnavailableError");
  } finally {
    delete process.env.INSIGHT_DB_PATH;
    rmSync(fixtureDir, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error("FAIL: prove-delivery-python-bridge-exit-codes");
  console.error(err);
  process.exit(1);
});
