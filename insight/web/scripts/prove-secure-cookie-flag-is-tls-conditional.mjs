// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 5. Two parts, no browser, no TLS listener:
//
//   PART A -- isSecureRequest() itself, compiled with the local tsc and dynamic-imported (same
//   pattern as every other proof here), table-tested against x-forwarded-proto and URL-protocol
//   inputs -- the pure, injectable seam dossier risk 3 asked for.
//
//   PART B -- a textual check that auth.ts actually WIRES isSecureRequest() into
//   NextAuth()'s useSecureCookies, using next-auth's request-aware config form, and does not
//   override cookies.sessionToken.options.httpOnly/sameSite anywhere (both must stay Auth.js's
//   own unconditional defaults -- verified directly against @auth/core@0.41.3's own source,
//   see auth.ts's own header comment for the citation). This does NOT execute @auth/core's cookie
//   pipeline end to end (that would need a real sign-in round trip, which needs argon2-cffi on
//   this exact machine, genuinely absent) -- flagged in .sdlc/plans/307.md's Open Questions as a
//   real, honest boundary, not silently assumed.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { WEB, runTsc, runScenarioInWebAsync } from "./lib/tsc-scratch.mjs";

// --------------------------------------------------------------------------------------- part A

function scratchTsconfig() {
  return {
    compilerOptions: {
      // `types: ["node"]` (and therefore runScenarioInWebAsync, so tsc's upward node_modules walk
      // can actually find @types/node) rather than the `lib: ["dom"]` this used to carry: the
      // security fix gave secure.ts a `process.env` default for its injectable env, which needs
      // Node's own types declared, and @types/node declares the URL constructor too -- so this
      // covers what "dom" was originally added for as well.
      target: "ES2022", lib: ["ES2022"], module: "ESNext", moduleResolution: "Bundler",
      strict: true, noEmit: false, outDir: "out", esModuleInterop: true, skipLibCheck: true,
      forceConsistentCasingInFileNames: true, types: ["node"],
    },
    include: ["*.ts"],
  };
}

function compileSecure(dir) {
  writeFileSync(path.join(dir, "tsconfig.json"), JSON.stringify(scratchTsconfig(), null, 2));
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({ type: "module" }));
  writeFileSync(
    path.join(dir, "secure.ts"),
    readFileSync(path.join(WEB, "src", "lib", "auth", "secure.ts"), "utf-8"),
  );
  const { ok, output } = runTsc(dir);
  assert.ok(ok, `secure.ts must compile clean with the local tsc:\n${output}`);
  return path.join(dir, "out", "secure.js");
}

function fakeHeaders(map) {
  return { get: (name) => map[name.toLowerCase()] ?? null };
}

const TRUSTED = { INSIGHT_TRUST_PROXY_PROTO: "1" };
const UNTRUSTED = {}; // the default: no server-side opt-in, so the forwarded header means nothing

