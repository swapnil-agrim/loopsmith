-- name: Throughput forecast
-- question: When will the backlog land?
-- personas: manager, leadership
-- reliability_class: 1
-- guardrail: the band is the honest statement of what is known (spec, on #11) -- always render the burndown and this band on the same axes, never the band alone. Bootstrap Monte Carlo (2000 trials, 4-week horizon) over the trailing weekly throughput distribution; band = [p10,p90] of simulated 4-week totals (an 80% interval). Deterministic by construction via hash() -- a pure function of (trial_id, week_no), not random()+setseed(), whose scalar-subquery-hoisting trick was re-verified live and found NOT to reproduce byte-for-byte at 2000-trial scale (see #109 plan Design decision C). DARK METRIC: inherits #1's gap -- fact_goal.outcome/terminal_ts are 0/19 populated in real ingest today (#109 research dossier); fixture-verified only.
-- data_status: dark
WITH history AS (
    SELECT
        CAST(date_trunc('week', terminal_ts) AS DATE) AS week,
        count(*) AS done_count
    FROM fact_goal
    WHERE outcome = 'done'
    GROUP BY 1
),
hn AS (SELECT count(*) AS n FROM history),
hi AS (SELECT done_count, ROW_NUMBER() OVER (ORDER BY week) - 1 AS idx FROM history),
grid AS (
    SELECT t.trial_id, w.week_no, (hash(t.trial_id * 4 + w.week_no) % 1000003) / 1000003.0 AS r
    FROM range(2000) AS t(trial_id) CROSS JOIN range(4) AS w(week_no)
),
draws AS (
    SELECT g.trial_id, hi.done_count
    FROM grid g JOIN hn ON true
    JOIN hi ON hi.idx = CAST(FLOOR(g.r * hn.n) AS INTEGER)
),
totals AS (SELECT trial_id, SUM(done_count) AS total_done FROM draws GROUP BY trial_id)
SELECT
    quantile_cont(total_done, 0.10) AS p10_total_done,
    quantile_cont(total_done, 0.90) AS p90_total_done,
    count(*) AS trial_count
FROM totals
