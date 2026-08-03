-- name: Prevented rework (self-derived multiplier)
-- question: What did the gates save?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: cost = wall-clock elapsed seconds from fact_goal.claimed_ts to fact_goal.terminal_ts, populated only by goal_lifecycle.py (#217) for github-discovery-mode projects -- a goal missing either timestamp, or with terminal_ts < claimed_ts (clock skew, never legitimate), is EXCLUDED from both cost buckets, never coerced to 0 and never counted as evidence either way. NOTED DUPLICATION (not fixed here): this cost formula is byte-identical to 2.sql's cycle-time computation -- the spec reserves a distinct dollar/token "cost" concept for metric #17 (fact_event.cost_cents, equally unpopulated today), so this metric's "cost" is really elapsed wall-clock time standing in for a real cost unit and should move to fact_event.cost_cents (or whatever #17 lands on) once that data exists. LOOPED IS DELIBERATELY A DIFFERENT POPULATION FROM THE BLOCKED-PLAN-REVIEW NUMERATOR -- do not conflate the two: blocked_plan_review_count counts fact_event rows with kind='gate' AND gate='plan_review' AND verdict='block' (the numerator, spec line 413); looped_goals is instead defined by the REVIEW cycle -- a goal with >=1 fact_event row where kind='gate' AND gate='post_review' AND cycle IS NOT NULL AND cycle >= 1 (work.py writes gate='post_review' with cycle=review_cycles, capped by max_review_cycles; spec lines 413/423-426 describe goals that loop implement->review->implement, a review-cycle loop, not a plan-review-block loop). Defining "looped" as "was blocked at plan_review" (the same population as the numerator) would make the multiplier circular -- every blocked plan is by definition looped, so the cost delta would be derived from the very population being counted, not an independent comparison; that reading is rejected here even though it looks like a literal parse of gate='plan_review' AND verdict='block' alone. BOTH POPULATIONS ARE INERT ON EVERY REAL STORE TODAY (#243): ledger_writer._write_event only ever writes six generic columns and never gate/verdict/cycle, so blocked_plan_review_count and looped_goal_count are both zero forever until that gap closes -- named here, not hidden. Median not mean throughout (quantile_cont(x,0.5), matching 2.sql/3.sql/11.sql/13.sql/32.sql/42.sql -- percentiles never rendered as means, spec section 6). INSUFFICIENT-DATA, NOT A GUESS: qualifying_pair_count (LEAST of non_looped_goal_count/looped_goal_count) is 0 whenever either bucket is empty, propagated via NULL medians over an empty FILTER -- no invented minimum-N; cost_delta_seconds/avoided_cost_seconds are NULL exactly then and only then. RESIDUE (#129, not fixed here): dash/render.py's _measured() takes the MAX across every _count-suffixed column on this row, so blocked_plan_review_count/class1_count/class2_count/total_count being >0 can show "has data" in the generic catalog even when avoided_cost_seconds is NULL (e.g. three blocked goals, none done yet) -- a consumer must read qualifying_pair_count or check avoided_cost_seconds IS NULL directly, never the catalog's has-data badge, for the true signal.
WITH plan_review_events AS (
    SELECT goal_id, verdict, reliability_class
    FROM fact_event
    WHERE kind = 'gate' AND gate = 'plan_review' AND goal_id IS NOT NULL
),
blocked AS (
    SELECT
        count(*) AS blocked_plan_review_count,
        count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
        count(*) AS total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
            AS coverage_pct
    FROM plan_review_events
    WHERE verdict = 'block'
),
looped_goals AS (
    -- amendment A: "looped" is the REVIEW cycle (post_review, cycle>=1), a population
    -- deliberately different from the plan_review-block numerator above -- see guardrail.
    SELECT DISTINCT goal_id
    FROM fact_event
    WHERE kind = 'gate' AND gate = 'post_review' AND goal_id IS NOT NULL
        AND cycle IS NOT NULL AND cycle >= 1
),
goal_cost AS (
    SELECT
        fact_goal.goal_id,
        date_diff('second', fact_goal.claimed_ts, fact_goal.terminal_ts) AS cost_seconds,
        EXISTS (
            SELECT 1 FROM looped_goals lg WHERE lg.goal_id = fact_goal.goal_id
        ) AS looped
    FROM fact_goal
    WHERE fact_goal.outcome = 'done'
      AND fact_goal.claimed_ts IS NOT NULL AND fact_goal.terminal_ts IS NOT NULL
      AND fact_goal.terminal_ts >= fact_goal.claimed_ts
),
buckets AS (
    SELECT
        count(*) FILTER (WHERE NOT looped) AS non_looped_goal_count,
        count(*) FILTER (WHERE looped) AS looped_goal_count,
        quantile_cont(cost_seconds, 0.5) FILTER (WHERE NOT looped) AS non_looped_median_cost_seconds,
        quantile_cont(cost_seconds, 0.5) FILTER (WHERE looped) AS looped_median_cost_seconds
    FROM goal_cost
)
SELECT
    blocked.blocked_plan_review_count,
    buckets.non_looped_goal_count,
    buckets.looped_goal_count,
    LEAST(buckets.non_looped_goal_count, buckets.looped_goal_count) AS qualifying_pair_count,
    buckets.non_looped_median_cost_seconds,
    buckets.looped_median_cost_seconds,
    (buckets.looped_median_cost_seconds - buckets.non_looped_median_cost_seconds)
        AS cost_delta_seconds,
    (blocked.blocked_plan_review_count
        * (buckets.looped_median_cost_seconds - buckets.non_looped_median_cost_seconds))
        AS avoided_cost_seconds,
    blocked.class1_count,
    blocked.class2_count,
    blocked.total_count,
    blocked.coverage_pct
FROM blocked, buckets
