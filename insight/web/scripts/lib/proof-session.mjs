// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2]. Shared "give this browser a real session" machinery for the CI-only
// browser proofs.
//
// WHY THIS EXISTS. E18.S2 made every app route private by default. The browser proofs written
// before it (#304's prove-absence-primitives-render.mjs -> /dev/absence-states, #305's
// prove-shell-responsive-frame.mjs -> /) navigate as an ANONYMOUS visitor, so from this story
// onward the proxy correctly 302s both of them to /login and their assertions find nothing. That
// is the feature working, not a regression -- but it means those proofs must now authenticate.
//
// WHY A MINTED COOKIE AND NOT A LOGIN FORM ROUND-TRIP. Driving the real /login form would make
// these proofs depend on a populated accounts store AND an importable argon2-cffi inside the web
// CI job (a Node job with no Python provisioning today) -- that is issue #307's separate
// deployment gap, not something a rendering proof should carry. Minting the session directly uses
// Auth.js's OWN encode() and its OWN cookie name, so the cookie is byte-identical in format to one
// a real login issues, and the proxy validates it through exactly the same decode path. As a
// bonus, that makes these proofs a live check that a VALID session really is allowed through --
// the positive half of default-deny, which the offline Part C proof can only assert against stubs.
//
// The secret below is a test fixture, not a credential: it is passed to the throwaway `next start`
// these proofs spawn, and the server is killed when they finish. Nothing signs anything real with
// it, and it must never be used as a deployment's AUTH_SECRET.
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { encode } from "next-auth/jwt";

export const PROOF_AUTH_SECRET =
  "insight-e18s2-proof-fixture-secret-not-a-real-credential";

// No `__Secure-` prefix: @auth/core's defaultCookies() only adds it when useSecureCookies is on,
// and auth.ts computes that per request via isSecureRequest() -- these proofs run `next start`
// over plain http, so the unprefixed name is the one the server will look for. If a proof is ever
// pointed at an https origin, this has to become the prefixed name or the session silently reads
// as anonymous (a redirect to /login, not an error).
export const SESSION_COOKIE_NAME = "authjs.session-token";

// issue #310 [E19.S2]: found live, not anticipated -- src/proxy.ts's own auth() call always
// carries a real Request, so isSecureRequest(request) correctly evaluates useSecureCookies=false
// for a loopback-hosted plain-http proof server (src/lib/auth/secure.ts's LOOPBACK_HOSTS carve-
// out), matching SESSION_COOKIE_NAME above -- exactly what made prove-role-forbidden-real-
// server.mjs work. But a Server Component's OWN no-argument auth() call (next-auth/lib/index.js's
// "React Server Components" branch) always calls the app's config factory with `request:
// undefined`, and auth.ts's own useSecureCookies ternary reads `request ? isSecureRequest(request)
// : true` -- FAIL CLOSED when there is no request to judge, UNCONDITIONALLY, regardless of the
// server's real scheme. So any route whose page.tsx calls auth() itself (first one: /ic, issue
// #310) looks for the `__Secure-`-prefixed cookie name no matter what -- @auth/core's own
// defaultCookies() (cookie.ts:59-60): `${useSecureCookies ? "__Secure-" : ""}authjs.session-token`.
// A cookie minted only under SESSION_COOKIE_NAME above silently reads as anonymous THERE even
// though it works fine at the proxy layer -- not a redirect (proxy already let the request through
// on role), just a null session inside the page. This constant, and sessionCookieHeader() below,
// exist so a proof exercising a Server Component's own auth() call (not just proxy.ts's role
// gate) can send BOTH names and let each layer find whichever one it is actually looking for.
export const SECURE_SESSION_COOKIE_NAME = "__Secure-authjs.session-token";

/** A `Cookie` header value carrying `role`/`actor` under BOTH the plain and `__Secure`-prefixed
 * session cookie names (see SECURE_SESSION_COOKIE_NAME's own comment for why both are needed).
 *
 * TWO SEPARATELY-ENCODED TOKENS, not one token string reused under two names -- found live, the
 * second half of the same discovery. `encode()`'s own `salt` parameter IS the cookie name (see
 * mintSessionToken's own comment: it is mixed into the key derivation), so a token minted with
 * `salt: SESSION_COOKIE_NAME` decodes to NULL under `SECURE_SESSION_COOKIE_NAME` -- a mismatch
 * fails closed rather than throwing, which made the first version of this function look plausible
 * right up until it was actually run against a real server. Each token below carries the
 * IDENTICAL payload, salted for the cookie name it will actually be decoded under. */
