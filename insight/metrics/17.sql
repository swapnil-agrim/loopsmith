-- name: Cost per landed goal
-- question: Unit economics — what does a landed goal cost, by lane and model?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: FUNCTIONALLY INERT TODAY (#243): ledger_writer.py's _write_event writes only six generic columns and never model/cost_cents, so every real `insight ingest` row has both NULL forever until that gap closes -- this view's logic is fully tested against fixtures that bypass the ledger/ingest pipeline entirely, same posture 22.sql/15.sql/16.sql already established. POPULATION IS "LANDED", READ LITERALLY: kind='spend' events joined to fact_goal rows with outcome='done' (the same vocabulary 22.sql's own goal_cost CTE already uses) -- a spend event on a goal that is still open, parked, or failed is real spend but not yet spend ON A LANDED GOAL, so it is excluded here entirely, not zero-weighted. GROUPED INCLUDING NULL AS A REAL BUCKET, both sides: a done goal with no recorded lane, or a spend event with no recorded model, is its own row, never dropped and never folded into a fabricated default -- "model unknown"/"lane unknown" is a real state this repo's own no-fabricated-defaults posture requires rendering honestly, matching DuckDB's own correct NULL-as-a-group GROUP BY semantics (deliberately NOT a NULL-key equality JOIN, which would silently drop those rows -- this is why cost totals and the population are computed in ONE GROUP BY over a single CTE chain, never two CTEs joined on (project_id, lane, model)). DOUBLE-COUNTING RESIDUE, NAMED NOT FIXED: a goal spending across two models (e.g. drafted on one tier, escalated to another) contributes to landed_goal_count in EVERY (lane, model) bucket its spend events touch, via COUNT(DISTINCT goal_id) inside each bucket, not across buckets -- so Σ landed_goal_count across a project's own rows can exceed its true landed-goal count. This is accepted, not engineered around: the question this metric answers is "of the cost attributed to lane×model slice X, what's the per-goal rate", not "how many goals were purely on model X" -- a goal that used two models genuinely has cost attributable to both. Mirrors 22.sql's own "RESIDUE, NAMED NOT FIXED" posture for view_rows[0] rather than silently picking a dedup rule that would misstate the population. cost_cents_per_landed_goal = SUM(cost_cents) / NULLIF(COUNT(DISTINCT goal_id), 0) -- SUM over an all-NULL cost_cents bucket renders SQL NULL (no COALESCE anywhere in this file), correctly propagating "spend events exist here but none carry a cost figure" rather than a fabricated 0. EMPTY-STORE SHAPE, DECIDED: zero spend/done rows produces zero rows in metric_17, no phantom row -- matching 22.sql's/15.sql's own empty-store doctrine. No minimum-N floor on cost_cents_per_landed_goal (a rate from as few as one landed goal reads with full confidence) -- the same accepted, named-not-fixed tension 22.sql's own guardrail already records for qualifying_pair_count, not newly introduced here. reliability_class: 2, a real decision not inherited silently: spec's own vocabulary table marks spend class 2, and the real write path routes through stream=ledger.EVENTS (loop.py's spend CLI verb), which read_all_with_reliability tags class 2 by directory -- no fork to resolve here, unlike 16.sql's post_review case, so this view reads fact_event with NO reliability_class = 1 filter, matching 22.sql's/15.sql's/16.sql's own class-2 posture.
WITH spend_events AS (
    SELECT project_id, goal_id, model, cost_cents, reliability_class
    FROM fact_event
    WHERE kind = 'spend' AND project_id IS NOT NULL AND goal_id IS NOT NULL
),
done_goals AS (
    SELECT project_id, goal_id, lane FROM fact_goal WHERE outcome = 'done'
),
landed_spend AS (
    SELECT se.project_id, dg.lane, se.model, se.goal_id, se.cost_cents, se.reliability_class
    FROM spend_events se
    JOIN done_goals dg ON dg.project_id = se.project_id AND dg.goal_id = se.goal_id
)
SELECT
    project_id, lane, model,
    SUM(cost_cents) AS total_cost_cents,
    COUNT(DISTINCT goal_id) AS landed_goal_count,
    ROUND(SUM(cost_cents) * 1.0 / NULLIF(COUNT(DISTINCT goal_id), 0), 4)
        AS cost_cents_per_landed_goal,
    count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
    count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
    count(*) AS total_count,
    ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
        AS coverage_pct
FROM landed_spend
GROUP BY project_id, lane, model
ORDER BY project_id, lane, model
