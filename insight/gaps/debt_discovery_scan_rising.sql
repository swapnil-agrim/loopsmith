-- name: Discovery-scan debt inventory rising for 3 consecutive scans
-- class: Debt
-- metric: 30
-- action: triage the discovery-scan candidates driving the rise (see the pack's own raw_payload candidate titles for this project) before the backlog grows unmanaged, or confirm the growth is expected and move on
-- severity: WARN
-- guardrail: SAME TWO ABSENT PATHS AS METRIC 30, RE-DERIVED INLINE, NEVER SELECT FROM metric_30 (.sdlc/plans/121.md Design decision 4; house convention, #120's own precedent) -- degraded_adapter non-empty nulls candidate_count before any comparison (packs.py's own degraded_adapter vocabulary is an ingest-adapter concern; discovery-scan.sh itself emits no degraded[] of its own, grep-confirmed zero hits); a project's first snapshot has population 0 (no trailing_p85 is derivable from zero prior points). PARTITION BY project_id FROM THE START (PR #186's own shipped bug, applied here from day one, not retrofitted): every window function below is partitioned by project_id, verified live this session against an interleaved two-project fixture that a naive OVER(ORDER BY collected_ts) with no PARTITION BY would have spliced into one fabricated cross-project run. THE 'RISING' CRITERION, MEASURED NOT ASSERTED (.sdlc/plans/121.md Design decision 2): three criteria were built and Monte-Carlo measured (500 trials/shape, matching test_gaps_threshold_no_false_positive.py's own methodology, against i.i.d.-stationary synthetic healthy candidate_count series, Normal(baseline, baseline*cv) per point, clipped non-negative, rounded -- NOT a random walk, which drifts and is not representative of a healthy flat project, an error this session's own harness made and corrected before trusting a number from it). Naive candidate_count > prior_count (bare LAG delta, metric 30's own >0 idiom) fired on 86-100% of healthy synthetic projects across four shapes -- unusable, the same trap spec:538-546 already names for Threshold. A bare LAG delta>0 with a k=3 consecutive-rises run-length filter (reusing #119's own gaps-and-islands pattern verbatim but against the single-prior-snapshot LAG rather than a trailing quantile) improved matters but was UNSTABLE and still too high for a 30-point series specifically: 1.6% (n=7) to 41.8-51.2% (n=30, three reseeds) -- rejected, because a single prior point is a much noisier reference than a trailing quantile (a LAG delta is essentially a coin flip on direction, ~50% base rate, vs a p85 crossing's own ~15% by construction). THE SHIPPED CRITERION instead computes candidate_count's OWN TRAILING P85 -- quantile_cont(candidate_count, 0.85) OVER (PARTITION BY project_id ORDER BY collected_ts ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), same strictly-prior frame shape as insight/gaps/baseline.py's own TRAILING_P85_EXPR, NOT pasted verbatim from that constant (it is hardcoded to lead_time_seconds/merge_ts/merge_sha, a different table's columns entirely) and therefore correctly OUT of test_gaps_baseline_fragment_is_referenced.py's own scope, which filters by class: Threshold (NOT a filename glob -- its GAPS_DIR constant is dead code, so a Debt rule named threshold_*.sql would still be out of scope) -- generalizing the spec's own 'derived baseline, not a magic number' principle from the Threshold class to this Debt rule's own 'rising' concept, a legitimate reuse of technique across gap classes, not a class-boundary violation (a gap CLASS names the KIND of defect, not the SQL idiom used to derive a baseline for it) -- combined with a run of breach_run_length >= 3 (K_CONSECUTIVE_BREACHES's own value, reusing the EXACT reserved identifier name insight/tests/test_gaps_no_literal_thresholds.py's own regex already exempts, so this rule needs zero changes to that shared, catalog-wide static guard). MEASURED (500 trials/shape, i.i.d.-stationary, baseline/cv matching #119's own three shapes plus a small-baseline fourth), RE-MEASURED AT REVIEW against the shipped SQL through evaluate_rule: n=30,cv=.20 -> 92/500 (18.4%); n=10,cv=.10 -> 41/500 (8.2%); n=7,cv=.05 -> 19/500 (3.8%); n=15,baseline=5,sigma=1.5 -> 53/500 (10.6%). An earlier revision of this comment reported 83/52/35/50; those figures did NOT reproduce against this file's own criterion (the generated data reproduces exactly -- the naive-criterion rate is 99-100% either way -- so the divergence was in evaluation, not in the sample), and they are corrected here rather than re-explained. Compare #119's own finding that a single p85 crossing fires 91-99% on healthy stationary data: the run-of-3 requirement is what buys the reduction, and these are the numbers that justify it.0% -- in the same 7-20% band #119's own corrected Threshold rule measured, not the 86-100%/42-51% every rejected alternative produced. SENSITIVITY, VERIFIED LIVE: a sustained 2x step-up (10 snapshots at 20, then 10 at 40) fires WARN starting at the FIRST regressed snapshot, 3 evidence rows, then correctly stops firing once the trailing baseline itself absorbs the new regime -- the same accepted, named limitation .sdlc/plans/119.md's own Threshold rule discloses, inherited here rather than re-litigated. A single one-off spike among an otherwise flat series does not fire (PASS) -- never on one crossing. NO `file` COLUMN IN EVIDENCE, A DELIBERATE, DISCLOSED OMISSION (.sdlc/plans/121.md Design decision 3, NOT an oversight): discovery-scan/v1's own wire schema (skills/sdlc-loop/scripts/discovery-scan.sh:15) carries candidates as an array with no structured per-item location field; each candidate's own evidence[] sub-array ('file:line' strings) is that SHELL SCRIPT's own internal, undocumented-at-the-schema-level implementation detail, not a versioned field discovery-scan/v1 itself guarantees, so parsing it here to synthesize a `file` column would misrepresent an implementation detail as a stable schema-level guarantee it is not -- named and rejected for the identical reason .sdlc/plans/121.md's own Design decision 1 rejects fabricating a `knowledge/gaps.md`-queries schema that was never written; evidence instead carries candidate_count/trailing_p85/trailing_series (a list(...) window aggregate, #119's own multi-observation evidence idiom) and breach_run_length, all honestly derived from what discovery-scan/v1 actually guarantees. SEVERITY IS WARN, NOT FAIL, MATCHING THRESHOLD'S OWN CHOICE FOR THE IDENTICAL RUN-LENGTH SHAPE (a sustained pattern is worth investigating, not an automatic hard failure the way metric 30's own delta>=5 magnitude cutoff was; that cutoff is metric 30's own guardrail-disclosed 'arbitrary, not spec-derived' choice, not reused here). CANDIDATE_COUNT/RAW_PAYLOAD MALFORMED-JSON DISCLOSURE, SAME CATALOG-WIDE POSTURE AS EVERY SIBLING RULE: syntactically invalid raw_payload text still raises DuckDB's InvalidInputException from json_extract/json_array_length themselves, loud not silent, disclosed-but-unfixed catalog-wide, out of this story's scope (.sdlc/plans/120.md Risks; unchanged here).
-- population: SELECT count(*) FROM (SELECT project_id, collected_ts, quantile_cont(candidate_count, 0.85) OVER (PARTITION BY project_id ORDER BY collected_ts ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS trailing_p85 FROM (SELECT project_id, collected_ts, degraded_adapter, CASE WHEN COALESCE(len(degraded_adapter), 0) = 0 THEN CAST(json_array_length(json_extract(raw_payload, '$.candidates')) AS INTEGER) ELSE NULL END AS candidate_count FROM fact_collector_pack WHERE schema = 'discovery-scan/v1') p WHERE candidate_count IS NOT NULL) WHERE trailing_p85 IS NOT NULL
WITH packs AS (
    SELECT
        project_id,
        collected_ts,
        degraded_adapter,
        CASE
            WHEN COALESCE(len(degraded_adapter), 0) = 0
                THEN CAST(json_array_length(json_extract(raw_payload, '$.candidates')) AS INTEGER)
            ELSE NULL
        END AS candidate_count
    FROM fact_collector_pack
    WHERE schema = 'discovery-scan/v1'
),
measured AS (
    SELECT project_id, collected_ts, candidate_count
    FROM packs
    WHERE candidate_count IS NOT NULL
),
baselined AS (
    SELECT
        project_id, collected_ts, candidate_count,
        quantile_cont(candidate_count, 0.85) OVER (PARTITION BY project_id ORDER BY collected_ts ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS trailing_p85,
        list(candidate_count) OVER (PARTITION BY project_id ORDER BY collected_ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS trailing_series
    FROM measured
),
flagged AS (
    SELECT *,
        (trailing_p85 IS NOT NULL AND candidate_count > trailing_p85) AS breach,
        ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY collected_ts) AS rn
    FROM baselined
),
runs AS (
    SELECT *,
        rn - ROW_NUMBER() OVER (PARTITION BY project_id, breach ORDER BY collected_ts) AS grp
    FROM flagged
),
run_lengths AS (
    SELECT *,
        count(*) FILTER (WHERE breach) OVER (PARTITION BY project_id, grp) AS breach_run_length
    FROM runs
)
SELECT project_id, collected_ts, candidate_count, trailing_p85, trailing_series, breach_run_length
FROM run_lengths
WHERE breach AND breach_run_length >= 3
ORDER BY project_id, collected_ts
