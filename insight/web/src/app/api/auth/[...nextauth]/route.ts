// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 6.
//
// DO NOT add `export const runtime = "edge";` to this file. This is an ORDINARY Route Handler
// (not the special proxy.ts convention -- see Decision 6 and Task 8's own guards for that file's
// different rule), so an explicit `runtime = "edge"` opt-in is valid Next.js and would build
// cleanly -- it would just silently break pythonBridge.ts's child_process call the next time
// someone actually signs in, since Edge lacks it. Node.js is this Route Handler's default; stay
// on it by omission. See scripts/prove-python-bridge-exit-codes.mjs's guard assertion below.
//
// `export { GET, POST } from "@/auth"` (the plan's own literal text) does NOT compile --
// auth.ts's `NextAuth(...)` call destructures `handlers` (an OBJECT carrying GET/POST as its own
// properties), not top-level GET/POST named exports; found live during implementation as a real
// `tsc --noEmit` error (TS2305, "no exported member 'GET'"), fixed here to Auth.js v5's actual
// documented shape -- destructure `handlers`, then re-export its two methods by name.
import type { NextRequest } from "next/server";

import { handlers, withSignOutFailureTracking } from "@/auth";

export const { GET } = handlers;

// issue #308 [E18.S3] code review finding 1. GET needs no wrapping (a GET to /api/auth/signout
// only ever renders Auth.js's own confirmation page -- see @auth/core/lib/index.js's `case
// "signout": return render.signout();` under the GET branch -- it never calls events.signOut, so
// there is nothing to detect). POST is wrapped because it is the path that actually mutates
// state: `withSignOutFailureTracking` (auth.ts's own export, shared with this file's sibling
// entry point, `signOut()`) wraps the WHOLE underlying call, so `events.signOut`'s failure flag
// (set only when a real sign-out's bumpEpoch() throws) is visible here immediately after
// `handlers.POST` returns. For every OTHER action this same POST handler serves (signin,
// callback, session update) the flag simply never gets set, so this wrapper is a transparent
// passthrough for all of them -- no action-path parsing needed to scope it correctly.
export async function POST(request: NextRequest): Promise<Response> {
  const { result: response, revocationFailed } = await withSignOutFailureTracking(() =>
    handlers.POST(request),
  );
  if (revocationFailed) {
    // Deliberately a fresh Response, not `response` -- discarding the original also discards
    // whatever Set-Cookie header @auth/core's own (already-run, unconditional) cookie-clear step
    // put on it, so a client driving this route directly does not get told its cookie was
    // cleared by a response that simultaneously reports failure. Matches finding 1's constraint:
    // this sign-out must not present as successful. See Task 7's proof script for the scenario
    // that exercises this against a genuinely unwritable sessions file.
    return Response.json(
      {
        error:
          "sign-out could not revoke the session server-side (the epoch write failed); see " +
          "server logs",
      },
      { status: 500 },
    );
  }
  return response;
}
