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
// issue #315 [E20.S4]. E17's shared primitives and the IC curation list -- see this file's own
// composition below and .sdlc/plans/315.md Decisions D2/D3/D4.
import { Metric } from "@/components/Metric";
import { IntegrityStrip } from "@/components/IntegrityStrip";
import { ActorReadout, type ActorReadoutState } from "@/components/ActorReadout";
import { fetchDeliveryMetrics } from "@/lib/delivery/pythonBridge";
import { findMetric } from "@/lib/api/findMetric";
import { IC_PRIMARY_READOUT_IDS } from "@/lib/ic/curation";

/** The serialised payload, inlined the way insight/dash/ic.py already inlines its own
 * (`<script type="application/json" id="insight-ic-data">`) -- the instinct issue #310 says must
 * survive the port. Present on purpose: spec §5.2 and insight/tests/test_dash_ic_no_leak.py both
 * make the point that data inlined in a script block is readable via View Source whether or not
 * any JS ever parses it, so the whole-body leak assertion needs a serialised surface to actually
 * be asserting something. Escapes `<` -- a `</script>` inside a JSON string value would otherwise
 * close this block early and turn the payload into markup, the one way a JSON blob becomes an
 * injection rather than merely a disclosure.
 *
 * UNCHANGED BY ISSUE #315 (D4): still the identical `IcPayload` object `fetchIcPayload` returns,
 * widened only by three non-actor-identifying booleans (Task 2) -- the cross-actor leak proof's
 * whole-body assertions are unaffected by construction, not merely by hope. See that proof's own
 * regression run in this story's verification. */
function jsonScript(payload: unknown): string {
  return JSON.stringify(payload).replaceAll("<", "\\u003c");
}

/** The numeral shown on a genuinely measured "My cost" tile -- never called for the absent case
 * (D1's explicit carve-out keeps cost's own reasonText a separate string literal below, matching
 * insight/dash/ic.py's own docstring wording exactly, rather than routing through this
 * function). */
