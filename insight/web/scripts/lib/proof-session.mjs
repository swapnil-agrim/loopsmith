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
  };
}

/** A Playwright context carrying a valid Auth.js session for `baseUrl`. */
export async function authenticatedContext(browser, baseUrl) {
  const token = await encode({
    // salt IS the cookie name in Auth.js's JWT scheme -- it is mixed into the key derivation, so a
    // mismatch here decodes to null (anonymous) rather than throwing.
    salt: SESSION_COOKIE_NAME,
    secret: PROOF_AUTH_SECRET,
    token: { name: "proof-user", sub: "proof-user", role: "admin" },
  });
  const context = await browser.newContext();
  await context.addCookies([{ name: SESSION_COOKIE_NAME, value: token, url: baseUrl }]);
  return context;
}
