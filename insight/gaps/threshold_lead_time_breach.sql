-- name: Lead time breaches its own trailing p85 on 3 consecutive merges
-- class: Threshold
-- metric: 3
-- action: investigate why the last 3 (or more) consecutively measured merges all landed above their own trailing p85 -- a sustained regression, not a one-off
-- severity: WARN
-- guardrail: (a) THE HELPER IS PASTED, NOT IMPORTED -- trailing_p85's own column expression is insight/gaps/baseline.py's TRAILING_P85_EXPR pasted verbatim (checked mechanically by insight/tests/test_gaps_baseline_fragment_is_referenced.py, whitespace-normalised, since insight.gaps.loader never renders a .sql file through Python string interpolation -- #116 Design decision 7). (b) ONLY MEASURED ROWS ENTER THE WINDOW -- `measured` filters `lead_time_seconds IS NOT NULL` before any windowing (.sdlc/plans/119.md Design decision 2). (c) TRAILING MEANS STRICTLY PRIOR -- `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` excludes the row being judged from its own baseline (Design decision 3); a project's first measured merge gets a NULL baseline by construction. (d) EVERY OBSERVATION WITH A DERIVABLE TRAILING BASELINE IS EVALUATED, NOT ONLY THE LATEST -- `run_lengths` computes breach_run_length over every row with a non-NULL trailing_p85 (Design decision 4; round 1's own blocking finding that latest-only evaluation renders a real breach PASS forever once a newer merge lands is not reopened by this revision). (e) FIRES ONLY ON A RUN OF >= K_CONSECUTIVE_BREACHES (3) CONSECUTIVE BREACHES -- a single crossing, however large, does NOT fire (Design decision D-new; the reviewer's own 10,000x single-spike fixture correctly renders PASS, verified live in .sdlc/plans/119.md). (f) EVIDENCE IS EVERY BREACH ROW BELONGING TO A QUALIFYING RUN, not only the row where the run first reaches length 3 -- `count(*) FILTER (WHERE breach) OVER (PARTITION BY project_id, grp)` has no ORDER BY inside its OVER(...), so DuckDB's documented no-ORDER-BY default frame (the entire partition) gives every row in a run the SAME, final run length (Design decision 5). (g) NAMED, ACCEPTED LIMITATIONS, NOT BUGS -- the unbounded PRECEDING frame means one extreme outlier permanently elevates every later baseline in that project, and a sustained regression's own visible run eventually ends once enough regressed points accumulate into the baseline itself (see .sdlc/plans/119.md Risks); a perfectly (or near-perfectly) zero-variance step-up can be slower to fire or miss a run of 3 entirely, because the new regime's own trailing quantile catches up to the new regime's own value fastest when there is no spread (same plan, "A named, measured edge case"). (h) 0.85 IS THE BASELINE'S OWN DEFINITION, NOT A BANNED MAGNITUDE (Design decision 9) -- it is the quantile parameter that defines p85, not a tuned domain threshold; `breach_run_length >= 3` compares a COUNT OF CONSECUTIVE BOOLEAN BREACH FLAGS (a run length) against K_CONSECUTIVE_BREACHES, not a domain magnitude -- insight/tests/test_gaps_no_literal_thresholds.py's own docstring states precisely how it tells the two apart (Design decision 8). (i) NO goal_id IN EVIDENCE -- fact_merge_lead_time carries no goal attribution, same as every prior round.
-- population: SELECT count(*) FROM (SELECT quantile_cont(lead_time_seconds, 0.85) OVER (PARTITION BY project_id ORDER BY merge_ts, merge_sha ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS trailing_p85 FROM fact_merge_lead_time WHERE lead_time_seconds IS NOT NULL) WHERE trailing_p85 IS NOT NULL
WITH measured AS (
    SELECT project_id, merge_sha, merge_ts, lead_time_seconds
    FROM fact_merge_lead_time
    WHERE lead_time_seconds IS NOT NULL
),
baselined AS (
    SELECT
        project_id, merge_sha, merge_ts, lead_time_seconds,
        quantile_cont(lead_time_seconds, 0.85) OVER (PARTITION BY project_id ORDER BY merge_ts, merge_sha ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS trailing_p85,
        list(lead_time_seconds) OVER (PARTITION BY project_id ORDER BY merge_ts, merge_sha ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS trailing_series
    FROM measured
),
flagged AS (
    SELECT *,
        (trailing_p85 IS NOT NULL AND lead_time_seconds > trailing_p85) AS breach,
        ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY merge_ts, merge_sha) AS rn
    FROM baselined
),
runs AS (
    SELECT *,
        rn - ROW_NUMBER() OVER (PARTITION BY project_id, breach ORDER BY merge_ts, merge_sha) AS grp
    FROM flagged
),
run_lengths AS (
    SELECT *,
        count(*) FILTER (WHERE breach) OVER (PARTITION BY project_id, grp) AS breach_run_length
    FROM runs
)
SELECT project_id, merge_sha, merge_ts, lead_time_seconds, trailing_p85, trailing_series, breach_run_length
FROM run_lengths
WHERE breach AND breach_run_length >= 3
ORDER BY project_id, merge_ts, merge_sha
