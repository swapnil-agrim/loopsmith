-- name: Handoff graph
-- question: Who blocks whom?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: a plain edge list -- fact_handoff rows grouped by (project_id, area, from_actor, to_actor), counted -- a dependency map, not a triage list; who hands off to whom, how often, per area. No status/severity_rank (Decision F -- this is a graph, not a gate). No window function: a GROUP BY that includes project_id satisfies Decision G's per-project partitioning requirement without needing an explicit PARTITION BY. Counts hand-offs OPENED, regardless of ack/settlement state -- an edge exists the moment a hand-off is opened, independent of whether or how it was later answered (unanswered/deferred/resolved are metrics 33/34's own concern, not this one's).
-- data_status: dark
SELECT
    project_id,
    area,
    from_actor,
    to_actor,
    count(*) AS handoff_count
FROM fact_handoff
GROUP BY project_id, area, from_actor, to_actor
ORDER BY project_id, area, handoff_count DESC
