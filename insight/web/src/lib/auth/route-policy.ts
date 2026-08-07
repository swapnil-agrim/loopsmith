// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 2/3/7.
//
// THE single source of truth for "which app routes are public." Imported by BOTH proxy.ts
// (to decide whether to redirect a real request) and
// scripts/prove-every-route-is-private-by-default.mjs (to decide what every DISCOVERED route
// should do) -- importing the SAME constants and the SAME decide() from both call sites is what
// stops the two from silently drifting apart (a hardcoded second list in the proof would happily
// pass while the proxy protects nothing).
//
// Deliberately two SMALL, EXPLICIT lists, not a regex: every entry here is a hole in "protected
// by default," so this stays short enough to read in full rather than clever. Static assets
// (_next/*, favicon.ico, fonts/*) are excluded a different way entirely -- see proxy.ts's
// own matcher, Decision 7 -- because they are not app routes this filesystem walk discovers.

/** Exact-match public routes: the login page itself. Without this exemption, an unauthenticated
 * visitor could never reach the page that lets them stop being unauthenticated -- the redirect
 * loop dossier risk 4 named. */
export const PUBLIC_EXACT_ROUTES: readonly string[] = ["/login"];

/** Prefix-match public routes: Auth.js mounts several sub-paths under /api/auth (session, csrf,
 * callback/credentials, signin, signout, providers) -- all of Auth.js's own machinery, which must
 * be reachable before any session exists by construction. */
export const PUBLIC_PREFIX_ROUTES: readonly string[] = ["/api/auth"];

export function isPublicRoute(pathname: string): boolean {
  if (PUBLIC_EXACT_ROUTES.includes(pathname)) return true;
  return PUBLIC_PREFIX_ROUTES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export type RouteDecision = "allow" | "redirect";

/** Pure: (pathname, hasSession) -> allow/redirect. Deliberately has NO Next.js import anywhere in
 * this file -- that is what makes it callable from a plain Node script with no browser, no
 * next/server, no build (dossier risk 5 / insight/verify_web.py's "no browser in the local gate"
 * constraint). Written as two early-returns falling through to a single `redirect` at the end --
 * the fall-through-deny shape Decision 2 calls for, not an if/else-if chain a later edit could
 * silently invert. */
export function decide(pathname: string, hasSession: boolean): RouteDecision {
  if (isPublicRoute(pathname)) return "allow";
  if (hasSession) return "allow";
  return "redirect";
}
