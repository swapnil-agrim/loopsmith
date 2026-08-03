-- name: Budget-exhaustion rate
-- question: Are budgets mis-set — did the run stop on budget or an empty backlog?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: STRUCTURALLY MORE SPECULATIVE THAN 15/16/17/18.sql, NAMED HONESTLY: this reads kind='run_stop', a CURRENTLY NONEXISTENT event kind -- ledger.py's KINDS/EVENT_KINDS and loop.py's own _EMIT_KINDS have no "run"-level kind at all today, so unlike 15/16/17/18 (real, schema-defined-and-currently-unpopulated columns on an EXISTING kind, blocked only by #243's dropped-column bug), #243 alone can never populate this view -- a NEW writer at run_loop's BUDGET/DONE branches (loop.py) is required, tracked as a named follow-up, not opened here (see Out of scope). REJECTED ALTERNATIVE, CONSIDERED NOT USED: reusing per-goal park events' reason_class='budget' as a proxy for "the run stopped on budget" -- rejected because loop.py's own _reason_class docstring states outright that 'budget' is PROVABLY unreachable through that classifier (run_loop's BUDGET branch breaks before _record/_reason_class is ever called on that path), so this would silently measure per-goal park frequency for a reason that can never fire, not run-level budget exhaustion -- exactly the "quietly measures something different from what it claims" failure mode this issue's own instructions forbid. Also rejected: fact_goal's terminal outcome, which has no "why did the RUN containing this goal stop" concept at all. GOAL_ID IS NULL IS THE RUN-NOT-GOAL SCOPING, MADE OBSERVABLE: a run-stop event is scoped to a project, not any one goal within it (fact_event's own grain is per-goal; this reads run_stop AND goal_id IS NULL AND reason_class IN ('budget','backlog-empty') specifically so a park event on a real goal_id, even one carrying reason_class='budget', can never be swept in here -- this is what actually excludes the rejected alternative above, not just prose). "backlog-empty" IS NOT YET A BLESSED REASON_CLASSES SPELLING EITHER: ledger.py's REASON_CLASSES vocabulary today has 'budget' (for a different, per-goal-park question) but no 'backlog-empty' member at all -- a future author landing the real run_stop writer must add both the kind and reconcile this spelling before this view can ever return a row against real data. NO INVENTED MAGNITUDE THRESHOLD (mirrors 16.sql's own Decision 1, the same class of fabrication rejected here for the same reason): the project's budget.max_iterations/max_tokens/max_minutes config (exact keys verified against loop.py's _budget_spent) is exposed as DESCRIPTIVE CONTEXT COLUMNS alongside the rate, never as a synthesized PASS/FAIL -- inventing a magnitude cutoff (e.g. "FAIL if rate > 0.5") would need an arbitrary literal spec gives no basis for; the judgment "are budgets mis-set" stays with the reader. Malformed config_json DEGRADES PER-PROJECT, DOES NOT CRASH THE VIEW: project_budget_config wraps every json_extract in the same json_valid/TRY_CAST guard 16.sql's own Amendment A already established, so one bad config_json row degrades only THAT project's three columns to NULL, never a raised exception for the whole view. stop_counts IS GROUP-BY-AGGREGATED BEFORE THE LEFT JOIN to project_budget_config, so the join never interacts with the aggregation -- this is what makes the empty-store shape correct: a project with zero run_stop rows never enters stop_counts at all, so it never appears in the final SELECT regardless of whether it has a dim_project row. No status/severity_rank column, deliberately, matching 22.sql's/15.sql's own precedent (neither has one): this is structurally a rate (like 22's multiplier), not a threshold-gated flag (like 16's cap-proximity alarm). reliability_class: 2, a real decision: a future run_stop writer would, by every existing precedent in this codebase (park/spend/gate all route through stream=ledger.EVENTS), be agent/loop-emitted rather than Python-lifecycle-guaranteed, so class 2 is the honest, consistent choice, declared explicitly rather than inherited by copy-paste.
WITH run_stops AS (
    SELECT project_id, reason_class, reliability_class
    FROM fact_event
    WHERE kind = 'run_stop' AND goal_id IS NULL AND project_id IS NOT NULL
      AND reason_class IN ('budget', 'backlog-empty')
),
stop_counts AS (
    SELECT
        project_id,
        count(*) FILTER (WHERE reason_class = 'budget') AS budget_stop_count,
        count(*) FILTER (WHERE reason_class = 'backlog-empty') AS backlog_empty_stop_count,
        count(*) AS total_stop_count,
        ROUND(count(*) FILTER (WHERE reason_class = 'budget') * 1.0
            / NULLIF(count(*), 0), 4) AS budget_exhaustion_rate,
        count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
        count(*) AS total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0
            / NULLIF(count(*), 0), 4) AS coverage_pct
    FROM run_stops
    GROUP BY project_id
),
project_budget_config AS (
    -- mirrors 16.sql's Amendment A json_valid/TRY_CAST guard exactly: a malformed
    -- config_json row degrades THAT project's three columns to NULL, never raises for
    -- the whole view. Keys verified against loop.py's _budget_spent.
    SELECT
        project_id,
        CASE WHEN json_valid(config_json)
            THEN TRY_CAST(json_extract(config_json, '$.budget.max_iterations') AS BIGINT)
            ELSE NULL END AS max_iterations,
        CASE WHEN json_valid(config_json)
            THEN TRY_CAST(json_extract(config_json, '$.budget.max_tokens') AS BIGINT)
            ELSE NULL END AS max_tokens,
        CASE WHEN json_valid(config_json)
            THEN TRY_CAST(json_extract(config_json, '$.budget.max_minutes') AS BIGINT)
            ELSE NULL END AS max_minutes
    FROM dim_project
)
SELECT
    sc.project_id,
    sc.budget_stop_count,
    sc.backlog_empty_stop_count,
    sc.total_stop_count,
    sc.budget_exhaustion_rate,
    pbc.max_iterations, pbc.max_tokens, pbc.max_minutes,
    sc.class1_count, sc.class2_count, sc.total_count, sc.coverage_pct
FROM stop_counts sc
LEFT JOIN project_budget_config pbc ON pbc.project_id = sc.project_id
ORDER BY sc.project_id
