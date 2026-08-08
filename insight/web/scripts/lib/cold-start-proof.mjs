// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #574 (P2, filed from #313's [E20.S2] retro): prove-delivery-cold-start-no-numerals.mjs
// and prove-manager-cold-start-no-numerals.mjs were near-identical copies -- getFreePort/
// waitForServer/startNext/seedFixture/NUMERAL_SLOT_RE/extractNumeralTexts/
// assertNoNumeralsAnywhere/the two-section main() flow were the same shape, differing only in
// fetch path, populate-flag name(s), and floor value/derivation. This file is that extraction,
// landed (issue #314 [E20.S3], .sdlc/plans/314.md Decision B) before a THIRD near-copy
// (leadership) would have made it three. Delivery and manager are converted to thin call sites of
// runColdStartNoNumeralsProof() below -- byte-for-byte preserving their existing floor values,
// same pass/fail semantics, same executed-negative-control discipline (log lines rephrased
// generically where a caller-specific detail, e.g. "metric_12", would otherwise have to leak into
// shared code -- see .sdlc/plans/314.md Step 2's own Verify note: "same log lines modulo the
// harness's own generic phrasing"). Leadership is written directly against this harness, never
// existing as a standalone copy.
//
// See prove-delivery-cold-start-no-numerals.mjs's OWN header comment (still present, history
// preserved) for the full "why a cold-start proof at all" rationale -- this file is pure
// mechanism, moved here, not a rewrite of the underlying method.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { mintSessionToken, proofServerEnv, SESSION_COOKIE_NAME } from "./proof-session.mjs";

