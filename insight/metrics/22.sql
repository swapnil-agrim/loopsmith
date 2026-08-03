-- name: Prevented rework (self-derived multiplier)
-- question: What did the gates save?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: cost = wall-clock elapsed seconds from fact_goal.claimed_ts to fact_goal.terminal_ts, populated only by goal_lifecycle.py (#217) for github-discovery-mode projects -- a goal missing either timestamp, or with terminal_ts < claimed_ts (clock skew, never legitimate), is EXCLUDED from both cost buckets, never coerced to 0 and never counted as evidence either way. NOTED DUPLICATION (not fixed here): this cost formula is byte-identical to 2.sql's cycle-time computation -- the spec reserves a distinct dollar/token "cost" concept for metric #17 (fact_event.cost_cents, equally unpopulated today), so this metric's "cost" is really elapsed wall-clock time standing in for a real cost unit and should move to fact_event.cost_cents (or whatever #17 lands on) once that data exists. LOOPED IS DELIBERATELY A DIFFERENT POPULATION FROM THE BLOCKED-PLAN-REVIEW NUMERATOR -- do not conflate the two: blocked_plan_review_count counts fact_event rows with kind='gate' AND gate='plan_review' AND verdict='block' (the numerator, spec line 413); looped_goals is instead defined by the REVIEW cycle -- a goal with >=1 fact_event row where kind='gate' AND gate='post_review' AND cycle IS NOT NULL AND cycle >= 1 (work.py writes gate='post_review' with cycle=review_cycles, capped by max_review_cycles; spec lines 413/423-426 describe goals that loop implement->review->implement, a review-cycle loop, not a plan-review-block loop). Defining "looped" as "was blocked at plan_review" (the same population as the numerator) would make the multiplier circular -- every blocked plan is by definition looped, so the cost delta would be derived from the very population being counted, not an independent comparison; that reading is rejected here even though it looks like a literal parse of gate='plan_review' AND verdict='block' alone. BOTH POPULATIONS ARE INERT ON EVERY REAL STORE TODAY (#243): ledger_writer._write_event only ever writes six generic columns and never gate/verdict/cycle, so blocked_plan_review_count and looped_goal_count are both zero forever until that gap closes -- named here, not hidden. Median not mean throughout (quantile_cont(x,0.5), matching 2.sql/3.sql/11.sql/13.sql/32.sql/42.sql -- percentiles never rendered as means, spec section 6). INSUFFICIENT-DATA, NOT A GUESS: qualifying_pair_count (LEAST of non_looped_goal_count/looped_goal_count) is 0 whenever either bucket is empty, propagated via NULL medians over an empty FILTER -- no invented minimum-N; cost_delta_seconds/avoided_cost_seconds are NULL exactly then and only then. RESIDUE (#129, not fixed here): dash/render.py's _measured() takes the MAX across every _count-suffixed column on this row, so blocked_plan_review_count/class1_count/class2_count/total_count being >0 can show "has data" in the generic catalog even when avoided_cost_seconds is NULL (e.g. three blocked goals, none done yet) -- a consumer must read qualifying_pair_count or check avoided_cost_seconds IS NULL directly, never the catalog's has-data badge, for the true signal. MULTI-PROJECT PARTITION FIX (independent pre-merge PR review, BLOCKING, the same class of bug 30.sql's own guardrail names as PR #186): every CTE below now carries project_id and every join/GROUP BY includes it, so this view returns ONE ROW PER PROJECT instead of one blended row. Reproduced live pre-fix: (a) goal_id-collision -- looped_goals selected a bare DISTINCT goal_id and the EXISTS join keyed on goal_id alone, so a goal in one project sharing a goal_id with an unrelated project's goal was swept into the wrong project's looped/non-looped bucket purely by id collision; (b) blended aggregates -- blocked/buckets had no PARTITION and no GROUP BY, so a multi-project store (the real --repos shape, insight/__main__.py) paired one project's blocked_plan_review_count against a cost_delta_seconds computed across BOTH projects' goals, producing a NEGATIVE avoided_cost_seconds that described neither project. Both are fixed the same way 30.sql's LAG fix was: carry project_id through every CTE (blocked, post_review_events, looped_goals, multiplier_coverage, goal_cost, buckets) and key every join/GROUP BY on (project_id, goal_id) or project_id alone, never goal_id alone. EMPTY-STORE SHAPE, DECIDED: a fully empty store now returns ZERO rows, not one phantom row -- the pre-fix single blended row was itself an artifact of the old unpartitioned CROSS JOIN of two always-one-row aggregates; there is no dimension table of "expected projects" to synthesize a phantom row from, the identical reasoning 30.sql's own guardrail already applies ("a project never scanned at all... produces zero rows from this view, not a synthesized ABSENT row"). This does not break the CoverageDenominatorMissing contract: insight/dash/render.py's extract_coverage() returns None (no raise) whenever the row itself is None, which is exactly what fetchone()-shaped code sees over zero rows -- verified by test_metric_22_returns_zero_rows_over_a_fully_empty_store, which still calls render_dashboard() and asserts it does not raise. RESIDUE, NAMED NOT FIXED: insight/dash/render.py's generic catalog table (_metric_rows) reads only view_rows[0] for its coverage denominator -- for a genuinely multi-project store this view now returns multiple rows, so the catalog's summary line reflects only the alphabetically-first project_id's coverage, not an aggregate across all of them (the same class of gap #131 fixed for dash/leadership.py's Impact tile specifically; not re-fixed here, out of this focused pass's scope). COVERAGE, TWO FIGURES NOT ONE (finding 3, same PR review): class1_count/class2_count/total_count/coverage_pct are held to their EXACT contractual names (insight.metrics.reliability.COVERAGE_DENOMINATOR_COLUMNS, enforced by test_class_2_metrics_expose_a_coverage_denominator.py) and cover ONLY the blocked_plan_review_count numerator -- they say nothing about the post_review reads that decide the ENTIRE looped/non-looped split, which is what the multiplier (cost_delta_seconds / avoided_cost_seconds) is actually built from. A store where every block event happens to be reliability_class=1 would report coverage_pct=1.0 (misleadingly "fully covered") while the looped-bucket read underneath the multiplier was 100% reliability_class=2 -- a wrong coverage figure is worse than none (spec section 3). Fixed by adding a SECOND, differently-named quartet -- multiplier_class1_count/multiplier_class2_count/multiplier_total_count/multiplier_coverage_pct -- computed over every post_review gate event this view reads to decide the looped/non-looped split (not just the cycle>=1 subset that ends up IN the looped bucket: a low-reliability read that correctly finds NO qualifying cycle is just as unmeasured as one that does). Read both: coverage_pct qualifies blocked_plan_review_count alone, multiplier_coverage_pct qualifies non_looped_goal_count/looped_goal_count/the medians/cost_delta_seconds/avoided_cost_seconds. KNOWN TENSIONS, RECORDED NOT FIXED HERE (PR review, non-blocking): (1) the no-hardcoded-multiplier static guard (test_metrics_no_hardcoded_multiplier.py) is deliberately narrowed to scan ONLY this file (its own docstring explains why: a repo-wide version false-positives on 11.sql's bare-literal `* 4` and 41.sql's `100.0 * ...` percent cast) -- metrics #23/#27/#29 will have no such protection when they land; a follow-up ticket to give each new metric its own narrow guard (or a real SQL-aware check) is warranted, not attempted in this focused pass. (2) qualifying_pair_count (LEAST of non_looped_goal_count/looped_goal_count) can be 1 -- a confident-looking number can rest on a median of exactly one goal per bucket; consistent with 2.sql/32.sql, which also have no minimum-N floor, so this is not a regression introduced here, but it remains a real tension with this repo's own "insufficient data, not a guess" doctrine, worth a dedicated minimum-N story across all of them rather than a one-off fix to this file alone. CONFIRMED CORRECT, NOT CHANGED (PR review): work.py:513-561 writes cycle non-NULL only on verdict='block' (cycle=(rec.get("review_cycles") if v=="block" else None)), so a clean first-pass approval writes cycle=NULL and is correctly excluded from the looped bucket by the cycle >= 1 predicate -- verified live with a dedicated fixture, kept as-is.
WITH plan_review_events AS (
    SELECT project_id, goal_id, verdict, reliability_class
    FROM fact_event
    WHERE kind = 'gate' AND gate = 'plan_review' AND goal_id IS NOT NULL AND project_id IS NOT NULL
),
blocked AS (
    SELECT
        project_id,
        count(*) AS blocked_plan_review_count,
        count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
        count(*) AS total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
            AS coverage_pct
    FROM plan_review_events
    WHERE verdict = 'block'
    GROUP BY project_id
),
post_review_events AS (
    -- amendment A: "looped" is the REVIEW cycle (post_review, cycle>=1), a population
    -- deliberately different from the plan_review-block numerator above -- see guardrail.
    -- Kept as its OWN CTE (not inlined into looped_goals) so multiplier_coverage below can read
    -- every post_review row this view examines -- including the ones that do NOT end up in the
    -- looped bucket -- for the second coverage figure (finding 3).
    SELECT project_id, goal_id, cycle, reliability_class
    FROM fact_event
    WHERE kind = 'gate' AND gate = 'post_review' AND goal_id IS NOT NULL AND project_id IS NOT NULL
),
looped_goals AS (
    SELECT DISTINCT project_id, goal_id
    FROM post_review_events
    WHERE cycle IS NOT NULL AND cycle >= 1
),
multiplier_coverage AS (
    -- finding 3: the reliability of the read that decides the ENTIRE looped/non-looped split,
    -- reported under its own name so it can never be misread as the numerator's coverage_pct.
    SELECT
        project_id,
        count(*) FILTER (WHERE reliability_class = 1) AS multiplier_class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS multiplier_class2_count,
        count(*) AS multiplier_total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
            AS multiplier_coverage_pct
    FROM post_review_events
    GROUP BY project_id
),
goal_cost AS (
    SELECT
        fact_goal.project_id,
        fact_goal.goal_id,
        date_diff('second', fact_goal.claimed_ts, fact_goal.terminal_ts) AS cost_seconds,
        EXISTS (
            SELECT 1 FROM looped_goals lg
            WHERE lg.goal_id = fact_goal.goal_id AND lg.project_id = fact_goal.project_id
        ) AS looped
    FROM fact_goal
    WHERE fact_goal.outcome = 'done'
      AND fact_goal.claimed_ts IS NOT NULL AND fact_goal.terminal_ts IS NOT NULL
      AND fact_goal.terminal_ts >= fact_goal.claimed_ts
),
buckets AS (
    SELECT
        project_id,
        count(*) FILTER (WHERE NOT looped) AS non_looped_goal_count,
        count(*) FILTER (WHERE looped) AS looped_goal_count,
        quantile_cont(cost_seconds, 0.5) FILTER (WHERE NOT looped) AS non_looped_median_cost_seconds,
        quantile_cont(cost_seconds, 0.5) FILTER (WHERE looped) AS looped_median_cost_seconds
    FROM goal_cost
    GROUP BY project_id
),
projects AS (
    -- The driving set of project_ids this view reports on: any project with at least one of the
    -- three independent signals (a blocked plan_review event, a post_review read, or a
    -- qualifying done goal) gets a row. A project with none of the three produces no row at all
    -- -- see the empty-store guardrail above.
    SELECT project_id FROM blocked
    UNION
    SELECT project_id FROM multiplier_coverage
    UNION
    SELECT project_id FROM buckets
)
SELECT
    projects.project_id,
    COALESCE(blocked.blocked_plan_review_count, 0) AS blocked_plan_review_count,
    COALESCE(buckets.non_looped_goal_count, 0) AS non_looped_goal_count,
    COALESCE(buckets.looped_goal_count, 0) AS looped_goal_count,
    LEAST(COALESCE(buckets.non_looped_goal_count, 0), COALESCE(buckets.looped_goal_count, 0))
        AS qualifying_pair_count,
    buckets.non_looped_median_cost_seconds,
    buckets.looped_median_cost_seconds,
    (buckets.looped_median_cost_seconds - buckets.non_looped_median_cost_seconds)
        AS cost_delta_seconds,
    (COALESCE(blocked.blocked_plan_review_count, 0)
        * (buckets.looped_median_cost_seconds - buckets.non_looped_median_cost_seconds))
        AS avoided_cost_seconds,
    COALESCE(blocked.class1_count, 0) AS class1_count,
    COALESCE(blocked.class2_count, 0) AS class2_count,
    COALESCE(blocked.total_count, 0) AS total_count,
    blocked.coverage_pct,
    COALESCE(multiplier_coverage.multiplier_class1_count, 0) AS multiplier_class1_count,
    COALESCE(multiplier_coverage.multiplier_class2_count, 0) AS multiplier_class2_count,
    COALESCE(multiplier_coverage.multiplier_total_count, 0) AS multiplier_total_count,
    multiplier_coverage.multiplier_coverage_pct
FROM projects
LEFT JOIN blocked ON blocked.project_id = projects.project_id
LEFT JOIN buckets ON buckets.project_id = projects.project_id
LEFT JOIN multiplier_coverage ON multiplier_coverage.project_id = projects.project_id
ORDER BY projects.project_id
