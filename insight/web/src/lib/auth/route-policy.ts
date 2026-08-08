// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decisions 2/3/7 (public-vs-private, the original shape).
// issue #309 [E19.S1], .sdlc/plans/309.md Decisions 1-4 (the role matrix, the "forbid" verdict).
// issue #311 [E19.S3], .sdlc/plans/311.md Decision 1 (navLabel/implemented added to every entry so
// nav.ts and the proofs read reachability off the SAME table decide() reads -- no second copy).
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

export interface RoutePattern {
  readonly exact?: readonly string[];
  readonly prefix?: readonly string[];
}

// issue #311 [E19.S3] Decision 1: nav-only metadata, riding along on the SAME table entries
// decide() reads, instead of a second `Record<Role, ...>` that could drift on shape. `implemented`
// is read ONLY by src/lib/nav.ts's navItemsFor() -- decide() and proxy.ts never look at it, so
// enforcement is byte-for-byte unchanged by this story; a nav-only flag that gated enforcement
// would be a security regression.
interface RouteMeta {
  /** Nav label. Absent -> this entry never appears in nav, for any role, ever -- independent of
   *  `implemented`. The one entry this is used for today: /dev/absence-states (shared + implemented,
   *  but env-gated out of every production build, exactly the reason nav.ts's OLD header comment
   *  already recorded before this story). */
  readonly navLabel?: string;
  /** True iff a real page/route handler exists on disk for this pattern TODAY. Independent of
   *  policy: decide() governs a reserved-but-unbuilt route the moment it is listed here regardless
   *  of this flag (this file's own ROLE_ROUTES comment, unchanged by this story) -- ONLY nav
   *  reachability consults `implemented`. An E20 story flips one `false` to `true` in place, in
   *  this same file, when its page lands -- no second file to remember to edit. */
  readonly implemented: boolean;
}

function matchesPattern(pathname: string, pattern: RoutePattern): boolean {
  return matchesRoutes(pathname, pattern.exact ?? [], pattern.prefix ?? []);
}

/** One representative path per table entry -- `exact[0]` if present, else `prefix[0]`. For a
 *  prefix-matched pattern this is what "the full cross product" means (issue #311's own resolution):
 *  one path per TABLE ENTRY, not an enumeration of infinite children. Exported so nav.ts and every
 *  proof read the identical derivation, never retype a route string -- the exact defect issue #311
 *  fixes (prove-role-route-matrix.mjs's old hand-typed `OWN_ROUTE` map). */
