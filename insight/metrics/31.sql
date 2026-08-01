-- name: Handoff graph
-- question: Who blocks whom?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: a plain edge list -- fact_handoff rows grouped by (project_id, area, from_actor, to_actor), counted -- a dependency map, not a triage list; who hands off to whom, how often, per area. No status/severity_rank (Decision F -- this is a graph, not a gate). No window function: a GROUP BY that includes project_id satisfies Decision G's per-project partitioning requirement without needing an explicit PARTITION BY. Counts hand-offs OPENED, regardless of ack/settlement state -- an edge exists the moment a hand-off is opened, independent of whether or how it was later answered (unanswered/deferred/resolved are metrics 33/34's own concern, not this one's). BLOCKING FINDING FROM AN INDEPENDENT REVIEW, FIXED HERE (WHERE issue IS NOT NULL added below): fact_handoff has no goal_id column (store.py:126-138), so an issue-less hand-off (handoff.py's own hand_off() falls back to issue=None whenever the backlog source has no create_dependency) can never be durably re-matched to its own later ack the way an issue-keyed pair is -- the ack lands instead as a second, orphaned fact_handoff row with from_actor/to_actor/area/opened_ts all NULL; without this filter that orphaned row would GROUP BY into its own phantom (area=NULL, from_actor=NULL, to_actor=NULL) edge with a non-zero handoff_count, a graph edge nothing real ever opened. Both the issue-less hand-off itself and its orphaned ack are therefore excluded from this graph entirely, not merged into one edge and not double-counted as two -- a disclosed, real coverage gap (same posture as metric 38's own coupled CTE, which already carries this identical filter for the identical reason), not a silent miscount; the real fix is a goal_id column on fact_handoff or a durable key in ledger_writer.py, out of scope here per Decision K.
-- data_status: dark
SELECT
    project_id,
    area,
    from_actor,
    to_actor,
    count(*) AS handoff_count
FROM fact_handoff
WHERE issue IS NOT NULL
GROUP BY project_id, area, from_actor, to_actor
ORDER BY project_id, area, handoff_count DESC
