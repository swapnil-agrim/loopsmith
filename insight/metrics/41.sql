-- name: Portfolio table
-- question: How does each project compare on throughput, park rate, and gate coverage?
-- personas: manager, leadership
-- reliability_class: 1
-- proxy: true
-- data_status: mixed
-- guardrail: DATA_STATUS: MIXED, NAMED PER COLUMN, NOT ASSERTED BLINDLY -- done_count and park_rate re-derive from fact_goal/fact_event, the SAME dark tables 1.sql/14.sql already declare dark (fact_goal.outcome is NULL on all 19 rows this repo's real ingest has today, per #109's research dossier); gate_coverage_pct reads fact_collector_pack, which is LIVE (5 rows this session) and already carries 24.sql's own proxy:true label for the different reason of per-pack-not-per-goal grain, inherited here since this view duplicates that same aggregation. DUPLICATION, NAMED NOT HIDDEN (Decision 1): 1.sql (Throughput) and 14.sql (Park rate) have NO project_id anywhere in their SELECT/GROUP BY -- 1.sql is GROUP BY 1 on week only, globally; 14.sql has no GROUP BY at all, one global row -- both pinned by their own exact-shape tests, so there is no join key to reuse them against a per-project spine without editing them first, which would drag two already-shipped, out-of-scope files (and their pinned tests) into this story. This view therefore RE-DERIVES the park-rate CTE (terminal goals LEFT JOIN parked goals) and the gate row/status logic (denominator-empty checks, PASS threshold) INLINE, word-for-word in spirit with 14.sql/24.sql, just GROUP BY project_id instead of global/pack-grain -- the follow-up that would remove this duplication is to give 1.sql and 14.sql their own project_id column and GROUP BY in a dedicated story, updating test_metric_1_throughput.py's and test_metric_14_park_rate.py's own pinned exact-shape assertions at the same time; not attempted here. WINDOW, ALL THREE COLUMNS (Decision 7): done_count, park_rate, and gate_coverage_pct are every one of them ALL-TIME/CUMULATIVE -- gate_coverage_pct in particular is NOT a "current state" tile the way 26.sql's own current-tile-only design is; it aggregates across EVERY historical alignment-collect/v1 pack for that project, not just the latest one, deliberately avoiding the identical-collected_ts tie-break 30.sql's own guardrail already carries as an accepted, unresolved limitation. A consumer wanting "gate coverage right now" must query metric_24 directly, filtered to that project's latest collected_ts. MEASURED/ABSENT SYMMETRY (mirrors 3.sql's own "measured_count and total_count must always render together" rule): gate_coverage_pct is NULL whenever gates_measured_count is 0 OR itself NULL, never a false 0% that could be misread as "gates ran and all failed" -- and the two NULL causes are different facts a consumer must not merge: gates_measured_count = 0 means packs exist for this project but every gate in them was absent; gates_measured_count IS NULL means the project was never scanned at all (zero fact_collector_pack rows), the dim_project LEFT JOIN spine (Decision 4) still renders the row, just with every gate column NULL. SCALE ASYMMETRY, INHERITED NOT INTRODUCED: park_rate is a 0-1 fraction (14.sql's own scale) while gate_coverage_pct is a 0-100 percentage (24.sql's own scale) -- do not compare the two columns on the same axis. PASS-ONLY SHARE: gate_pass_count/gate_coverage_pct count PASS only -- WARN and FAIL both count as non-pass in the share; a consumer wanting the WARN/FAIL split queries metric_24 directly for that project. NULL-NOT-ZERO THROUGHOUT (Decision 2b's split, applied identically here): done_count is NULL via the LEFT JOIN, never COALESCEd to 0 -- a project with genuinely zero 'done' goals and a project whose fact_goal rows exist but are entirely non-terminal are structurally different and must not both render 0. NO status/severity_rank (Decision 3): a descriptive cross-project comparison table, not a gate -- inventing a cross-project pass/fail threshold here would compound one undocumented calibration into a second, less justified one on top of 24.sql's own. NO window function anywhere in this file (Decision 8) -- every aggregation is a plain GROUP BY project_id, so the entire unpartitioned-window bug class PR #186 exists to document is structurally impossible here, not merely guarded against. gate_rows REUSES 24.sql's own two denominator checks exactly: plan_gate is ABSENT when COALESCE(commits_with_source, 0) <= 0 (24.sql:24); review_gate is ABSENT when COALESCE(window_commit_count, 0) <= 0 -- window_commit_count is a real fact_collector_pack COLUMN (24.sql:29's own choice, following 5.sql/20.sql's precedent of reading it as a table column rather than re-extracting via JSON), NOT a raw_payload-derived approximation. RELIABILITY-CLASS ENFORCEMENT (#114, spec line 563: "a NOW metric must not read any reliability_class=2 row"): parked now filters `AND reliability_class = 1`, a bare equality not `!= 2` -- a NULL reliability_class is excluded identically to a 2, not grandfathered as trusted, because a real ingested fact_event row is NEVER NULL (ledger_writer.py always tags it); a NULL seen here is evidence of an ingest-path regression, not a legitimate legacy row.
WITH throughput AS (
    SELECT project_id, count(*) AS done_count
    FROM fact_goal
    WHERE outcome = 'done'
    GROUP BY project_id
),
terminal AS (
    SELECT project_id, goal_id FROM fact_goal WHERE outcome IN ('done', 'failed')
),
parked AS (
    SELECT DISTINCT project_id, goal_id FROM fact_event WHERE kind = 'parked' AND reliability_class = 1
),
park AS (
    SELECT
        t.project_id,
        count(*) FILTER (WHERE p.goal_id IS NOT NULL) AS parked_terminal_count,
        count(*) AS terminal_count,
        ROUND(count(*) FILTER (WHERE p.goal_id IS NOT NULL) * 1.0 / NULLIF(count(*), 0), 4) AS park_rate
    FROM terminal t
    LEFT JOIN parked p USING (project_id, goal_id)
    GROUP BY t.project_id
),
gate_packs AS (
    SELECT
        project_id,
        window_commit_count,
        CAST(json_extract(raw_payload, '$.dimensions.d1.commits_with_source') AS INTEGER) AS commits_with_source,
        CAST(json_extract(raw_payload, '$.dimensions.d1.plan_existed_pct') AS INTEGER) AS plan_existed_pct,
        CAST(json_extract(raw_payload, '$.dimensions.d5.commits_with_review_pct') AS INTEGER) AS commits_with_review_pct
    FROM fact_collector_pack
    WHERE schema = 'alignment-collect/v1'
),
gate_rows AS (
    SELECT project_id, (COALESCE(commits_with_source, 0) <= 0) AS absent, plan_existed_pct AS pct
    FROM gate_packs
    UNION ALL
    SELECT project_id, (COALESCE(window_commit_count, 0) <= 0) AS absent, commits_with_review_pct AS pct
    FROM gate_packs
),
gate AS (
    SELECT
        project_id,
        count(*) FILTER (WHERE NOT absent AND pct IS NOT NULL) AS gates_measured_count,
        count(*) FILTER (WHERE absent OR pct IS NULL) AS gates_absent_count,
        count(*) FILTER (WHERE NOT absent AND pct >= 80) AS gate_pass_count,
        ROUND(
            100.0 * count(*) FILTER (WHERE NOT absent AND pct >= 80)
            / NULLIF(count(*) FILTER (WHERE NOT absent AND pct IS NOT NULL), 0),
            2
        ) AS gate_coverage_pct
    FROM gate_rows
    GROUP BY project_id
)
SELECT
    dp.project_id,
    th.done_count,
    pk.parked_terminal_count,
    pk.terminal_count,
    pk.park_rate,
    gt.gates_measured_count,
    gt.gates_absent_count,
    gt.gate_pass_count,
    gt.gate_coverage_pct
FROM dim_project dp
LEFT JOIN throughput th USING (project_id)
LEFT JOIN park pk USING (project_id)
LEFT JOIN gate gt USING (project_id)
ORDER BY dp.project_id
