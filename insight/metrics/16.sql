-- name: Review-cycle distribution
-- question: Is the cap masking a problem?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: FUNCTIONALLY INERT TODAY (#243): ledger_writer.py's _write_event writes only six generic columns and never cycle, so every real `insight ingest` row has cycle NULL forever until that gap closes -- this view's logic is fully tested against fixtures that bypass the ledger/ingest pipeline entirely, same posture 22.sql already established. `>=` IS THE DELIBERATE BOUNDARY, NOT `=`: work.py's own post_review() parks a goal the instant `cycles >= cap` first becomes true (work.py:536-542, verified by reading it directly), so goal_max_cycle > cap is unreachable through the loop's own enforcement today -- but a strict `=` would silently hide a goal that somehow drifted past its cap, which is the worse case to hide; fires at cap and defensively at cap+1, never at cap-1. NULL CAP READS ABSENT, NEVER A FALLBACK LITERAL (mirrors 42.sql's own missing-key/missing-config collapse, Decision 2a): two NULL paths both real -- no dim_project row at all for the project (the LEFT JOIN finds nothing), or a dim_project row whose config_json has no work.max_review_cycles key (json_extract returns NULL for a missing path) -- either way cap/goals_at_cap_count/mass_at_cap_share all render NULL and status renders 'ABSENT', never 0/0.0/a code-default like work.py's own settings().get('max_review_cycles', 3): the cap is a per-project value this repo alone happens to set to 5, and guessing one project's cap from another's config or from the code default would silently misjudge every project that never set the flag. AMENDMENT A -- MALFORMED config_json DEGRADES PER-PROJECT, DOES NOT CRASH THE VIEW: project_cap wraps its CAST(json_extract(...)) in `CASE WHEN json_valid(config_json) THEN ... ELSE NULL END` -- without this guard one dim_project row containing invalid JSON raises InvalidInputException for the ENTIRE view (reproduced live by an independent reviewer against the full 26-file catalog), because the CTE reads every dim_project row unconditionally; 42.sql shares this identical unguarded json_extract(config_json, ...) idiom and is NOT fixed here -- flagged as adjacent debt, since the real ingest path json.loads()-validates before writing and this is unreachable through normal ingest. PASS/FAIL ONLY, NO WARN TIER (Decision 1, a deliberate rejection of a graduated share threshold that would need an unwarranted magnitude literal): status = 'FAIL' when goals_at_cap_count > 0, else 'PASS' when cap is known, else 'ABSENT' -- spec's own wording is "mass ... IS the alarm", one goal exhausting the cap is already the signal, not a fraction of the backlog. reliability_class: 2, a real decision not inherited silently -- spec's own vocabulary table marks post_review class 1 in principle, but work.py's real ledger.safe_append for gate='post_review' routes through stream=ledger.EVENTS exactly like park does, so read_all_with_reliability's directory-based tagging (#248) marks every real row class 2 regardless; filtering reliability_class = 1 here would make this view permanently and silently empty even after #243 closes, so it declares class 2 honestly and reports coverage alongside instead (matching 22.sql's identical fork for the same population). PER-PROJECT PARTITIONING IS MANDATORY (#144's own PR was BLOCKED for cross-project leakage): every CTE carries project_id through its own GROUP BY/JOIN, one row per project, a colliding goal_id across two projects never blends -- following 22.sql's fix, not 14.sql's still-unpartitioned gap (adjacent debt, not fixed here). severity_rank is a lateral column alias derived FROM this file's own already-computed status (CASE status WHEN 'PASS' THEN 0 WHEN 'ABSENT' THEN 1 WHEN 'WARN' THEN 2 WHEN 'FAIL' THEN 3 ELSE NULL END, unreachable ELSE, matching 24.sql's pattern) so the two columns cannot disagree by construction; this view's own status CASE never produces 'WARN'. view_rows[0] catalog residue (#253, named not fixed) and the metrics-side no-literal-threshold static guard gap (#253) both apply here exactly as 22.sql's own guardrail already named for itself -- not re-fixed in this pass.
WITH post_review_cycles AS (
    SELECT project_id, goal_id, cycle, reliability_class
    FROM fact_event
    WHERE kind = 'gate' AND gate = 'post_review' AND cycle IS NOT NULL
      AND project_id IS NOT NULL AND goal_id IS NOT NULL
),
goal_cycles AS (
    SELECT project_id, goal_id, MAX(cycle) AS goal_max_cycle
    FROM post_review_cycles
    GROUP BY project_id, goal_id
),
coverage AS (
    SELECT
        project_id,
        count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
        count(*) AS total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4) AS coverage_pct
    FROM post_review_cycles
    GROUP BY project_id
),
project_cap AS (
    -- Amendment A: a malformed config_json row degrades to a NULL cap for THAT project only,
    -- never a raised exception for the whole view -- see the guardrail above.
    SELECT
        project_id,
        CASE
            WHEN json_valid(config_json)
                -- TRY_CAST, not CAST: json_valid only proves the text is JSON, not that the value
                -- fits an INTEGER. A syntactically fine {"work":{"max_review_cycles":9999...9}}
                -- raises ConversionException and takes down the WHOLE view and the whole catalog
                -- with it, healthy sibling projects included -- the same blast radius the
                -- json_valid guard was added to close, one layer in.
                -- NULLIF(..., 0) because 0 means the cap is DISABLED, not "hit immediately":
                -- work.py:536,542 does `cap = int(... or 0)` then `over_cap = cap and cycles >= cap`,
                -- so a configured 0 is falsy and the loop never parks for the cap at all. Without
                -- this, a project that switched the cap off rendered mass=1.0, status=FAIL -- a
                -- confident alarm about a mechanism that is not running. Mirrors work.py's own
                -- falsy-zero semantics rather than inventing a second reading of the same value.
                -- TRUNC via DOUBLE, not a bare cast: DuckDB rounds half-to-even (3.5 -> 4) while
                -- work.py's `int(...)` truncates toward zero (3.5 -> 3), so a fractional cap
                -- would render a cap one higher than the one actually enforced -- a silent
                -- off-by-one in the very number this metric exists to compare against.
                THEN NULLIF(TRY_CAST(TRUNC(TRY_CAST(json_extract(config_json, '$.work.max_review_cycles') AS DOUBLE)) AS INTEGER), 0)
            ELSE NULL
        END AS cap
    FROM dim_project
),
project_mass AS (
    SELECT
        gc.project_id,
        pc.cap,
        COUNT(*) AS looped_goal_count,
        COUNT(*) FILTER (WHERE pc.cap IS NOT NULL AND gc.goal_max_cycle >= pc.cap)
            AS goals_at_cap_count_raw
    FROM goal_cycles gc
    LEFT JOIN project_cap pc ON pc.project_id = gc.project_id
    GROUP BY gc.project_id, pc.cap
)
SELECT
    project_mass.project_id,
    project_mass.cap,
    project_mass.looped_goal_count,
    CASE WHEN project_mass.cap IS NULL THEN NULL ELSE project_mass.goals_at_cap_count_raw END
        AS goals_at_cap_count,
    CASE
        WHEN project_mass.cap IS NULL THEN NULL
        ELSE ROUND(
            project_mass.goals_at_cap_count_raw * 1.0 / NULLIF(project_mass.looped_goal_count, 0),
            4
        )
    END AS mass_at_cap_share,
    CASE
        WHEN project_mass.cap IS NULL THEN 'ABSENT'
        WHEN project_mass.goals_at_cap_count_raw > 0 THEN 'FAIL'
        ELSE 'PASS'
    END AS status,
    CASE status
        WHEN 'PASS' THEN 0
        WHEN 'ABSENT' THEN 1
        WHEN 'WARN' THEN 2
        WHEN 'FAIL' THEN 3
        ELSE NULL
    END AS severity_rank,
    coverage.class1_count,
    coverage.class2_count,
    coverage.total_count,
    coverage.coverage_pct
FROM project_mass
JOIN coverage USING (project_id)
ORDER BY project_mass.project_id
