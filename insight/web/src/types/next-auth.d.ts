// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 1. `role` comes back from
// insight.accounts.store.verify_user via pythonBridge.ts and is carried on the session for E19
// (authorization) to consume later -- NOTHING in this story reads it to make a routing decision;
// that is explicitly out of scope (roles/authorization is E19).
import { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      role?: string;
    } & DefaultSession["user"];
  }
  interface User {
    role?: string;
  }
}
