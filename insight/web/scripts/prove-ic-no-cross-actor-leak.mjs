// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #310 [E19.S2], .sdlc/plans/310.md Task 6. THE proving script for done-when 1/2/3: an
// authenticated IC requesting another actor's data receives their own, server-side, and the
// parameter/header is never consulted -- checked against a REAL booted `next start`, not a stub.
//
// Follows prove-role-forbidden-real-server.mjs's exact pattern (getFreePort/startNext,
// redirect: "manual", raw res.text()) -- CI-only, same family as prove:fonts/prove:role-forbidden
// (a booted-server proof has no place in the always-on offline local gate; see
// insight/web/README.md and insight/verify_web.py's own docstring). Wired as
// `npm run prove:ic-no-leak` in .github/workflows/ci.yml's `web` job, after `prove:role-forbidden`
// and the new `actions/setup-python` + `pip install -e insight/` steps Decision 7 adds (this
// proof's own `seed-ic-fixture.py` needs a real `duckdb`, which the web job never installed
// before this story).
//
// NEEDLE LIST mirrors insight/tests/test_dash_ic_no_leak.py's own exactly -- same fixture
// (insight.tests.ic_fixture.seed_alice_bob_carol, seeded here via seed-ic-fixture.py), same
// three actors, same "carol has zero relationship to alice" / "bob is alice's one sanctioned
// hand-off counterparty" shape.
//
// EXECUTED NEGATIVE CONTROL (amendment SHOULD-FIX 3, mandatory -- this repo does not settle
// falsifiability with prose, see test_dash_ic_no_leak.py's own
// test_negative_control_proves_the_leak_methodology_has_teeth for the Python-side precedent this
// mirrors). Section 5 below runs the SAME `assertCarolNeedlesAbsent` check the leak assertion
// relies on against carol's OWN /ic page -- which legitimately contains her own identifiers --
// and asserts that check FAILS there. Without this, a broken/tautological needle check (e.g. a
// typo that made every `includes()` call vacuously false) would pass step 2 without ever having
// been capable of catching a real leak.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { proofServerEnv, sessionCookieHeader } from "./lib/proof-session.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const REPO_ROOT = path.resolve(WEB, "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

// ---- fixture ----------------------------------------------------------------------------------

/** Seeds a throwaway DuckDB store via seed-ic-fixture.py (Task 6's own shared seeder, which
 * itself reuses insight.tests.ic_fixture.seed_alice_bob_carol -- the SAME rows
 * test_dash_ic_no_leak.py/test_cli_web_ic.py already assert against, not a fourth independent
 * copy). Returns the seeded store's path. */
