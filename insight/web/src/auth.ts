// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 1, 5, 6.
//
// Node runtime ONLY -- imports pythonBridge.ts, which shells out via child_process. This module
// must never be imported from proxy.ts for its Credentials-provider side; it IS imported for the
// `auth` wrapper (session validation only, Decision 6), which is safe because Auth.js's own JWT
// decode path never reaches the Credentials provider's authorize() -- and cheap enough to run on
// every request even though proxy.ts, unlike this file, runs on Node too (Decision 6: the split
// is about per-request cost, not runtime capability).
import { AsyncLocalStorage } from "node:async_hooks";

import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { redirect } from "next/navigation";

import { isSecureRequest } from "@/lib/auth/secure";
import { verifyCredentials, InvalidCredentialsError } from "@/lib/auth/pythonBridge";
import { bumpEpoch, getEpoch } from "@/lib/auth/sessionEpoch";

// issue #308 [E18.S3] code review finding 1 (BLOCKING). @auth/core's own signout action
// (node_modules/@auth/core/lib/actions/signout.js:14-27) wraps `events.signOut?.()` in a
// try/catch and SWALLOWS anything it throws -- logs via `logger.error(new SignOutError(e))`,
// then unconditionally clears the cookie and reports success. `bumpEpoch()` below CAN throw
// (e.g. an unwritable `insight-web-sessions.json`): when it does, the pre-fix code let that
// exception vanish into that swallow, so sign-out reported success while the epoch never moved
// and a previously-captured copy of the same cookie stayed valid forever -- fail-open, exactly
// what done-when 1 forbids.
//
// BOTH real sign-out entry points in this app converge on that SAME swallowing code (verified
// live against @auth/core@0.41.3, not assumed): the exported `signOut()` below (re-exported by
// Shell.tsx's Server Action form) is `next-auth/lib/actions.js`'s own `signOut(options, config)`,
// which builds a synthetic `Request` and calls `Auth(req, {...config, raw, skipCSRFCheck})`
// in-process; a raw `POST /api/auth/signout` (route.ts's exported `POST`, driven over real HTTP
// by Task 7's proof script) is `next-auth/index.js`'s `httpHandler`, which calls
// `Auth(reqWithEnvURL(req), config)` -- the SAME `@auth/core` entry point. Both then flow through
// `@auth/core/lib/index.js`'s `AuthInternal()` to its `case "signout": return await
// actions.signOut(cookies, sessionStore, options);` -- the identical `actions/signout.js` file
// whose try/catch swallows `events.signOut`'s exception either way. So there genuinely is ONE
// interception point that covers both paths (this is what the plan's Decision 1 already claimed
// for the HAPPY path; this fix relies on the exact same convergence for the FAILURE path).
//
// THE FIX: rather than trying to prevent @auth/core's own swallow (impossible -- it's
// node_modules) or reordering when bumpEpoch() runs (which would either duplicate it ahead of
// Auth.js's own CSRF gate on the POST path -- reopening a forced-logout-via-CSRF hole the
// swallow does NOT otherwise create -- or need its own redirect-handling reimplementation), this
// uses an AsyncLocalStorage-scoped flag that `events.signOut` below sets on failure, checked by
// BOTH wrapper entry points immediately after the (unmodified, still correctly CSRF-gated)
// underlying Auth.js flow completes. AsyncLocalStorage, not a plain module-level boolean: two
// concurrent sign-out requests in the same Node process must not stomp each other's flag, and
// this correctly isolates each `run()` call's own async chain, all within ONE bundle's module
// instance for a single request/action's own call graph -- this is NOT the cross-bundle problem
// Decision 2's own header warns about (that one needed state to survive from a WRITE in one
// request/bundle to a READ in a LATER, unrelated request possibly in another bundle; this only
// needs to survive within a single in-flight sign-out call).
//
// Bumping the epoch twice (this mechanism plus any other future events.signOut consumer) is
// harmless -- a monotonic increment, either bump revokes -- so no attempt is made to prevent
// redundant coverage; only silent, best-effort swallowing is unacceptable.
const signOutFailureFlag = new AsyncLocalStorage<{ failed: boolean }>();

