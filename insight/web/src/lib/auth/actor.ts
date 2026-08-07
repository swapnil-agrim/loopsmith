// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #310 [E19.S2], .sdlc/plans/310.md Decisions 1-4. Spec §5.2 rule 1.
//
// THE single place in this application that produces an actor identity -- the direct analogue of
// route-policy.ts's decide() for the role axis, and guarded the same way: every call site imports
// THIS function, and scripts/prove-actor-is-session-bound.mjs Part B asserts that no other file
// under src/ so much as names an actor. Spec §5.2 rule 1: "the actor is resolved from the session
// and nowhere else. An IC requesting /ic?actor=someone-else receives THEIR OWN data; the parameter
// is never consulted. Without this, adding login makes the product less safe than the static build
// it replaced."
//
// THE SIGNATURE IS THE ENFORCEMENT. This function takes a `Session | null` and nothing else. There
// is no overload, no options bag, no second parameter -- so a query string, a path segment, a
// header or a request body is not merely ignored here, it is not physically passable. "The
// parameter is never consulted" therefore holds by CONSTRUCTION, the same property proxy.ts's 403
// branch has (there is no code path from it to any page's data-fetching), rather than by review.
// A reviewer can be wrong about a call site; a type error cannot be.
//
// WHY session.user.name AND NOT A NEW CLAIM. auth.ts's authorize() already returns
// `{ id: username, name: username, role }`, and Auth.js's own core copies that id onto `token.sub`
// and that name onto `session.user.name`. The web-login username IS the actor. Minting a parallel
// `actor` claim would create a second source of truth for one identity, and the two would drift.
//
// NAMED LIMITATION THIS FILE DOES NOT SOLVE, and must not be read as having considered-and-
// dismissed (plan-review amendment 4; the same honesty insight/dash/actor.py's own docstring
// already practises for the CLI). The string this function returns lives in the WEB ACCOUNTS
// namespace (insight/accounts/store.py -- whatever was typed at `insight users add <username>`).
// The analytics namespace it will eventually be matched against is a DIFFERENT, uncoupled string
// space: fact_event.actor_id / fact_handoff.{from,to}_actor / fact_pr_review.actor are populated
// at ingest from the SDLC loop's own ledger.actor config key, else `gh api user -q .login`, else
// $USER. Nothing in this repository reconciles the two. Two failure shapes follow:
//   (1) SAFE BUT USELESS -- the two names differ, and the operator's own view renders entirely
//       absent even though real data about them exists under another key. insight/dash/ic.py's
//       _actor_ever_appeared already detects exactly this and banners it; whichever story wires
//       the first real IC query should reuse it rather than reinvent it.
//   (2) UNSAFE -- a web username collides with a DIFFERENT real person's actor_id (a reused
//       handle, a templated account). Scoping is then correct by construction in SQL and still
//       resolves to the wrong human's data. Correctness here means "the session's own username was
//       used", never "that username is the person reading the screen."
// Closing this needs a provisioning-time assertion that the two namespaces agree, which is neither
// this story's surface nor a decision an unattended loop should make.
import type { Session } from "next-auth";

/** The session's actor identity, or `null` when there isn't one.
 *
 * FAILS CLOSED, and never throws (Decision 3 -- the same "not a crash, a denial" posture
 * route-policy.ts's isKnownRole() takes for the role axis, and the same intent
 * insight/dash/actor.py's ActorResolutionError carries for the CLI). A missing session, a session
 * with no `user`, and a `name` that is absent, empty, or whitespace-only all return `null`. There
 * is deliberately no fallback identity: a placeholder default here would be an unauthenticated
 * viewer silently adopting SOMEBODY's actor id, which is the precise failure this story exists to
 * make impossible.
 *
 * `name` is `string | null | undefined` on Auth.js's own DefaultSession["user"], so all three are
 * handled rather than narrowed away with a cast. */
export function resolveActor(session: Session | null): string | null {
  const name = session?.user?.name;
  if (typeof name !== "string") return null;
  const trimmed = name.trim();
  return trimmed === "" ? null : trimmed;
}
