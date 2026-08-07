// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// THE PROOF THAT WAS MISSING, AND WHAT IT COST.
//
// Every other proof in this directory mints its session token directly, via
// proof-session.mjs's mintSessionToken()/sessionCookieHeader(). That is deliberate and useful --
// it is what lets a role-matrix proof exercise four roles without four real logins -- but it
// means that until this file existed, NOTHING in the suite ever drove the actual login form.
// The suite proved, thoroughly, that a session cookie it minted ITSELF was honoured. It never
// proved that signing in PRODUCES such a cookie.
//
// That gap hid a total authentication failure. `useSecureCookies` does not only set the `Secure`
// attribute; @auth/core's defaultCookies() uses it to choose the cookie's NAME
// (`__Secure-authjs.session-token` vs `authjs.session-token`), and Auth.js mixes that name into
// the JWT's key derivation as the salt -- proof-session.mjs's own mintTokenForCookieName() says
// so in as many words. auth.ts computed that flag per request and fell back to `true` when
// next-auth invoked its config factory WITHOUT a request -- which is precisely what the
// in-process Server Action path does, i.e. every browser sign-in. So sign-in WROTE
// `__Secure-authjs.session-token` while every later request READ `authjs.session-token`. A
// correct username and password returned a 303 to `/`, set a real cookie, and bounced straight
// back to /login: no error, no log line, no failing test. Signing in over
// /api/auth/callback/credentials worked throughout (both halves have a request there), which is
// exactly why curl-level checks looked healthy while the product was unusable in a browser.
//
// A source-shape tripwire could not catch this -- prove-secure-cookie-flag-is-tls-conditional.mjs
// asserted the broken line verbatim, because the line matched the reasoning that produced it. The
// reasoning was simply never checked against the path the product uses. Only an end-to-end
// sign-in can close that, so that is what this file does: it drives the SAME no-JS form POST a
// browser performs, then spends the resulting cookies on a protected route.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PROOF_AUTH_SECRET, SESSION_COOKIE_NAME, SECURE_SESSION_COOKIE_NAME } from "./lib/proof-session.mjs";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const REPO_ROOT = path.resolve(WEB, "..", "..");
const NEXT_BIN = path.join(WEB, "node_modules", ".bin", "next");

const USERNAME = "proof-login-user";
const PASSWORD = "proof-login-password-8";
const ROLE = "manager";
// A route the role matrix grants to `manager` and the proxy refuses to anyone anonymous, so
// reaching it with a 200 is only possible with a session the app itself accepts.
const PROTECTED_PATH = "/delivery";

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
      const res = await fetch(url, { redirect: "manual" });
      if (res.status < 500) return;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`server did not become ready at ${url} within ${timeoutMs}ms: ${lastErr}`);
}

/** A real accounts store containing exactly one real argon2id record, written by the SAME Python
 * code path `insight users add` uses -- the Node side verifies through that bridge, so a stubbed
 * hash would prove nothing about whether sign-in actually succeeds. */
function seedAccountsStore(dir) {
  const accountsPath = path.join(dir, "insight-accounts.json");
  const script = `
import sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from insight.accounts import store
store.add_user(${JSON.stringify(USERNAME)}, ${JSON.stringify(PASSWORD)}, ${JSON.stringify(ROLE)},
               accounts_path=${JSON.stringify(accountsPath)})
`;
  const scriptPath = path.join(dir, "seed.py");
  writeFileSync(scriptPath, script);
  const res = spawnSync("python3", [scriptPath], { encoding: "utf-8" });
  assert.equal(
    res.status, 0,
    `seeding the accounts store must succeed, else this proof cannot distinguish "sign-in is ` +
    `broken" from "there is no such user":\n${res.stderr}`,
  );
  return accountsPath;
}

/** `AUTH_URL` is set to the server's OWN loopback origin, which is the realistic local
 * deployment AND the configuration that used to break: loopback is the one case where the
 * request-bearing branch answers "not secure", so it is the only case that can disagree with a
 * no-request branch that fails closed to "secure". Proving it here means proving the fix at the
 * exact point of failure, not at a convenient one. */
