-- name: Change failure rate (proxy)
-- question: Did shipping break things?
-- personas: IC, manager, leadership, cross-functional
-- reliability_class: 1
-- proxy: true
-- guardrail: this counts revert/fixup/squash/amend-shaped commit subjects (alignment-collect d7), not actual production incidents -- a rebase-and-fixup-heavy workflow can score artificially high with zero real breakage, and a team that force-pushes away its fixups can score artificially low. Render adjacent to #1 (throughput) per the spec's throughput/quality pairing rule. A `fixes:` linking convention would upgrade this from proxy to exact (spec #5, CONV) -- not built here.
SELECT
    collected_ts,
    window_commit_count,
    CAST(json_extract(raw_payload, '$.dimensions.d7.repeated_revert_or_fixup_count') AS INTEGER)
        AS repeated_revert_or_fixup_count,
    ROUND(
        CAST(json_extract(raw_payload, '$.dimensions.d7.repeated_revert_or_fixup_count') AS INTEGER) * 1.0
            / NULLIF(window_commit_count, 0),
        4
    ) AS change_failure_rate
FROM fact_collector_pack
WHERE schema = 'alignment-collect/v1'
