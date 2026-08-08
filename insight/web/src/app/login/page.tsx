// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md. Done-when 1: composed from E17's TOKEN UTILITIES
// (globals.css's @theme block and the flight-deck layer beneath it) -- there is still no shared
// form/input/button PRIMITIVE in this repo, so this remains ad hoc utility CSS by necessity, not
// by preference (ponytail: the ceiling here is a real <Input>/<Button> pair once a SECOND form
// exists to justify factoring one out; the sign-out control is a bare button and does not count).
//
// Reads `searchParams` -- this alone opts the page into dynamic rendering (Next.js's own rule for
// any page consuming that prop), so `next build` never tries to statically prerender a page that
// needs a real request (dossier risk 7).
//
// The shell renders around this route too (one root layout, a pre-existing quirk), and correctly
// shows no nav and no sign-out control to an anonymous visitor. So this page supplies its own
// framing rather than assuming any chrome above it.
import { loginAction } from "./actions";

const FIELD_CLASS =
  "w-full rounded border border-panel-rule-hard bg-panel-ground px-3 py-2.5 text-panel-bone " +
  "outline-none transition-colors placeholder:text-panel-faint focus:border-panel-amber";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const hasError = typeof params.error === "string";
  const callbackUrl = typeof params.callbackUrl === "string" ? params.callbackUrl : "/";

  return (
    <div className="flex min-h-[70vh] items-center justify-center px-4">
      <div className="panel-rise flex w-full max-w-sm flex-col gap-6">
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline gap-2.5">
            <span
              className="panel-num text-panel-bone"
              style={{ fontSize: "var(--panel-text-title)", letterSpacing: "-0.03em" }}
            >
              LoopSmith
            </span>
            <span className="panel-label panel-label-accent">Insight</span>
          </div>
          <p className="text-panel-dim" style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.55 }}>
            Sign in to reach the panels your role can see.
          </p>
        </div>

        <form action={loginAction} className="panel-instrument flex flex-col gap-4 p-6">
          <input type="hidden" name="callbackUrl" value={callbackUrl} />

          {hasError ? (
            // ONE generic message, matching actions.ts's own deliberate refusal to distinguish
            // "wrong password" from "no such user". Styled as a real alert rather than a stray red
            // line, and marked assertive so it is announced rather than silently repainted.
            <p
              role="alert"
              className="rounded border border-panel-red/40 bg-panel-void px-3 py-2 text-panel-red"
              style={{ fontSize: "var(--panel-text-small)" }}
            >
              Invalid username or password.
            </p>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <label className="panel-label" htmlFor="username">
              Username
            </label>
            <input
              id="username"
              name="username"
              type="text"
              required
              autoComplete="username"
              autoFocus
              className={FIELD_CLASS}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="panel-label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              required
              autoComplete="current-password"
              className={FIELD_CLASS}
            />
          </div>

          {/* text-panel-ground, NOT text-panel-void-ink: void-ink is a mid-grey meant for text on
              the DARK void surface, and on amber it renders as a washed-out label that reads as a
              disabled button. Near-black on amber is the high-contrast pairing this palette is
              built for. */}
          <button
            type="submit"
            className="mt-1 rounded bg-panel-amber px-3 py-2.5 text-panel-ground transition-opacity hover:opacity-90"
            style={{ fontSize: "var(--panel-text-body)", letterSpacing: "0.02em" }}
          >
            Sign in
          </button>
        </form>
      </div>
    </div>
  );
}