function proofEnv(baseUrl, accountsPath, sessionsDir) {
  return {
    ...process.env,
    AUTH_SECRET: PROOF_AUTH_SECRET,
    AUTH_URL: baseUrl,
    INSIGHT_ACCOUNTS_PATH: accountsPath,
    INSIGHT_SESSIONS_PATH: path.join(sessionsDir, "sessions.json"),
  };
}

async function startNext(accountsPath, sessionsDir) {
  const port = await getFreePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const proc = spawn(NEXT_BIN, ["start", "-p", String(port)], {
    cwd: WEB,
    stdio: ["ignore", "pipe", "pipe"],
    env: proofEnv(baseUrl, accountsPath, sessionsDir),
  });
  let out = "";
  proc.stdout.on("data", (d) => (out += d.toString()));
  proc.stderr.on("data", (d) => (out += d.toString()));
  try {
    await waitForServer(`${baseUrl}/login`);
  } catch (err) {
    proc.kill();
    throw new Error(`${err.message}\n-- next start output --\n${out}`);
  }
  return { proc, baseUrl, output: () => out };
}

/** Minimal cookie jar. Deliberately stores whatever name the server chose rather than looking for
 * a name we expect -- the whole defect was a name disagreement, so a jar that normalised names
 * would erase the very thing under test. */
function newJar() {
  return new Map();
}

function absorb(jar, res) {
  const setCookies = typeof res.headers.getSetCookie === "function"
    ? res.headers.getSetCookie()
    : [res.headers.get("set-cookie")].filter(Boolean);
  for (const line of setCookies) {
    const [pair] = line.split(";");
    const idx = pair.indexOf("=");
    if (idx <= 0) continue;
    jar.set(pair.slice(0, idx).trim(), pair.slice(idx + 1).trim());
  }
}

function cookieHeader(jar) {
  return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
}

/** Drives the form EXACTLY as a browser without client JS does: a multipart POST back to /login
 * carrying the `$ACTION_ID_...` field Next renders into the form for progressive enhancement.
 * This is the Server Action path -- the one next-auth invokes with no request object. */
async function signInThroughTheForm(baseUrl, jar) {
  const loginRes = await fetch(`${baseUrl}/login`, { headers: { cookie: cookieHeader(jar) } });
  absorb(jar, loginRes);
  const html = await loginRes.text();
  const match = html.match(/\$ACTION_ID_[a-f0-9]+/);
  assert.ok(
    match,
    "the login page must render Next's $ACTION_ID_... field; without it this proof would be " +
    "testing something other than the Server Action path the browser uses",
  );

  const body = new FormData();
  body.set(match[0], "");
  body.set("callbackUrl", "/");
  body.set("username", USERNAME);
  body.set("password", PASSWORD);

  const res = await fetch(`${baseUrl}/login`, {
    method: "POST",
    body,
    headers: { cookie: cookieHeader(jar) },
    redirect: "manual",
  });
  absorb(jar, res);
  return res;
}

