// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #310 [E19.S2], .sdlc/plans/310.md Decisions 5/6, Task 4. Spec §5.2 rules 1-3.
//
// WHAT THIS PAGE IS. The first real Server-Component-to-Python-query data surface in this
// application -- the dossier's own finding was that no /ic route and no actor-scoped fetch path
// existed anywhere before this story -- and the enforcement point for done-when 1 ("the actor is
// resolved from the session and nowhere else"). It is NOT the polished IC dashboard: issue #315
// [E20.S4] owns the absence primitives (insight/dash/charts.py's render_aging_wip/status marks),
// coverage denominators, and any visual/design polish. This page renders every
// collect_ic_payload field as plain lists/counts on purpose (Decision 6), so done-when 2's leak
// test has real goal ids and PR numbers to search for, without absorbing #315's own scope.
//
// FORCE-DYNAMIC, EXPLICITLY (Decision 5). Belt-and-suspenders on top of the implicit dynamic-
// rendering trigger auth()'s own internal cookies() read already provides -- there is zero
// existing precedent for a session-dependent data route anywhere in this codebase (no
// revalidate/force-cache/unstable_cache/export const dynamic anywhere else under src/), so
// nothing here is trusted by analogy. scripts/prove-ic-no-cross-actor-leak.mjs proves this live:
// two different identities, fetched sequentially against the SAME running server process, get
// different responses.
export const dynamic = "force-dynamic";
//
// NO `searchParams` PROP, AND NO `params` PROP -- ON PURPOSE, load-bearing rather than incidental
// tidiness. Next.js passes searchParams to a page ONLY when the page declares it, so not
// declaring it means there is no local binding for `?actor=someone-else` to flow out of. Nothing
// here calls headers() or cookies() either. scripts/prove-actor-is-session-bound.mjs Part B
// asserts all of that against this file's real source, so a future edit cannot quietly
// reintroduce any of them.
//
// THE SESSION-RESOLVED IDENTITY IS NEVER BOUND TO A VARIABLE NAMED `actor` IN THIS FILE. Not a
// stylistic accident: scripts/prove-actor-is-session-bound.mjs Part B scans every file under
// src/ for the bare identifier `actor` and fails the build if one appears anywhere outside
// src/lib/auth/actor.ts itself. `resolvedActor` is used throughout instead.
//
// THE IDENTITY-NAMESPACE LIMIT THIS PAGE DOES NOT SOLVE (Decision 3, .sdlc/plans/310.md; tracked
// by follow-up issue #495). `resolveActor()` returns exactly the web-login username -- a
// DIFFERENT, uncoupled string space from the analytics actor_id namespace fact_event.actor_id /
// fact_handoff.from_actor / fact_handoff.to_actor / fact_pr_review.actor are populated from
// (ledger.actor config, else `gh api user -q .login`, else $USER). This page's leak proof
// demonstrates scoping correctness for whatever string the session carries -- it does not and
// cannot prove that string names the right human. insight/dash/ic.py's own module docstring
// names the identical limitation for the offline CLI equivalent.
//
// The shell comes from app/layout.tsx, which wraps every route -- no second copy here (#305).
import { auth } from "@/auth";
import { resolveActor } from "@/lib/auth/actor";
import { fetchIcPayload, type IcPayload } from "@/lib/ic/pythonBridge";

/** The serialised payload, inlined the way insight/dash/ic.py already inlines its own
 * (`<script type="application/json" id="insight-ic-data">`) -- the instinct issue #310 says must
 * survive the port. Present on purpose: spec §5.2 and insight/tests/test_dash_ic_no_leak.py both
 * make the point that data inlined in a script block is readable via View Source whether or not
 * any JS ever parses it, so the whole-body leak assertion needs a serialised surface to actually
 * be asserting something. Escapes `<` -- a `</script>` inside a JSON string value would otherwise
 * close this block early and turn the payload into markup, the one way a JSON blob becomes an
 * injection rather than merely a disclosure. */
function jsonScript(payload: unknown): string {
  return JSON.stringify(payload).replaceAll("<", "\\u003c");
}

function formatCost(cost: IcPayload["cost"]): string {
  if (!cost.n) {
    return "not yet instrumented (tokens_in/tokens_out/cost_cents have zero writers)";
  }
  return `${cost.tokens_in ?? 0} tokens in / ${cost.tokens_out ?? 0} tokens out / ${cost.cost_cents ?? 0} cents`;
}

export default async function IcPage() {
  const resolvedActor = resolveActor(await auth());

  // Fails closed (Decision 3). Unreachable in practice -- src/proxy.ts redirects an
  // unauthenticated request to /login and 403s a non-`ic` role before this component is ever
  // resolved -- but it is the last line of defence, not an assumption about the proxy. Renders no
  // identity at all, and never calls fetchIcPayload(), rather than falling back to a placeholder.
  if (resolvedActor === null) {
    return (
      <main>
        <p data-testid="ic-actor-unresolved">
          This view is scoped to a signed-in individual, and no identity could be resolved from
          your session.
        </p>
      </main>
    );
  }

  const payload = await fetchIcPayload(resolvedActor);

  return (
    <main>
      <h1>Your queue</h1>
      <p data-testid="ic-actor">{resolvedActor}</p>

      {!payload.actor_ever_appeared && (
        <p data-testid="ic-cold-start-banner">
          Identity &quot;{resolvedActor}&quot; has never appeared in this project&apos;s ledger
          &mdash; check <code>{"ledger.actor"}</code> in <code>.sdlc/config.json</code>. Every
          clause below is empty because nothing was found for this identity, not necessarily
          because there is nothing to show.
        </p>
      )}

      <h2>My queue ({payload.my_queue.length})</h2>
      {payload.my_queue.length === 0 ? (
        <p>No open claims.</p>
      ) : (
        <ul>
          {payload.my_queue.map((row) => (
            <li key={row.goal_id}>
              {row.goal_id} &mdash; claimed {row.claimed_ts}
            </li>
          ))}
        </ul>
      )}

      <h2>Blocked on me ({payload.blocked_on_me.length})</h2>
      {payload.blocked_on_me.length === 0 ? (
        <p>
          {payload.handoff_ever_ingested
            ? "Nothing blocked on you right now."
            : "No hand-off has ever been recorded for this project yet."}
        </p>
      ) : (
        <ul>
          {payload.blocked_on_me.map((row) => (
            <li key={`${row.from_actor}:${row.issue}:${row.opened_ts}`}>
              from {row.from_actor} &mdash; {row.area} #{row.issue} ({row.priority})
            </li>
          ))}
        </ul>
      )}

      <h2>My parks</h2>
      <p data-testid="ic-park-count">{payload.park_count}</p>

      <h2>My gate verdicts given ({payload.verdicts_given.length})</h2>
      {payload.verdicts_given.length === 0 ? (
        <p>None given yet.</p>
      ) : (
        <ul>
          {payload.verdicts_given.map((row) => (
            <li key={row.pr_number}>
              PR #{row.pr_number}: {row.verdict}
            </li>
          ))}
        </ul>
      )}

      <h2>My cost</h2>
      <p data-testid="ic-cost">{formatCost(payload.cost)}</p>

      {/* jsonScript() above is the escape, and the payload rendered here comes straight from
          fetchIcPayload(resolvedActor), the one session-derived call above -- there is no
          request-derived value in scope on this page to inject. */}
      <script
        type="application/json"
        id="insight-ic-data"
        dangerouslySetInnerHTML={{ __html: jsonScript(payload) }}
      />
    </main>
  );
}
