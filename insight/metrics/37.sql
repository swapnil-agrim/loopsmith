-- name: Ownership concentration
-- question: Bus factor -- is one person who an area routes through?
-- personas: manager, leadership
-- reliability_class: 1
-- guardrail: "vs CODEOWNERS" has no faithful source in this repo (Decision D): no CODEOWNERS file exists (grep-confirmed), no reader for one exists anywhere in insight/, and dim_actor.areas (the column that WOULD carry a roster) has zero writer anywhere in the codebase. DECISION: do not build a CODEOWNERS reader -- out of scope for a metrics-only story. Instead scoped to the actor<->area signal that DOES exist: fact_handoff.area x fact_handoff.to_actor -- who actually RECEIVES hand-offs in each area. THIS IS A DISCLOSED, NAMED BIAS, NOT A SILENT SUBSTITUTION: a bus factor computed from hand-off participation only sees actors who were ever the RECIPIENT of a hand-off in that area -- blind to an area's sole maintainer who never needed a hand-off, and blind to anyone on the real roster who simply never appeared in a fact_handoff row. This is a LOWER BOUND on true concentration risk (the real roster can only be MORE distributed than the hand-off-participant slice suggests, never less). Named follow-up (not built here): a CODEOWNERS reader that populates dim_actor.areas, letting a future view compute concentration against the DECLARED roster instead of the OBSERVED participant slice. CONCENTRATION MEASURE: per (project_id, area), the top actor's share of to_actor hand-offs -- max(count per to_actor) / sum(count per to_actor). Chosen over an HHI for the same reason 24.sql/30.sql's own guardrails favor a plain, explainable number over a derived index: "the busiest person in area X handles 80% of its hand-offs" is a sentence a manager can act on without translation. THRESHOLDS (Plan's own calibration, spec silent, same posture as 24.sql's pct>=80/50 and 30.sql's delta cutoffs): share >= 0.75 -> FAIL, 0.5 <= share < 0.75 -> WARN, share < 0.5 -> PASS. ABSENT when total_handoffs_in_area < 3 -- below that, "concentration" is not a structurally answerable question (a single hand-off trivially "concentrates" 100% on whoever received it, a fact about volume, not risk) -- same false-zero-trap reasoning 30.sql's first-snapshot ABSENT already applies. The ONE metric among this story's seven carrying PASS/WARN/FAIL/ABSENT + severity_rank (Decision F) -- a bus-factor risk level is the one Layer-3 question shaped like a gate. Every window function partitions BY project_id, area per Decision G. SEPARATE, BLOCKING FINDING FROM AN INDEPENDENT REVIEW, FIXED HERE (issue IS NOT NULL added to per_actor's own FROM fact_handoff below): fact_handoff has no goal_id column (store.py:126-138), so an issue-less hand-off's own later ack lands as a second, orphaned row (issue NULL, area/to_actor both NULL, only ack_ts/ack_state populated) rather than merging into the original -- without this filter that orphaned row would GROUP BY into its own phantom area=NULL ownership row, ABSENT only because a small fixture's total_handoffs_in_area stays under the volume floor -- at higher volume it would render a real PASS/WARN/FAIL status for an area that does not exist. issue IS NOT NULL excludes both the issue-less hand-off and its orphaned ack from this view entirely, a disclosed, real coverage gap on top of the bus-factor lower-bound already named above, not a silent miscount; the real fix is a goal_id column on fact_handoff or a durable key in ledger_writer.py, out of scope here per Decision K.
-- data_status: dark
WITH per_actor AS (
    SELECT
        project_id,
        area,
        to_actor,
        count(*) AS handoff_count
    FROM fact_handoff
    WHERE issue IS NOT NULL
    GROUP BY project_id, area, to_actor
),
per_area AS (
    SELECT
        project_id,
        area,
        sum(handoff_count) AS total_handoffs,
        max(handoff_count) AS top_actor_count
    FROM per_actor
    GROUP BY project_id, area
)
SELECT
    project_id,
    area,
    top_actor_count,
    total_handoffs,
    top_actor_count::DOUBLE / total_handoffs AS top_actor_share,
    CASE
        WHEN total_handoffs < 3 THEN 'ABSENT'
        WHEN top_actor_share >= 0.75 THEN 'FAIL'
        WHEN top_actor_share >= 0.5 THEN 'WARN'
        ELSE 'PASS'
    END AS status,
    CASE status
        WHEN 'PASS' THEN 0
        WHEN 'ABSENT' THEN 1
        WHEN 'WARN' THEN 2
        WHEN 'FAIL' THEN 3
        ELSE NULL
    END AS severity_rank
FROM per_area
