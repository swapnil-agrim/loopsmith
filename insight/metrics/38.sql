-- name: Cross-area coupling
-- question: Architecture or people?
-- personas: manager, leadership, cross-functional
-- reliability_class: 1
-- guardrail: "share of goals needing a hand-off, trend" -- requires relating fact_goal rows to fact_handoff rows. fact_handoff has no goal_id (only issue INTEGER, store.py:126-138) but fact_goal DOES carry issue INTEGER (store.py:78-100), so the join key is fact_goal.issue = fact_handoff.issue, both nullable integers, project_id on both sides. Per (project_id, month): share = count(DISTINCT goal_id) FILTER (WHERE a matching fact_handoff.issue exists) / count(DISTINCT goal_id), month = CAST(date_trunc('month', created_ts) AS DATE) -- the CAST(... AS DATE) is required by test_metrics_date_trunc_guard.py, which fails any .sql file calling date_trunc( without an AS DATE) cast somewhere in the query BODY. A fact_goal row with created_ts IS NULL or issue IS NULL is EXCLUDED from the denominator (can't be placed on the trend line, and can't be matched to a hand-off by issue number) -- a documented coverage gap, same posture as metric_3's measured_count/total_count pattern, NOT a silent miscount as "uncoupled". NAMED, REAL GAP (Decision E): fact_handoff's issue-only key means this join can never relate a hand-off to a goal that has no GitHub issue number -- a local-file-goal-only project (no discovery.source: github) would see every goal excluded from this metric's denominator, not a correct 0% coupling; a consumer reading a small total_count without reading this guardrail could misinterpret "few goals with issue numbers" as "few goals". No status/severity_rank (Decision F -- a percentage + trend series, not a threshold gate; the spec's own Layer-3 table gives no pass/warn/fail language for it).
-- data_status: dark
WITH goal_months AS (
    SELECT
        project_id,
        goal_id,
        issue,
        CAST(date_trunc('month', created_ts) AS DATE) AS month
    FROM fact_goal
    WHERE created_ts IS NOT NULL AND issue IS NOT NULL
),
coupled AS (
    SELECT DISTINCT project_id, issue
    FROM fact_handoff
    WHERE issue IS NOT NULL
)
SELECT
    goal_months.project_id,
    goal_months.month,
    count(DISTINCT goal_months.goal_id) FILTER (
        WHERE EXISTS (
            SELECT 1 FROM coupled
            WHERE coupled.project_id = goal_months.project_id
                AND coupled.issue = goal_months.issue
        )
    ) AS coupled_count,
    count(DISTINCT goal_months.goal_id) AS total_count,
    count(DISTINCT goal_months.goal_id) FILTER (
        WHERE EXISTS (
            SELECT 1 FROM coupled
            WHERE coupled.project_id = goal_months.project_id
                AND coupled.issue = goal_months.issue
        )
    )::DOUBLE / count(DISTINCT goal_months.goal_id) AS coupled_share
FROM goal_months
GROUP BY goal_months.project_id, goal_months.month
ORDER BY goal_months.project_id, goal_months.month
