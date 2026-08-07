// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #305 [E17.S4], .sdlc/plans/305.md Decision 3 (the original, placeholder-list shape).
// issue #311 [E19.S3], .sdlc/plans/311.md Decision 2 (this file's full rewrite: NAV_ITEMS ->
// navItemsFor(), role-aware, derived from route-policy.ts's own table).
//
// Plain, browser-free nav derivation -- deliberately kept OUT of Shell.tsx (no React import here)
// so scripts/prove-nav-items.mjs can compile and assert on it without a JSX-capable scratch dir.
// The only import is route-policy.ts (also framework-free -- no next/react/next-auth import
// anywhere in it), so navItemsFor() stays callable from a plain `tsc`-compiled scratch dir with
// zero DOM, exactly as NAV_ITEMS was before this story.
import {
  SHARED_AUTHENTICATED_ROUTES, ROLE_ROUTES, isKnownRole, representativePath,
} from "./auth/route-policy";

export interface NavItem {
  readonly label: string;
  readonly href: string; // ALWAYS present -- see navItemsFor()'s own comment for why.
}

/** The nav a session with `role` (possibly unknown/absent) and `hasSession` may see. Mirrors
 *  decide()'s own three-gate shape on purpose (route-policy.ts's decide()) -- session gate first
 *  (no session -> nothing, not even Home: the shared carve-out never applies without a session
 *  either), then the SAME unconditional shared-route allowance decide() gives every session
 *  regardless of role, then the SAME per-role gate. An item is included ONLY when its table entry
 *  both HAS a navLabel and IS implemented -- an entry allowed by policy but not yet built (e.g.
 *  ROLE_ROUTES.manager today) is never emitted, which is what keeps nav free of dead links to E20
 *  routes that don't exist yet. Every item this function returns therefore always has an `href` --
 *  there is no "placeholder" case left; an item nav cannot yet link to is simply never returned,
 *  not returned hrefless. */
export function navItemsFor(hasSession: boolean, role: string | undefined): readonly NavItem[] {
  if (!hasSession) return [];
  const items: NavItem[] = [];
  for (const entry of SHARED_AUTHENTICATED_ROUTES) {
    if (entry.navLabel && entry.implemented) {
      items.push({ label: entry.navLabel, href: representativePath(entry) });
    }
  }
  if (isKnownRole(role)) {
    const entry = ROLE_ROUTES[role];
    if (entry.navLabel && entry.implemented) {
      items.push({ label: entry.navLabel, href: representativePath(entry) });
    }
  }
  return items;
}
