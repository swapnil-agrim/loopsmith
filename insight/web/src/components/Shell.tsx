// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #305 [E17.S4], .sdlc/plans/305.md Decision 1.
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
// No "use client": nothing here is interactive (plain <Link>, no state), so this stays a Server
// Component like every other file under src/app/ and src/components/ today.
import Link from "next/link";

import { NAV_ITEMS } from "@/lib/nav";

export function Shell({ children }: { children: React.ReactNode }) {
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
