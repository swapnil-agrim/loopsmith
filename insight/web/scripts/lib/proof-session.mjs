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
export async function mintSessionToken(role = "admin") {
  return encode({
    // salt IS the cookie name in Auth.js's JWT scheme -- it is mixed into the key derivation, so a
    // mismatch here decodes to null (anonymous) rather than throwing.
    salt: SESSION_COOKIE_NAME,
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
    token: { name: "proof-user", sub: "proof-user", role, sessionEpoch: 0 },
  });
}

/** A Playwright context carrying a valid Auth.js session for `baseUrl`. */
export async function authenticatedContext(browser, baseUrl) {
  const token = await mintSessionToken("admin");
  const context = await browser.newContext();
  await context.addCookies([{ name: SESSION_COOKIE_NAME, value: token, url: baseUrl }]);
  return context;
}
