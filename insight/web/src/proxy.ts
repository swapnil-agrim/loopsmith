// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 2, 6, 7 (proxy.ts convention, catch-all matcher).
// issue #309 [E19.S1], .sdlc/plans/309.md Decisions 1, 5 (role-aware decide(), the forbid response).
// NAMED proxy.ts, not middleware.ts: Next 16's `middleware.ts` convention is deprecated in favor
// of this one, and isProxyFile(page) forces the Node.js runtime for this file unconditionally
// (verified against next@16.3.0's own dist -- see #307 Decision 6). DO NOT add an "export const
// runtime" of ANY kind to this file (E1031 build error), and a stray sibling src/middleware.ts
// must not exist alongside it (E900 build error) -- both unchanged from #307.
//
// This file still does not call pythonBridge.ts, on cost grounds: it runs against EVERY request
// the matcher below matches, and a login-grade credential check has no business running on every
// navigation. It only validates an EXISTING session via Auth.js's own `auth()` wrapper (a JWT
// decode, cheap on every request) and now also reads that session's `role` -- no new cost class,
// the role is already sitting on the decoded token (#307 Decision 1), never a second lookup.
import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { decide } from "@/lib/auth/route-policy";

const handler = auth((req) => {
  const decision = decide(req.nextUrl.pathname, !!req.auth, req.auth?.user?.role);
  if (decision === "redirect") {
    const loginUrl = new URL("/login", req.nextUrl);
    loginUrl.searchParams.set("callbackUrl", req.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }
  if (decision === "forbid") {
    // issue #309 [E19.S1] Decision 5: the ENTIRE response body, fixed and minimal -- no route
    // name, no role name, no rendered app shell. Constructed and returned HERE, before Next.js
    // resolves the matched route to a Server Component at all -- there is no code path from this
    // branch to any page's data-fetching, so "renders none of the underlying data" (done-when 2)
    // holds by construction, not by review. See scripts/prove-role-route-matrix.mjs Part B for the
    // whole-body assertion this depends on staying true.
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
  return NextResponse.next();
});

// `export default auth(...)` -- the shape next-auth's own docs show -- does NOT work here, and the
// reason is specific to Decision 5. auth.ts passes NextAuth a FUNCTION (the "lazy initialization"
// config form) so useSecureCookies can be computed per request. Verified in
// next-auth/lib/index.js's initAuth(): its `typeof config === "function"` branch returns
// `async (...args) => ...`, so `auth(cb)` RESOLVES TO the handler instead of BEING it, while the
// static-object branch just below returns a plain `(...args) => ...` that is one. Next 16's proxy
// loader (next/dist/build/templates/middleware.js) then does `typeof handlerUserland !== "function"`
// on the default export and throws "The Proxy file "/proxy" must export a function named `proxy` or
// a default function" for EVERY request -- the whole app 500s.
//
// `next build` does not catch this: it is a runtime value shape, not a static one, so typecheck,
// lint and build all pass green (next-auth's own .d.ts overloads declare the wrapper form as
// returning the handler, so the types actively assert the opposite of what runs). Only a booted
// server sees it, which is why CI's browser proofs caught it and the local gate could not.
//
// Awaiting is correct under BOTH config forms -- `await` on a non-promise is a no-op -- so this
// keeps working if auth.ts ever moves back to a static config object.
export default async function proxy(...args: Parameters<Awaited<typeof handler>>) {
  const fn = await handler;
  return fn(...args);
}

// Decision 2: a catch-all, not an include-list -- this proxy runs for every app route by
// construction. Excludes only genuine static assets, never an app route (that distinction lives
// in route-policy.ts's allowlist instead, per Decision 7): Next.js's own internals
// (_next/static, _next/image), favicon.ico, and this repo's real self-hosted fonts under
// public/fonts/ (insight/web/public/fonts/*.woff2, E17.S2) -- without excluding the last one, an
// unauthenticated font request 404s into a redirect and the login page renders in system fonts
// instead of the instrument design language it is supposed to demonstrate (done-when 1).
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon\\.ico|fonts/).*)"],
};
