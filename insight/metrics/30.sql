-- name: Debt inventory + trend
-- question: Is debt growing?
-- personas: manager, leadership, cross-functional
-- reliability_class: 1
-- guardrail: MULTI-PROJECT PARTITION FIX (PR #186, BLOCKING, found by an independent pre-merge review): the trend CTE's `LAG(candidate_count IGNORE NULLS) OVER (ORDER BY collected_ts)` originally had NO `PARTITION BY`, and neither `packs`, `trend`, nor the final SELECT projected `project_id` at all -- so the view was unconditionally GLOBAL across every project sharing the same store. Reachable in practice, not merely theoretical: `insight/__main__.py`'s `--repos` flag (#106) ingests multiple `project_id`s into the SAME store over ONE connection, and this view's own personas line (`manager, leadership, cross-functional`) names exactly the audience who runs a multi-repo rollup. Reproduced live pre-fix: projA's 2026-07-28 snapshot (20 candidates, itself a correct first-scan ABSENT) leaked into projB's 2026-07-29 snapshot (1 candidate, ALSO projB's own first-ever scan) as a false `prior_count=20`, rendering `PASS` ("debt shrank from 20 to 1") for a project that had never been scanned before -- the exact false-confident-PASS-instead-of-ABSENT outcome ABSENT PATH 2 below exists to prevent, just triggered across a project boundary rather than within one project's own history. FIXED by adding `project_id` to `packs`/`trend`/the final SELECT and `PARTITION BY project_id` to the LAG, so each project's trend is computed only against its OWN prior snapshot, never another project's -- pinned by test_metric_30_debt_inventory.py::test_a_second_projects_first_scan_does_not_trend_against_a_different_project (fixtures/30_multi_project.jsonl, two projects in one store/connection, the exact --repos shape). SWEEP FOR THE SAME CLASS OF BUG ELSEWHERE IN THIS STORY (24.sql, 26.sql, this file): 24.sql's ROW_NUMBER() OVER () (unpartitioned) was checked and found NOT equivalent to this bug -- it generates an internal-only pack_id never projected into 24.sql's own final output and never used to relate one row's VALUE to another row's (status is computed entirely per-row, from that same row's own denominator/pct columns), so cross-project rows sharing that global numbering cannot corrupt each other's status the way an unpartitioned LAG can. 24.sql DOES share this file's separate, NON-BLOCKING gap -- it also does not project project_id, so a multi-repo consumer cannot attribute a plan_gate/review_gate row to a project -- fixed alongside this file in the same PR (see 24.sql's own guardrail) since both were touched by the same review pass; 5.sql and 20.sql have the identical pre-existing attribution gap but were NOT touched here (out of this PR's scope, named as a candidate for one shared follow-up covering all four views rather than four separate patches). 26.sql already projected project_id/goal_id from the start and has no window function of any kind, so it was not part of this sweep's findings. ABSENT PATH 1, ADAPTER-LEVEL (the case test_metric_30_debt_inventory.py's absent-gate test pins): discovery-scan.sh itself emits no degraded[] codes of its own (grep-confirmed: `grep -n degraded skills/sdlc-loop/scripts/discovery-scan.sh` -- zero hits, full stop, not even in a comment) -- its ONLY failure signal is packs.py's own degraded_adapter vocabulary (adapter_exit_nonzero/adapter_timeout/adapter_output_not_json/etc, an INGEST-adapter concern, never a collector one, unlike 24.sql's degraded_collector path). A pack whose degraded_adapter is non-empty renders ABSENT and its candidate_count is nulled out BEFORE computing the trend, so one bad scan cannot corrupt the next real snapshot's delta -- verified live: LAG(candidate_count IGNORE NULLS) skips a NULLed-out degraded row and correctly diffs against the last GOOD snapshot instead. ABSENT PATH 2, FIRST-SNAPSHOT: a project's very first discovery-scan snapshot has no prior snapshot to diff against -- "is debt growing" is structurally unanswerable at n=1, so it renders ABSENT (trend unmeasurable) rather than a guessed PASS ("not growing"), the same false-zero-trap reasoning .sdlc/plans/110.md Design decision A.1 already applied to 20.sql's empty-window case. NULL-CANDIDATE_COUNT SYMMETRY (post-review, non-blocking item 1): a non-degraded row whose candidate_count itself extracted NULL (a malformed payload missing the "candidates" key entirely -- not producible by the real discovery-scan.sh today, which always emits the key, grep-confirmed) also renders ABSENT rather than falling through the numeric comparisons to a false FAIL -- the same class of NULL-comparison-is-falsy bug 24.sql's pct fields needed fixing for, applied here for symmetry though not independently pinned by a fixture (defensive-only, like the degraded-array COALESCE above). SIGN FIX, CHECKED AND NOT NEEDED HERE (pre-PR review asked for it in both 24.sql and 30.sql "wherever the pattern appears"): 24.sql's fix widened a numeric-denominator equality check (`COALESCE(commits_with_source,0) = 0`) to `<= 0` because a negative denominator would otherwise fall through to a false FAIL. This file's only COALESCE(...,0) checks are `COALESCE(len(degraded_adapter), 0) = 0/> 0` -- `len()` of an array returns a non-negative count or NULL, never a negative number, so the equality-vs-negative hazard 24.sql's numeric denominators had does not exist for an array-length check; no `<= 0`/`> 0` change was needed or made here. candidate_count/prior_count themselves are `json_array_length(...)` (also never negative) and a LAG of the same, so the FAIL/WARN/PASS delta thresholds (`candidate_count - prior_count`) have no analogous negative-denominator input to guard against either -- a negative delta is a legitimate, intended PASS case (debt shrank), not a hazard. THRESHOLD DECISION (Plan's own calibration, spec silent): delta<=0 PASS, 0<delta<5 WARN, delta>=5 FAIL -- arbitrary absolute-count cutoffs, not spec-derived. LAG TIEBREAKER, ACCEPTED LIMITATION (post-review, non-blocking item 4, not pinned; re-scoped, not weakened, by the PARTITION BY fix above): LAG(...) OVER (PARTITION BY project_id ORDER BY collected_ts) has no secondary sort key WITHIN a project's own partition, so two packs from the SAME project sharing an identical collected_ts have an implementation-defined (not SQL-standard-guaranteed) relative order; fact_collector_pack carries no sequence/insertion-order column to break the tie with (store.py's DDL, re-checked -- no such column exists on this table). Two DIFFERENT projects sharing an identical collected_ts is no longer a tiebreaker question at all -- the PARTITION BY means they are never compared against each other by this LAG in the first place, tie or no tie. Stable across every repeated run in this session's own testing, but that is an observed property of this DuckDB build, not a guarantee this view provides; a future story adding a monotonic sequence column would be the correct fix, not attempted here. OUT OF SCOPE, NOT MODELED: a project NEVER scanned at all (zero fact_collector_pack rows for this schema) produces zero rows from this view, not a synthesized ABSENT row -- there is no "expected scans" dimension table to LEFT JOIN against, so this is a structurally different kind of absence from pipeline.py's own missing-.sdlc/pipeline.json case (None/exit 3); a consuming dashboard must render an empty result set as "never scanned" itself, this SQL cannot express that as a row-level status. SEVERITY_RANK (post-pre-PR review): an INTEGER column, `CASE status WHEN 'PASS' THEN 0 WHEN 'ABSENT' THEN 1 WHEN 'WARN' THEN 2 WHEN 'FAIL' THEN 3 ELSE NULL END`, derived FROM the already-computed status column (lateral column alias) rather than a second CASE over candidate_count/prior_count -- mirrors pipeline.py's own `_ORDER`, pinned by test_metric_severity_rank.py against pipeline.py's real source (read as TEXT/AST, never imported). ELSE NULL is UNREACHABLE by this file's own status CASE today (its own ELSE always resolves to 'FAIL', never a fifth value) -- defensive-only, not test-verified, same posture as this file's other unpinned claims above.
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
trend AS (
    SELECT
        project_id,
        collected_ts,
        degraded_adapter,
        candidate_count,
        LAG(candidate_count IGNORE NULLS) OVER (PARTITION BY project_id ORDER BY collected_ts) AS prior_count
    FROM packs
)
SELECT
    project_id,
    collected_ts,
    candidate_count,
    prior_count,
    CASE
        WHEN COALESCE(len(degraded_adapter), 0) > 0 THEN 'ABSENT'
        WHEN prior_count IS NULL THEN 'ABSENT'
        WHEN candidate_count IS NULL THEN 'ABSENT'
        WHEN candidate_count - prior_count <= 0 THEN 'PASS'
        WHEN candidate_count - prior_count < 5 THEN 'WARN'
        ELSE 'FAIL'
    END AS status,
    CASE status
        WHEN 'PASS' THEN 0
        WHEN 'ABSENT' THEN 1
        WHEN 'WARN' THEN 2
        WHEN 'FAIL' THEN 3
        ELSE NULL
    END AS severity_rank
FROM trend
ORDER BY project_id, collected_ts
