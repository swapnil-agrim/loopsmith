// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #308 [E18.S3], .sdlc/plans/308.md Decision 1/2/8/9, Task 7. THE proof for done-when 1
// ("logout invalidates the session server-side -- a test replays the old cookie and is refused")
// and done-when 2 ("sessions expire; an expired session is refused and redirects to login").
//
// Browser-free (plain fetch()) and sleep-free (every revocation/expiry state is minted directly
// via next-auth/jwt's own encode(), never produced by waiting) -- so, unlike
// prove-shell-responsive-frame.mjs/prove-fonts-actually-apply.mjs, this belongs in `npm run test`
// (Decision 8/`test`'s own always-on, offline requirement), not CI's browser-only `web` job.
//
// SERVER LIFECYCLE: getFreePort()/waitForServer() below are the SAME pattern
// prove-shell-responsive-frame.mjs already established (verbatim, not re-derived) -- kept local to
// this file rather than factored into a shared lib, since scripts/lib/proof-session.mjs's own
// header already draws the line at "give this browser a real session," and this proof needs no
// browser at all; duplicating ~15 lines here was judged cheaper than growing that file's scope.
//
// Decision 8: THIS SCRIPT RUNS ITS OWN `next build` before `next start`, into the SAME `.next/`
// output the `build` CHECKS step also produces (not a separate output directory) -- that is what
// makes the `build` CHECKS step's own later `next build` (package.json's `test` runs before
// `build`, insight/verify_web.py's CHECKS order, unchanged) able to reuse this run's warm
// `.next/cache/` instead of a second cold build. See insight/web/README.md and the PR description
// for the measured wall-clock delta this decision was made contingent on (Decision 8's own
// plan-review requirement -- a real number, not the plausibility of the incremental-cache
// argument).
//
// ponytail: this script pays its own `next build` because `verify_web.py`'s CHECKS order
// (`test` before `build`) is a repo-wide contract this goal deliberately does not touch (Decision
// 8 rejects reordering CHECKS). Once a FUTURE goal reorders CHECKS for unrelated reasons, this
// script should switch to reusing the shared `.next` output the way
// prove-shell-responsive-frame.mjs already does for its CI-only proof, instead of building again.
//
// Scenarios 1-4 share ONE server boot. Scenario 5 (code review finding 1 -- an unwritable
// sessions file must not let sign-out report success) needs a SECOND `next start` instance, since
// INSIGHT_SESSIONS_PATH is read once per process at request time and cannot be changed under the
// first server -- it reuses the SAME `.next/` build output rather than paying for a second `next
// build` (see that scenario's own comment for why that reuse is valid).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { mkdtempSync, rmSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";

import { encode } from "next-auth/jwt";

import { proofServerEnv, PROOF_AUTH_SECRET, SESSION_COOKIE_NAME } from "./lib/proof-session.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

// Auth.js's defaultCookies() (@auth/core/src/lib/utils/cookie.ts:59-70, cited already by
// secure.ts/proof-session.mjs): the CSRF cookie is `authjs.csrf-token`, `__Host-`-prefixed only
// when useSecureCookies is on. These proofs run `next start` over plain http (useSecureCookies
// false), so the unprefixed name is the one the server sets and expects -- same reasoning
// proof-session.mjs's own SESSION_COOKIE_NAME comment already gives for that cookie.
const CSRF_COOKIE_NAME = "authjs.csrf-token";

// ---- server lifecycle (verbatim pattern from prove-shell-responsive-frame.mjs) -----------------

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

/** Runs `next build`, returning the elapsed ms -- Decision 8's own plan-review requirement is a
 * MEASURED number for this cost, not an estimate, so main() prints it. Throws (with captured
 * output) on a nonzero exit -- a smoke-test proof must not silently start a server against a
 * build that never happened. */
