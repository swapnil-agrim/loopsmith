// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #307 [E18.S2], .sdlc/plans/307.md Decision 1. `role` comes back from
// insight.accounts.store.verify_user via pythonBridge.ts and is carried on the session.
// issue #309 [E19.S1], .sdlc/plans/309.md Decision 2: proxy.ts now DOES read it to make a routing
// decision (`req.auth?.user?.role` -> route-policy.ts `decide()`). It stays a widened `string`
// here, deliberately NOT narrowed to the four-role union: the Python side stores `--role` as a
// free-form string by design, so an out-of-vocabulary role must arrive as data and be denied at
// runtime by `isKnownRole()`, not be a compile error that never fires in production.
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
