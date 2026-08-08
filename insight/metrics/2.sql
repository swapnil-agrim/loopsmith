-- name: Cycle time
-- question: How long does a goal take?
-- personas: IC, manager, leadership, cross-functional
-- reliability_class: 1
-- guardrail: percentiles never rendered as means; the distribution is right-skewed (spec §6). FORMERLY DARK (cleared 2026-08-08, see the end of this line): claimed_ts/terminal_ts are unpopulated by any shipped ingest writer as of #109 (0/19 real goals, per #109 research dossier) -- this view returns rows only once a ledger-persistence writer lands; a fixture-green test is not evidence of a live dashboard number. VERIFIED 2026-08-08, LABEL CLEARED: the `data_status: dark` above this line is removed. Its stated condition was claimed_ts/terminal_ts being unpopulated (0/19 at #109). Measured on this repo's own store after the ledger-persistence writer and the events-stream payload fix landed: 73 goals, 71 with claimed_ts, 69 with terminal_ts, and 65 rows carrying a real cycle_time_seconds. The dashboard number is now evidence-backed, not fixture-green. The rest of this guardrail still stands -- percentiles are still never means, and the distribution is still right-skewed.
-- Consumer contract: SELECT DISTINCT p50_seconds, p85_seconds, excluded_negative_duration_count, total_count FROM metric_2 for the two percentile numbers plus the exclusion count and population size (all constant across every row); SELECT goal_id, cycle_time_seconds FROM metric_2 for the raw scatter series (goal_id IS NULL identifies the all-excluded placeholder row, see below).
-- Negative-duration decision (post-review fold-in): a row with terminal_ts < claimed_ts (clock skew or a bad write -- verified live to silently produce negative percentiles otherwise) is EXCLUDED, not surfaced as a degraded count the way metric_3 separates measured_count/total_count. Those are different in kind: metric_3's NULL is a legitimate, expected, common state (an unmeasured squash-merge row a real collector reports today), worth counting so a dashboard doesn't lie about coverage. A negative cycle time is never legitimate -- terminal_ts can't precede claimed_ts for a real "done" goal -- so it is a data-integrity bug in whatever wrote it, not a fact about flow; including it would silently corrupt the percentiles/scatter for every genuinely-measured goal in the same result set, which is the one thing this metric must never do. Fold-in (round 3): the exclusion was silent -- if a bad writer someday drops half the goals, a consumer would see clean percentiles with zero signal that anything was dropped. Added `excluded_negative_duration_count`, a broadcast constant (same pattern as p50_seconds/p85_seconds).
-- Fold-in (round 4): the round-3 count itself went silent in the ONE scenario it exists for -- with 100% of the done population negative-duration, the outer WHERE dropped every row, so the CROSS JOIN's broadcast value vanished along with the percentiles, making "all data is corrupt" render identically to "no data has arrived yet" (an empty table). Restructured as population (the full done-and-both-timestamps-present count, split into good/excluded) LEFT JOINed to the good rows: a genuinely empty table still yields zero rows (population itself has none, unchanged); an all-excluded population now yields exactly one row (goal_id/cycle_time_seconds/percentiles NULL, excluded_negative_duration_count > 0) instead of silently disappearing -- the two cases are now distinguishable by reading excluded_negative_duration_count on the one surviving row.
-- Fold-in (round 5, issue #217): population.total_count was computed by the round-4 CTE but never selected -- excluded_negative_duration_count (a legitimate 0 whenever nothing was excluded) was left as the ONLY `_count`-suffixed column reaching insight.dash.render._measured(), which takes the MAX across all such columns as its "was anything measured" signal. On this repo's own real post-#217 ingest (~36 genuinely-measured done goals, zero negative-duration), that made _measured() return 0 and the dashboard render "no data yet" over 36 real rows -- the inverted-dishonesty failure this product exists to prevent. total_count is added to the final SELECT below so _measured() has a real population signal regardless of how many rows were excluded; nothing new is computed, it is round 4's own population.total_count, finally selected.
WITH population AS (
    SELECT
        count(*) FILTER (WHERE terminal_ts < claimed_ts) AS excluded_negative_duration_count,
        count(*) AS total_count
    FROM fact_goal
    WHERE outcome = 'done' AND claimed_ts IS NOT NULL AND terminal_ts IS NOT NULL
),
good AS (
    SELECT
        goal_id,
        date_diff('second', claimed_ts, terminal_ts) AS cycle_time_seconds,
        quantile_cont(date_diff('second', claimed_ts, terminal_ts), 0.5)  OVER () AS p50_seconds,
        quantile_cont(date_diff('second', claimed_ts, terminal_ts), 0.85) OVER () AS p85_seconds
    FROM fact_goal
    WHERE outcome = 'done' AND claimed_ts IS NOT NULL AND terminal_ts IS NOT NULL
        AND terminal_ts >= claimed_ts
)
SELECT
    good.goal_id,
    good.cycle_time_seconds,
    good.p50_seconds,
    good.p85_seconds,
    population.excluded_negative_duration_count,
    -- issue #217: selected so insight.dash.render._measured() has a real population signal --
    -- without it, excluded_negative_duration_count (legitimately 0 when nothing was excluded)
    -- was the only `_count` column reaching that heuristic, and the dashboard reported "no data
    -- yet" over genuinely-measured rows. See this file's own round-5 fold-in comment above.
    population.total_count
FROM population
LEFT JOIN good ON true
WHERE population.total_count > 0
