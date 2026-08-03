-- name: Tokens per phase
-- question: Where does the budget go?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: FUNCTIONALLY INERT TODAY (#243): ledger_writer.py's _write_event writes only six generic columns and never phase/tokens_in/tokens_out, so every real `insight ingest` row has all three NULL forever until that gap closes -- this view's logic is fully tested against fixtures that bypass the ledger/ingest pipeline entirely, same posture 22.sql/15.sql/16.sql/17.sql already established. DELIBERATELY NOT RESTRICTED TO LANDED GOALS, UNLIKE 17.sql: population is every kind='spend' row, with NO join to fact_goal/outcome='done' -- "where does budget go" is a question about ALL spend, including spend on goals that are still open, parked, or failed, and restricting to landed goals the way 17.sql does would silently hide exactly the spend a budget-exhaustion investigation most needs to see; two metrics reading the same event kind for two different questions must not silently share a denominator (mirrors 15.sql's own park-share vs 14.sql's terminal-share discipline). GROUPED BY (project_id, phase), phase IS NULL IS A REAL BUCKET: phase is spec-optional, so a spend event with no phase is grouped under phase IS NULL and reported, not dropped -- mirrors 15.sql's "parks exist but unclassified" row shape. total_tokens NULL-GUARD IS LOAD-BEARING, NOT STYLISTIC: total_tokens_in=SUM(tokens_in), total_tokens_out=SUM(tokens_out), and total_tokens = CASE WHEN SUM(tokens_in) IS NULL AND SUM(tokens_out) IS NULL THEN NULL ELSE COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0) END -- the COALESCE is on the AGGREGATE, not the row, so a phase bucket whose events carry tokens_in but never tokens_out still reports its real total_tokens_in figure in total_tokens instead of the whole sum going NULL because one side was empty; but a bucket where EVERY row has BOTH tokens_in IS NULL and tokens_out IS NULL -- the exact real-world shape today, per #243, where nothing populates either column -- must render total_tokens IS NULL too, matching its own honest total_tokens_in/total_tokens_out NULLs, not a fabricated 0 that would contradict the two NULLs sitting right next to it in the same row; a bare COALESCE(SUM(a),0)+COALESCE(SUM(b),0) gets this wrong (renders 0 whenever both sides are NULL), which is why the CASE guard above is required. Checked against 17.sql for the same hazard: 17.sql's only summed column is SUM(cost_cents) alone (no COALESCE-then-add anywhere), so it already renders NULL honestly on an all-NULL bucket with no fix needed there. EMPTY-STORE SHAPE, DECIDED: zero spend rows produces zero rows in metric_18, no phantom row -- matching 22.sql's/15.sql's/17.sql's own empty-store doctrine. reliability_class: 2, same real decision as 17.sql: spec's own vocabulary table marks spend class 2, and the real write path routes through stream=ledger.EVENTS, which read_all_with_reliability tags class 2 by directory -- no fork to resolve, so this view reads fact_event with NO reliability_class = 1 filter.
WITH spend_events AS (
    SELECT project_id, phase, tokens_in, tokens_out, reliability_class
    FROM fact_event
    WHERE kind = 'spend' AND project_id IS NOT NULL
)
SELECT
    project_id, phase,
    SUM(tokens_in) AS total_tokens_in,
    SUM(tokens_out) AS total_tokens_out,
    CASE WHEN SUM(tokens_in) IS NULL AND SUM(tokens_out) IS NULL THEN NULL
         ELSE COALESCE(SUM(tokens_in), 0) + COALESCE(SUM(tokens_out), 0) END AS total_tokens,
    count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
    count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
    count(*) AS total_count,
    ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
        AS coverage_pct
FROM spend_events
GROUP BY project_id, phase
ORDER BY project_id, phase