function runNextBuild(env) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const proc = spawn(NEXT_BIN, ["build"], { cwd: WEB, stdio: ["ignore", "pipe", "pipe"], env });
    let out = "";
    proc.stdout.on("data", (d) => (out += d.toString()));
    proc.stderr.on("data", (d) => (out += d.toString()));
    proc.on("close", (code) => {
      const elapsedMs = Date.now() - t0;
      if (code === 0) resolve(elapsedMs);
      else reject(new Error(`next build exited ${code} after ${elapsedMs}ms\n${out}`));
    });
  });
}

async function startNext(env) {
  const port = await getFreePort();
  const proc = spawn(NEXT_BIN, ["start", "-p", String(port)], {
    cwd: WEB,
    stdio: ["ignore", "pipe", "pipe"],
    env,
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

// ---- cookie jar + fetch helpers (no browser -- plain fetch, manual redirects, manual cookies) --

/** Parses the `name=value` pairs off a response's Set-Cookie headers (attributes like Path/
 * HttpOnly/SameSite are irrelevant here -- this proof runs everything against the same origin and
 * path, and Node's fetch does not enforce cookie attributes for us the way a browser would). */
function parseSetCookies(res) {
  const jar = {};
  for (const setCookie of res.headers.getSetCookie()) {
    const pair = setCookie.split(";", 1)[0];
    const eq = pair.indexOf("=");
    jar[pair.slice(0, eq)] = pair.slice(eq + 1);
  }
  return jar;
}

function cookieHeader(jar) {
  return Object.entries(jar)
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
}

/** One request against `baseUrl${urlPath}`, manual-redirect (a redirect response's own status/
 * Location header IS the assertion surface for done-when 1/2 -- following it would hide exactly
 * what this proof needs to see), returning both the raw Response and any cookies it set. */
async function request(baseUrl, urlPath, { method = "GET", cookies = {}, headers = {}, body } = {}) {
  const res = await fetch(`${baseUrl}${urlPath}`, {
    method,
    redirect: "manual",
    headers: {
      ...(Object.keys(cookies).length ? { cookie: cookieHeader(cookies) } : {}),
      ...headers,
    },
    body,
  });
  return { res, setCookies: parseSetCookies(res) };
}

function assertAllowed(res, label) {
  assert.equal(
    res.status, 200,
    `${label}: expected 200 (allowed through), got ${res.status}` +
    (res.status >= 300 && res.status < 400 ? ` redirecting to ${res.headers.get("location")}` : ""),
  );
}

function assertRedirectedToLogin(res, baseUrl, label) {
  assert.ok(
    res.status >= 300 && res.status < 400,
    `${label}: expected a redirect (3xx), got ${res.status}`,
  );
  const location = res.headers.get("location") ?? "";
  // proxy.ts's own redirect (loginUrl below) is relative ("/login?callbackUrl=..."), not
  // absolute -- resolve against baseUrl before reading .pathname, or `new URL()` throws
  // ERR_INVALID_URL on a bare relative path.
  assert.ok(
    new URL(location, baseUrl).pathname === "/login",
    `${label}: expected a redirect to /login, got Location: ${location}`,
  );
}

/** Mints a session cookie value the same way scripts/lib/proof-session.mjs's own
 * authenticatedContext() does (real Auth.js encode(), same secret/salt/cookie name a genuine
 * sign-in would use) -- `sessionEpoch: 0` matches what auth.ts's own jwt() callback stamps onto a
 * FRESH sign-in for an account whose epoch has never been bumped (Decision 1/2). `maxAgeSeconds`
 * lets scenario 3 mint an ALREADY-EXPIRED token directly, no waiting. */
async function mintSessionCookie(sub, { maxAgeSeconds } = {}) {
  return encode({
    salt: SESSION_COOKIE_NAME,
    secret: PROOF_AUTH_SECRET,
    token: { name: sub, sub, role: "admin", sessionEpoch: 0 },
    ...(maxAgeSeconds !== undefined ? { maxAge: maxAgeSeconds } : {}),
  });
}

/** Drives a real Auth.js CSRF double-submit sign-out against `baseUrl` for `sessionCookie` (the
 * same round-trip scenario 2 below performs inline) -- factored out so the Finding 1 scenario can
 * reuse it against a SECOND server instance without duplicating the CSRF handshake. Returns the
 * raw sign-out Response. */
async function signOutViaRealCsrfRoundTrip(baseUrl, sessionCookie) {
  const { res: csrfRes, setCookies: csrfCookies } = await request(baseUrl, "/api/auth/csrf");
  assert.equal(csrfRes.status, 200, "GET /api/auth/csrf must succeed to obtain a CSRF token");
  const { csrfToken } = await csrfRes.json();
  assert.ok(typeof csrfToken === "string" && csrfToken.length > 0, "csrf response must carry a csrfToken string");
  const csrfCookieValue = csrfCookies[CSRF_COOKIE_NAME];
  assert.ok(csrfCookieValue, `GET /api/auth/csrf must set the ${CSRF_COOKIE_NAME} cookie`);

  const { res } = await request(baseUrl, "/api/auth/signout", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ csrfToken }).toString(),
    cookies: {
      [CSRF_COOKIE_NAME]: csrfCookieValue,
      [SESSION_COOKIE_NAME]: sessionCookie,
    },
  });
  return res;
}

