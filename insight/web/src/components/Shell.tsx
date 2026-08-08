// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #305 [E17.S4], .sdlc/plans/305.md Decision 1 / issue #308 [E18.S3],
// .sdlc/plans/308.md Decision 7.
//
// The single composition point for masthead + navigation + content frame. Rendered from exactly
// one place -- src/app/layout.tsx, the App Router root layout -- which Next.js renders around
// EVERY route in this app; there is no second root layout and no Pages Router page for one to
// compete with. That's what makes "every page uses it" hold structurally: a page cannot render
// without going through layout.tsx, and layout.tsx renders nothing but <Shell>. See
// .sdlc/plans/305.md Decision 1 for the one residual gap this does not close (a future nested
// route-segment layout.tsx rendering its own competing chrome inside this shell's content frame)
// and Decision 4 for why min-w-0 + overflow-x-auto on the content slot below -- not a breakpoint
// -- is what keeps wide content from ever pushing the PAGE itself into horizontal scroll.
//
// This file is a Server Component EXCEPT for the nav list, which moved into NavLinks.tsx: marking
// the active route needs usePathname(), and a Server Component cannot read it. Only that leaf is
// a Client Component; the masthead, the sign-out Server Action form and the frame all stay on the
// server, so no credential-adjacent code crosses the boundary.
import { navItemsFor } from "@/lib/nav";
import { auth, signOut } from "@/auth";

import { NavLinks } from "./NavLinks";

export async function Shell({ children }: { children: React.ReactNode }) {
  // issue #308 [E18.S3], .sdlc/plans/308.md Decision 7. One auth() call, used to decide whether a
  // sign-out control has anything to sign out of, AND (issue #311 [E19.S3]) to compute which nav
  // items this session's role can reach -- Shell renders around EVERY route, including /login
  // (this app has only one root layout, a pre-existing quirk this goal does not fix), where there
  // is never a session and neither the button nor any nav item may appear.
  const session = await auth();
  // issue #311 [E19.S3] Decision 2: navItemsFor() mirrors decide()'s own gates -- no session means
  // an empty nav, fail closed, same as an anonymous /login visitor gets no dead links or hints
  // about views they cannot open.
  const navItems = navItemsFor(!!session?.user, session?.user?.role);
  const signedIn = !!session?.user;

  return (
    <div
      data-testid="shell-root"
      className="flex min-h-screen flex-col text-panel-bone"
    >
      <header
        data-testid="shell-masthead"
        // panel-sweep puts a slow amber pass across the masthead -- the one
        // ambient motion in the app, and the cue that this is a live instrument
        // rather than a rendered report. It is suppressed entirely under
        // prefers-reduced-motion (globals.css).
        className="panel-sweep flex shrink-0 items-center gap-4 border-b border-panel-rule-hard bg-panel-panel/70 px-5 py-3 backdrop-blur-sm"
      >
        <div className="flex items-baseline gap-2.5">
          <span
            className="panel-num text-panel-bone"
            style={{ fontSize: "var(--panel-text-title)", letterSpacing: "-0.03em" }}
          >
            LoopSmith
          </span>
          <span className="panel-label panel-label-accent">Insight</span>
        </div>

        {signedIn && (
          <div className="ml-auto flex items-center gap-4">
            {/* Who am I, and as what? The pre-redesign masthead showed neither,
                which on a product whose whole point is role-scoped views left
                the reader unable to tell why a panel was or was not there. */}
            <div className="hidden items-baseline gap-2 sm:flex">
              <span className="panel-label">{session.user.name}</span>
              <span
                className="rounded-full border border-panel-void-edge px-2 py-0.5 text-panel-dim"
                style={{ fontSize: "var(--panel-text-micro)", letterSpacing: "0.1em" }}
              >
                {String(session.user.role ?? "").toUpperCase()}
              </span>
            </div>

            {/* issue #308 [E18.S3], .sdlc/plans/308.md Decision 7: deliberately minimal -- a bare
                Server Action form, no confirmation dialog, no dropdown, no icon. Only rendered
                when a session exists, so it is absent on /login and anywhere else nobody is
                signed in. */}
            <form
              action={async () => {
                "use server";
                // issue #308 [E18.S3], .sdlc/plans/308.md Decision 7. `signOut` itself is not
                // directly assignable to a <form action={...}> -- its exported type takes an
                // OPTIONS object, not a FormData (next-auth/index.d.ts's signIn() gets a FormData
                // overload; signOut() does not) -- found live during implementation as a real
                // tsc --noEmit TS2322. Wrapped in an inline Server Action closure instead, which
                // is Auth.js's own documented pattern for this exact button.
                await signOut();
              }}
            >
              <button
                type="submit"
                data-testid="shell-signout-button"
                className="panel-label rounded border border-transparent px-2 py-1 transition-colors hover:border-panel-void-edge hover:text-panel-bone"
              >
                Sign out
              </button>
            </form>
          </div>
        )}
      </header>

      <div className="flex min-h-0 flex-1">
        <nav
          data-testid="shell-nav"
          className="hidden w-52 shrink-0 border-r border-panel-rule px-3 py-5 sm:block"
        >
          <NavLinks items={navItems} />
        </nav>

        {/* min-w-0 overrides the flex-item default (min-width: auto), which otherwise refuses to
            shrink below its content's intrinsic width -- THAT default is the actual mechanism
            that would let wide content push the whole page into horizontal scroll.
            overflow-x-auto then gives this element its own scrollbar once its content is wider
            than the space this flex layout gives it, instead of <html> growing to fit. */}
        {/* A <div>, deliberately, NOT a <main>: both existing pages already open with their own
            <main> (src/app/page.tsx, src/app/dev/absence-states/page.tsx), and a document carries
            exactly one main landmark. Making the shell's content slot a <main> would nest one
            inside another on every page and break landmark navigation for assistive tech. The
            landmark belongs to the page; the shell only supplies the frame around it. */}
        <div
          data-testid="shell-content"
          className="min-w-0 flex-1 overflow-x-auto px-5 py-6 sm:px-8 sm:py-8"
        >
          {children}
        </div>
      </div>
    </div>
  );
}
