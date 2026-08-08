-- name: Park rate
-- question: How often does it stop?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: DARK METRIC: fact_goal.outcome and fact_event are 0/19 and 0 populated respectively in this repo's own real ingest today (#110 research dossier) -- a fixture-green test is not evidence of a live dashboard number. ASSUMPTION, NOT A CONFIRMED FACT (extends #109 plan Design decision F): reads fact_event kind = 'parked' as the lifecycle stream's assumed landing shape -- see 12.sql's guardrail for the Class-2 'park' vs lifecycle 'parked' spelling collision this must not be confused with. NUMERATOR DECISION (Plan's own choice, not spec-dictated): counts DISTINCT terminal goals with one-or-more parks, not raw park-event count -- a goal parked three times counts once, keeping park_rate a proportion in [0,1] comparable across windows and matching the metric's own "rate" naming; a raw-event numerator would let park_rate exceed 1 and conflate "how often does work stop" with "how many times does the same goal stop repeatedly", two different questions. Cannot tell you whether a park was resolved in a minute or sat for weeks, or why it parked -- that is #13 (attention volume) and the unbuilt #15 (park taxonomy), respectively. RELIABILITY-CLASS ENFORCEMENT (#114, spec line 563: "a NOW metric must not read any reliability_class=2 row"): parked_goals now filters `AND reliability_class = 1`, a bare equality not `!= 2` -- a NULL reliability_class is excluded identically to a 2, not grandfathered as trusted, because a real ingested fact_event row is NEVER NULL (ledger_writer.py always tags it); a NULL seen here is evidence of an ingest-path regression, not a legitimate legacy row. VERIFIED 2026-08-08, LABEL CLEARED: the `data_status: dark` above this line is removed. Its stated condition was fact_goal.outcome and fact_event being unpopulated (0/19 and 0 at #110). Measured on this repo's own store: 69 goals carry an outcome and fact_event holds 694 rows, 17 of them kind='parked' -- so the 'parked' spelling this view assumes is confirmed by real data rather than assumed. NOTE the remaining, narrower caveat: this view shares 12.sql's reading of the lifecycle vocabulary, and 12.sql stays DARK for a reason that does NOT affect this metric (its ack branch never fires; park rate does not read ack).
WITH terminal_goals AS (
    SELECT project_id, goal_id FROM fact_goal WHERE outcome IN ('done', 'failed')
),
parked_goals AS (
    SELECT DISTINCT project_id, goal_id FROM fact_event WHERE kind = 'parked' AND reliability_class = 1
)
SELECT
    count(*) FILTER (WHERE pg.goal_id IS NOT NULL) AS parked_terminal_count,
    count(*) AS terminal_count,
    ROUND(count(*) FILTER (WHERE pg.goal_id IS NOT NULL) * 1.0 / NULLIF(count(*), 0), 4) AS park_rate
FROM terminal_goals tg
LEFT JOIN parked_goals pg USING (project_id, goal_id)
