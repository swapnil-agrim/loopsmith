-- name: Aging WIP
-- question: What is quietly rotting?
-- personas: manager
-- reliability_class: 1
-- guardrail: explicit exception to the individual-grain guardrail (spec: "aging WIP (#10) ... about unblocking a person, not ranking one"). Returns claimed_ts, NOT a computed age -- a static .sql view has no runtime "now", so age = today - claimed_ts is the consumer's job at render time, keeping this view deterministic and fixture-testable. DARK METRIC: fact_event holds 0 rows in every real ingest run today (#109 research dossier). ASSUMPTION, NOT A CONFIRMED FACT (see #109 plan Design decision F): this reads fact_event with kind IN (claimed,done,parked,failed), but spec section A.3's controlled vocabulary for fact_event.kind is {phase,gate,verify,slice,spend,retro,park,scan} and section A.1 says lifecycle "stays in entries and is never re-emitted" into events -- #180 (ledger persistence) must confirm or revise which table/vocabulary this replay actually reads before this view is dashboard-real. LAST-CLAIM-WINS (post-review BLOCKING fix, round 3): DERIVED FROM skills/sdlc-loop/scripts/ledger.py's open_claims() (the spec's own named ground truth for metric #7/#10 -- neither the round-2 review nor the round-2 fix opened that file), which is `held[goal] = (actor, ts)` on every claim -- an OVERWRITE, not an addition. On a re-claim with no terminal event released in between (spec #35, lease contention), the EARLIER claimant's own hold is dropped entirely, not doubled or left ambiguous -- only the LATEST claim on a goal can be its current open episode. The prior version of this file (round 2) picked, per actor, the oldest episode among ALL of that actor's own claims that individually looked un-terminaled -- which let a goal a1 no longer holds (because a2 re-claimed it) still surface under a1. This view now first resolves, PER GOAL, which single claim is the current one (latest by claimed_ts) and whether a terminal event (>= that claim's own ts, matching #7's same-second-close fix) has released it since -- only then ranks each actor's own oldest CURRENTLY-HELD goal.
-- data_status: dark
WITH claims AS (
    SELECT project_id, goal_id, actor_id, ts AS claimed_ts FROM fact_event WHERE kind = 'claimed'
),
terminals AS (
    SELECT project_id, goal_id, ts AS terminal_ts FROM fact_event WHERE kind IN ('done', 'parked', 'failed')
),
latest_claim AS (
    SELECT project_id, goal_id, actor_id, claimed_ts,
           ROW_NUMBER() OVER (PARTITION BY project_id, goal_id ORDER BY claimed_ts DESC) AS rn
    FROM claims
),
current_holder AS (
    -- Per goal, only its LATEST claim can be the current holder (open_claims()'s overwrite
    -- semantics) -- and only if no terminal has released it since (>=, not >, so a same-second
    -- claim-then-done closes immediately, matching #7's own fix).
    SELECT lc.project_id, lc.goal_id, lc.actor_id, lc.claimed_ts
    FROM latest_claim lc
    WHERE lc.rn = 1
      AND NOT EXISTS (
          SELECT 1 FROM terminals t
          WHERE t.project_id = lc.project_id AND t.goal_id = lc.goal_id
            AND t.terminal_ts >= lc.claimed_ts
      )
)
SELECT actor_id, goal_id, claimed_ts
FROM (
    SELECT actor_id, goal_id, claimed_ts,
           ROW_NUMBER() OVER (PARTITION BY actor_id ORDER BY claimed_ts ASC) AS rn
    FROM current_holder
) ranked
WHERE rn = 1
