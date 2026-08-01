-- name: Adoption & flag correlation
-- question: Which flags correlate with which outcomes?
-- personas: manager, leadership
-- reliability_class: 1
-- guardrail: COMPARISON, NOT CORRELATION (Decision 2, verbatim requirement): this view supports comparison, not causal or statistical correlation -- N is the project count (SELECT count(DISTINCT project_id) FROM dim_project). A Pearson/point-biserial coefficient computed over the number of projects a real store actually holds (this repo: 1 dim_project row, verified live; a realistic rollup: a handful) is statistically meaningless and would manufacture false confidence, the exact "fabricating a number poisons the one thing this product sells" failure mode the spec's own Effectiveness paragraph names for a different metric. LONG FORM (one row per project x flag, not flags-as-columns): generalises to a new flag with zero schema change, at the one-time readability cost of pivoting 8 rows per project instead of reading 8 columns -- the same trade 24.sql made choosing a per-gate-row UNION ALL over side-by-side gate columns. EIGHT FLAGS, ALL TEXT REGARDLESS OF JSON TYPE: parallel.enabled, ledger.enabled, verify.enforce, work.require_review, work.auto_merge, gates.hard_plan_gate.enabled, review.independent, knowledge_graph.enabled -- two of these (work.require_review, work.auto_merge) are STRING-valued in this repo's own real config, not boolean, which is why flag_value is extracted via json_extract_string (returns unquoted VARCHAR for any JSON scalar type) and never a BOOLEAN cast (which would raise or null out on the two string-valued flags) -- a consumer must not assume every flag_value is 'true'/'false'. MISSING-KEY/MISSING-CONFIG COLLAPSE, DELIBERATE (Decision 2a): json_extract_string(NULL, path) and json_extract_string(config, missing_path) both return NULL -- "this project has never had a config snapshot ingested" and "this project's config was ingested but lacks this specific key" collapse to the SAME flag_value IS NULL; a consumer needing the finer distinction queries dim_project.config_json IS NULL directly. WHAT THIS FORBIDS ABSOLUTELY: flag_value must never render 'false' for a flag whose key is genuinely absent -- a missing key is not a disabled flag; there is no COALESCE(..., 'false') anywhere in this query. TWO MEANINGS OF "ADOPTION", NOT THE SAME (Decision 6): dim_project.adopted (BOOLEAN, issue #106) means "was .sdlc/ present and readable for this repo at ingest time" -- repo-level onboarding. This metric's own "adoption" means which LoopSmith features/flags a project has turned on. This view reads config_json and the eight flags, NOT dim_project.adopted -- the two concepts share the English word and nothing else. OUTCOME AGGREGATES, LIVE TABLES ONLY (Decision 2b): fact_goal.outcome is deliberately NOT used -- it is NULL on all 19 rows in this repo's real ingest, dark for the same reason 14.sql already carries that label. Instead: fact_merge_lead_time (merge_total_count, merge_measured_count FILTER WHERE lead_time_seconds IS NOT NULL, merge_lead_time_p50_seconds), fact_pr_check (pr_check_total_count FILTER WHERE conclusion IS NOT NULL -- a check still IN_PROGRESS/QUEUED has no conclusion yet and must not count as measured either way; pr_check_pass_rate over UPPER(conclusion) = 'SUCCESS'), fact_pr_review (pr_review_total_count FILTER WHERE verdict IS NOT NULL; pr_review_approval_rate over UPPER(verdict) IN ('APPROVED', 'APPROVE')). NULL/ZERO SPLIT, STRONGER THAN A NAIVE COALESCE: each outcome CTE is GROUP BY project_id then LEFT JOINed onto the project_flags spine -- a project with ZERO rows in a fact table produces NO row in that CTE, so *_total_count renders NULL ("this table has never been touched for this project"), a structurally different fact from *_total_count > 0 with *_measured_count/pass_rate showing rows exist but were unresolvable -- never conflated, never COALESCEd to 0. TWO VERDICT VOCABULARIES, BOTH REAL, NAMED EXPLICITLY (Decision 2b): pr_review_approval_rate counts UPPER(verdict) IN ('APPROVED', 'APPROVE') -- 'APPROVED' is the native GitHub review state (gh_reader.py:276); 'APPROVE' (no trailing -D) is this project's own loopsmith:approve PR-comment verdict (gh_reader.py:207-218, the marker split on ':' and kept verbatim, lower-case). This repo's own real PRs carry ZERO native reviews (GitHub forbids self-approval; the loop opens every PR under one account, per gh_reader.py's own module docstring) -- a single-vocabulary UPPER(verdict) = 'APPROVED' comparison would silently zero this repo's own real approval rate. A future flag or table addition must not quietly regress to comparing only the native spelling. LONG-FORM REPETITION COST, NAMED (Decision 2): each project's outcome aggregates repeat identically across its eight flag rows -- a deliberate trade for schema-free flag extension, not an oversight. NEITHER dark NOR proxy (Decision 5): all four sources (dim_project.config_json, fact_merge_lead_time, fact_pr_check, fact_pr_review) are live in this repo's real ingest today (1/70/134/36 rows, verified live this session) -- the one metric in this pair that is dashboard-real on day one. NO status/severity_rank (Decision 3), NO window function anywhere (Decision 8) -- every row carries a non-NULL project_id via the dim_project CROSS JOIN flags spine (Decision 4).
WITH flags(flag_key) AS (
    VALUES
        ('parallel.enabled'),
        ('ledger.enabled'),
        ('verify.enforce'),
        ('work.require_review'),
        ('work.auto_merge'),
        ('gates.hard_plan_gate.enabled'),
        ('review.independent'),
        ('knowledge_graph.enabled')
),
project_flags AS (
    SELECT
        dp.project_id,
        f.flag_key,
        json_extract_string(dp.config_json, '$.' || f.flag_key) AS flag_value
    FROM dim_project dp
    CROSS JOIN flags f
),
merge_stats AS (
    SELECT
        project_id,
        count(*) AS merge_total_count,
        count(*) FILTER (WHERE lead_time_seconds IS NOT NULL) AS merge_measured_count,
        ROUND(
            quantile_cont(lead_time_seconds, 0.5) FILTER (WHERE lead_time_seconds IS NOT NULL),
            2
        ) AS merge_lead_time_p50_seconds
    FROM fact_merge_lead_time
    GROUP BY project_id
),
check_stats AS (
    SELECT
        project_id,
        count(*) FILTER (WHERE conclusion IS NOT NULL) AS pr_check_total_count,
        ROUND(
            count(*) FILTER (WHERE UPPER(conclusion) = 'SUCCESS') * 1.0
            / NULLIF(count(*) FILTER (WHERE conclusion IS NOT NULL), 0),
            4
        ) AS pr_check_pass_rate
    FROM fact_pr_check
    GROUP BY project_id
),
review_stats AS (
    -- 'APPROVED' = native GitHub review state (gh_reader.py:276); 'APPROVE' = this project's own
    -- loopsmith:approve PR-comment verdict (gh_reader.py:207-218, lower-cased then split on ':').
    -- Both real, verified live this session by reading gh_reader.py directly -- see the guardrail
    -- above and Decision 2b.
    SELECT
        project_id,
        count(*) FILTER (WHERE verdict IS NOT NULL) AS pr_review_total_count,
        ROUND(
            count(*) FILTER (WHERE UPPER(verdict) IN ('APPROVED', 'APPROVE')) * 1.0
            / NULLIF(count(*) FILTER (WHERE verdict IS NOT NULL), 0),
            4
        ) AS pr_review_approval_rate
    FROM fact_pr_review
    GROUP BY project_id
)
SELECT
    pf.project_id,
    pf.flag_key,
    pf.flag_value,
    ms.merge_total_count,
    ms.merge_measured_count,
    ms.merge_lead_time_p50_seconds,
    cs.pr_check_total_count,
    cs.pr_check_pass_rate,
    rs.pr_review_total_count,
    rs.pr_review_approval_rate
FROM project_flags pf
LEFT JOIN merge_stats ms USING (project_id)
LEFT JOIN check_stats cs USING (project_id)
LEFT JOIN review_stats rs USING (project_id)
ORDER BY pf.project_id, pf.flag_key
