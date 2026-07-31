-- name: Autonomy rate
-- question: How often does the loop finish unaided?
-- personas: manager, leadership, cross-functional
-- reliability_class: 1
-- guardrail: DARK METRIC: fact_goal.claimed_ts/terminal_ts/outcome are 0/19 populated in this repo's own real ingest today (#110 research dossier, re-verified this session) -- a fixture-green test is not evidence of a live dashboard number. ASSUMPTION, NOT A CONFIRMED FACT (extends #109 plan Design decision F): reads fact_event with kind IN (parked,ack) as the entries-stream lifecycle vocabulary 7.sql/10.sql already assumed lands there -- but spec section A.3's own Class-2 vocabulary for fact_event.kind ALSO contains a member literally spelled 'park' (singular, agent-emitted, carrying reason_class/why), a DIFFERENT thing from the lifecycle 'parked' this view reads; #105/#180 must confirm which spelling/table the persisted lifecycle stream actually uses. SPAN DECISION (Plan's own choice, not spec-dictated): span = [claimed_ts, terminal_ts] read as the FIRST claim to the FINAL terminus (fact_goal is one row per goal_id, so both columns hold one value even across a park/reclaim cycle) -- the widest window an intervention could occur in, the conservative direction for an autonomy claim. TERMINAL DECISION: outcome IN (done,failed), matching the spec's literal "terminal goals" denominator regardless of whether span is known; a done goal whose claimed_ts or terminal_ts is NULL cannot be proven free of intervention and is EXCLUDED from the numerator (never counted autonomous) while still counted in terminal_count -- a conservative reading, not a spec requirement. SCOPE VS 13.sql (documented post-review, an asymmetry a reader would otherwise trip on): this view deliberately restricts intervention detection to e.ts BETWEEN claimed_ts AND terminal_ts because autonomy_rate is an EXISTENCE question about one goal's own execution episode ("did THIS run need help") -- 13.sql's intervention_count is instead a lifetime, unscoped count, because it answers a DIFFERENT, magnitude question ("how much attention in total"), where a park/ack recorded outside the stored span (e.g. against a re-opened or re-claimed goal) is still real attention spent and must not be dropped just because it falls outside this view's own conservative window. A goal can therefore show autonomy_rate-clean (no in-span park/ack) while 13.sql's intervention_count for the same goal is positive -- both numbers are correct simultaneously, because they are scoped to answer different questions, not a bug in either. FALSE-ZERO TRAP (post-review, named explicitly): if #105 populates fact_goal.outcome BEFORE back-filling claimed_ts/terminal_ts -- a plausible partial-migration order, since #105 is this guardrail's own named dependency -- every terminal 'done' goal falls into the span-unknown bucket above while terminal_count still increments, and autonomy_rate renders a real, non-null 0.0: a FALSE ZERO ("the loop never finishes unaided"), not an absence ("no data yet"). Whoever drops this file's data_status:dark label must confirm claimed_ts/terminal_ts are populated, not just outcome, or this view reports a confident lie. REJECTED ALTERNATIVE: fact_handoff (store.py, populated since #99) already carries ack_ts/ack_state/issue -- a purpose-built home for "was this acked", keyed by issue rather than goal_id. Not used here because insight/tests/test_metrics_testing.py:37 explicitly earmarks fact_handoff for #112 (fact_handoff + dim_actor), a different story; this view instead reads fact_event.kind='ack', extending #109 Decision F's own entries-stream reading. Once #105 (this view's fact_event ack) and #112 (fact_handoff's ack_state) both land, there will be TWO disagreeing "was this acked" surfaces -- one keyed by goal_id, one by issue -- with nothing in the schema tying them together; not resolved here.
-- data_status: dark
WITH terminal_goals AS (
    SELECT project_id, goal_id, outcome, claimed_ts, terminal_ts
    FROM fact_goal
    WHERE outcome IN ('done', 'failed')
),
interventions AS (
    SELECT DISTINCT e.project_id, e.goal_id
    FROM fact_event e
    JOIN terminal_goals g
      ON g.project_id = e.project_id AND g.goal_id = e.goal_id
    WHERE e.kind IN ('parked', 'ack')
      AND g.claimed_ts IS NOT NULL AND g.terminal_ts IS NOT NULL
      AND e.ts >= g.claimed_ts AND e.ts <= g.terminal_ts
)
SELECT
    count(*) FILTER (
        WHERE tg.outcome = 'done'
          AND tg.claimed_ts IS NOT NULL AND tg.terminal_ts IS NOT NULL
          AND iv.goal_id IS NULL
    ) AS autonomous_done_count,
    count(*) AS terminal_count,
    ROUND(
        count(*) FILTER (
            WHERE tg.outcome = 'done'
              AND tg.claimed_ts IS NOT NULL AND tg.terminal_ts IS NOT NULL
              AND iv.goal_id IS NULL
        ) * 1.0 / NULLIF(count(*), 0), 4
    ) AS autonomy_rate
FROM terminal_goals tg
LEFT JOIN interventions iv USING (project_id, goal_id)
