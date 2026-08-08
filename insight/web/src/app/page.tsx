// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// The landing panel.
//
// WHAT THIS REPLACES. Until now this route rendered a hand-built PLACEHOLDER Metric object --
// `{ id: 0, label: "LoopSmith Insight", state: "absent_unbuilt" }` -- through a one-line
// renderLabel() helper, so the entire signed-in home page was the two words "LoopSmith Insight"
// and nothing else. It was scaffolding from E17.S1 that outlived its story. Worse, it was a fake
// metric: an object shaped like a reading, with an id that indexes nothing, in a product whose
// central claim is that it never invents readings. It is deleted rather than restyled.
//
// What replaces it is derived entirely from real state: the session decides who you are, and
// navItemsFor() -- the SAME function the shell's nav uses, so the two can never disagree about
// what you may reach -- decides what is on offer. Nothing here is decorative filler.
export const dynamic = "force-dynamic";

import Link from "next/link";

import { auth } from "@/auth";
import { navItemsFor } from "@/lib/nav";

export default async function Home() {
  const session = await auth();
  const role = session?.user?.role;
  const panels = navItemsFor(!!session?.user, role).filter((item) => item.href !== "/");

  return (
    <main className="flex flex-col gap-10">
      <header className="panel-rise flex max-w-3xl flex-col gap-4">
        <span className="panel-label panel-label-accent">Engineering delivery instrumentation</span>
        <h1
          className="text-panel-bone"
          style={{ fontSize: "clamp(1.75rem, 4vw, 2.75rem)", lineHeight: 1.1, letterSpacing: "-0.02em" }}
        >
          Every reading on this panel is one you can trace.
        </h1>
        {/* The thesis, said once, in the first place a stakeholder lands. It is the reason the
            rest of the UI looks the way it does, so stating it here is orientation, not marketing. */}
        <p className="text-panel-dim" style={{ fontSize: "var(--panel-text-body)", lineHeight: 1.65 }}>
          A metric that was never measured is shown as <span className="text-panel-bone">absent</span>,
          never as zero &mdash; hatched, achromatic, and carrying no numeral at all. If a panel is dark,
          the instrument is not connected; it is not a healthy reading. That distinction is enforced by
          the type system, not by review.
        </p>
      </header>

      <section aria-label="Available panels" className="panel-rise flex flex-col gap-3" style={{ animationDelay: "90ms" }}>
        <div className="flex items-center gap-3">
          <span className="panel-label panel-label-accent shrink-0">Your panels</span>
          <span className="h-px min-w-4 flex-1 bg-panel-rule" aria-hidden="true" />
        </div>

        {panels.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {panels.map((panel, i) => (
              <Link
                key={panel.href}
                href={panel.href}
                className="panel-instrument panel-rise group flex flex-col gap-2 p-4 no-underline"
                style={{ animationDelay: `${120 + i * 60}ms` }}
              >
                <span className="panel-label">Panel</span>
                <span
                  className="text-panel-bone transition-colors group-hover:text-panel-amber"
                  style={{ fontSize: "var(--panel-text-head)" }}
                >
                  {panel.label}
                </span>
                <span className="panel-num text-panel-faint" style={{ fontSize: "var(--panel-text-micro)" }}>
                  {panel.href}
                </span>
              </Link>
            ))}
          </div>
        ) : (
          // An honest empty state, not a blank area. `cross-functional` legitimately reaches no
          // panel yet, and saying so is the same rule the metrics follow: name the absence and its
          // reason rather than rendering nothing and letting the reader guess.
          <div
            data-testid="no-panels"
            className="panel-void-surface flex flex-col gap-1.5 p-5"
            style={{ borderStyle: "dotted" }}
          >
            <span className="panel-label" style={{ color: "var(--panel-void-ink)" }}>
              No panels
            </span>
            <span style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.5 }}>
              {role
                ? `No panel is built for the "${role}" role yet. This is an absent view, not an empty one -- nothing is being hidden from you.`
                : "Sign in to see the panels your role can reach."}
            </span>
          </div>
        )}
      </section>
    </main>
  );
}
