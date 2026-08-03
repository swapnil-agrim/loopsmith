-- name: Gate catch rate by gate
-- question: Where are defects actually caught?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: SHALLOW-DARK (#243): gate/verdict/cycle already exist as fact_event columns (store.py), blocked purely by ledger_writer.py's _write_event only ever writing six generic columns and never gate/verdict/cycle -- no schema gap here, only a missing writer, same posture 15/16.sql already name. LATE-CATCH IS A 12-WAY CASE OVER ledger.GATE_KINDS's FULL VOCABULARY, EVERY VALUE NAMED EXPLICITLY (no bare ELSE doing the classifying work): plan_review -> FALSE (review_context.py labels it "the implementation PLAN, before any code" -- structurally cannot be late, no code exists yet); code_review/post_review/merge -> TRUE (work.py's merge() returns a "PARK: no PR for this goal" first line -- proof these three are structurally unreachable until a PR exists, i.e. after code was written); decision/alignment -> NULL because they are POSITIONED outside the implement-vs-review pipeline by design (decision_gate.py's _NO_GOAL sentinel fires on any edit at any point, an alignment check is not anchored to that sequencing either); verify/risk_security/risk_contract/risk_migration/risk_release/risk_debug -> NULL for a DIFFERENT reason -- these six have ZERO writers anywhere in the codebase (loop.py's own comment: GATE_KINDS has 12 members but most aren't written), so their pipeline position is genuinely UNKNOWN, not merely unattached by design -- the two NULL groups are named separately so a future reader never conflates "sentinel, no position" with "unwritten, unknown position"; a final bare ELSE NULL is unreachable today (all 12 GATE_KINDS values are enumerated above) and exists only as a defensive catch for a hypothetical 13th gate kind. NOTHING MECHANICALLY PINS THIS CASE AGAINST ledger.GATE_KINDS ITSELF beyond test_metric_23_case_enumerates_every_ledger_gate_kind (insight/tests/test_metric_23_gate_catch_rate.py), which AST-parses GATE_KINDS off disk and asserts every value appears as a quoted literal in this file's own raw text -- a hypothetical future 13th GATE_KINDS value would otherwise fall silently into the unreachable ELSE NULL branch with no signal a classification decision is newly owed. avg_catch_cycle is non-NULL only for post_review (the only gate that ever carries a cycle; every other gate's row has cycle NULL by construction, so AVG over an all-NULL group correctly returns NULL, never a fabricated 0) -- a finer within-post_review lateness signal, deliberately not folded into a 3rd/4th late_catch tier. project_late_catch_share's DENOMINATOR (project_catch_count) INCLUDES unclassified (NULL-tier) catches, so it plus its implicit "early" complement do not have to sum to 1.0 -- the gap is directly visible by comparing against the per-gate rows carrying late_catch IS NULL, matching 15.sql's own "shares don't have to sum to 1, the gap is visible elsewhere" honesty for reason_share. PER-PROJECT PARTITIONING: every CTE and the final JOIN/GROUP BY carries project_id throughout, verified live with two projects sharing a colliding goal_id on different gates with different cycle values -- avg_catch_cycle rendered correctly per-project, never blended. CLASS 2: every real gate write routes through stream=ledger.EVENTS (work.py's post_review/code_review/merge, decision_gate.py's _emit_decision_event, loop.py's _EMIT_GATE_KINDS plan_review/alignment path), which read_all_with_reliability tags every row class 2 by directory alone -- no reliability_class = 1 filter anywhere in this file. view_rows[0] CATALOG RESIDUE (#253, the same inherited debt 15/16/17/18/19/22.sql already name): this view emits multiple rows per project, one per (gate, late_catch), so the generic catalog table's coverage figure reflects only one arbitrary such row, not an aggregate.
WITH gate_events AS (
    SELECT project_id, goal_id, gate, verdict, cycle, reliability_class
    FROM fact_event
    WHERE kind = 'gate' AND gate IS NOT NULL AND project_id IS NOT NULL AND verdict IS NOT NULL
),
classified AS (
    SELECT project_id, goal_id, gate, verdict, cycle, reliability_class,
        CASE
            WHEN gate = 'plan_review' THEN FALSE
            WHEN gate IN ('code_review', 'post_review', 'merge') THEN TRUE
            WHEN gate IN ('decision', 'alignment', 'verify', 'risk_security',
                          'risk_contract', 'risk_migration', 'risk_release', 'risk_debug')
                THEN NULL
            ELSE NULL
        END AS late_catch
    FROM gate_events
),
project_catches AS (
    SELECT
        project_id,
        count(*) FILTER (WHERE verdict = 'block') AS project_catch_count,
        count(*) FILTER (WHERE verdict = 'block' AND late_catch) AS project_late_catch_count
    FROM classified
    GROUP BY project_id
)
SELECT
    classified.project_id,
    classified.gate,
    classified.late_catch,
    count(*) AS gate_event_count,
    count(*) FILTER (WHERE classified.verdict = 'block') AS catch_count,
    ROUND(count(*) FILTER (WHERE classified.verdict = 'block') * 1.0 / NULLIF(count(*), 0), 4)
        AS catch_rate,
    AVG(classified.cycle) FILTER (WHERE classified.verdict = 'block') AS avg_catch_cycle,
    project_catches.project_catch_count,
    project_catches.project_late_catch_count,
    ROUND(project_catches.project_late_catch_count * 1.0
        / NULLIF(project_catches.project_catch_count, 0), 4) AS project_late_catch_share,
    count(*) FILTER (WHERE classified.reliability_class = 1) AS class1_count,
    count(*) FILTER (WHERE classified.reliability_class = 2) AS class2_count,
    count(*) AS total_count,
    ROUND(count(*) FILTER (WHERE classified.reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
        AS coverage_pct
FROM classified
JOIN project_catches ON project_catches.project_id = classified.project_id
GROUP BY classified.project_id, classified.gate, classified.late_catch,
    project_catches.project_catch_count, project_catches.project_late_catch_count
ORDER BY classified.project_id, classified.gate