export function representativePath(pattern: RoutePattern): string {
  const [first] = pattern.exact ?? pattern.prefix ?? [];
  if (!first) throw new Error("route pattern has neither exact nor prefix entries");
  return first;
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
//
// issue #311 [E19.S3]: exported (was module-private) so nav.ts and the rewritten proofs can read
// it, and widened from one RoutePattern with a two-element `exact` array to an array of
// one-route-each entries so each route can carry its own navLabel/implemented independently --
// "/" is a real, linked nav item; "/dev/absence-states" is implemented but has no navLabel, so it
// never appears in nav (env-gated out of production regardless). decide()'s shared-route check
// below becomes `.some(...)` over this array -- the same matched set as before, `"/"` and
// `"/dev/absence-states"` both still `allow`.
export const SHARED_AUTHENTICATED_ROUTES: readonly (RoutePattern & RouteMeta)[] = [
  { exact: ["/"], navLabel: "Home", implemented: true },
  { exact: ["/dev/absence-states"], implemented: true }, // no navLabel -> never in nav
];

// issue #309 [E19.S1] done-when 1: THE role -> route matrix, the ONE declarative place. E20's
// dashboard pages do not exist yet -- entries here are route PATTERNS (prefix-matched, so
// "/manager" already covers a future "/manager/team-a"), so the matrix already governs them the
// moment they land, the same "not sampling" property #307 done-when 3 established for the
// public/private axis. String literals match src/lib/nav.ts's own placeholder labels
// (Manager/Leadership/IC/Cross-functional) as the best available guess at E20's eventual paths --
// see .sdlc/plans/309.md Decision 4's own note if E20 lands different paths later.
//
// issue #311 [E19.S3]: `navLabel`/`implemented` added per entry (Decision 1). `implemented: false`
// on manager/leadership/cross-functional records, honestly, that policy already governs these
// routes today even though no page exists at any of them yet -- nav.ts's navItemsFor() is the ONLY
// reader of `implemented`; decide() below is unchanged. `ic` is `implemented: true` since #310
// shipped its real page.
//
// issue #312 [E20.S1] Goal A: widened from ONE entry per role to an ARRAY of entries per role --
// `manager`/`leadership`/`ic` each now reach a SECOND, shared route (`/delivery`) in addition to
// their own dashboard. `Record<Role, ...>` itself is untouched (still one key per Role, so the
// compiler still rejects a literal missing any role); only the VALUE type widens from a single
// `RoutePattern & RouteMeta` to `readonly (RoutePattern & RouteMeta)[]`. Shape mirrors
// SHARED_AUTHENTICATED_ROUTES's own array-of-entries shape above, for the same reason: multiple
// independent routes need independent navLabel/implemented metadata. `cross-functional` gets NO
// `/delivery` entry -- its denial is structural (decide()'s fall-through-deny, same as every other
// unlisted route), never a special case or an explicit deny marker.
//
// issue #312 [E20.S1] Goal B, Task B3: the three `/delivery` entries flip `implemented: false` ->
// `true` HERE, in the SAME change that lands app/delivery/page.tsx -- required by
// prove-role-route-matrix.mjs Part C's filesystem<->table drift check (both directions), and
// exactly why that check now requires ALL matching entries to agree, not just one (the Direction-1
// `.find()` -> `.filter()`+`.every()` fix in that script, same change).
export const ROLE_ROUTES: Readonly<Record<Role, readonly (RoutePattern & RouteMeta)[]>> = {
  manager: [
    // issue #313 [E20.S2]: flipped true now that app/manager/page.tsx is real. The ONE policy
    // edit this story makes -- decide() already governed /manager for the manager role before
    // this flip; `implemented` is nav-only metadata (this file's own RouteMeta doc comment).
    { prefix: ["/manager"], navLabel: "Manager", implemented: true },
    { prefix: ["/delivery"], navLabel: "Delivery panel", implemented: true },
  ],
  leadership: [
    { prefix: ["/leadership"], navLabel: "Leadership", implemented: false },
    { prefix: ["/delivery"], navLabel: "Delivery panel", implemented: true },
  ],
  ic: [
    { prefix: ["/ic"], navLabel: "IC", implemented: true },
    { prefix: ["/delivery"], navLabel: "Delivery panel", implemented: true },
  ],
  "cross-functional": [
    { prefix: ["/cross-functional"], navLabel: "Cross-functional", implemented: false },
    // NO /delivery entry -- denial is by omission, the same fall-through-deny decide() already
    // applies to every unlisted route. Never add a "denied: true" marker; absence from the table
    // IS the denial.
  ],
};

// issue #312 [E20.S1] Goal A: was a single matchesPattern() call against ROLE_ROUTES[role] --
// ROLE_ROUTES[role] is now an array, so a role's route is allowed iff ANY of its entries matches.
function isRouteAllowedForRole(pathname: string, role: string | undefined): boolean {
  if (!isKnownRole(role)) return false;
  return ROLE_ROUTES[role].some((entry) => matchesPattern(pathname, entry));
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
  if (SHARED_AUTHENTICATED_ROUTES.some((entry) => matchesPattern(pathname, entry))) return "allow";
  if (isRouteAllowedForRole(pathname, role)) return "allow";
  return "forbid";
}