function seedFixture(scratchDir) {
  const dbPath = path.join(scratchDir, "s.duckdb");
  const result = spawnSync(
    "python3",
    [path.join(WEB, "scripts", "lib", "seed-ic-fixture.py"), "--db", dbPath],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
  assert.equal(
    result.status, 0,
    `seed-ic-fixture.py must exit 0 (is duckdb installed? Decision 7's CI step):\n` +
      `${result.stdout}\n${result.stderr}`,
  );
  return dbPath;
}

// ---- server lifecycle (identical pattern to prove-role-forbidden-real-server.mjs) --------------

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

/** Same as prove-role-forbidden-real-server.mjs's startNext(), plus INSIGHT_DB_PATH pointing at
 * the seeded fixture store (Decision 5's own text: this is what lets the CLI bridge -- a
 * child_process.spawn, not a fetch() -- read from a throwaway store instead of a real one). */
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

// ---- session-carrying fetch ---------------------------------------------------------------

/** GETs `pathname` on the real running server, carrying a REAL Auth.js session for `role`/`actor`
 * under BOTH cookie names (proof-session.mjs's sessionCookieHeader() -- /ic's own page.tsx calls
 * auth() directly, not just proxy.ts, and a Server Component's own no-argument auth() call always
 * looks for the `__Secure-`-prefixed name regardless of the server's real scheme; see
 * sessionCookieHeader()'s own comment for the live-found reason). `role`/`actor` both `undefined`
 * means no cookie at all (an anonymous request). `redirect: "manual"` so a 3xx is observable
 * directly, never silently followed. */
async function fetchAs(baseUrl, pathname, role, actor, extraHeaders = {}) {
  const headers = { ...extraHeaders };
  if (role !== undefined) {
    headers.cookie = await sessionCookieHeader(role, actor);
  }
  return fetch(`${baseUrl}${pathname}`, { headers, redirect: "manual" });
}

// ---- needle lists (mirrors test_dash_ic_no_leak.py exactly) --------------------------------

const CAROL_NEEDLES = ["carol", "g-carol-1", "g-carol-2", "9103", "303"];
// Bob's own exclusive identifiers -- his queue goal id, his own park's goal id (never actually
// fetched by this view at all, per Decision 4, but checked anyway as belt-and-suspenders), his
// verdict text and PR number. Deliberately excludes bare "bob" -- his from_actor mention on
// alice's one open hand-off (issue 301) is the ONE sanctioned exception the positive control
// below checks FOR, not against.
const BOB_EXCLUSIVE_NEEDLES = ["g-bob-1", "g-bob-2", "9102", "changes_requested"];
// Alice's own exclusive identifiers, used only for the cross-actor check against BOB's page below
// (bob has no legitimate relationship to any of these).
const ALICE_EXCLUSIVE_NEEDLES = ["g-alice-1", "9101"];

// ---- build-artifact noise ------------------------------------------------------------------
//
// Next.js's OWN per-build random buildId -- freshly generated on every `next build`, unrelated to
// source content (verified empirically: two consecutive `next build`s of the byte-identical
// source produced two different `.next/BUILD_ID` values, e.g. "za1F0ONaTZKv3Db0BWHXS" vs
// "ihF9HFtKw3mBPIdwTgJ0l") -- is inlined into EVERY authenticated page's raw HTML via the RSC
// flight payload's own router-state field (literally `\"b\":\"<buildId>\"` in the raw response
// text). Turbopack's content-hashed chunk/asset filenames under `_next/static/chunks/` are the
// same kind of noise: effectively-random alphanumeric strings, identical for every actor's page
// in a given server boot, carrying no application data at all. A short bare-digit needle like
// carol's own "303" (her fact_handoff issue number) can land as a SUBSTRING of either one by pure
// chance. That is exactly what happened in CI on PR #517 (an unrelated CSS-constant refactor that
// only changed which module a string lived in, which was enough to reshuffle Turbopack's chunk
// hashes / this build's buildId): `assertCarolNeedlesAbsent` tripped on "303" appearing inside a
// build artifact, not inside carol's data -- a false positive, reproduced by inspecting the raw
// dumped HTML (no occurrence of "303" tied to carol's actual payload; every random-length hash
// string in the page is a candidate collision surface for a 3-character bare-digit needle).
//
// Stripping these two KNOWN, proven noise sources before the needle check runs removes exactly
// that false-positive surface -- and nothing else. In particular this does NOT restrict the
// haystack to the extracted `insight-ic-data` payload or to visible rendered text: the RSC flight
// script's own DATA fields (where a "hide it in the client" leak would actually surface, per step
// 2's own comment above) are left completely untouched, so the assertion's power to catch a REAL
// leak is unchanged.
const BUILD_ID = readFileSync(path.join(WEB, ".next", "BUILD_ID"), "utf-8").trim();
const NEXT_STATIC_ASSET_RE = /_next\/static\/[^"'\\)\s]+/g;

function stripBuildArtifacts(body) {
  return body.split(BUILD_ID).join("<build-id>").replace(NEXT_STATIC_ASSET_RE, "_next/static/<asset>");
}

/** The SAME check step 2 below relies on, factored out so section 5 (the executed negative
 * control) can run it a second time against a body where it MUST fail -- shared, so the positive
 * and negative uses can never drift into checking different things (mirrors
 * test_dash_ic_no_leak.py's own `_assert_carol_absent` helper). Checks the build-artifact-stripped
 * haystack (see stripBuildArtifacts() above) so a random chunk-hash/buildId collision can never
 * produce a false "carol leaked" report -- carol's real needles never live inside those artifacts,
 * so this cannot create a false NEGATIVE either (confirmed live: section 5's negative control,
 * which runs this exact function against carol's OWN page, still fails as required -- see below). */
function assertCarolNeedlesAbsent(body) {
  const haystack = stripBuildArtifacts(body);
  for (const needle of CAROL_NEEDLES) {
    assert.ok(!haystack.includes(needle), `carol leaked via ${JSON.stringify(needle)}`);
  }
}

/** Same discipline as assertCarolNeedlesAbsent() above, for bob's/alice's own exclusive needles --
 * factored out so every call site strips build-artifact noise identically instead of each of the
 * four inline `.includes()` loops this replaces risking drifting out of sync with each other. */
function assertBobExclusiveNeedlesAbsent(body, context) {
  const haystack = stripBuildArtifacts(body);
  for (const needle of BOB_EXCLUSIVE_NEEDLES) {
    assert.ok(!haystack.includes(needle), `bob's own data leaked into ${context} via ${JSON.stringify(needle)}`);
  }
}

function assertAliceExclusiveNeedlesAbsent(body, context) {
  const haystack = stripBuildArtifacts(body);
  for (const needle of ALICE_EXCLUSIVE_NEEDLES) {
    assert.ok(!haystack.includes(needle), `alice's own data leaked into ${context} via ${JSON.stringify(needle)}`);
  }
}

/** Extracts and parses the inlined `<script type="application/json" id="insight-ic-data">`
 * payload from a raw response body -- same pattern test_dash_ic.py's own `_data_script` helper
 * uses on the Python side. */
function extractIcPayload(body) {
  const m = body.match(/<script type="application\/json" id="insight-ic-data">(.*?)<\/script>/s);
  assert.ok(m, "inlined insight-ic-data script not found in response body");
  return JSON.parse(m[1].replaceAll("\\u003c", "<"));
}

/** Deep-equality on the extracted payload, `generated_at` excluded -- found live, not anticipated
 * (see this proof's own header comment / step 4's comment): `collect_ic_payload` stamps
 * `generated_at` from the REAL wall clock on every call, and `insight web ic` has no `--now`
 * override (Decision 2's contract is deliberately request/response, not a build artifact with an
 * injectable clock) -- so no two live requests, however identical in every other respect, can
 * ever produce the exact same `generated_at`. Comparing the payload with that one field excluded
 * is what "the parameter changed nothing about which data was fetched" actually asserts. */
function assertSameDataIgnoringGeneratedAt(bodyA, bodyB, message) {
  const { generated_at: _a, ...dataA } = extractIcPayload(bodyA);
  const { generated_at: _b, ...dataB } = extractIcPayload(bodyB);
  assert.deepEqual(dataA, dataB, message);
}

async function main() {
  const t0 = Date.now();
  const scratchDir = path.join(REPO_ROOT, ".sdlc-proof-scratch-ic-no-leak");
  rmSync(scratchDir, { recursive: true, force: true });
  mkdirSync(scratchDir, { recursive: true });
  const dbPath = seedFixture(scratchDir);

  const { proc, baseUrl } = await startNext(dbPath);
  try {
    // 1. POSITIVE CONTROL -- guards against a vacuous pass from an empty/broken page. If this
    //    fails, every assertion below it is meaningless (there would be nothing to leak).
    const aliceRes = await fetchAs(baseUrl, "/ic", "ic", "alice");
    assert.equal(aliceRes.status, 200, `alice's own /ic request must succeed, got ${aliceRes.status}`);
    const aliceBody = await aliceRes.text();
    assert.ok(aliceBody.includes("g-alice-1"), "alice's own goal id must appear in her own page");
    assert.ok(aliceBody.includes("bob"), "bob's from_actor mention (the one sanctioned exception) must appear");
    console.log("OK: positive control -- alice's /ic page contains her own goal id and bob's sanctioned mention");

    // 2. LEAK ASSERTION (done-when 2, "the whole response body" -- Next inlines the RSC flight
    //    payload via __next_f script tags, so a plain res.text() on the full HTML document
    //    already captures it; no separate ?_rsc= fetch needed).
    assertCarolNeedlesAbsent(aliceBody);
    assertBobExclusiveNeedlesAbsent(aliceBody, "alice's page");
    console.log("OK: alice's raw response body carries none of carol's or bob's exclusive identifiers");

    // 3. CACHE / CROSS-ACTOR ASSERTION (done-when 2 + Decision 5) -- SAME running server process,
    //    no restart. If the App Router served a cached copy of alice's rendered output to bob,
    //    this step fails.
    const bobRes = await fetchAs(baseUrl, "/ic", "ic", "bob");
    assert.equal(bobRes.status, 200, `bob's own /ic request must succeed, got ${bobRes.status}`);
    const bobBody = await bobRes.text();
    assert.notEqual(bobBody, aliceBody, "bob's page must not be byte-identical to alice's (cache leak)");
    assertAliceExclusiveNeedlesAbsent(bobBody, "bob's page");
    assert.ok(bobBody.includes("g-bob-1"), "bob's own goal id must appear in his own page");
    assert.ok(bobBody.includes("9102"), "bob's own PR number must appear in his own page");
    console.log("OK: bob's page (same server, no restart) differs from alice's and carries only his own data");

    // 4. DONE-WHEN 1 ASSERTION -- a query param AND an X-Actor header, both naming bob, riding
    //    ALICE's own session cookie. Proves the parameter/header is never consulted, not merely
    //    ignored in the happy path (a page that silently fell back to the session on a *missing*
    //    param could still consult one when present without any test above catching it).
    //
    //    NOT literal full-body byte-identity, found live rather than assumed: (a)
    //    collect_ic_payload's generated_at is a real wall-clock timestamp with no --now override
    //    on the CLI bridge, so it differs on every separate request regardless of anything this
    //    story's code does; (b) Next.js's OWN router embeds the request's raw query string into
    //    the RSC flight payload's routing metadata (`"q"`/`"c"` fields) for EVERY request that
    //    carries one, whether or not the page component ever reads searchParams -- a framework
    //    artifact, not application data, and it would make even a flawless implementation fail a
    //    literal byte-for-byte check. The assertion below is a strictly more precise version of
    //    "the parameter changed nothing about which data was fetched": the extracted JSON
    //    payload matches exactly (generated_at excluded) AND the raw body still carries none of
    //    bob's/carol's needles beyond the one sanctioned mention -- so a "hide it in the client"
    //    bug hiding in the RSC flight bytes would still be caught even if the parsed payload
    //    happened to look clean.
    const aliceViaQueryParam = await fetchAs(baseUrl, "/ic?actor=bob", "ic", "alice");
    assert.equal(aliceViaQueryParam.status, 200);
    const aliceViaQueryParamBody = await aliceViaQueryParam.text();
    assertSameDataIgnoringGeneratedAt(
      aliceViaQueryParamBody, aliceBody,
      "/ic?actor=bob with alice's session must return the exact same data as plain /ic -- the query param was consulted",
    );
    assertCarolNeedlesAbsent(aliceViaQueryParamBody);
    assertBobExclusiveNeedlesAbsent(aliceViaQueryParamBody, "alice's page via ?actor=bob");

    const aliceViaHeader = await fetchAs(baseUrl, "/ic", "ic", "alice", { "X-Actor": "bob" });
    assert.equal(aliceViaHeader.status, 200);
    const aliceViaHeaderBody = await aliceViaHeader.text();
    assertSameDataIgnoringGeneratedAt(
      aliceViaHeaderBody, aliceBody,
      "/ic with an X-Actor: bob header on alice's session must return the exact same data as plain /ic -- the header was consulted",
    );
    assertCarolNeedlesAbsent(aliceViaHeaderBody);
    assertBobExclusiveNeedlesAbsent(aliceViaHeaderBody, "alice's page via X-Actor: bob");
    console.log("OK: neither ?actor=bob nor X-Actor: bob (alice's own session) change which data is returned");

    // 5. EXECUTED NEGATIVE CONTROL (amendment SHOULD-FIX 3, mandatory -- this repo does not
    //    settle falsifiability with prose; see test_dash_ic_no_leak.py's own
    //    test_negative_control_proves_the_leak_methodology_has_teeth for the exact Python-side
    //    precedent this mirrors). Mint CAROL's own session and fetch /ic AS her: her own page
    //    legitimately contains her own needles (g-carol-1, 9103, ...) -- that is not a leak, it
    //    is her own data. Running the SAME assertCarolNeedlesAbsent check step 2 relied on
    //    against HER OWN page must FAIL. Without this, a broken/tautological check (e.g. a typo
    //    that made every `includes()` call vacuously false, or a needle list that stopped
    //    matching real output) would have passed step 2 without ever having been capable of
    //    catching a real leak.
    const carolRes = await fetchAs(baseUrl, "/ic", "ic", "carol");
    assert.equal(carolRes.status, 200, `carol's own /ic request must succeed, got ${carolRes.status}`);
    const carolBody = await carolRes.text();
    assert.throws(
      () => assertCarolNeedlesAbsent(carolBody),
      "NEGATIVE CONTROL FAILED: assertCarolNeedlesAbsent found nothing wrong with carol's OWN " +
        "page, which legitimately contains her own identifiers -- this check has no teeth and " +
        "would not catch a real leak either",
    );
    console.log("OK: negative control -- the same needle check correctly FAILS against carol's own page");
  } finally {
    proc.kill();
    rmSync(scratchDir, { recursive: true, force: true });
  }

  console.log(`\nOK: prove-ic-no-cross-actor-leak -- no cross-actor leak across two real identities in one server boot (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-ic-no-cross-actor-leak");
  console.error(err);
  process.exit(1);
});
