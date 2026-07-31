-- name: Interventions per goal
-- question: How much human attention per unit shipped?
-- personas: manager, leadership
-- reliability_class: 1
-- guardrail: DARK METRIC: fact_goal.outcome and fact_event are 0/19 and 0 populated respectively in this repo's own real ingest today (#110 research dossier) -- a fixture-green test is not evidence of a live dashboard number. ASSUMPTION, NOT A CONFIRMED FACT (extends #109 plan Design decision F): reads fact_event kind IN (parked,ack) as the lifecycle stream's assumed landing shape -- see 12.sql's guardrail for the Class-2 'park' vs lifecycle 'parked' spelling collision this must not be confused with. POPULATION DECISION (Plan's own choice): computed over outcome='done' goals only, reading the metric's own question literally ("per unit shipped" -- a failed goal did not ship) -- NOT all terminal goals; #12/#14's spec text says "terminal" verbatim, a word this metric's spec text does not repeat. SCHEMA GAP found while designing this view (new, not previously flagged): the ledger's own ack-to-goal linkage (ledger.py handoff_key(): issue-or-goal) can key an ack by "issue" alone with no "goal" field set; fact_event carries goal_id but no issue column, so a goal-less, issue-keyed ack would be invisible to this join once persistence lands -- an open question for #105, not resolved here. A goal with zero park/ack events legitimately contributes intervention_count=0 to the distribution -- 0 is real signal (autonomy), not a missing value. SCOPE VS 12.sql (documented post-review): unlike 12.sql, this view applies NO span filter (no claimed_ts/terminal_ts bound on e.ts) -- deliberate, not an oversight: 12.sql answers an EXISTENCE question about one execution episode ("did THIS run need help"), so it conservatively bounds the window; this view answers a MAGNITUDE question ("how much attention in total, per unit shipped"), so a park/ack recorded outside the stored span is still real attention spent and counting it is the more correct reading of "how much", not less. A goal can be autonomy_rate-clean under 12.sql while still showing a positive intervention_count here -- both are correct, because the two metrics are scoped to different questions. IDEMPOTENCE VS 12.sql/14.sql (documented post-review): 12.sql and 14.sql both guard against duplicate lifecycle rows for the same goal with `SELECT DISTINCT` before counting, because both are existence checks ("did it happen at least once") where a re-emitted duplicate event must not change the answer. This view deliberately does NOT deduplicate: park_count/ack_count are raw event counts, because intervention_count is meant to measure attention VOLUME, and a genuine second park on the same goal (two separate lease-contention or review cycles, not a duplicate emission of the same event) is a second unit of attention that a distribution over p50/p85 must be able to see -- collapsing repeats here would silently flatten every goal's count to 0 or 1 and destroy the percentile's whole reason for existing. This does mean a literally duplicate-emitted event (the same park re-recorded, not a second real park) would inflate this view's count while 12.sql/14.sql would not notice it at all; no emitter-side de-duplication guarantee exists in this story to rule that out, so the risk is named here rather than assumed away. REJECTED ALTERNATIVE (post-review): fact_handoff (store.py, populated since #99) already carries ack_ts/ack_state/issue, a purpose-built home for "was this acked" -- not used here for the same reason as 12.sql's guardrail: insight/tests/test_metrics_testing.py:37 earmarks fact_handoff for #112 (fact_handoff + dim_actor), not this story; this view reads fact_event.kind='ack' instead. See 12.sql's guardrail for the resulting two-disagreeing-surfaces risk once both #105 and #112 land. Consumer contract: SELECT DISTINCT p50_interventions, p85_interventions FROM metric_13 for the two percentile numbers (constant across every row); SELECT goal_id, intervention_count FROM metric_13 for the per-goal distribution.
-- data_status: dark
WITH done_goals AS (
    SELECT project_id, goal_id FROM fact_goal WHERE outcome = 'done'
),
per_goal_events AS (
    SELECT g.project_id, g.goal_id,
           count(*) FILTER (WHERE e.kind = 'parked') AS park_count,
           count(*) FILTER (WHERE e.kind = 'ack') AS ack_count
    FROM done_goals g
    LEFT JOIN fact_event e
      ON e.project_id = g.project_id AND e.goal_id = g.goal_id AND e.kind IN ('parked', 'ack')
    GROUP BY g.project_id, g.goal_id
)
SELECT
    goal_id,
    park_count + ack_count AS intervention_count,
    quantile_cont(park_count + ack_count, 0.5)  OVER () AS p50_interventions,
    quantile_cont(park_count + ack_count, 0.85) OVER () AS p85_interventions
FROM per_goal_events