function formatCostNumeral(cost: IcPayload["cost"]): string {
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

  // issue #315 [E20.S4] Task 5: fetched alongside the existing session-scoped payload, not instead
  // of it -- the six curated catalog cards (aggregate-only, no --actor) and the five bespoke
  // actor-scoped readouts are two independent data sources composed on one page (D7's own
  // cross-reference note in insight/dash/ic.py names the same "two surfaces" split).
  const [metrics, payload] = await Promise.all([fetchDeliveryMetrics(), fetchIcPayload(resolvedActor)]);
  const curated = IC_PRIMARY_READOUT_IDS.map((id) => findMetric(metrics, id));

  // D1's corrected per-table gate: `actor_ever_appeared` alone is NOT enough (it can be true via a
  // completely unrelated table -- insight/dash/ic.py's own module docstring) -- each bespoke
  // readout additionally requires ITS OWN source table to have ever been ingested for anyone.
  const myQueueState: ActorReadoutState =
    payload.actor_ever_appeared && payload.my_queue_ever_ingested ? "measured" : "absent_no_data";
  const blockedOnMeState: ActorReadoutState =
    payload.actor_ever_appeared && payload.handoff_ever_ingested ? "measured" : "absent_no_data";
  const parksState: ActorReadoutState =
    payload.actor_ever_appeared && payload.park_ever_ingested ? "measured" : "absent_no_data";
  const verdictsState: ActorReadoutState =
    payload.actor_ever_appeared && payload.verdicts_ever_ingested ? "measured" : "absent_no_data";
  // "My cost" is DELIBERATELY NOT gated by any ever_ingested flag (D1's explicit carve-out): its
  // absence rests on a static codebase fact (no writer populates tokens_in/tokens_out/cost_cents
  // yet), not a runtime ingestion state that changes as the store fills up. Folding it into the
  // ever_ingested pattern would be meaningless -- there is no "cost table" distinct from the actor
  // check to ask "has it ever been ingested."
  const costState: ActorReadoutState = payload.cost.n ? "measured" : "absent_unbuilt";

  return (
    <main className="flex flex-col gap-10">
      <header className="panel-rise flex flex-col gap-1">
        <span className="panel-label panel-label-accent">Individual contributor</span>
        <h1 className="text-panel-bone" style={{ fontSize: "var(--panel-text-title)", letterSpacing: "-0.01em" }}>
          Your queue
        </h1>
        <p data-testid="ic-actor" className="text-panel-dim" style={{ fontSize: "var(--panel-text-small)" }}>
          {resolvedActor}
        </p>
      </header>

      {!payload.actor_ever_appeared && (
        <p data-testid="ic-cold-start-banner" className="panel-rise text-panel-dim" style={{ fontSize: "var(--panel-text-small)" }}>
          Identity &quot;{resolvedActor}&quot; has never appeared in this project&apos;s ledger
          &mdash; check <code>{"ledger.actor"}</code> in <code>.sdlc/config.json</code>. Every
          clause below is empty because nothing was found for this identity, not necessarily
          because there is nothing to show.
        </p>
      )}

      {/* Scoped to the 6 curated IC-tagged catalog ids, not the full 42 -- reports THIS
          instrument's own coverage, mirroring manager/leadership's own IntegrityStrip usage. */}
      <IntegrityStrip metrics={curated} />

      <section aria-label="Primary readouts" className="flex flex-col gap-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {curated.map((metric, i) => (
            <Metric key={metric.id} metric={metric} index={i} />
          ))}
        </div>
      </section>

      <section aria-label="Your own data" className="flex flex-col gap-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <ActorReadout
            label="My queue"
            state={myQueueState}
            numeralText={myQueueState === "measured" ? String(payload.my_queue.length) : null}
            reasonText={
              myQueueState === "measured"
                ? null
                : "fact_event has never recorded a claimed/done/parked/failed row for anyone yet."
            }
            readoutId="ic-my-queue-count"
            index={0}
          />
          <ActorReadout
            label="Blocked on me"
            state={blockedOnMeState}
            numeralText={blockedOnMeState === "measured" ? String(payload.blocked_on_me.length) : null}
            reasonText={
              blockedOnMeState === "measured"
                ? null
                : "No hand-off has ever been recorded for this project yet (fact_handoff has never been populated)."
            }
            readoutId="ic-blocked-on-me-count"
            index={1}
          />
          <ActorReadout
            label="My parks"
            state={parksState}
            numeralText={parksState === "measured" ? String(payload.park_count) : null}
            reasonText={
              parksState === "measured"
                ? null
                : "fact_event has never recorded a parked row for anyone yet."
            }
            readoutId="ic-park-count"
            index={2}
          />
          <ActorReadout
            label="My gate verdicts given"
            state={verdictsState}
            numeralText={verdictsState === "measured" ? String(payload.verdicts_given.length) : null}
            reasonText={
              verdictsState === "measured"
                ? null
                : "fact_pr_review has never recorded a verdict for anyone yet."
            }
            readoutId="ic-verdicts-given-count"
            index={3}
          />
          <ActorReadout
            label="My cost"
            state={costState}
            numeralText={costState === "measured" ? formatCostNumeral(payload.cost) : null}
            reasonText={
              costState === "measured" ? null : "tokens_in/tokens_out/cost_cents have zero writers"
            }
            readoutId="ic-cost"
            index={4}
          />
        </div>

        {/* Detail lists beneath the tiles above -- unchanged in content from the pre-restyle page,
            per .sdlc/plans/315.md Task 5: the numeral now lives in the tile; the underlying rows
            still render here. */}
        <div className="panel-rise flex flex-col gap-6">
          <div>
            <h2 className="panel-label">My queue</h2>
            {payload.my_queue.length === 0 ? (
              <p className="text-panel-dim">No open claims.</p>
            ) : (
              <ul>
                {payload.my_queue.map((row) => (
                  <li key={row.goal_id}>
                    {row.goal_id} &mdash; claimed {row.claimed_ts}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <h2 className="panel-label">Blocked on me</h2>
            {payload.blocked_on_me.length === 0 ? (
              <p className="text-panel-dim">
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
          </div>

          <div>
            <h2 className="panel-label">My gate verdicts given</h2>
            {payload.verdicts_given.length === 0 ? (
              <p className="text-panel-dim">None given yet.</p>
            ) : (
              <ul>
                {payload.verdicts_given.map((row) => (
                  <li key={row.pr_number}>
                    PR #{row.pr_number}: {row.verdict}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

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
