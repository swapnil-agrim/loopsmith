// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] Goal B, Task B4 (done-when 4): "Cold-start test: empty fact tables, assert
// no readout renders a numeral." The exact regression class this whole story exists to prevent --
// insight/dash/panel.py once shipped a literal `0` for "goals landed" against an empty store
// (test_dash_panel_absence.py's own test_cold_start_never_renders_a_bare_zero is the Python-side
// version of this same guarantee; test_api_metrics_route.py::
// test_cold_start_every_metric_is_absent_with_no_numeral_in_the_body is the API-layer version).
// This is the WEB-layer version: a real running Next server, a real booted `insight web delivery`
// bridge, a real schema-only zero-row DuckDB store, and a raw HTML response body scanned for
// digits inside every `metric-numeral` slot -- not merely "the field looks empty."
//
// CI-ONLY, same family as prove:ic-no-leak -- needs a real DuckDB store, which `npm run test`'s
// offline chain (run in a fresh worktree, before any pip install -- the exact #310 scar this
// repo's other CI-only proofs already document) cannot provide. Wired as
// `npm run prove:delivery-cold-start`, in .github/workflows/ci.yml's `web` job, reusing the SAME
// actions/setup-python + pip install -e insight/ steps prove:role-forbidden/prove:ic-bridge/
// prove:ic-no-leak already share -- no new install step needed.
//
// Mirrors prove-ic-no-cross-actor-leak.mjs's own getFreePort/startNext/plain-fetch shape (no
// browser dependency needed -- this only needs response TEXT, not computed styles) and, above
// all, its MANDATORY executed negative control discipline: this repo settles falsifiability by
// RUNNING a negative control, never by asserting it in prose (see that file's own header comment).
// Section 2 below reseeds the SAME store shape with metric_12 populated and asserts the SAME
// "no digits anywhere" check now FAILS -- without this, a broken/tautological regex (one that
// never matches the real markup) would pass section 1 vacuously.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { mintSessionToken, proofServerEnv, SESSION_COOKIE_NAME } from "./lib/proof-session.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const REPO_ROOT = path.resolve(WEB, "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

// ---- fixture ------------------------------------------------------------------------------

function seedFixture(scratchDir, filename, { populateMetric12 } = {}) {
  const dbPath = path.join(scratchDir, filename);
  const args = [path.join(WEB, "scripts", "lib", "seed-cold-start-store.py"), "--db", dbPath];
  if (populateMetric12) args.push("--populate-metric-12");
  const result = spawnSync("python3", args, { cwd: REPO_ROOT, encoding: "utf-8" });
  assert.equal(
    result.status, 0,
    `seed-cold-start-store.py must exit 0 (is duckdb installed? ci.yml's install step):\n` +
      `${result.stdout}\n${result.stderr}`,
  );
  return dbPath;
}

// ---- server lifecycle (identical pattern to prove-ic-no-cross-actor-leak.mjs) ---------------

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

/** Same as prove-ic-no-cross-actor-leak.mjs's own startNext(dbPath): INSIGHT_DB_PATH points the
 * delivery bridge's CLI spawn at the seeded fixture store, not a real one. */
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

/** GETs /delivery carrying a real Auth.js session for `role` -- page.tsx itself calls no auth()
 * (delivery data is aggregate-only, no actor), so only proxy.ts's own cookie lookup matters here;
 * the plain (non `__Secure-`-prefixed) cookie name is enough, same as
 * prove-role-forbidden-real-server.mjs's own fetchAs(). */
async function fetchDeliveryAs(baseUrl, role) {
  const token = await mintSessionToken(role, "proof-user");
  return fetch(`${baseUrl}/delivery`, {
    headers: { cookie: `${SESSION_COOKIE_NAME}=${token}` },
    redirect: "manual",
  });
}

// ---- the digit-scan check itself -------------------------------------------------------------

const DIGIT = /[0-9]/;
// Non-greedy `[^<]*` capture of the numeral slot's own inner text -- mirrors
// prove-absence-primitives-render.mjs's own numeral-slot text extraction, done here via regex
// against the raw HTML (no browser) since Next inlines the full server-rendered markup into the
// document a plain res.text() already captures (same reasoning
// prove-ic-no-cross-actor-leak.mjs's own extractIcPayload() gives for reading the RSC-inlined
// script tag directly).
const NUMERAL_SLOT_RE = /data-testid="metric-numeral"[^>]*>([^<]*)</g;

