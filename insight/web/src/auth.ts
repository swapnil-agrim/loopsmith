// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 1, 5, 6.
//
// Node runtime ONLY -- imports pythonBridge.ts, which shells out via child_process. This module
// must never be imported from proxy.ts for its Credentials-provider side; it IS imported for the
// `auth` wrapper (session validation only, Decision 6), which is safe because Auth.js's own JWT
// decode path never reaches the Credentials provider's authorize() -- and cheap enough to run on
// every request even though proxy.ts, unlike this file, runs on Node too (Decision 6: the split
// is about per-request cost, not runtime capability).
import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";

import { isSecureRequest } from "@/lib/auth/secure";
import { verifyCredentials, InvalidCredentialsError } from "@/lib/auth/pythonBridge";

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

export const { handlers, auth, signIn, signOut } = NextAuth((request) => {
  warnIfSessionsWillFailClosed();
  return {
  // Decision 5: computed PER REQUEST via next-auth's own "lazy initialization" config form
  // (documented in next-auth/index.d.ts) -- verified against @auth/core@0.41.3's own
  // defaultCookies() (src/lib/utils/cookie.ts:59-70): httpOnly/sameSite=lax stay unconditional,
  // only `secure` reads this.
  // `: true` when there is no request to judge -- fail CLOSED, same rule as isSecureRequest()'s own
  // fallbacks (security review of #307): an un-judgeable case must never be the one that silently
  // drops `Secure`.
  useSecureCookies: request ? isSecureRequest(request) : true,
  session: { strategy: "jwt" }, // required for the Credentials provider (Auth.js's own constraint)
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
      return token;
    },
    async session({ session, token }) {
      if (typeof token.role === "string") session.user.role = token.role;
      return session;
    },
  },
  };
});
