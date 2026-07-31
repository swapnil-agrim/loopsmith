-- name: Gate coverage
-- question: Which gates actually ran?
-- personas: manager, leadership, cross-functional
-- reliability_class: 1
-- proxy: true
-- guardrail: PER-DIMENSION DENOMINATOR, NOT THE FLAT degraded[] ARRAY (corrected post-review -- a BLOCKING finding, see below): alignment-collect.sh accumulates ALL of a run's degrade codes into ONE flat array with no per-dimension attribution (grep-confirmed: add_degraded is called from three unrelated sites -- no_git, no_test_command (d2-only, unrelated to d1/d5), no_recognized_source (d1-only, does NOT touch d5)). Gating ABSENT on "degraded_collector is non-empty" would therefore render a pack degraded ONLY by no_test_command as ABSENT for BOTH gates even when d1/d5 are fully, correctly measured -- reproduced live this session: a synthetic pack with degraded:['no_test_command'], commits_with_source=3, window_commit_count=3, plan_existed_pct=100, commits_with_review_pct=100 renders PASS/PASS under this view, not ABSENT/ABSENT (the bug an array-wide check would have produced) -- and this repo's own real, unmodified collector run against itself independently reproduces the same shape (degraded:['no_test_command'], commits_with_source=115, window_commit_count=136, both real). Instead, this view reads EACH gate's OWN real denominator, already exposed by the payload: plan_gate is ABSENT when d1.commits_with_source<=0 (no commit in the window touched a recognized source file -- the percentage has no denominator, covers no_git, no_recognized_source, AND a naturally source-free window uniformly, with no degrade-code parsing needed); review_gate is ABSENT when window_commit_count<=0 (the real fact_collector_pack column, following 5.sql/20.sql's own precedent of reading it as a table column rather than re-extracting via JSON -- covers no_git and a genuinely empty window). SIGN FIX (post-pre-PR-review, defensive-only, unpinned): both denominator checks compare `<= 0`, not `= 0` -- `COALESCE(x,0) = 0` is FALSE for a negative x, so a negative commits_with_source/window_commit_count would fall through the equality check straight into the pct thresholds and render a confident FAIL instead of ABSENT (reproduced live this session: commits_with_source=-5, plan_existed_pct=0 renders plan_gate=FAIL under the old `= 0` check). UNREACHABLE VIA THE REAL COLLECTOR (grep-confirmed: alignment-collect.sh only ever increments a git-log count, never decrements or accepts a negative input) -- not independently pinned by a fixture, same defensive posture as the NULL-PCT symmetry fix below; the failure direction of the old code was the safe one (a wrong FAIL, not a wrong PASS), so this was non-urgent, but left asymmetric with the NULL/COALESCE guards elsewhere in this file it would be exactly the kind of gap a future reader has to re-derive rather than find already closed. COALESCE(...,0)<=0 also catches a NULL raw_payload (a totally-failed adapter run -- collectors.py's run_source returns payload=None on timeout/spawn-failure/non-JSON-output, and packs.py's ingest_collectors then writes raw_payload=NULL and window_commit_count=NULL via _EMPTY_WINDOW, verified by reading both files this session) without a separate degraded_adapter check. THIS MAPPING IS VERIFIED AGAINST TODAY'S EXACT THREE degrade CODES ONLY (no_git, no_test_command, no_recognized_source, alignment-collect.sh grep-confirmed this session) -- a future fourth code that invalidates d1/d5 WITHOUT zeroing either denominator would not be caught by this view and would need this guardrail re-verified against the collector at that time; not claimed as an eternal property. NULL-PCT SYMMETRY (post-review, non-blocking item 1): pct IS NULL (the percentage itself failed to extract even though its denominator was nonzero -- should not occur from the real script today, but the same defensive posture applied to the arrays below is applied here too) also renders ABSENT, not FAIL by falling through a false NULL comparison; unpinned by a fixture, defensive-only, same as the array COALESCE. degraded_collector/degraded_adapter are exposed as passthrough, informational columns only (NOT used for gating) so a consumer can see which code(s), if any, accompanied a given pack. THRESHOLD DECISION (Plan's own calibration, spec silent on both PASS/WARN/FAIL cutoffs): pct>=80 PASS, 50<=pct<80 WARN, pct<50 FAIL -- arbitrary round numbers, not spec-derived, chosen for a first cut and named as such rather than hidden. PROXY, PER SPEC'S OWN WORDING (line 486-489): this view is per COLLECTOR PACK (one row per alignment-collect window), not per GOAL x GATE as the spec's ideal shape describes -- plan_existed_pct/commits_with_review_pct are per-commit aggregates, not per-goal; the per-goal emitter is explicitly not built in this story. MODELS TWO GATES PER PACK (plan_gate from d1, review_gate from d5) as separate rows via UNION ALL, matching the issue's own "d1 + d5" framing. CANNOT TELL YOU which specific goal or commit the low/absent coverage belongs to -- this is a window-level aggregate. SCOPE NOTE, SETTLED (pre-PR review): the spec's own section 7 rule "any pack with a degrade code => ABSENT" governs the GAP ENGINE (subsystem C.2), a later and different consumer than Layer 2's gate coverage this file ships -- it does not contradict this view's per-dimension gating and is out of scope here; named so this design decision is not re-opened by a future reader who finds section 7's blanket rule and assumes it should have applied to 24.sql too. SEVERITY_RANK: an INTEGER column, `CASE status WHEN 'PASS' THEN 0 WHEN 'ABSENT' THEN 1 WHEN 'WARN' THEN 2 WHEN 'FAIL' THEN 3 ELSE NULL END`, derived FROM the already-computed status column (a DuckDB lateral column alias, confirmed live both duckdb versions) rather than a second, parallel CASE over the raw denominator/pct columns -- so status and severity_rank cannot disagree with each other by construction. Mirrors pipeline.py's own `_ORDER = {PASS:0, ABSENT:1, WARN:2, FAIL:3}` (skills/sdlc-loop/scripts/pipeline.py, re-read this session) -- pinned by test_metric_severity_rank.py, which reads pipeline.py's _ORDER off disk as TEXT/AST (never `import`s it -- insight/ must never import skills, test_import_boundary.py) so this mapping cannot silently drift from pipeline.py's own source. The ELSE NULL branch is UNREACHABLE by this file's own status CASE today (that CASE's own ELSE always resolves to one of the four canonical literals, never a fifth value) -- stated explicitly as defensive-only, not claimed test-verified, same posture as the NULL/COALESCE guards above.
WITH packs AS (
    SELECT
        ROW_NUMBER() OVER () AS pack_id,
        collected_ts,
        window_commit_count,
        degraded_collector,
        degraded_adapter,
        CAST(json_extract(raw_payload, '$.dimensions.d1.commits_with_source') AS INTEGER) AS commits_with_source,
        CAST(json_extract(raw_payload, '$.dimensions.d1.plan_existed_pct') AS INTEGER) AS plan_existed_pct,
        CAST(json_extract(raw_payload, '$.dimensions.d5.commits_with_review_pct') AS INTEGER) AS commits_with_review_pct
    FROM fact_collector_pack
    WHERE schema = 'alignment-collect/v1'
),
gates AS (
    SELECT pack_id, collected_ts, degraded_collector, degraded_adapter,
           'plan_gate' AS gate, plan_existed_pct AS pct,
           (COALESCE(commits_with_source, 0) <= 0) AS denominator_empty
    FROM packs
    UNION ALL
    SELECT pack_id, collected_ts, degraded_collector, degraded_adapter,
           'review_gate' AS gate, commits_with_review_pct AS pct,
           (COALESCE(window_commit_count, 0) <= 0) AS denominator_empty
    FROM packs
)
SELECT
    collected_ts,
    gate,
    pct,
    degraded_collector,
    degraded_adapter,
    CASE
        WHEN denominator_empty THEN 'ABSENT'
        WHEN pct IS NULL THEN 'ABSENT'
        WHEN pct >= 80 THEN 'PASS'
        WHEN pct >= 50 THEN 'WARN'
        ELSE 'FAIL'
    END AS status,
    CASE status
        WHEN 'PASS' THEN 0
        WHEN 'ABSENT' THEN 1
        WHEN 'WARN' THEN 2
        WHEN 'FAIL' THEN 3
        ELSE NULL
    END AS severity_rank
FROM gates
ORDER BY collected_ts, gate