async function main() {
  const stateDir = mkdtempSync(path.join(tmpdir(), "insight-web-session-revocation-proof-"));
  const env = {
    ...proofServerEnv(),
    // issue #308 [E18.S3], .sdlc/plans/308.md Decision 2 -- an isolated scratch sessions file,
    // never the real `.sdlc/insight-web-sessions.json`, so this proof's own signOut() call can
    // never bump a real deployment's epoch state and a leftover epoch from a previous run of this
    // proof can never leak into the next one.
    INSIGHT_SESSIONS_PATH: path.join(stateDir, "sessions.json"),
  };

  let proc;
  try {
    const buildMs = await runNextBuild(env);
    console.log(`OK: scoped \`next build\` completed in ${buildMs}ms`);

    const started = await startNext(env);
    proc = started.proc;
    const { baseUrl } = started;

    // ---- Scenario 1: positive control -----------------------------------------------------
    // Proves the harness itself works BEFORE asserting any refusal below -- a real, valid,
    // freshly-minted session cookie must be accepted. Without this, scenarios 2/3 passing would
    // be ambiguous: is the server correctly refusing a revoked/expired cookie, or does it refuse
    // EVERY cookie (a broken harness reads identically to a working revocation check)?
    const revocationSub = "proof-revocation-user";
    const originalCookie = await mintSessionCookie(revocationSub);
    {
      const { res } = await request(baseUrl, "/", { cookies: { [SESSION_COOKIE_NAME]: originalCookie } });
      assertAllowed(res, "positive control: fresh valid session cookie");
    }
    console.log("OK: positive control -- a freshly-minted, valid session cookie is accepted");

    // ---- Scenario 2: done-when 1 -- logout invalidates the session server-side ------------
    // Real Auth.js CSRF double-submit: GET /api/auth/csrf for a token + its matching cookie, then
    // POST /api/auth/signout carrying BOTH that CSRF cookie/token AND the session cookie from
    // scenario 1 -- exercising the real events.signOut -> bumpEpoch() path end to end (Decision 1
    // /2), no stub.
    const signOutRes = await signOutViaRealCsrfRoundTrip(baseUrl, originalCookie);
    assert.ok(
      signOutRes.status < 400,
      `POST /api/auth/signout must succeed (got ${signOutRes.status}) -- a failure here means ` +
      "the real events.signOut -> bumpEpoch() path never ran, and the replay assertion below " +
      "would prove nothing",
    );
    console.log("OK: POST /api/auth/signout (real CSRF round-trip) succeeded");

    // The REPLAY: the exact same, unmodified cookie from scenario 1, after the signout above.
    {
      const { res } = await request(baseUrl, "/", { cookies: { [SESSION_COOKIE_NAME]: originalCookie } });
      assertRedirectedToLogin(res, baseUrl, "done-when 1: replaying the original cookie after sign-out");
    }
    console.log("OK: done-when 1 -- the original session cookie is refused after sign-out (redirected to /login)");

    // ---- Scenario 3: done-when 2 -- an expired session is refused, redirects to login -----
    // A DIFFERENT username from scenario 2 -- this must fail on EXPIRY, not on being caught by
    // scenario 2's epoch bump (which only ever touched revocationSub's epoch).
    const expiredCookie = await mintSessionCookie("proof-expiry-user", { maxAgeSeconds: -3600 });
    {
      const { res } = await request(baseUrl, "/", { cookies: { [SESSION_COOKIE_NAME]: expiredCookie } });
      assertRedirectedToLogin(res, baseUrl, "done-when 2: an already-expired session cookie");
    }
    console.log("OK: done-when 2 -- an expired session cookie is refused (redirected to /login)");

    // ---- Scenario 4: negative control ------------------------------------------------------
    // A FRESH cookie, minted AFTER scenarios 2/3 ran, for a THIRD username that was never signed
    // out and never expired -- must still be accepted. Without this, scenarios 2/3 passing could
    // mean "the server now rejects every cookie unconditionally," not "revocation/expiry are
    // specifically enforced" -- the same false-positive class prove-shell-responsive-frame.mjs's
    // own negative control rules out for its overflow assertion.
    const freshCookie = await mintSessionCookie("proof-negative-control-user");
    {
      const { res } = await request(baseUrl, "/", { cookies: { [SESSION_COOKIE_NAME]: freshCookie } });
      assertAllowed(res, "negative control: a fresh, never-revoked, never-expired cookie for a different user");
    }
    console.log(
      "OK: negative control -- a fresh cookie for an unrelated, never-touched user is still accepted " +
      "after the revocation/expiry refusals above (proving those refusals are specific, not blanket)",
    );
  } finally {
    if (proc) proc.kill();
    rmSync(stateDir, { recursive: true, force: true });
  }

  // ---- Scenario 5: code review finding 1 -- a sign-out whose epoch write genuinely fails must
  // NOT report success, and a replayed cookie must never be accepted while sign-out claimed OK ---
  // Needs a SEPARATE `next start` instance (INSIGHT_SESSIONS_PATH is read once per process, at
  // request time -- it cannot be changed for the already-running server above), but NOT a second
  // `next build`: this reuses the SAME `.next/` output the first build already produced (Decision
  // 8 pays for exactly one build; env vars this app reads via `process.env` at request time are
  // never inlined at build time, so a second `next start` against the same build with a different
  // env is valid).
  //
  // POSIX permission bits are not a concept on Windows, and root ignores them entirely -- both
  // make chmod-based unwritability untestable here. Skipped (not deleted) on either, mirroring
  // insight/tests/test_accounts_store.py's own os.name/getuid-based skip convention for the exact
  // same reason (see e.g. its test_read_accounts_raises_a_clean_error_not_a_raw_permission_error).
  if (process.platform === "win32") {
    console.log(
      "SKIP: Finding 1 unwritable-sessions-file scenario (POSIX permission bits are not a " +
      "concept on Windows)",
    );
  } else if (typeof process.getuid === "function" && process.getuid() === 0) {
    console.log(
      "SKIP: Finding 1 unwritable-sessions-file scenario (running as root -- chmod does not " +
      "deny root's own write access)",
    );
  } else {
    const unwritableDir = mkdtempSync(
      path.join(tmpdir(), "insight-web-session-revocation-unwritable-"),
    );
    const unwritableEnv = {
      ...proofServerEnv(),
      INSIGHT_SESSIONS_PATH: path.join(unwritableDir, "sessions.json"),
    };
    // No write permission on the DIRECTORY (not the file -- the file never exists) is what makes
    // sessionEpoch.ts's writeSessionsFileAtomic() fail: it always creates a fresh, randomly-named
    // temp file inside this directory before renaming it into place, and that create is what a
    // read-only directory refuses (EACCES) -- see sessionEpoch.ts's own writeSessionsFileAtomic
    // docstring. Set AFTER mkdtempSync (which needs to create the dir with normal permissions
    // first), BEFORE the server that will try to write into it ever starts.
    chmodSync(unwritableDir, 0o555);
    let unwritableProc;
    try {
      const started = await startNext(unwritableEnv);
      unwritableProc = started.proc;
      const { baseUrl: unwritableBaseUrl } = started;

      const failSub = "proof-unwritable-sessions-user";
      const failCookie = await mintSessionCookie(failSub);

      // Positive control for THIS server instance, same reasoning as scenario 1 -- proves this
      // second server is itself working before asserting anything about a failed sign-out below.
      {
        const { res } = await request(unwritableBaseUrl, "/", {
          cookies: { [SESSION_COOKIE_NAME]: failCookie },
        });
        assertAllowed(res, "Finding 1: fresh cookie accepted before the failed sign-out attempt");
      }

      const failedSignOutRes = await signOutViaRealCsrfRoundTrip(unwritableBaseUrl, failCookie);
      // THE "surfaced, not swallowed" assertion. Pre-fix, @auth/core's own actions/signout.js
      // swallows bumpEpoch()'s throw and clears the cookie / reports success regardless (see
      // auth.ts's own header comment for the verified evidence trail) -- this would have been a
      // 2xx/3xx here. Post-fix, route.ts's wrapped POST detects the revocation failure (via
      // auth.ts's withSignOutFailureTracking) and returns 500 instead of forwarding that success.
      assert.ok(
        failedSignOutRes.status >= 400,
        "Finding 1: POST /api/auth/signout against an unwritable sessions file must NOT report " +
        `success -- got ${failedSignOutRes.status}, expected >= 400 (the pre-fix code returned ` +
        "a 2xx/3xx here, silently swallowing the write failure)",
      );
      console.log(
        `OK: Finding 1 -- sign-out against an unwritable sessions file is surfaced as a ` +
        `failure (status ${failedSignOutRes.status}), never reported as success`,
      );

      // THE "not silently still-accepted-while-said-OK" assertion. Sign-out above explicitly did
      // NOT report success, so the original cookie remaining valid here is the EXPECTED, honest
      // outcome -- not a regression. What this rules out is the forbidden combination: "sign-out
      // said OK" AND "the old cookie still works", which the status assertion above already
      // shows did not happen. (What this scenario does NOT and cannot prove: that revocation
      // itself succeeds when the file is genuinely unwritable -- it structurally cannot, there is
      // nowhere to persist the revocation. That is a real, named limitation of the epoch design,
      // not something this fix claims to have solved -- see auth.ts's own header comment.)
      {
        const { res } = await request(unwritableBaseUrl, "/", {
          cookies: { [SESSION_COOKIE_NAME]: failCookie },
        });
        assertAllowed(
          res,
          "Finding 1: replaying the cookie after a FAILED (not successful) sign-out is still " +
          "accepted -- an honest failure, not a silent, undetected revocation",
        );
      }
      console.log(
        "OK: Finding 1 -- no deceptive combination observed (a sign-out that failed to revoke " +
        "never simultaneously claims success)",
      );
    } finally {
      if (unwritableProc) unwritableProc.kill();
      chmodSync(unwritableDir, 0o755);
      rmSync(unwritableDir, { recursive: true, force: true });
    }
  }

  console.log("\nOK: prove-session-revocation-and-expiry");
}

main().catch((err) => {
  console.error("FAIL: prove-session-revocation-and-expiry");
  console.error(err);
  process.exit(1);
});