async function partA() {
  const { isSecureRequest } = await runScenarioInWebAsync(".secure-cookie-proof-scratch-", async (dir) => {
    const emitted = compileSecure(dir);
    return import(pathToFileURL(emitted).href);
  });

  const cases = [
    // --- THE REGRESSION THIS PROOF EXISTS FOR (security review of #307, BLOCKING) --------------
    // A client-suppliable header must NOT be able to strip `Secure` off the session cookie. Before
    // the fix this returned false, i.e. one spoofed header downgraded the cookie on a real TLS
    // request; the browser would then send it over plaintext http://.
    { name: "SPOOF: x-forwarded-proto:http on an https url, proxy NOT trusted", input: { headers: fakeHeaders({ "x-forwarded-proto": "http" }), url: "https://insight.example.com/login", env: UNTRUSTED }, want: true },
    { name: "SPOOF: x-forwarded-proto:http, no url, proxy NOT trusted", input: { headers: fakeHeaders({ "x-forwarded-proto": "http" }), env: UNTRUSTED }, want: true },

    // --- the header IS authoritative, but only behind the explicit server-side opt-in -----------
    { name: "trusted proxy, x-forwarded-proto: https", input: { headers: fakeHeaders({ "x-forwarded-proto": "https" }), env: TRUSTED }, want: true },
    { name: "trusted proxy, x-forwarded-proto: http", input: { headers: fakeHeaders({ "x-forwarded-proto": "http" }), env: TRUSTED }, want: false },
    { name: "trusted proxy, appended chain 'https, http' takes the client-nearest value", input: { headers: fakeHeaders({ "x-forwarded-proto": "https, http" }), env: TRUSTED }, want: true },
    { name: "trusted proxy, uppercase HTTPS", input: { headers: fakeHeaders({ "x-forwarded-proto": "HTTPS" }), env: TRUSTED }, want: true },
    { name: "trusted proxy but header absent, falls through to the url", input: { headers: fakeHeaders({}), url: "https://insight.example.com/login", env: TRUSTED }, want: true },

    // --- the server's own view of the URL -------------------------------------------------------
    { name: "no header, https:// url", input: { headers: fakeHeaders({}), url: "https://example.com/login", env: UNTRUSTED }, want: true },
    { name: "no header, http://localhost (local dev)", input: { headers: fakeHeaders({}), url: "http://localhost:3000/login", env: UNTRUSTED }, want: false },
    { name: "no header, http://127.0.0.1 (local dev)", input: { headers: fakeHeaders({}), url: "http://127.0.0.1:3000/login", env: UNTRUSTED }, want: false },

    // --- fail CLOSED wherever we cannot tell ----------------------------------------------------
    { name: "no header, plaintext on a REAL host -> still Secure", input: { headers: fakeHeaders({}), url: "http://insight.example.com/login", env: UNTRUSTED }, want: true },
    { name: "no header, unparseable url", input: { headers: fakeHeaders({}), url: "not a url", env: UNTRUSTED }, want: true },
    { name: "no header, no url, no signal at all", input: { headers: fakeHeaders({}), env: UNTRUSTED }, want: true },
  ];
  for (const c of cases) {
    const got = isSecureRequest(c.input);
    assert.equal(got, c.want, `isSecureRequest(${c.name}) => expected ${c.want}, got ${got}`);
    console.log(`OK: isSecureRequest(${c.name}) === ${c.want}`);
  }
}

// --------------------------------------------------------------------------------------- part B

function partB() {
  const authSrc = readFileSync(path.join(WEB, "src", "auth.ts"), "utf-8");
  assert.ok(
    /NextAuth\(\s*\(?\s*request\s*\)?\s*=>/.test(authSrc),
    "auth.ts must use next-auth's request-aware lazy-init form (NextAuth((request) => ({...}))) " +
    "so useSecureCookies can be computed per request -- see Decision 5",
  );
  assert.ok(
    /useSecureCookies:\s*request\s*\?\s*isSecureRequest\(request\)\s*:\s*true/.test(authSrc),
    "auth.ts must wire useSecureCookies to isSecureRequest(request), and its no-request branch " +
    "must fail CLOSED (`: true`) -- an un-judgeable request is exactly the case that must not " +
    "silently drop `Secure` (security review of #307)",
  );
  // Plan-review SHOULD-FIX: this negative assertion passes VACUOUSLY today, since auth.ts (Task
  // 5) never defines a `cookies` key at all -- it is a cheap tripwire against a FUTURE edit
  // adding one badly, not a proof that httpOnly/sameSite are correct (that rests on reading
  // @auth/core's own source, cited below, not on this regex). A determined or careless
  // reformatting of auth.ts could dodge this specific pattern while still adding an override;
  // treat a change to this assertion's inputs as a signal to re-read it, not evidence alone.
  assert.ok(
    !/cookies\s*:\s*\{[^}]*sessionToken[^}]*options[^}]*(httpOnly|sameSite)/s.test(authSrc),
    "auth.ts must not override httpOnly/sameSite -- both must stay @auth/core's own " +
    "unconditional defaults (verified: defaultCookies(), src/lib/utils/cookie.ts:59-70)",
  );
  console.log("OK: auth.ts wires isSecureRequest into useSecureCookies and does not override httpOnly/sameSite (tripwire, not a full proof -- see comment above)");
}

async function main() {
  await partA();
  partB();
}

main();