function extractNumeralTexts(body) {
  return [...body.matchAll(NUMERAL_SLOT_RE)].map((m) => m[1]);
}

/** THE check both section 1 (must pass) and section 2's negative control (must fail) share --
 * factored out so the two can never drift into checking different things (mirrors
 * prove-ic-no-cross-actor-leak.mjs's own assertCarolNeedlesAbsent() pattern). */
function assertNoNumeralsAnywhere(body) {
  const numerals = extractNumeralTexts(body);
  // THE "not vacuous" guard (mirrors test_dash_panel_absence.py:63's own
  // `assert values, "no readouts rendered at all"`): 4 primary readouts + 42 board cells = 46.
  // Without this, a selector that matched ZERO elements would make "no digit found" pass
  // trivially, for the wrong reason.
  assert.ok(
    numerals.length >= 46,
    "expected at least 46 metric-numeral slots (4 primary readouts + 42 board cells), found " +
    `${numerals.length} -- a check that finds nothing is not a check`,
  );
  for (const text of numerals) {
    assert.ok(!DIGIT.test(text), `a metric-numeral slot rendered a digit: ${JSON.stringify(text)}`);
  }
}

async function main() {
  const t0 = Date.now();
  const scratchDir = path.join(REPO_ROOT, ".sdlc-proof-scratch-delivery-cold-start");
  rmSync(scratchDir, { recursive: true, force: true });
  mkdirSync(scratchDir, { recursive: true });

  try {
    // ---- 1. THE MANDATORY FIXTURE: schema present, zero rows, no ingest ----------------------
    const coldDbPath = seedFixture(scratchDir, "cold.duckdb", { populateMetric12: false });
    let server = await startNext(coldDbPath);
    let coldBody;
    try {
      const res = await fetchDeliveryAs(server.baseUrl, "manager");
      assert.equal(
        res.status, 200,
        `manager on /delivery (cold-start store) must succeed, got ${res.status}`,
      );
      coldBody = await res.text();
      assertNoNumeralsAnywhere(coldBody);
      console.log(
        "OK: cold-start /delivery renders no numerals anywhere " +
        `(${extractNumeralTexts(coldBody).length} metric-numeral slots, all empty)`,
      );
    } finally {
      server.proc.kill();
    }

    // ---- 2. EXECUTED NEGATIVE CONTROL (mandatory, not prose) ----------------------------------
    // Reseed with metric_12 populated -- the SAME fixture shape
    // test_api_metrics_route.py::test_populated_store_serialises_autonomy_rate_as_measured_via_
    // the_real_endpoint uses. A fresh server (INSIGHT_DB_PATH is baked into the child process's
    // env at spawn time, so a different store means a different server, not a live env swap).
    const populatedDbPath = seedFixture(scratchDir, "populated.duckdb", { populateMetric12: true });
    server = await startNext(populatedDbPath);
    try {
      const res = await fetchDeliveryAs(server.baseUrl, "manager");
      assert.equal(
        res.status, 200,
        `manager on /delivery (populated store) must succeed, got ${res.status}`,
      );
      const populatedBody = await res.text();
      assert.throws(
        () => assertNoNumeralsAnywhere(populatedBody),
        "NEGATIVE CONTROL FAILED: assertNoNumeralsAnywhere found nothing wrong with a page " +
          "rendered against a store where metric_12 legitimately has a value -- this check has " +
          "no teeth and would not catch a real regression of the literal-`0` bug class either",
      );
      console.log(
        "OK: negative control -- the same digit-scan check correctly FAILS once metric_12 is " +
        "populated (a real numeral legitimately appears on the page)",
      );
    } finally {
      server.proc.kill();
    }
  } finally {
    rmSync(scratchDir, { recursive: true, force: true });
  }

  console.log(`\nOK: prove-delivery-cold-start-no-numerals (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-delivery-cold-start-no-numerals");
  console.error(err);
  process.exit(1);
});
