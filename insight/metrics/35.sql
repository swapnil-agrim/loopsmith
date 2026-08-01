-- name: Lease contention
-- question: Is parallel work actually safe?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: derived from ledger.py's own open_claims() state machine (re-derived this session, not re-invented): held[goal] = (actor, ts) on every 'claimed' event is a TOTAL OVERWRITE, and held.pop(goal, None) on a terminal event (done/parked/failed) is a TOTAL CLEAR -- so a goal's current state after replaying any prefix of its events is entirely determined by that prefix's own LAST event, matching 10.sql's own current_holder shape exactly (ROW_NUMBER() OVER (PARTITION BY project_id, goal_id ORDER BY ts DESC, actor_id DESC, is_claim ASC) = 1 AND is_claim = 1). "Lease contention" (goals claimed by 2+ actors) means a 'claimed' event arrived while a DIFFERENT actor's 'claimed' event was still the live state -- no terminal event released the goal in between -- computed via LAG(kind)/LAG(actor_id) OVER (PARTITION BY project_id, goal_id ORDER BY ts), restricted to kind IN (claimed,done,parked,failed) (the same four kinds 10.sql's own current_holder CTE reads): a 'claimed' row is a contended transition when the immediately-preceding row in that ordered, filtered sequence is ALSO 'claimed' and by a DIFFERENT actor_id (same actor re-claiming with no terminal in between is not "2+ actors" and is not flagged). `contended` is bool_or() of that flag across the goal's ENTIRE history (a historical safety signal, not a live-state one -- a goal contended in the past and now closed still reports contended=true). `current_actor_id`/`claimed_ts` are the goal's CURRENT holder (NULL when the goal's latest event is a terminal, i.e. currently closed). "EXPIRED LEASES" DELIBERATELY NOT COMPUTED IN-SQL, following 10.sql's own explicit precedent for the identical class of problem: a static .sql view has no runtime "now", so age/expiry = now - claimed_ts is the consumer's job at render time (ttl_hours=12 per .sdlc/config.json:93, a literal named constant here -- no module in insight/ reads config.json's `ledger` block today -- gh_reader.py and artifact_reader.py do read the file, but neither exposes the lease settings, and widening one is out of scope for a metrics-only story). No status/severity_rank (Decision C/F -- matches 10.sql's own precedent of no status column either). Every window function partitions BY project_id (plus goal_id) per Decision G.
-- data_status: dark
WITH transitions AS (
    SELECT
        project_id,
        goal_id,
        ts,
        actor_id,
        kind,
        LAG(kind) OVER (PARTITION BY project_id, goal_id ORDER BY ts) AS prior_kind,
        LAG(actor_id) OVER (PARTITION BY project_id, goal_id ORDER BY ts) AS prior_actor_id
    FROM fact_event
    WHERE kind IN ('claimed', 'done', 'parked', 'failed')
),
contended_goals AS (
    SELECT
        project_id,
        goal_id,
        bool_or(
            kind = 'claimed' AND prior_kind = 'claimed' AND prior_actor_id <> actor_id
        ) AS contended
    FROM transitions
    GROUP BY project_id, goal_id
),
current_holder AS (
    SELECT project_id, goal_id, actor_id AS current_actor_id, ts AS claimed_ts
    FROM (
        SELECT
            project_id,
            goal_id,
            actor_id,
            ts,
            CASE WHEN kind = 'claimed' THEN 1 ELSE 0 END AS is_claim,
            ROW_NUMBER() OVER (
                PARTITION BY project_id, goal_id
                ORDER BY ts DESC, actor_id DESC, CASE WHEN kind = 'claimed' THEN 1 ELSE 0 END ASC
            ) AS rn
        FROM fact_event
        WHERE kind IN ('claimed', 'done', 'parked', 'failed')
    ) ranked
    WHERE rn = 1 AND is_claim = 1
),
all_claimed_goals AS (
    SELECT DISTINCT project_id, goal_id
    FROM fact_event
    WHERE kind = 'claimed'
)
SELECT
    all_claimed_goals.project_id,
    all_claimed_goals.goal_id,
    COALESCE(contended_goals.contended, false) AS contended,
    current_holder.current_actor_id,
    current_holder.claimed_ts
FROM all_claimed_goals
LEFT JOIN contended_goals
    ON all_claimed_goals.project_id = contended_goals.project_id
    AND all_claimed_goals.goal_id = contended_goals.goal_id
LEFT JOIN current_holder
    ON all_claimed_goals.project_id = current_holder.project_id
    AND all_claimed_goals.goal_id = current_holder.goal_id
