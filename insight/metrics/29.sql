-- name: Retro grade mix
-- question: Intent vs shipped
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: DEEP-DARK (#147 Design decision 1): no `grade` column existed on fact_event before this story. fact_goal.retro_grade EXISTS but is NOT used here -- it has ZERO writers anywhere in insight/ingest/ (neither artifact_reader.py's _GOAL_UPSERT_SQL nor goal_lifecycle.py's _LIFECYCLE_UPSERT_SQL name it), and more fundamentally it is a SINGLE SCALAR PER GOAL, structurally incapable of expressing "trend" (spec's own word) -- only an event-level read with its own ts per emission can plot a mix changing over time, and a goal re-graded more than once correctly contributes more than one point under this reading, a deliberate residue, not a bug. RETRO_GRADES = (achieved, partial, diverged) IS A CLOSED VOCABULARY ENFORCED AT WRITE TIME (ledger.py's EVENT_ENUM_FIELDS), so `grade` needs no free-text parsing, unlike 27.sql's `why` -- but fact_event.grade being genuinely NULL is not a 4th grade, it means "a retro happened, its grade is unmeasured", the real shape of every row today since no writer populates it yet. ADOPTS 15.sql's OWN "measured but unclassified" totals + grade_breakdown (WHERE grade IS NOT NULL) LEFT JOIN SHAPE VERBATIM, rejecting a bare `WHERE grade IS NOT NULL` filter alone, which would silently DROP the one honest row per (project_id, month) that has retro activity but no populated grade (grade/grade_count/grade_share all NULL, total_retro_count > 0, graded_retro_count = 0) -- the realistic shape of every real project today, not a hidden gap. grade_share's DENOMINATOR IS total_retro_count, NOT graded_retro_count -- mirrors 15.sql's reason_share exactly, so a month's achieved/partial/diverged shares do NOT have to sum to 1.0 when some retros that month are ungraded; the gap stays visible via graded_retro_count < total_retro_count on every row for that bucket, never silently renormalized against only the classified subset. Bucketed by CAST(date_trunc('month', ts) AS DATE) AS month, satisfying the file-level "calls date_trunc must also mention AS DATE" guard, matching 38.sql's own idiom. EMPTY-STORE SHAPE: zero kind='retro' rows for a (project_id, month) produces zero rows for that bucket -- no "expected months" dimension table to synthesize a phantom row from, same doctrine as 30.sql's own "a project never scanned at all produces zero rows" reasoning, applied here to months. PER-PROJECT-PER-MONTH PARTITIONING: verified live with two projects sharing the identical month with different grade mixes -- neither project's shares blend into the other's. view_rows[0] CATALOG RESIDUE (#253, the same inherited debt 15/16/17/18/19/22.sql already name): this view emits multiple rows per project, one per (month, grade). CLASS 2: retro emitted via loop.py's _EMIT_KINDS, the same stream=ledger.EVENTS routing as spend/gate (#146's own precedent), tagged class 2 by directory alone -- no reliability_class = 1 filter anywhere in this file.
WITH retro_events AS (
    SELECT project_id, goal_id, CAST(date_trunc('month', ts) AS DATE) AS month, grade,
        reliability_class
    FROM fact_event
    WHERE kind = 'retro' AND project_id IS NOT NULL AND ts IS NOT NULL
),
totals AS (
    SELECT
        project_id, month,
        count(*) AS total_retro_count,
        count(*) FILTER (WHERE grade IS NOT NULL) AS graded_retro_count,
        count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
        count(*) AS total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
            AS coverage_pct
    FROM retro_events
    GROUP BY project_id, month
),
grade_breakdown AS (
    SELECT project_id, month, grade, count(*) AS grade_count
    FROM retro_events
    WHERE grade IS NOT NULL
    GROUP BY project_id, month, grade
)
SELECT
    totals.project_id,
    totals.month,
    grade_breakdown.grade,
    grade_breakdown.grade_count,
    CASE
        WHEN grade_breakdown.grade IS NULL THEN NULL
        ELSE ROUND(grade_breakdown.grade_count * 1.0 / NULLIF(totals.total_retro_count, 0), 4)
    END AS grade_share,
    totals.total_retro_count,
    totals.graded_retro_count,
    totals.class1_count, totals.class2_count, totals.total_count, totals.coverage_pct
FROM totals
LEFT JOIN grade_breakdown
    ON grade_breakdown.project_id = totals.project_id AND grade_breakdown.month = totals.month
ORDER BY totals.project_id, totals.month, grade_breakdown.grade