export async function sessionCookieHeader(role, actor) {
  const [plain, secure] = await Promise.all([
    mintTokenForCookieName(SESSION_COOKIE_NAME, role, actor),
    mintTokenForCookieName(SECURE_SESSION_COOKIE_NAME, role, actor),
  ]);
  return `${SESSION_COOKIE_NAME}=${plain}; ${SECURE_SESSION_COOKIE_NAME}=${secure}`;
}

/** The env a proof's `next start` must be spawned with for the minted cookie to validate. */
export function proofServerEnv() {
  return {
    ...process.env,
    AUTH_SECRET: PROOF_AUTH_SECRET,
    // AUTH_TRUST_HOST is NOT optional here, and the reason is a real operational trap worth
    // stating rather than a test detail. `next start` sets NODE_ENV=production, and @auth/core's
    // setEnvDefaults (lib/utils/env.js:40-44) defaults `trustHost` to
    // `!!(AUTH_URL ?? AUTH_TRUST_HOST ?? VERCEL ?? CF_PAGES ?? NODE_ENV !== "production")` --
    // so on a self-hosted production server with none of those set, trustHost is FALSE, every
    // session lookup comes back UntrustedHost, and next-auth's parseSessionResponse turns that
    // non-OK response into "no session" (deliberately fail-closed). The visible symptom is not an
    // error: it is a silent, permanent redirect to /login for everyone, including users who just
    // signed in successfully. Any real deployment of this app must set AUTH_URL or
    // AUTH_TRUST_HOST for the same reason -- see insight/web/README.md.
    AUTH_TRUST_HOST: "1",
    // issue #308 [E18.S3]. sessionEpoch.ts defaults INSIGHT_SESSIONS_PATH to the REPO's own
    // `.sdlc/insight-web-sessions.json`. Point it at a throwaway instead: these proofs must not
    // read (or, if a future proof signs out, write) the developer's real session store, and an
    // empty store is what makes `sessionEpoch: 0` in authenticatedContext() below provably the
    // live epoch rather than merely the usual one. Callers that need to inspect the file
    // themselves (prove-session-revocation-and-expiry.mjs) override this key after spreading.
    INSIGHT_SESSIONS_PATH: path.join(
      mkdtempSync(path.join(tmpdir(), "insight-proof-sessions-")),
      "sessions.json",
    ),
  };
}

// issue #309 [E19.S1], .sdlc/plans/309.md item (b) (independent plan-review gap): factored out of
// authenticatedContext() below so a role-carrying session can be minted WITHOUT a Playwright
// browser at all -- prove-role-forbidden-real-server.mjs drives a real booted `next start` with
// plain `fetch()` and a `Cookie` header instead, which is enough to prove a real 403 (no DOM
// assertion needed for that proof). Uses Auth.js's own encode(), same as authenticatedContext(),
// so the minted cookie is byte-identical in format to what a real sign-in issues and is decoded by
// the exact same next-auth code path `req.auth` runs through at the proxy layer -- this is what
// makes that proof a real test of "does `role` survive JWT-decode -> session-callback", not a stub.
//
// issue #310 [E19.S2]: `actor` became a SECOND parameter for one reason -- proving that one actor
// never receives another's data structurally requires minting two DIFFERENT actors in one proof
// run, and this function hardcoded `"proof-user"` for every session it had ever minted. Appended
// as an optional trailing parameter, defaulted to the old literal, so both pre-existing call sites
// (authenticatedContext() below, and prove-role-forbidden-real-server.mjs's fetchAs()) keep their
// exact previous behaviour untouched. `name` and `sub` are set to the SAME string deliberately:
// that is what a real sign-in produces (auth.ts's authorize() returns `{ id: username, name:
// username, role }`, and Auth.js copies id onto sub), so a proof cannot accidentally exercise a
// split identity the real login path can never issue.
/** Shared by mintSessionToken() and sessionCookieHeader() -- `salt` IS the cookie name in
 * Auth.js's JWT scheme (it is mixed into the key derivation, so a mismatch here decodes to null
 * (anonymous) rather than throwing), and issue #310's sessionCookieHeader() needs the identical
 * payload signed under TWO different salts (see that function's own comment). Factored out
 * rather than duplicated so the two never drift on what a "real sign-in token" contains. */