// This file lives at scripts/lib/cold-start-proof.mjs -- three ".." from here (lib/ -> scripts/
// -> web/) reaches insight/web/, the same derivation tsc-scratch.mjs's own WEB constant uses for
// a file at the identical depth.
export const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..", "..");
const REPO_ROOT = path.resolve(WEB, "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

// ---- fixture ------------------------------------------------------------------------------

/** Seeds a throwaway DuckDB store via seed-cold-start-store.py. `extraArgs` (e.g.
 * `["--populate-metric-5", "--populate-metric-23"]`) are appended verbatim -- an empty array
 * seeds the schema-only, zero-row cold fixture every caller's section 1 needs. */
export function seedFixture(scratchDir, filename, extraArgs = []) {
  const dbPath = path.join(scratchDir, filename);
  const args = [path.join(WEB, "scripts", "lib", "seed-cold-start-store.py"), "--db", dbPath, ...extraArgs];
  const result = spawnSync("python3", args, { cwd: REPO_ROOT, encoding: "utf-8" });
  assert.equal(
    result.status, 0,
    "seed-cold-start-store.py must exit 0 (is duckdb installed? ci.yml's install step):\n" +
      `${result.stdout}\n${result.stderr}`,
  );
  return dbPath;
}

// ---- server lifecycle -----------------------------------------------------------------------

export function getFreePort() {
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

export async function waitForServer(url, timeoutMs = 30000) {
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

/** INSIGHT_DB_PATH points the route's own CLI-bridge spawn at the seeded fixture store, not a
 * real one -- identical pattern to prove-ic-no-cross-actor-leak.mjs's own startNext(dbPath). */
export async function startNext(dbPath) {
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

/** GETs `route` carrying a real Auth.js session for `role` -- none of delivery/manager/leadership
 * page.tsx calls auth() itself (all aggregate-only, no actor), so only proxy.ts's own cookie
 * lookup matters here; the plain (non `__Secure-`-prefixed) cookie name is enough, same as
 * prove-role-forbidden-real-server.mjs's own fetchAs(). */
async function fetchRouteAs(baseUrl, route, role) {
  const token = await mintSessionToken(role, "proof-user");
  return fetch(`${baseUrl}${route}`, {
    headers: { cookie: `${SESSION_COOKIE_NAME}=${token}` },
    redirect: "manual",
  });
}

// ---- the digit-scan check itself -------------------------------------------------------------

const DIGIT = /[0-9]/;
// Non-greedy `[^<]*` capture of the numeral slot's own inner text -- mirrors
// prove-absence-primitives-render.mjs's own numeral-slot text extraction, done here via regex
// against the raw HTML (no browser) since Next inlines the full server-rendered markup into the
// document a plain res.text() already captures.
const NUMERAL_SLOT_RE = /data-testid="metric-numeral"[^>]*>([^<]*)</g;

export function extractNumeralTexts(body) {
  return [...body.matchAll(NUMERAL_SLOT_RE)].map((m) => m[1]);
}

/** THE check both section 1 (must pass) and section 2's negative control (must fail) share --
 * factored out so the two can never drift into checking different things. `floor` is the "not
 * vacuous" guard: a selector that matched ZERO elements would make "no digit found" pass
 * trivially, for the wrong reason. */
export function makeAssertNoNumeralsAnywhere(floor) {
  return function assertNoNumeralsAnywhere(body) {
    const numerals = extractNumeralTexts(body);
    assert.ok(
      numerals.length >= floor,
      `expected at least ${floor} metric-numeral slots, found ${numerals.length} -- a check ` +
      "that finds nothing is not a check",
    );
    for (const text of numerals) {
      assert.ok(!DIGIT.test(text), `a metric-numeral slot rendered a digit: ${JSON.stringify(text)}`);
    }
  };
}

/** The full two-section (`cold.duckdb` must pass / `populated.duckdb` must throw) orchestration,
 * shared by every caller (delivery, manager, leadership).
 *
 * - `floor` is either a plain number (delivery's own hardcoded 46, unchanged) or an async thunk
 *   `() => Promise<number>` (manager/leadership derive it live from their own curation list --
 *   each caller owns its own "floor on the floor" sanity check inside its thunk, since only the
 *   caller knows what "too small" means for its own list).
 * - `populateFlags` (string[]) are passed to seed-cold-start-store.py for the SECOND (populated)
 *   fixture only -- the cold fixture is always seeded with none. A single-element array is
 *   byte-identical in behaviour to a single `--populate-*` flag (issue #314 [E20.S3] Step 9's own
 *   harness-signature amendment: `populateFlag` (string) -> `populateFlags` (string[])).
 * - `assertPopulated(body)` is an optional extra check run against the POPULATED body, after the
 *   mandatory negative control's `assert.throws` -- e.g. leadership's own assertion that metric
 *   23's numeral AND its coverage denominator both actually render (Decision A's class-2 positive
 *   proof). Defaults to a no-op so delivery and manager, which need no such check, are unaffected
 *   by its addition. */
export async function runColdStartNoNumeralsProof({
  proofName, route, role, populateFlags, floor, assertPopulated,
}) {
  const t0 = Date.now();
  const numeralFloor = typeof floor === "function" ? await floor() : floor;
  const assertNoNumeralsAnywhere = makeAssertNoNumeralsAnywhere(numeralFloor);

  const scratchDir = path.join(REPO_ROOT, `.sdlc-proof-scratch-${proofName}`);
  rmSync(scratchDir, { recursive: true, force: true });
  mkdirSync(scratchDir, { recursive: true });

  try {
    // ---- 1. THE MANDATORY FIXTURE: schema present, zero rows, no ingest ----------------------
    const coldDbPath = seedFixture(scratchDir, "cold.duckdb", []);
    let server = await startNext(coldDbPath);
    let coldBody;
    try {
      const res = await fetchRouteAs(server.baseUrl, route, role);
      assert.equal(
        res.status, 200,
        `${role} on ${route} (cold-start store) must succeed, got ${res.status}`,
      );
      coldBody = await res.text();
      assertNoNumeralsAnywhere(coldBody);
      console.log(
        `OK: cold-start ${route} renders no numerals anywhere ` +
        `(${extractNumeralTexts(coldBody).length} metric-numeral slots, all empty)`,
      );
    } finally {
      server.proc.kill();
    }

    // ---- 2. EXECUTED NEGATIVE CONTROL (mandatory, not prose) ----------------------------------
    // A fresh server (INSIGHT_DB_PATH is baked into the child process's env at spawn time, so a
    // different store means a different server, not a live env swap).
    const populatedDbPath = seedFixture(scratchDir, "populated.duckdb", populateFlags);
    server = await startNext(populatedDbPath);
    try {
      const res = await fetchRouteAs(server.baseUrl, route, role);
      assert.equal(
        res.status, 200,
        `${role} on ${route} (populated store) must succeed, got ${res.status}`,
      );
      const populatedBody = await res.text();
      assert.throws(
        () => assertNoNumeralsAnywhere(populatedBody),
        "NEGATIVE CONTROL FAILED: assertNoNumeralsAnywhere found nothing wrong with a page " +
          "rendered against a store where the populated fixture legitimately has a value -- " +
          "this check has no teeth and would not catch a real regression of the literal-`0` " +
          "bug class either",
      );
      console.log(
        `OK: negative control -- the same digit-scan check correctly FAILS once ${route}'s ` +
        "populated fixture legitimately carries a value (a real numeral legitimately appears " +
        "on the page)",
      );
      if (assertPopulated) {
        assertPopulated(populatedBody);
      }
    } finally {
      server.proc.kill();
    }
  } finally {
    rmSync(scratchDir, { recursive: true, force: true });
  }

  console.log(`\nOK: ${proofName} (${Date.now() - t0}ms)`);
}
