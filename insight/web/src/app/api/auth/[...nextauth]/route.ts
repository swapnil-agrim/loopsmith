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
import { handlers } from "@/auth";

export const { GET, POST } = handlers;
