// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #309 [E19.S1], .sdlc/plans/309.md. NOT part of the plan's own Task list -- added to close
// a real proof gap the plan's independent review raised: every role assertion elsewhere in this
// story (prove-role-route-matrix.mjs's Parts A/B) injects `role` directly into a STUB `@/auth`
// module. Nothing proved that `role` actually survives the REAL Auth.js pipeline -- JWT encode at
// sign-in, JWT decode + the `session` callback (auth.ts) at request time -- when `auth()` is
// invoked at the proxy layer against a REAL running server. This script is that proof, and nothing
// more: a SMALL extension of the existing booted-server proof infrastructure
// (scripts/lib/proof-session.mjs, this file's own server-lifecycle functions copied verbatim from
// prove-absence-primitives-render.mjs/prove-shell-responsive-frame.mjs), not new infrastructure.
//
// CI-ONLY, same separation as prove:fonts/prove:absence-states/prove:shell-responsive -- see
// insight/web/README.md and insight/verify_web.py's own docstring for why a booted-server proof
// does not belong in `npm run test`/the repo-wide local gate. Wired as `npm run
// prove:role-forbidden`, in .github/workflows/ci.yml's `web` job.
//
// Deliberately NO Playwright/browser here, unlike the other three CI-only proofs -- this proof
// only needs an HTTP status code and a JSON body, both directly observable with plain `fetch()`
// and a `Cookie` header carrying a session minted by scripts/lib/proof-session.mjs's
// mintSessionToken(role) (issue #309 item (b): factored out of authenticatedContext() for exactly
// this). No DOM assertion is needed, so no browser dependency is added at all -- even a CI-only
// one -- for this specific proof.
//
// "/manager" is used as the forbidden/allowed route under test even though no such PAGE exists yet
// (E20 hasn't shipped one) -- and that absence is exactly why this proof works cleanly: proxy.ts
// runs BEFORE Next.js resolves the matched route to a page at all (the same fact Decision 5 relies
// on), so the 403 for a role-mismatched request never depends on a page existing. The one case
// where a request IS allowed through (a real "manager" session hitting "/manager") is asserted by
// its absence of a 403 AND a concrete 404 -- proving the proxy passed it through to Next's own
// routing, which then correctly finds no page there, rather than by assuming "not 403" alone means
// "worked as intended."
//
// issue #312 [E20.S1] Goal A: the same pattern is reused verbatim, unchanged, for "/delivery" --
// granted to manager/leadership/ic, denied to cross-functional, no page shipped in Goal A either
// (Task A3 -- the page -- was dropped from Goal A by plan amendment; only the route grant landed).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { mintSessionToken, proofServerEnv, SESSION_COOKIE_NAME } from "./lib/proof-session.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

// ---- server lifecycle (identical pattern to prove-absence-primitives-render.mjs / --------------
// ---- prove-shell-responsive-frame.mjs) ----------------------------------------------------------

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

