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
// No "use client": there is no client-side interactivity here -- issue #308 [E18.S3] adds a
// <form> below whose action is a Server Action (an inline closure calling signOut(), see that
// form's own comment for why), and a Server Action form needs no client JS to submit, so this
// stays a Server Component like every other file under src/app/ and src/components/ today. (The
// original wording here was "nothing here is interactive," which was true when this file had
// only a plain <Link>; corrected to "no client-side interactivity" now that a form exists, since
// the mechanism -- not the absence of anything happening on click -- is what actually matters.)
import Link from "next/link";

import { NAV_ITEMS } from "@/lib/nav";
import { auth, signOut } from "@/auth";

export async function Shell({ children }: { children: React.ReactNode }) {
  // issue #308 [E18.S3], .sdlc/plans/308.md Decision 7. One auth() call, used only to decide
  // whether a sign-out control has anything to sign out of -- Shell renders around EVERY route,
  // including /login (this app has only one root layout, a pre-existing quirk this goal does not
  // fix), where there is never a session and the button must not appear.
  const session = await auth();

  return (
    <div
      data-testid="shell-root"
      className="flex min-h-screen flex-col bg-panel-ground text-panel-bone"
    >
      <header
        data-testid="shell-masthead"
        className="flex shrink-0 items-center border-b border-panel-rule px-4 py-3"
      >
        <span className="font-mono" style={{ fontSize: "var(--panel-text-title)" }}>
          LoopSmith Insight
        </span>
        {/* issue #308 [E18.S3], .sdlc/plans/308.md Decision 7: deliberately minimal -- a bare
            Server Action form, no confirmation dialog, no dropdown, no icon. Matches the "ad hoc
            Tailwind, not a shared component" posture login/page.tsx's own ponytail: marker
            already accepts for this app's current maturity. Only rendered when a session exists,
            so it is absent on /login and anywhere else nobody is signed in. */}
        {session?.user ? (
          <form
            action={async () => {
              "use server";
              // issue #308 [E18.S3], .sdlc/plans/308.md Decision 7. `signOut` itself is not
              // directly assignable to a <form action={...}> -- its exported type takes an
              // OPTIONS object, not a FormData (next-auth/index.d.ts's signIn() gets a FormData
              // overload; signOut() does not) -- found live during implementation as a real
              // tsc --noEmit TS2322. Wrapped in an inline Server Action closure instead, which is
              // Auth.js's own documented pattern for this exact button (see signOut()'s own
              // doc comment in next-auth/index.d.ts).
              await signOut();
            }}
            className="ml-auto"
          >
            <button
              type="submit"
              data-testid="shell-signout-button"
              className="rounded px-2 py-1.5 text-panel-bone hover:bg-panel-raised"
              style={{ fontSize: "var(--panel-text-body)" }}
            >
              Sign out
            </button>
          </form>
        ) : null}
      </header>

      <div className="flex min-h-0 flex-1">
        <nav data-testid="shell-nav" className="w-56 shrink-0 border-r border-panel-rule px-3 py-4">
          <ul className="flex flex-col gap-1">
            {NAV_ITEMS.map((item) =>
              item.href ? (
                <li key={item.label}>
                  <Link
                    href={item.href}
                    data-testid="shell-nav-link"
                    className="block rounded px-2 py-1.5 text-panel-bone hover:bg-panel-raised"
                    style={{ fontSize: "var(--panel-text-body)" }}
                  >
                    {item.label}
                  </Link>
                </li>
              ) : (
                <li key={item.label}>
                  <span
                    data-testid="shell-nav-placeholder"
                    aria-disabled="true"
                    className="block cursor-default px-2 py-1.5 text-panel-faint"
                    style={{ fontSize: "var(--panel-text-body)" }}
                  >
                    {item.label}
                  </span>
                </li>
              ),
            )}
          </ul>
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
        <div data-testid="shell-content" className="min-w-0 flex-1 overflow-x-auto px-6 py-6">
          {children}
        </div>
      </div>
    </div>
  );
}
