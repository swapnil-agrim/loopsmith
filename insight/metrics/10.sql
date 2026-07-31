-- name: Aging WIP
-- question: What is quietly rotting?
-- personas: manager
-- reliability_class: 1
-- guardrail: explicit exception to the individual-grain guardrail (spec: "aging WIP (#10) ... about unblocking a person, not ranking one"). Returns claimed_ts, NOT a computed age -- a static .sql view has no runtime "now", so age = today - claimed_ts is the consumer's job at render time, keeping this view deterministic and fixture-testable. DARK METRIC: fact_event holds 0 rows in every real ingest run today (#109 research dossier). ASSUMPTION, NOT A CONFIRMED FACT (see #109 plan Design decision F): this reads fact_event with kind IN (claimed,done,parked,failed), but spec section A.3's controlled vocabulary for fact_event.kind is {phase,gate,verify,slice,spend,retro,park,scan} and section A.1 says lifecycle "stays in entries and is never re-emitted" into events -- #180 (ledger persistence) must confirm or revise which table/vocabulary this replay actually reads before this view is dashboard-real.
-- data_status: dark
SELECT actor_id, goal_id, claimed_ts
FROM (
    SELECT c.actor_id, c.goal_id, c.claimed_ts,
           ROW_NUMBER() OVER (PARTITION BY c.actor_id ORDER BY c.claimed_ts ASC) AS rn
    FROM (
        WITH claims AS (SELECT project_id, goal_id, actor_id, ts AS claimed_ts FROM fact_event WHERE kind = 'claimed'),
             terminals AS (SELECT project_id, goal_id, ts AS terminal_ts FROM fact_event WHERE kind IN ('done','parked','failed'))
        SELECT c.project_id, c.goal_id, c.actor_id, c.claimed_ts,
               MIN(t.terminal_ts) FILTER (WHERE t.terminal_ts > c.claimed_ts) AS terminal_ts
        FROM claims c LEFT JOIN terminals t ON t.project_id = c.project_id AND t.goal_id = c.goal_id
        GROUP BY c.project_id, c.goal_id, c.actor_id, c.claimed_ts
        HAVING MIN(t.terminal_ts) FILTER (WHERE t.terminal_ts > c.claimed_ts) IS NULL
    ) c
) ranked
WHERE rn = 1
