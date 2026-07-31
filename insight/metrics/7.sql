-- name: Flow load (WIP)
-- question: How much is in flight?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: no metric renders at individual grain in the manager or leadership views (spec Guardrails). DARK METRIC: fact_event holds 0 rows in every real ingest run today (#109 research dossier). ASSUMPTION, NOT A CONFIRMED FACT (see #109 plan Design decision F): this reads fact_event with kind IN (claimed,done,parked,failed), but spec section A.3's controlled vocabulary for fact_event.kind is {phase,gate,verify,slice,spend,retro,park,scan} and section A.1 says lifecycle "stays in entries and is never re-emitted" into events -- #180 (ledger persistence) must confirm or revise which table/vocabulary this replay actually reads before this view is dashboard-real. COUNTS DISTINCT GOALS, NOT CLAIM EPISODES (post-review BLOCKING fix): the replay pairs each claimed event with its own next terminal event, so a lease-contended goal (spec #35, "goals claimed by 2+ actors") produces two open episodes for one goal with no release between them; wip_count uses count(DISTINCT cc.goal_id), not count(*), so #35's own scenario reads as one unit of in-flight work, not two. Deliberately NOT applied to metric 10 (per-actor grain: one goal open under two actors is a defensible read there, not a double count) -- see both files' test docstrings for the shared fixture case that pins this asymmetry.
-- data_status: dark
SELECT
    w.week_start,
    count(DISTINCT cc.goal_id) FILTER (
        WHERE cc.claimed_ts <= w.week_start AND (cc.terminal_ts IS NULL OR cc.terminal_ts > w.week_start)
    ) AS wip_count
FROM (
    SELECT CAST(unnest(generate_series(
        date_trunc('week', (SELECT min(claimed_ts) FROM (
            WITH claims AS (SELECT project_id, goal_id, ts AS claimed_ts FROM fact_event WHERE kind = 'claimed')
            SELECT claimed_ts FROM claims
        ))),
        date_trunc('week', (SELECT max(coalesce(terminal_ts, claimed_ts)) FROM (
            WITH claims AS (SELECT project_id, goal_id, ts AS claimed_ts FROM fact_event WHERE kind = 'claimed'),
                 terminals AS (SELECT project_id, goal_id, ts AS terminal_ts FROM fact_event WHERE kind IN ('done','parked','failed')),
                 closed AS (
                     SELECT c.project_id, c.goal_id, c.claimed_ts,
                            MIN(t.terminal_ts) FILTER (WHERE t.terminal_ts > c.claimed_ts) AS terminal_ts
                     FROM claims c LEFT JOIN terminals t ON t.project_id = c.project_id AND t.goal_id = c.goal_id
                     GROUP BY c.project_id, c.goal_id, c.claimed_ts
                 )
            SELECT claimed_ts, terminal_ts FROM closed
        ))),
        INTERVAL 7 DAY
    )) AS DATE) AS week_start
) w
LEFT JOIN (
    WITH claims AS (SELECT project_id, goal_id, ts AS claimed_ts FROM fact_event WHERE kind = 'claimed'),
         terminals AS (SELECT project_id, goal_id, ts AS terminal_ts FROM fact_event WHERE kind IN ('done','parked','failed'))
    SELECT c.project_id, c.goal_id, c.claimed_ts,
           MIN(t.terminal_ts) FILTER (WHERE t.terminal_ts > c.claimed_ts) AS terminal_ts
    FROM claims c LEFT JOIN terminals t ON t.project_id = c.project_id AND t.goal_id = c.goal_id
    GROUP BY c.project_id, c.goal_id, c.claimed_ts
) cc ON true
GROUP BY w.week_start
