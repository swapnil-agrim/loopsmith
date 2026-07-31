-- name: Merge frequency
-- question: Deploy proxy -- how often does work actually land?
-- personas: IC, manager, leadership, cross-functional
-- reliability_class: 1
-- guardrail: one row per git-facts/v1 collector snapshot, not a week-bucketed trend -- avoids re-deriving date_trunc buckets (see #108's date_trunc duckdb-version note). A squash-merge-convention repo will read low for the same structural reason as #3, not because merges are actually rare.
SELECT
    collected_ts,
    window_since_days,
    window_commit_count,
    window_merge_count,
    ROUND(window_merge_count * 1.0 / NULLIF(window_since_days, 0), 4) AS merges_per_day
FROM fact_collector_pack
WHERE schema = 'git-facts/v1'
