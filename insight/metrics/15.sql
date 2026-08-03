-- name: Park taxonomy
-- question: Why does it stop?
-- personas: manager, cross-functional
-- reliability_class: 2
-- data_status: dark
-- guardrail: FUNCTIONALLY INERT TODAY (#243): ledger_writer.py's _write_event writes only six generic columns and never reason_class, so every real `insight ingest` row has reason_class NULL forever until that gap closes -- this view's logic is fully tested against fixtures that bypass the ledger/ingest pipeline entirely, same posture 22.sql already established. SPELLING, NOT AN ASSUMPTION SHARED WITH 14.sql: reads kind = 'park' (loop.py:203's real, live-verified `ledger.safe_append(sdlc_dir, "park", ...)`), never 14.sql's `kind = 'parked'` -- 12.sql's own guardrail already names this collision; the two views read two different, both currently under-populated, kinds. DENOMINATOR IS PER PARK EVENT, NOT PER PARKED GOAL (Decision 5, deliberate): reason_share is reason_count / total_park_count over every kind='park' row, un-deduplicated -- loop.py:189-204's own `_record` emits one park/reason_class event per call, so a goal re-parked three times for the SAME reason_class is three real, independent signals about which reasons recur (confirmed reachable this session), not one; collapsing to DISTINCT goal_id the way 14.sql's differently-scoped park_rate does would silently hide a goal that parks repeatedly behind one that parked once. Shares of the emitted reason_class rows therefore do NOT need to sum to 1.0 on their own -- the classified/total gap is visible directly via classified_park_count vs total_park_count on every row, never hidden by an invented 'unclassified' vocabulary value (spec's controlled vocabulary has no such member). ZERO PARKS VS UNCLASSIFIED PARKS, NOT THE SAME STATE: a project with zero kind='park' events produces zero rows (no phantom division-by-zero row, matching 22.sql's empty-store doctrine); a project whose parks exist but carry no reason_class yet (today's real shape) instead gets exactly ONE row via a LEFT JOIN from the project-level totals onto the per-reason_class breakdown, with reason_class/reason_count/reason_share all NULL but total_park_count > 0 and classified_park_count = 0 -- 'parks happened, taxonomy unmeasured' renders honestly instead of vanishing. PER-PROJECT PARTITIONING IS MANDATORY (#144's own PR was BLOCKED for cross-project leakage): every CTE below carries project_id through its own GROUP BY, following 22.sql's fix, not 14.sql's still-unpartitioned gap (named as adjacent debt, not fixed here). class1_count/class2_count/total_count/coverage_pct (insight/metrics/reliability.py's COVERAGE_DENOMINATOR_COLUMNS, pasted verbatim) are computed once per project over ALL of that project's kind='park' events and repeated identically across every reason_class row for that project -- the same long-form repetition trade 42.sql's own guardrail names explicitly for its per-project-per-flag shape: a consumer reading any one row for a project sees the whole project's coverage figure, not a fragment. view_rows[0] catalog residue (#253, named not fixed): dash/render.py's _metric_rows reads only the first (project_id, reason_class) row for its generic-catalog summary -- one arbitrary reason bucket of one arbitrary project, not an aggregate; same class of gap 22.sql's own guardrail already flagged.
WITH park_events AS (
    SELECT project_id, goal_id, reason_class, reliability_class
    FROM fact_event
    WHERE kind = 'park' AND project_id IS NOT NULL
),
totals AS (
    SELECT
        project_id,
        count(*) AS total_park_count,
        count(*) FILTER (WHERE reason_class IS NOT NULL) AS classified_park_count,
        count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
        count(*) AS total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4) AS coverage_pct
    FROM park_events
    GROUP BY project_id
),
reason_breakdown AS (
    SELECT project_id, reason_class, count(*) AS reason_count
    FROM park_events
    WHERE reason_class IS NOT NULL
    GROUP BY project_id, reason_class
)
SELECT
    totals.project_id,
    reason_breakdown.reason_class,
    reason_breakdown.reason_count,
    CASE
        WHEN reason_breakdown.reason_class IS NULL THEN NULL
        ELSE ROUND(reason_breakdown.reason_count * 1.0 / NULLIF(totals.total_park_count, 0), 4)
    END AS reason_share,
    totals.total_park_count,
    totals.classified_park_count,
    totals.class1_count,
    totals.class2_count,
    totals.total_count,
    totals.coverage_pct
FROM totals
LEFT JOIN reason_breakdown ON reason_breakdown.project_id = totals.project_id
ORDER BY totals.project_id, reason_breakdown.reason_class