/** Runs `fn()` with a fresh failure flag in scope; `events.signOut` below sets it if `bumpEpoch()`
 * throws. Returns both the underlying result and whether a revocation failure was observed --
 * exported so both sign-out entry points (this file's own `signOut()` wrapper, and
 * app/api/auth/[...nextauth]/route.ts's wrapped `POST`) can share the identical detection
 * mechanism instead of two independent, divergent copies. */
export async function withSignOutFailureTracking<T>(
  fn: () => Promise<T>,
): Promise<{ result: T; revocationFailed: boolean }> {
  const ctx = { failed: false };
  const result = await signOutFailureFlag.run(ctx, fn);
  return { result, revocationFailed: ctx.failed };
}

// Fires ONCE, on the first real request, when this deployment is configured such that every
// session will silently fail closed. @auth/core's setEnvDefaults (lib/utils/env.js:40-44) resolves
// trustHost to `!!(AUTH_URL ?? AUTH_TRUST_HOST ?? VERCEL ?? CF_PAGES ?? NODE_ENV !== "production")`,
// so a plain self-hosted `next start` with none of those set gets trustHost=false -> every session
// lookup returns UntrustedHost -> next-auth's parseSessionResponse maps that non-OK response to
// "no session" -> the proxy redirects EVERY user to /login forever, including one who just signed
// in with correct credentials. Nothing else surfaces it: no crash, no log, no failed check. This
// cost #307's CI two full red runs to diagnose from the outside, which is the argument for the
// warning -- README documentation alone leaves the operator with a working-looking app and no
// thread to pull. Deliberately a warning, not a throw: refusing to boot would turn a misconfigured
// deployment into a hard outage, and the fail-closed behaviour is itself safe.
//
// Checked per request rather than at module scope on purpose -- at module scope it would also fire
// during `next build`, where NODE_ENV is production too but no deployment is being configured, and
// a warning that cries wolf on every build is one nobody reads.
let warnedUntrustedHost = false;
function warnIfSessionsWillFailClosed() {
  if (warnedUntrustedHost) return;
  if (process.env.NODE_ENV !== "production") return;
  if (process.env.AUTH_URL || process.env.AUTH_TRUST_HOST || process.env.VERCEL || process.env.CF_PAGES) return;
  warnedUntrustedHost = true;
  console.warn(
    "[insight] AUTH_URL / AUTH_TRUST_HOST is unset in a production build: Auth.js will treat this " +
    "host as untrusted, every session will read as anonymous, and EVERY user will be redirected " +
    "to /login even after a correct sign-in. Set AUTH_URL (preferred) or AUTH_TRUST_HOST=1. " +
    "See insight/web/README.md.",
  );
}

/** The origin this deployment says it is served from, used ONLY when next-auth invokes the config
 * factory with no request (the in-process Server Action path). Returns undefined when nothing is
 * declared, which isSecureRequest() then treats as "no signal" and fails closed on. */
function deploymentOrigin(): string | undefined {
  return process.env.AUTH_URL ?? process.env.NEXTAUTH_URL;
}

// Fires ONCE when this deployment cannot name its own origin, because that is the one remaining
// configuration in which the write half and the read half of the session cookie can still pick
// DIFFERENT cookie names -- see the long comment on useSecureCookies below for the mechanism and
// for what it looked like in practice (a correct password, a real cookie, and an instant silent
// bounce back to /login). Loud on purpose: the failure it describes produces no error of its own,
// so a warning here is the only thing standing between an operator and an app that rejects every
// valid login for reasons nothing on the box will tell them.
//
// Distinct from warnIfSessionsWillFailClosed() above even though both name AUTH_URL: that one
// fires only in a production build and is about trustHost; this one fires in ANY environment,
// because `npm run dev` on plain localhost with no AUTH_URL hits this exact split too.
//
// A warning, not a throw, for the same reason: refusing to boot turns a misconfiguration into an
// outage, and there ARE deployments where the two halves agree anyway (a public hostname, where
// both branches independently answer `Secure`).
let warnedNoDeclaredOrigin = false;
function warnIfSecureCookieNameWillNotMatch() {
  if (warnedNoDeclaredOrigin) return;
  if (deploymentOrigin()) return;
  warnedNoDeclaredOrigin = true;
  console.warn(
    "[insight] AUTH_URL is unset, so sign-in through the login form cannot know whether this " +
    "deployment is served over TLS. On a loopback origin the session cookie will be WRITTEN as " +
    "`__Secure-authjs.session-token` and READ as `authjs.session-token`, and every correct " +
    "sign-in will bounce straight back to /login with no error. Set AUTH_URL to the exact origin " +
    "users reach (e.g. http://localhost:3000). See insight/web/README.md.",
  );
}