async function startNext() {
  const port = await getFreePort();
  const proc = spawn(NEXT_BIN, ["start", "-p", String(port)], {
    cwd: WEB,
    stdio: ["ignore", "pipe", "pipe"],
    // issue #307 [E18.S2]: the server needs the same AUTH_SECRET the proof mints its session
    // cookie with, or the proxy decodes that cookie to null and 302s every request to /login.
    env: proofServerEnv(),
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

// ---- session-carrying fetch ----------------------------------------------------------------

/** GETs `pathname` on the real running server, optionally carrying a REAL Auth.js session cookie
 * for `role` (minted by mintSessionToken(), the same encode() a real sign-in uses). `redirect:
 * "manual"` so a 3xx from proxy.ts's own NextResponse.redirect() is observable directly, never
 * silently followed. */
async function fetchAs(baseUrl, pathname, role) {
  const headers = {};
  if (role !== undefined) {
    const token = await mintSessionToken(role);
    headers.cookie = `${SESSION_COOKIE_NAME}=${token}`;
  }
  return fetch(`${baseUrl}${pathname}`, { headers, redirect: "manual" });
}

async function main() {
  const t0 = Date.now();
  const { proc, baseUrl } = await startNext();
  try {
    // 1. No session at all -- proxy.ts must still redirect to /login (role never inspected).
    //    Regression / sanity: proves this proof's own request plumbing reaches the real proxy.
    const anon = await fetchAs(baseUrl, "/manager", undefined);
    assert.ok(
      anon.status >= 300 && anon.status < 400,
      `an unauthenticated request to /manager must redirect, got status ${anon.status}`,
    );
    const anonLocation = new URL(anon.headers.get("location"), baseUrl);
    assert.equal(anonLocation.pathname, "/login", `expected redirect to /login, got ${anonLocation.pathname}`);
    console.log("OK: unauthenticated /manager -> redirect to /login (real server)");

    // 2. THE proof this script exists for: a REAL session, minted through Auth.js's own encode(),
    //    decoded through Auth.js's own JWT pipeline at the proxy layer (no stub anywhere in this
    //    path), carrying a role that does NOT match /manager -- must come back a REAL HTTP 403
    //    with the whole body exactly {"error":"forbidden"}, not a stub's approximation of one.
    const forbidden = await fetchAs(baseUrl, "/manager", "ic");
    assert.equal(
      forbidden.status, 403,
      `a real session with role "ic" hitting /manager must get a real 403, got ${forbidden.status}`,
    );
    const forbiddenBody = await forbidden.json();
    assert.deepEqual(
      forbiddenBody, { error: "forbidden" },
      `the real server's forbidden response body must be exactly {"error":"forbidden"}, got: ${JSON.stringify(forbiddenBody)}`,
    );
    console.log('OK: real session (role "ic") on /manager -> real HTTP 403, body exactly {"error":"forbidden"}');

    // 3. A real session whose role DOES match /manager must be let through by the proxy -- proven
    //    by the ABSENCE of a 403 and a concrete 404 (no /manager page exists yet), which shows the
    //    request reached Next's own route resolution rather than being forbidden at the proxy.
    const allowed = await fetchAs(baseUrl, "/manager", "manager");
    assert.notEqual(allowed.status, 403, 'a real "manager" session must not be forbidden on /manager');
    assert.equal(
      allowed.status, 404,
      `a real "manager" session on /manager (no page exists yet) must reach Next's own 404, got ${allowed.status} -- ` +
      "a non-404 here would mean this assertion needs updating once an actual /manager page ships",
    );
    console.log('OK: real session (role "manager") on /manager -> past the proxy (404, no page yet), not forbidden');

    // 4. An unknown role string, through the REAL pipeline, denies -- not a crash, not a 500.
    const unknownRole = await fetchAs(baseUrl, "/manager", "owner");
    assert.equal(unknownRole.status, 403, `an unknown role must be forbidden through the real server too, got ${unknownRole.status}`);
    console.log('OK: real session (unknown role "owner") on /manager -> real HTTP 403');

    // 5. Regression: Decision 3's shared-route carve-out still holds against a REAL session.
    const shared = await fetchAs(baseUrl, "/", "ic");
    assert.equal(shared.status, 200, `a real session must still reach the shared "/" route, got ${shared.status}`);
    console.log('OK: real session (role "ic") on shared route "/" -> 200, unaffected by the matrix');

    // 6-7. issue #312 [E20.S1] Goal A: the delivery route's role grants, proven against the REAL
    // Auth.js pipeline + REAL proxy.ts -- prove-role-route-matrix.mjs Parts A/B only prove this
    // against decide() directly and a STUBBED proxy.ts. proxy.ts denies BEFORE Next resolves any
    // page, so a denied cross-functional request never reaches app/delivery/page.tsx (or, once
    // Goal B lands, its data bridge) at all -- this is a structural guarantee about denial timing,
    // not merely a tested one, and this is the one place that guarantee is exercised against the
    // real, compiled proxy.ts. Mirrors exactly how /manager is proven above (steps 2-3): a real
    // 403 for the denied role, and a real, concrete 404 (not a 403) for a granted role -- Goal A
    // ships no page at /delivery, so "granted" is proven by absence of a 403 plus the same
    // past-the-proxy 404 signal used for /manager.
    const deliveryForbidden = await fetchAs(baseUrl, "/delivery", "cross-functional");
    assert.equal(
      deliveryForbidden.status, 403,
      `cross-functional on /delivery must get 403, got ${deliveryForbidden.status}`,
    );
    const deliveryForbiddenBody = await deliveryForbidden.json();
    assert.deepEqual(
      deliveryForbiddenBody, { error: "forbidden" },
      `the delivery forbidden body must carry no route name, no role name, no underlying data, got: ${JSON.stringify(deliveryForbiddenBody)}`,
    );
    console.log('OK: cross-functional on /delivery -> real HTTP 403, body exactly {"error":"forbidden"}');

    for (const role of ["manager", "leadership", "ic"]) {
      const deliveryAllowed = await fetchAs(baseUrl, "/delivery", role);
      assert.notEqual(
        deliveryAllowed.status, 403,
        `a real "${role}" session must not be forbidden on /delivery`,
      );
      assert.equal(
        deliveryAllowed.status, 404,
        `a real "${role}" session on /delivery (no page exists yet) must reach Next's own 404, got ${deliveryAllowed.status} -- ` +
        "a non-404 here would mean this assertion needs updating once an actual /delivery page ships",
      );
      console.log(`OK: real session (role "${role}") on /delivery -> past the proxy (404, no page yet), not forbidden`);
    }
  } finally {
    proc.kill();
  }

  console.log(`\nOK: prove-role-forbidden-real-server -- role survives Auth.js's real JWT-decode -> session-callback pipeline at the proxy layer (${Date.now() - t0}ms)`);
}

main().catch((err) => {
  console.error("FAIL: prove-role-forbidden-real-server");
  console.error(err);
  process.exit(1);
});
