// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 2/3/7 (public-vs-private, the original shape).
// issue #309 [E19.S1], .sdlc/plans/309.md Decisions 1-4 (the role matrix, the "forbid" verdict).
//
// THE single source of truth for "which app routes are public, which need a role, and which
// role." Imported by BOTH proxy.ts (to decide a real request) and this repo's route-privacy proof
// scripts (to decide what every route SHOULD do) -- importing the SAME decide() from every call
// site is what stops enforcement and its proof from drifting apart (issue #309 done-when 1: "one
// declarative place").
//
// Deliberately explicit lists everywhere, not a regex or a wildcard: every entry here is either a
// hole in "protected by default" (PUBLIC_*), a shared grant every authenticated session gets
// regardless of role (SHARED_AUTHENTICATED_ROUTES), or a role-specific grant (ROLE_ROUTES) -- all
// three stay short enough to read in full. Static assets (_next/*, favicon.ico, fonts/*) are
// excluded a different way entirely -- see proxy.ts's own matcher (#307 Decision 7) -- because
// they never reach decide() at all.

/** Exact-match public routes: the login page itself. Without this exemption, an unauthenticated
 * visitor could never reach the page that lets them stop being unauthenticated -- the redirect
 * loop dossier risk 4 named. */
export const PUBLIC_EXACT_ROUTES: readonly string[] = ["/login"];

/** Prefix-match public routes: Auth.js mounts several sub-paths under /api/auth (session, csrf,
 * callback/credentials, signin, signout, providers) -- all of Auth.js's own machinery, which must
 * be reachable before any session exists by construction. */
export const PUBLIC_PREFIX_ROUTES: readonly string[] = ["/api/auth"];

// issue #309 Decision 4: ONE matching rule, shared by isPublicRoute(), the shared-authenticated
// check, and the per-role check below -- previously isPublicRoute() had its own private copy of
// this logic; extracted here so all three callers can never drift apart on what "prefix match"
// means. `exact === pathname` OR `pathname` starts with `prefix + "/"` -- a prefix of "/" would
// therefore become `startsWith("//")`, which is why SHARED_AUTHENTICATED_ROUTES below uses
// `exact` for "/", never `prefix` (Decision 4's own footgun note).
function matchesRoutes(pathname: string, exact: readonly string[], prefix: readonly string[]): boolean {
  if (exact.includes(pathname)) return true;
  return prefix.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export function isPublicRoute(pathname: string): boolean {
  return matchesRoutes(pathname, PUBLIC_EXACT_ROUTES, PUBLIC_PREFIX_ROUTES);
}

// issue #309 [E19.S1] Decision 2: the canonical role vocabulary -- the closed union that did not
// exist ANYWHERE in the stack before this story (insight/__main__.py's --role stays a free-form,
// unvalidated string by design; store.py stores it verbatim). Deliberately narrow: adding a fifth
// role is a deliberate code change here, not a config toggle -- Decision 7's "small enough to read
// in full" philosophy, applied to roles instead of routes.
export type Role = "manager" | "leadership" | "ic" | "cross-functional";
const KNOWN_ROLES: readonly Role[] = ["manager", "leadership", "ic", "cross-functional"];

/** issue #309 Decision 2: a session's `role` is `string | undefined` (types/next-auth.d.ts,
 * unnarrowed, unchanged by this story) because the account that produced it may have been created
 * with any free-form string, or no role at all. This is the ONE place that decides what happens
 * then -- not a crash, a denial: everything that isn't a recognized Role narrows to `false` here,
 * which is what makes decide()'s fall-through default `forbid`, never `allow`. */
export function isKnownRole(role: string | undefined): role is Role {
  return typeof role === "string" && (KNOWN_ROLES as readonly string[]).includes(role);
}

interface RoutePattern {
  readonly exact?: readonly string[];
  readonly prefix?: readonly string[];
}

function matchesPattern(pathname: string, pattern: RoutePattern): boolean {
  return matchesRoutes(pathname, pattern.exact ?? [], pattern.prefix ?? []);
}

// issue #309 Decision 3: routes any AUTHENTICATED session may reach regardless of role -- the
// shared shell ("/", the only route src/lib/nav.ts links today) and the dev-only fixture route
// (/dev/absence-states, #304/#305) CI's browser proofs authenticate against via
// scripts/lib/proof-session.mjs's role:"admin" fixture, which is not and will never be one of the
// four roles above. Kept OUT of ROLE_ROUTES on purpose: these are not role-specific dashboards, so
// listing them under all four roles would be the same two strings copied four times for nothing,
// and would blur "the role matrix" with "routes every role already shares." Still a SMALL,
// EXPLICIT list -- a route lands here only by a deliberate edit, same discipline as every other
// list in this file. /dev/absence-states's OWN env gate (INSIGHT_DEV_ROUTES, checked inside the
// page itself) still removes it from real production, unrelated to and unchanged by this list.
const SHARED_AUTHENTICATED_ROUTES: RoutePattern = { exact: ["/", "/dev/absence-states"] };

// issue #309 [E19.S1] done-when 1: THE role -> route matrix, the ONE declarative place. E20's
// dashboard pages do not exist yet -- entries here are route PATTERNS (prefix-matched, so
// "/manager" already covers a future "/manager/team-a"), so the matrix already governs them the
// moment they land, the same "not sampling" property #307 done-when 3 established for the
// public/private axis. String literals match src/lib/nav.ts's own placeholder labels
// (Manager/Leadership/IC/Cross-functional) as the best available guess at E20's eventual paths --
// see .sdlc/plans/309.md Decision 4's own note if E20 lands different paths later.
export const ROLE_ROUTES: Readonly<Record<Role, RoutePattern>> = {
  manager: { prefix: ["/manager"] },
  leadership: { prefix: ["/leadership"] },
  ic: { prefix: ["/ic"] },
  "cross-functional": { prefix: ["/cross-functional"] },
};

function isRouteAllowedForRole(pathname: string, role: string | undefined): boolean {
  if (!isKnownRole(role)) return false;
  return matchesPattern(pathname, ROLE_ROUTES[role]);
}

export type RouteDecision = "allow" | "redirect" | "forbid";

/** Pure: (pathname, hasSession, role) -> allow/redirect/forbid. Still NO Next.js import anywhere
 * in this file (#307 Decision 2's own reasoning, extended) -- callable from a plain Node script,
 * no browser, no next/server, no build. `role` is OPTIONAL (`role?: string`) purely so existing
 * 2-argument call sites keep compiling -- every one of them already short-circuits on
 * `hasSession === false` before role would ever matter (issue #309 Decision 1).
 *
 * Fall-through-deny now has TWO layers, both early-return shaped, never else-if (#307 Decision
 * 2 / #309 Decision 1): first the session gate (no session -> redirect, role never even
 * inspected), then the role gate (no session bypass via SHARED_AUTHENTICATED_ROUTES, and no
 * known role whose matrix entry lists this route -> forbid). The LAST line is always
 * `return "forbid"` -- a route with no explicit entry anywhere above is denied, never allowed by
 * omission (issue #309 done-when 3). */
export function decide(pathname: string, hasSession: boolean, role?: string): RouteDecision {
  if (isPublicRoute(pathname)) return "allow";
  if (!hasSession) return "redirect";
  if (matchesPattern(pathname, SHARED_AUTHENTICATED_ROUTES)) return "allow";
  if (isRouteAllowedForRole(pathname, role)) return "allow";
  return "forbid";
}
