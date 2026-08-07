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

export const { handlers, auth, signIn, signOut } = NextAuth((request) => ({
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
}));
