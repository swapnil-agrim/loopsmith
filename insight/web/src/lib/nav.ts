// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #305 [E17.S4], .sdlc/plans/305.md Decision 3.
//
// Plain, browser-free nav data -- deliberately kept OUT of Shell.tsx (no React import here) so
// scripts/prove-nav-items.mjs can compile and assert on it without a JSX-capable scratch dir. No
// auth/session/role import anywhere in this file -- done-when 3 says the nav is "a placeholder
// list" until E19.S3 makes it role-aware; inventing an auth model here would be scope creep this
// story explicitly rejects.
export interface NavItem {
  readonly label: string;
  /** Present only for routes that exist today. Absent -> rendered as an inert placeholder. */
  readonly href?: string;
}

// Only "Home" carries an href -- "/" is the only production route that exists. The other five
// labels are copied verbatim from spec §8's own E20 ("Dashboards") story list (Delivery panel ·
// Manager · Leadership · IC · Cross-functional), not invented, so the shell composes the real,
// already-approved product shape. /dev/absence-states is deliberately never listed here, in
// either linked or placeholder form -- it's env-gated out of every production build
// (INSIGHT_DEV_ROUTES), so a nav entry pointing at it would 404 for every real user. See that
// page's own header comment for the permanent decision, and the dev-route guard below in
// prove-nav-items.mjs for the mechanical enforcement.
export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Home", href: "/" },
  { label: "Delivery panel" },
  { label: "Manager" },
  { label: "Leadership" },
  { label: "IC" },
  { label: "Cross-functional" },
];
