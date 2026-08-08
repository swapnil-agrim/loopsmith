// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// The nav list, split out of Shell.tsx as the app's ONLY Client Component.
//
// Why it had to split: marking the current route needs usePathname(), which is
// a client hook, and Shell.tsx must stay a Server Component -- it calls auth()
// and hosts the sign-out Server Action, neither of which may cross the client
// boundary. Pushing just the <ul> over keeps that boundary as small as it can
// be: this file receives an already-computed, already-authorised list of items
// and does nothing but render and highlight it. It performs no gating of its
// own, so nothing here can widen what navItemsFor() decided on the server.
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function NavLinks({ items }: { items: ReadonlyArray<{ label: string; href: string }> }) {
  const pathname = usePathname();

  return (
    <ul className="flex flex-col gap-0.5">
      {/* issue #311 [E19.S3]: navItemsFor() never returns an item without an href (Decision 2) --
          an item nav cannot yet link to is simply never returned, not returned hrefless, so the
          old placeholder <span> branch is dead code by construction and stays deleted. */}
      {items.map((item, i) => {
        // Exact match, or a real path-segment prefix. Deliberately NOT a bare
        // startsWith: that would light "/ic" for a future "/ic-admin", and the
        // root "/" would then match every route in the app.
        const active =
          pathname === item.href ||
          (item.href !== "/" && pathname.startsWith(`${item.href}/`));

        return (
          <li key={item.label}>
            <Link
              href={item.href}
              data-testid="shell-nav-link"
              aria-current={active ? "page" : undefined}
              className={
                "group relative flex items-center gap-2.5 rounded px-2.5 py-2 transition-colors " +
                (active
                  ? "bg-panel-raised text-panel-bone"
                  : "text-panel-dim hover:bg-panel-raised/60 hover:text-panel-bone")
              }
              style={{
                fontSize: "var(--panel-text-body)",
                animation: "panel-rise 500ms cubic-bezier(0.16,1,0.3,1) both",
                animationDelay: `${i * 50}ms`,
              }}
            >
              {/* The active marker is an amber bar, not a colour swap on the
                  label: at this type size a colour-only cue is the first thing
                  to disappear for a reader with low colour discrimination. */}
              <span
                aria-hidden="true"
                className="h-4 w-[2px] rounded-full transition-colors"
                style={{
                  backgroundColor: active ? "var(--panel-amber)" : "transparent",
                  boxShadow: active ? "0 0 8px var(--panel-glow)" : undefined,
                }}
              />
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
