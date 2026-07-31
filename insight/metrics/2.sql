-- name: Cycle time
-- question: How long does a goal take?
-- personas: IC, manager, leadership, cross-functional
-- reliability_class: 1
-- guardrail: percentiles never rendered as means; the distribution is right-skewed (spec §6). DARK METRIC: claimed_ts/terminal_ts are unpopulated by any shipped ingest writer as of #109 (0/19 real goals, per #109 research dossier) -- this view returns rows only once a ledger-persistence writer lands; a fixture-green test is not evidence of a live dashboard number.
-- data_status: dark
-- Consumer contract: SELECT DISTINCT p50_seconds, p85_seconds FROM metric_2 for the two percentile numbers (constant across every row); SELECT goal_id, cycle_time_seconds FROM metric_2 for the raw scatter series.
-- Negative-duration decision (post-review fold-in): a row with terminal_ts < claimed_ts (clock skew or a bad write -- verified live to silently produce negative percentiles otherwise) is EXCLUDED, not surfaced as a degraded count the way metric_3 separates measured_count/total_count. Those are different in kind: metric_3's NULL is a legitimate, expected, common state (an unmeasured squash-merge row a real collector reports today), worth counting so a dashboard doesn't lie about coverage. A negative cycle time is never legitimate -- terminal_ts can't precede claimed_ts for a real "done" goal -- so it is a data-integrity bug in whatever wrote it, not a fact about flow; including it would silently corrupt the percentiles/scatter for every genuinely-measured goal in the same result set, which is the one thing this metric must never do.
SELECT
    goal_id,
    date_diff('second', claimed_ts, terminal_ts) AS cycle_time_seconds,
    quantile_cont(date_diff('second', claimed_ts, terminal_ts), 0.5)  OVER () AS p50_seconds,
    quantile_cont(date_diff('second', claimed_ts, terminal_ts), 0.85) OVER () AS p85_seconds
FROM fact_goal
WHERE outcome = 'done' AND claimed_ts IS NOT NULL AND terminal_ts IS NOT NULL
    AND terminal_ts >= claimed_ts