async function main() {
  const fixtureDir = mkdtempSync(path.join(tmpdir(), "insight-login-proof-"));
  const accountsPath = seedAccountsStore(fixtureDir);
  const { proc, baseUrl, output } = await startNext(accountsPath, fixtureDir);

  try {
    const jar = newJar();
    const res = await signInThroughTheForm(baseUrl, jar);

    assert.ok(
      res.status >= 300 && res.status < 400,
      `a correct username and password must redirect, got ${res.status}. Server output:\n${output()}`,
    );
    console.log(`OK: the login form accepted correct credentials (${res.status} -> ${res.headers.get("location")})`);

    // (1) The defect, stated directly: sign-in must not write a cookie under a name the rest of
    // the app does not read. Both names existing at once is not acceptable either -- that was the
    // observed broken state.
    const wroteSecureName = jar.has(SECURE_SESSION_COOKIE_NAME);
    const wrotePlainName = jar.has(SESSION_COOKIE_NAME);
    assert.ok(
      wrotePlainName || wroteSecureName,
      `sign-in set no session cookie at all. Cookies seen: ${[...jar.keys()].join(", ") || "(none)"}`,
    );
    assert.ok(
      !(wrotePlainName && wroteSecureName),
      "sign-in wrote BOTH a __Secure- and a plain session cookie, which means the secure decision " +
      "is still being made twice with two different answers",
    );
    assert.equal(
      wroteSecureName, false,
      `on a plaintext loopback origin (AUTH_URL=${baseUrl}) sign-in must write ` +
      `${SESSION_COOKIE_NAME}, not ${SECURE_SESSION_COOKIE_NAME} -- the read path on this same ` +
      `origin looks for the former, and Auth.js salts the JWT with the cookie name, so a ` +
      `mismatch cannot be recovered from`,
    );
    console.log(`OK: sign-in wrote ${SESSION_COOKIE_NAME} (the name this origin's read path uses)`);

    // (2) The consequence, which is the part that actually matters to a user: the cookie sign-in
    // just issued must open a protected route. This is the assertion whose absence let a totally
    // broken login ship.
    const protectedRes = await fetch(`${baseUrl}${PROTECTED_PATH}`, {
      headers: { cookie: cookieHeader(jar) },
      redirect: "manual",
    });
    assert.equal(
      protectedRes.status, 200,
      `${PROTECTED_PATH} must be reachable with the session the login form just issued, got ` +
      `${protectedRes.status}${protectedRes.headers.get("location") ? ` -> ${protectedRes.headers.get("location")}` : ""}. ` +
      `This is the exact user-visible symptom of the cookie-name split: correct password, ` +
      `instant bounce back to /login.`,
    );
    console.log(`OK: ${PROTECTED_PATH} returns 200 carrying the session the form issued`);

    // (3) And the app must agree that somebody is signed in, with the right role -- a 200 alone
    // could in principle come from a page that renders fine while anonymous.
    const sessionRes = await fetch(`${baseUrl}/api/auth/session`, {
      headers: { cookie: cookieHeader(jar) },
    });
    const session = await sessionRes.json();
    assert.equal(session?.user?.name, USERNAME, `session must name the user who signed in, got ${JSON.stringify(session)}`);
    assert.equal(session?.user?.role, ROLE, `session must carry the account's role, got ${JSON.stringify(session)}`);
    console.log(`OK: /api/auth/session reports ${session.user.name} with role ${session.user.role}`);

    // (4) Fail-closed is still intact where it belongs: a WRONG password must not produce a
    // session. Without this, "sign-in always works" would pass every assertion above.
    const badJar = newJar();
    const loginRes = await fetch(`${baseUrl}/login`, { headers: { cookie: cookieHeader(badJar) } });
    absorb(badJar, loginRes);
    const actionId = (await loginRes.text()).match(/\$ACTION_ID_[a-f0-9]+/)[0];
    const badBody = new FormData();
    badBody.set(actionId, "");
    badBody.set("callbackUrl", "/");
    badBody.set("username", USERNAME);
    badBody.set("password", "definitely-not-the-password");
    const badRes = await fetch(`${baseUrl}/login`, {
      method: "POST", body: badBody, headers: { cookie: cookieHeader(badJar) }, redirect: "manual",
    });
    absorb(badJar, badRes);
    assert.ok(
      !badJar.has(SESSION_COOKIE_NAME) && !badJar.has(SECURE_SESSION_COOKIE_NAME),
      "a wrong password must not issue a session cookie under EITHER name",
    );
    const badProtected = await fetch(`${baseUrl}${PROTECTED_PATH}`, {
      headers: { cookie: cookieHeader(badJar) }, redirect: "manual",
    });
    assert.notEqual(badProtected.status, 200, `${PROTECTED_PATH} must stay closed after a failed sign-in`);
    console.log("OK: a wrong password issues no session and opens no protected route");
  } finally {
    proc.kill();
  }
}

await main();