function mintTokenForCookieName(cookieName, role, actor) {
  return encode({
    salt: cookieName,
    secret: PROOF_AUTH_SECRET,
    // issue #308 [E18.S3]. `sessionEpoch` is NOT optional: auth.ts's jwt() callback compares it
    // against getEpoch(token.sub) on every read of an existing token and returns null (-> no
    // session -> 302 to /login) on any mismatch, deliberately including the `undefined` a token
    // minted without it carries. 0 is what a genuine sign-in stamps for an account that has never
    // been signed out, and proofServerEnv() above gives every proof server an empty session store
    // so 0 is exactly what getEpoch() will return here. It must be stamped HERE rather than only in
    // authenticatedContext(): #309's fetch-driven proofs mint through this function too, and a
    // token without it reads as anonymous -- a 302 to /login that a role proof would misread as
    // "the role was denied".
    token: { name: actor, sub: actor, role, sessionEpoch: 0 },
  });
}

export async function mintSessionToken(role = "admin", actor = "proof-user") {
  return mintTokenForCookieName(SESSION_COOKIE_NAME, role, actor);
}

/** A Playwright context carrying a valid Auth.js session for `baseUrl`. `role` defaults to
 * "admin" (matching mintSessionToken's own default, issue #311 [E19.S3]) so both pre-existing call
 * sites (prove-absence-primitives-render.mjs, prove-session-revocation-and-expiry.mjs) keep
 * compiling and behaving identically when the parameter is omitted -- prove-shell-responsive-
 * frame.mjs is the only caller that now passes a real role, to render nav per-role.
 *
 * issue #311 [E19.S3]: found live, not anticipated -- sets BOTH cookie names (SECURE_SESSION_
 * COOKIE_NAME's own comment above, and sessionCookieHeader()'s same reasoning), not just the plain
 * one. Before this story no Playwright proof's ASSERTIONS depended on what a Server Component's
 * own no-argument auth() call saw (proxy.ts's separate, request-carrying auth() call -- the one
 * that gates access -- always found the plain cookie fine); nav is the first thing a Playwright
 * proof checks that is COMPUTED from Shell.tsx's own `await auth()` result, and that call always
 * looks for the __Secure- prefixed name regardless of scheme (same quirk /ic's page.tsx already
 * hit, per #310). A context carrying only the plain cookie makes Shell.tsx see `session === null`
 * and render an EMPTY nav even on an otherwise-successful, non-redirected page load -- reproduced
 * live via `npm run prove:shell-responsive` before this fix, not merely reasoned about. */
export async function authenticatedContext(browser, baseUrl, role = "admin") {
  const actor = "proof-user";
  const [plain, secure] = await Promise.all([
    mintTokenForCookieName(SESSION_COOKIE_NAME, role, actor),
    mintTokenForCookieName(SECURE_SESSION_COOKIE_NAME, role, actor),
  ]);
  const context = await browser.newContext();
  await context.addCookies([
    { name: SESSION_COOKIE_NAME, value: plain, url: baseUrl },
    // Chrome enforces the "__Secure-" cookie-name PREFIX itself (RFC 6265bis), independent of and
    // in addition to auth.ts's own useSecureCookies logic: a cookie named "__Secure-*" must carry
    // `secure: true`, AND (found live, empirically, after `secure: true` alone still failed) CDP's
    // Storage.setCookies rejects a Secure cookie specified via `url` on a plain-http origin --
    // `domain`/`path` must be given explicitly instead of `url` for this one. Chrome still SENDS a
    // Secure cookie over this proof's plain http://127.0.0.1 origin once set: loopback addresses
    // are a "potentially trustworthy origin" per the Secure Contexts spec, the same carve-out
    // secure.ts's own LOOPBACK_HOSTS constant relies on for the opposite (server-side) half of this
    // story.
    { name: SECURE_SESSION_COOKIE_NAME, value: secure, domain: new URL(baseUrl).hostname, path: "/", secure: true },
  ]);
  return context;
}
