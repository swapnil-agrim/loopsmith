// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 2, 6, 7. NAMED proxy.ts, not middleware.ts:
// Next 16's `middleware.ts` convention is deprecated (a build-time warnOnce says so) in favor of
// this one. Verified against next@16.3.0's own dist, not docs: `isProxyFile(page)` alone forces
// the Node.js runtime for this file, UNCONDITIONALLY (dist/build/index.js:1605,
// `hasNodeMiddleware = true` regardless of any exported `runtime`) -- so, unlike the earlier
// draft of this file (which was wrong), child_process genuinely IS available here.
//
// This file still does not call pythonBridge.ts, on cost grounds, not capability: this proxy
// runs against EVERY request the matcher below matches, and a login-grade credential check (a
// process spawn plus a ~23ms argon2id KDF) has no business running on every navigation. That
// check stays confined to auth.ts's authorize() callback, reached only through
// app/api/auth/[...nextauth]/route.ts's POST -- i.e. only on an actual sign-in attempt. This file
// only validates an EXISTING session via Auth.js's own `auth()` wrapper (next-auth's documented
// pattern -- see next-auth/index.d.ts's own example: "export default auth((req) => { ...
// req.auth ... })"), which decodes the session JWT with Web Crypto -- cheap enough to run on
// every matched request regardless of which runtime it's on.
//
// DO NOT add an "export const runtime" of ANY kind to this file. Verified against
// next@16.3.0's dist/build/analysis/get-page-static-info.js:606-621: a proxy file exporting any
// `runtime` value is not silently ignored, it throws in `next build` -- error code E1031,
// "Route segment config is not allowed in Proxy file ... Proxy always runs on Node.js runtime."
// See this task's own guard additions below (Step 2), which assert this file exports no
// `runtime` and that a stray sibling `src/middleware.ts` (E900: having both is also an
// unconditional build error) does not exist.
import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { decide } from "@/lib/auth/route-policy";

export default auth((req) => {
  const decision = decide(req.nextUrl.pathname, !!req.auth);
  if (decision === "redirect") {
    const loginUrl = new URL("/login", req.nextUrl);
    loginUrl.searchParams.set("callbackUrl", req.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
});

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
