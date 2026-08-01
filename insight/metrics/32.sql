-- name: Handoff response time
-- question: How fast do we unblock each other?
-- personas: IC, manager
-- reliability_class: 1
-- guardrail: ack_ts - opened_ts, p50/p85, grouped by (project_id, area, priority) -- mirrors 2.sql's own population/good CTE split (Decision I): a negative-duration row (ack_ts < opened_ts, clock skew or a bad write, never legitimate) is excluded from the percentile population, with a broadcast excluded_negative_duration_count per group -- the identical fold-in fix 2.sql's own history needed (an all-excluded group still produces one visible row carrying the count, not silently vanishing). JOIN PREDICATE, NOT COPYABLE FROM 2.sql VERBATIM (post-review, BLOCKING-adjacent): 2.sql's `LEFT JOIN good ON true` is correct there only because both its CTEs collapse to a single global row (no GROUP BY at all). This view's two CTEs are grouped by (project_id, area, priority) and each yield MANY rows, so the join uses the explicit predicate `population.project_id = good.project_id AND population.area = good.area AND population.priority = good.priority` -- an `ON true` here would cross-join every population group against every unrelated good group, corrupting both the percentiles and sample_count with values pulled from other area/priority combinations. Only acked hand-offs (ack_ts IS NOT NULL) enter the population at all -- an unanswered one has nothing to measure here (metric 33's own concern). A (project_id, area, priority) group with zero acked hand-offs produces no row at all -- a structurally different kind of absence from "every acked row was negative-duration," not a synthesized ABSENT row, same posture 30.sql's guardrail already accepts for "never scanned at all." SEPARATE, BLOCKING FINDING FROM AN INDEPENDENT REVIEW, FIXED HERE (issue IS NOT NULL added to both population's and good's own WHERE below): fact_handoff has no goal_id column (store.py:126-138), so an issue-less hand-off's later ack lands as a second, orphaned row (issue NULL, opened_ts NULL, only ack_ts/ack_state populated) rather than merging into the original -- without this filter that orphaned row would enter population (ack_ts IS NOT NULL) and form its own phantom (area=NULL, priority=NULL) group with a NULL response time (date_diff against a NULL opened_ts is NULL, silently dropped from good but still counted in population), a group describing a hand-off that was never really measured. issue IS NOT NULL excludes both the issue-less hand-off and its orphaned ack from this view entirely -- a disclosed, real coverage gap, not a silent miscount; the real fix is a goal_id column on fact_handoff or a durable key in ledger_writer.py, out of scope here per Decision K. ALSO FIXED HERE, NON-BLOCKING: population's own total_acked_count column is removed -- redundant given this CTE's own WHERE ack_ts IS NOT NULL (every row it produces is already acked by construction) and never selected in the final SELECT list; dead weight, not a second measurement.
-- data_status: dark
WITH population AS (
    SELECT
        project_id,
        area,
        priority,
        count(*) FILTER (WHERE ack_ts < opened_ts) AS excluded_negative_duration_count
    FROM fact_handoff
    WHERE ack_ts IS NOT NULL AND issue IS NOT NULL
    GROUP BY project_id, area, priority
),
good AS (
    SELECT
        project_id,
        area,
        priority,
        count(*) AS sample_count,
        quantile_cont(date_diff('second', opened_ts, ack_ts), 0.5) AS p50_seconds,
        quantile_cont(date_diff('second', opened_ts, ack_ts), 0.85) AS p85_seconds
    FROM fact_handoff
    WHERE ack_ts IS NOT NULL AND ack_ts >= opened_ts AND issue IS NOT NULL
    GROUP BY project_id, area, priority
)
SELECT
    population.project_id,
    population.area,
    population.priority,
    good.p50_seconds,
    good.p85_seconds,
    COALESCE(good.sample_count, 0) AS sample_count,
    population.excluded_negative_duration_count
FROM population
LEFT JOIN good
    ON population.project_id = good.project_id
    AND population.area = good.area
    AND population.priority = good.priority
