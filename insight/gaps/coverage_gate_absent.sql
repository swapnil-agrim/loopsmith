-- name: Applicable gate never measured
-- class: Coverage
-- metric: 24
-- action: repair the collector or widen the git window so the gate's own denominator becomes nonzero
-- severity: WARN
-- guardrail: DUPLICATES 24.sql'S OWN packs/gates CTEs INLINE, NOT `FROM metric_24` (Design decision 2, .sdlc/plans/117.md) -- gap rules are never registered as DuckDB views (load_gap_rules has no `conn` parameter, #116 Design decision 7) and load_gap_rules never calls load_metrics, so reading a `metric_24` view would be an unenforced same-connection ordering dependency nothing in this codebase's loader machinery protects; 41.sql's own precedent (duplicating 24.sql's gate CTEs and 14.sql's park CTE inline) is followed here instead. degraded_collector/degraded_adapter are carried through below as INFORMATIONAL, PASSTHROUGH columns only -- NEVER used for gating in this rule's WHERE clause. Gating on the flat degraded[] array is exactly the bug 24.sql's own guardrail documents finding and fixing: a pack degraded only by no_test_command would wrongly render BOTH gates absent even though d1/d5 measured fine. That is a DIFFERENT, SEPARATE rule's job -- coverage_degraded_collector.sql consumes degraded[] directly per spec section B.3.1 (Design decision 4) -- this rule and that one are two separate mechanisms, cited by name so a future reader does not assume either one already covers the other. NO goal_id IN EVIDENCE -- this view's own grain is per collector pack x gate, matching 24.sql's own documented limitation that the per-goal emitter is explicitly not built in this story (24.sql's own guardrail, cited, not re-litigated). POPULATION IS SCOPED TO schema = 'alignment-collect/v1' ONLY -- unlike coverage_degraded_collector's deliberately all-schema population (that rule's own guardrail explains why); a store that has never run the alignment-collect collector at all has nothing for THIS rule to check, so it reports the engine's own ABSENT, not a WARN.
-- population: SELECT count(*) FROM fact_collector_pack WHERE schema = 'alignment-collect/v1'
WITH packs AS (
    SELECT
        project_id,
        collected_ts,
        window_commit_count,
        degraded_collector,
        degraded_adapter,
        CAST(json_extract(raw_payload, '$.dimensions.d1.commits_with_source') AS INTEGER) AS commits_with_source
    FROM fact_collector_pack
    WHERE schema = 'alignment-collect/v1'
),
gates AS (
    SELECT project_id, collected_ts, degraded_collector, degraded_adapter,
           'plan_gate' AS gate,
           (COALESCE(commits_with_source, 0) <= 0) AS denominator_empty
    FROM packs
    UNION ALL
    SELECT project_id, collected_ts, degraded_collector, degraded_adapter,
           'review_gate' AS gate,
           (COALESCE(window_commit_count, 0) <= 0) AS denominator_empty
    FROM packs
)
SELECT project_id, collected_ts, gate, degraded_collector, degraded_adapter
FROM gates
WHERE denominator_empty
ORDER BY project_id, collected_ts, gate
