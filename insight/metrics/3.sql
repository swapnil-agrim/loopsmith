-- name: Lead time for change
-- question: First commit to merge -- how long from a change starting to it landing?
-- personas: IC, manager, leadership, cross-functional
-- reliability_class: 1
-- guardrail: measured_count and total_count must always render together -- a squash-merge-convention repo has most merges' lead_time_seconds NULL (degraded=["lead_time_requires_network"], see git_reader.py), and NULL means unmeasured, never zero. Percentiles are computed only over the measured subset.
SELECT
    quantile_cont(lead_time_seconds, 0.5)  AS p50_seconds,
    quantile_cont(lead_time_seconds, 0.85) AS p85_seconds,
    count(*) FILTER (WHERE lead_time_seconds IS NOT NULL) AS measured_count,
    count(*) AS total_count
FROM fact_merge_lead_time