export const { handlers, auth, signIn, signOut: nextAuthSignOut } = NextAuth((request) => {
  warnIfSessionsWillFailClosed();
  warnIfSecureCookieNameWillNotMatch();
  return {
  // Decision 5: computed PER REQUEST via next-auth's own "lazy initialization" config form
  // (documented in next-auth/index.d.ts) -- verified against @auth/core@0.41.3's own
  // defaultCookies() (src/lib/utils/cookie.ts:59-70): httpOnly/sameSite=lax stay unconditional,
  // only `secure` reads this.
  //
  // THE NO-REQUEST BRANCH USED TO BE A BARE `: true`, AND THAT MADE BROWSER SIGN-IN IMPOSSIBLE.
  // Measured live against this build, not reasoned about: `useSecureCookies` does not only set the
  // `Secure` ATTRIBUTE, it selects the cookie's NAME -- defaultCookies() returns
  // `__Secure-authjs.session-token` when true and `authjs.session-token` when false -- and
  // @auth/core salts the session JWT's encryption with that name (so the two are not even
  // interchangeable after the fact: renaming a captured cookie still fails to decrypt).
  //
  // next-auth invokes this factory WITHOUT a request on the in-process Server Action path, which
  // is exactly how src/app/login/actions.ts signs a browser in. So the two halves disagreed:
  //
  //   write (Server Action, no request) -> `: true`  -> sets __Secure-authjs.session-token
  //   read  (any later HTTP request)    -> isSecureRequest(request) on loopback -> false
  //                                     -> looks for authjs.session-token, which does not exist
  //
  // Result: a correct username and password produced a 303 to `/`, a real session cookie, and
  // then an immediate bounce back to /login with no session, no error, and nothing in the log.
  // Signing in over the /api/auth/callback/credentials route worked the whole time (both halves
  // have a request there), which is why every API-level check passed while the actual product was
  // unusable.
  //
  // The fix keeps the security property intact and only makes the un-judgeable case judgeable:
  // fall back to the deployment's OWN declared origin and run it through the identical
  // isSecureRequest() rules, so the no-request branch reaches the same verdict the request-bearing
  // branch will reach for that same deployment. An https:// AUTH_URL still yields `Secure` and the
  // `__Secure-` prefix; a real hostname over plaintext still yields `Secure`; only loopback --
  // where the request-bearing branch already says false -- now agrees. With AUTH_URL unset there
  // is still no signal at all and isSecureRequest still fails CLOSED to true, unchanged; that
  // deployment is the one warnIfSecureCookieNameWillNotMatch() below refuses to let pass silently.
  useSecureCookies: isSecureRequest(
    request ?? { headers: new Headers(), url: deploymentOrigin() },
  ),
  session: {
    strategy: "jwt", // required for the Credentials provider (Auth.js's own constraint)
    // issue #308 [E18.S3], .sdlc/plans/308.md Decision 9. Auth.js's own 30-day default
    // (@auth/core/src/lib/init.ts:71) was silently live -- no product spec names a number
    // (Open Questions #1), so these are the plan's own reasonable, explicit defaults: 8 hours,
    // refreshed (a new token issued, resetting its own expiry) on any request made more than 2
    // hours after the last issuance.
    //
    // maxAge IS NOT AN ABSOLUTE CEILING (security review of #308, correcting an earlier version
    // of this comment and of insight/web/README.md that both claimed it was). Verified live
    // against @auth/core@0.41.3's own session() action (node_modules/@auth/core/src/lib/actions/
    // session.ts:56-79): for the JWT strategy (this app's own, per `strategy` above), EVERY call
    // -- unconditionally, with no `updateAge`-gated check at all, unlike the database-strategy
    // branch immediately below it in that same file, which DOES check `sessionUpdateAge` before
    // bothering to write -- re-signs the token with `newExpires = fromDate(sessionMaxAge)` and
    // pushes a fresh cookie with that new expiry. `proxy.ts` calls `auth()` (which reaches this
    // same code path) on every guarded request, so a stolen, still-valid cookie that gets
    // replayed even once per `maxAge` window keeps re-signing itself indefinitely and NEVER
    // expires on its own. `maxAge` is therefore an INACTIVITY timeout (the cookie expires only if
    // it goes unused for a whole `maxAge` window), not a hard ceiling on a single token's total
    // lifetime -- `updateAge` is not "how often the ceiling resets," it is irrelevant to the JWT
    // branch's own refresh decision (the JWT branch refreshes on every check, not just after
    // `updateAge` has elapsed; only the database-strategy branch this app does not use reads
    // `updateAge` at all). A TRUE absolute ceiling would need a separate check -- e.g. an
    // `issuedAt` timestamp stamped onto the token at sign-in (mirroring how `sessionEpoch`
    // already stamps `token.sessionEpoch` in `callbacks.jwt` below) and compared against `_now()`
    // on every read, rejecting the token once `now - issuedAt > maxAge` regardless of activity --
    // which this app does not implement today. See insight/web/README.md for the full reasoning.
    maxAge: 60 * 60 * 8, // 8 hours
    updateAge: 60 * 60 * 2, // 2 hours
  },
  pages: { signIn: "/login" },
  providers: [
    Credentials({
      credentials: {
        username: { label: "Username" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const username = credentials?.username;
        const password = credentials?.password;
        if (typeof username !== "string" || typeof password !== "string") return null;
        try {
          const { role } = await verifyCredentials(username, password);
          return { id: username, name: username, role };
        } catch (e) {
          if (e instanceof InvalidCredentialsError) return null;
          // CredentialCheckUnavailableError (KDF/store/bridge broken): rethrow so Auth.js
          // surfaces a generic configuration error, NOT "invalid credentials" -- Decision 1's
          // whole point. A silently-returned null here would read to every user as "wrong
          // password" while the real cause is an operator problem.
          throw e;
        }
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user?.role) token.role = user.role;
      // issue #308 [E18.S3], .sdlc/plans/308.md Decision 1/2. `user` is truthy on EXACTLY the
      // request that just called authorize() (sign-in) -- never on any later read of this same
      // token. Stamp the epoch that is live for this account RIGHT NOW; every subsequent call
      // below re-reads the epoch fresh (sessionEpoch.ts's own header: no cache, by design) and
      // compares against what got stamped here.
      if (typeof user?.id === "string") {
        token.sessionEpoch = getEpoch(user.id);
        return token;
      }
      // Decision 1: this is the check half of revocation, run on EVERY other invocation of this
      // callback -- i.e. every proxy-guarded request that already carries a session (verified
      // live against @auth/core@0.41.3's own lib/actions/session.ts:50, cited in Decision 1).
      // If `events.signOut` below bumped the on-disk epoch since this particular token was
      // issued, the comparison fails and returning `null` is what makes next-auth treat the
      // request as having no session at all (@auth/core's session.ts:83 clears the cookie) --
      // exactly the refusal done-when 1's replay test asserts.
      //
      // issue #308 [E18.S3] code review finding 3: `token.sub` not being a string used to fall
      // straight through to `return token` below -- SKIPPING the epoch check entirely rather
      // than failing closed, in a function whose entire job is to fail closed. Not independently
      // exploitable without the app secret (a forged token still needs a valid signature), but a
      // fail-open DEFAULT has no place here: if this callback cannot verify the epoch at all, it
      // must refuse, not silently trust.
      if (typeof token.sub !== "string") {
        return null;
      }
      if (token.sessionEpoch !== getEpoch(token.sub)) {
        return null;
      }
      return token;
    },
    async session({ session, token }) {
      if (typeof token.role === "string") session.user.role = token.role;
      return session;
    },
  },
  events: {
    // issue #308 [E18.S3], .sdlc/plans/308.md Decision 1. Verified against
    // @auth/core/src/lib/actions/signout.ts:22-30: for a JWT-strategy config (this app's own,
    // per `session.strategy` above), sign-out ALWAYS decodes the existing session token and calls
    // `events.signOut?.({ token })` before clearing the cookie -- true whether triggered by the
    // exported `signOut()` server action (Task 6) or a raw POST to Auth.js's own built-in
    // `/api/auth/signout` route, which is what lets Task 7's proof hit that real endpoint with
    // plain `fetch()`, no bespoke Route Handler needed.
    //
    // The plan's own literal text (`async signOut({ token }) { ... }`) does not compile: the
    // message parameter's type is a UNION (`{ session: ... } | { token: ... }`, the two shapes
    // for a database vs. a JWT strategy), and destructuring a property absent from one union
    // member is a TS2339 under strict mode -- found live during implementation, the same class
    // of drift app/api/auth/[...nextauth]/route.ts's own header already documents for this
    // plan's literal snippets. Narrowed with `"token" in message` instead; this app only ever
    // configures the JWT strategy, so the `session` branch is unreachable in practice but the
    // type still has to be handled.
    async signOut(message) {
      if ("token" in message && typeof message.token?.sub === "string") {
        try {
          await bumpEpoch(message.token.sub);
        } catch (e) {
          // issue #308 [E18.S3] code review finding 1. @auth/core's own caller (signout.js)
          // swallows whatever this throws -- see this file's header comment above for the full
          // evidence trail. Set the shared flag BEFORE rethrowing so the wrapper below (which
          // wraps the ENTIRE call that leads here, on both sign-out paths) can detect the failure
          // once control returns to it, even though @auth/core itself will still swallow this
          // exact throw. Still rethrown (not just flagged) so @auth/core's own
          // `logger.error(new SignOutError(e))` fires too -- free, redundant server-side logging
          // of the underlying cause, on top of the flag being the mechanism that actually matters.
          const ctx = signOutFailureFlag.getStore();
          if (ctx) ctx.failed = true;
          throw e;
        }
      }
    },
  },
  };
});

/** issue #308 [E18.S3] code review finding 1. Replaces next-auth's own `signOut` export (renamed
 * `nextAuthSignOut` above) as the ONE that `Shell.tsx`'s Server Action form actually calls.
 * Forces `redirect: false` on the underlying call so this function keeps control after it
 * returns -- next-auth's own `signOut()` calls `next/navigation`'s `redirect()` itself when its
 * default `redirect: true` is left alone, which THROWS (Next's own control-flow mechanism) and
 * would skip the failure check below entirely; this function performs that same default redirect
 * itself, but only after checking `revocationFailed` first. See this file's header comment for
 * why `withSignOutFailureTracking` (wrapping the WHOLE underlying call, not a pre-emptive bump)
 * is what keeps this path's existing CSRF handling untouched -- next-auth's own `signOut()`
 * passes `skipCSRFCheck` for this in-process path already, unrelated to this fix. */
export async function signOut(options?: { redirectTo?: string; redirect?: boolean }): Promise<void> {
  const { result, revocationFailed } = await withSignOutFailureTracking(() =>
    nextAuthSignOut({ ...options, redirect: false }),
  );
  if (revocationFailed) {
    // Deliberately thrown, not a quiet return -- issue #308 [E18.S3] finding 1's central
    // constraint: "a sign-out that did not actually revoke must NOT present as a successful
    // sign-out." Shell.tsx's inline Server Action does not catch this, so it surfaces to Next.js
    // as a genuine Server Action error (not a redirect, not a 2xx) -- the opposite of the
    // pre-fix behavior, where the same underlying failure was invisible. The session cookie may
    // already have been cleared for THIS browser by next-auth's own unconditional cookie-apply
    // step inside `signOut()` above (that happens before the redirect option is even consulted) --
    // that is fine and does not violate the constraint: the constraint is about a REPLAYED,
    // separately-held copy of the old cookie, whose validity is governed entirely by the
    // persisted epoch, which this failure means never moved.
    throw new Error(
      "insight: sign-out could not revoke the session server-side (the epoch write failed -- " +
      "see server logs for the underlying error). Refusing to report success.",
    );
  }
  if (options?.redirect ?? true) {
    const target =
      options?.redirectTo ?? (result as { redirect?: string } | undefined)?.redirect ?? "/";
    redirect(target);
  }
}
