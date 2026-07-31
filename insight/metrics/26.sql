-- name: Verify reliability (current state)
-- question: Is the proving command trustworthy?
-- personas: IC, manager, cross-functional
-- reliability_class: 1
-- guardrail: DARK METRIC: fact_goal.verify_state is 0/19 populated in this repo's own real ingest today (#111 research dossier, re-verified this session -- grep -rn verify_state insight/ outside store.py's own DDL/upsert-column-list and test_store.py returns zero writers) -- a fixture-green test is not evidence of a live dashboard number. SCOPE DECISION (already made for this story, not relitigated here): no ingest reader for .sdlc/state/verify/*.json is added by this story -- building one crosses into E1's ingest scope and widens a medium story (#109/#110 precedent); the reader is a named follow-up (Punted). ABSENT DEFINITION: verify_state IS NULL renders ABSENT ("no evidence recorded for this goal at all"), distinct from a recorded 'pass' or 'fail' -- this is the case test_metric_26_verify_reliability.py's absent-gate test pins. VOCABULARY CHOICE, UNVERIFIED AGAINST ANY REAL WRITER: since no writer exists yet, this view assumes verify_state holds 'pass' or 'fail' (mirroring loop.py's own exit==0 vs nonzero split) and folds ANY other value -- including a value no future reader has been designed yet, or a typo -- into ABSENT rather than guessing a pass/fail reading for it; this is a defensive default, not a claim backed by a fixture exercising an unrecognized string. CURRENT-STATE ONLY, PER SPEC'S OWN FRAMING ("#26 ships as the current-state tile only; its trend is tranche 2", spec line 489): one row per goal, no window/history dimension -- matches loop.py's own overwrite-latest-only semantics for state/verify/<goal>.json (verified in the #111 dossier). CANNOT TELL YOU about the exit=3 NO-COMMAND case (a goal whose verify_command was never declared) -- whichever future story builds the reader must decide whether that renders 'fail', 'absent', or a third value; not resolved here, and this view has no fixture proving that case since it does not yet exist as data. SEVERITY_RANK (post-pre-PR review): an INTEGER column, `CASE status WHEN 'PASS' THEN 0 WHEN 'ABSENT' THEN 1 WHEN 'WARN' THEN 2 WHEN 'FAIL' THEN 3 ELSE NULL END`, derived FROM the already-computed status column rather than a second CASE over verify_state -- mirrors pipeline.py's own `_ORDER`, pinned by test_metric_severity_rank.py against pipeline.py's real source (read as TEXT/AST, never imported -- see that test's own module docstring). COLUMN-SHADOWING BUG, FOUND AND FIXED WHILE WIRING THIS UP (not in the original plan, not in 24.sql/30.sql -- specific to this file): fact_goal ALREADY HAS A REAL, UNRELATED COLUMN NAMED `status` (store.py's own `ALTER TABLE fact_goal ADD COLUMN IF NOT EXISTS status VARCHAR`, a goal-lifecycle field this metric does not read or write). A bare `SELECT ..., CASE ... AS status, CASE status WHEN ... END AS severity_rank FROM fact_goal` resolves the second CASE's `status` reference to fact_goal's REAL column (NULL in every fixture row here), not the just-computed alias -- reproduced live in a reduced repro: a table with a real NULL `status` column and a same-named computed alias silently returns rank 99 (the ELSE) instead of the intended 0, with no error. 24.sql/30.sql do not hit this because their final SELECT reads from an intermediate CTE (`gates`/`trend`) that carries no column named `status` at all, so DuckDB's lateral-alias resolution has nothing to shadow it with. FIX: this file's status computation is now itself a CTE (`scored`), so the outer SELECT's `status` reference resolves only against the CTE's own projected column -- fact_goal's real `status` column is never selected into `scored` and is therefore out of scope for the outer query, structurally, not by naming discipline alone. This view's own status CASE never emits WARN (verify_state only ever maps to PASS/FAIL/ABSENT here) -- severity_rank's WARN branch is exercised by 24.sql/30.sql's own fixtures instead, not this file's; still declared here for a consistent four-branch shape across all three views. ELSE NULL is UNREACHABLE by this file's own status CASE (its own ELSE always resolves to 'ABSENT', never a fifth value) -- defensive-only, not test-verified, same posture as this file's other unpinned claims above.
-- data_status: dark
WITH scored AS (
    SELECT
        project_id,
        goal_id,
        verify_state,
        CASE
            WHEN verify_state = 'pass' THEN 'PASS'
            WHEN verify_state = 'fail' THEN 'FAIL'
            ELSE 'ABSENT'
        END AS status
    FROM fact_goal
)
SELECT
    project_id,
    goal_id,
    verify_state,
    status,
    CASE status
        WHEN 'PASS' THEN 0
        WHEN 'ABSENT' THEN 1
        WHEN 'WARN' THEN 2
        WHEN 'FAIL' THEN 3
        ELSE NULL
    END AS severity_rank
FROM scored
