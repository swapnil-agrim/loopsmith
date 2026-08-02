-- name: Goal has no verify command
-- class: Coverage
-- metric: 26
-- action: set the goal's verify_command frontmatter field, or the project's verify.command config
-- severity: WARN
-- guardrail: EXACT RECONSTRUCTION OF loop.py's OWN PRECEDENCE, NOT A LABELLED PROXY (Design decision 1, .sdlc/plans/117.md) -- this reads the same two fields, in the same precedence, skills/sdlc-loop/scripts/loop.py:172-179 itself consults before printing NO-COMMAND and exiting 3: goal frontmatter verify_command (fact_goal.verify_command), falling back to project config verify.command (dim_project.config_json's $.verify.command via json_extract_string). A goal where BOTH are absent is a goal loop.py:verify_goal would print NO-COMMAND for today, at the current ingest snapshot -- not an approximation of that fact, the same two reads. NOT LABELLED proxy: true -- proxy describes a source that measures a different, coarser thing than its ideal target (e.g. 24.sql's per-commit pct standing in for a per-goal fact); this rule reads the literal two fields loop.py itself reads, so labelling it proxy would overstate its own uncertainty. IT DOES CARRY the ordinary ingest-freshness caveat every live table in this store already has: this reflects the LAST INGESTED SNAPSHOT of both fields, not a live re-read -- if a goal's frontmatter or the project's config.json changed since the last `insight ingest` run, this rule reports the state as of that snapshot. NO NON-LOCAL-GOAL CONTAMINATION -- fact_goal has exactly one writer (insight/ingest/artifact_reader.py), and it only ever writes local-.md-sourced goals (discover_goal_files, #102 Design decision E), the identical scope loop.py's own `goal_path.suffix == ".md" and goal_path.exists()` guard restricts itself to; every row this rule can see is already inside that same scope.
-- population: SELECT count(*) FROM fact_goal
-- config_field: verify.command
SELECT g.project_id, g.goal_id
FROM fact_goal g
LEFT JOIN dim_project dp ON dp.project_id = g.project_id
WHERE (g.verify_command IS NULL OR g.verify_command = '')
  AND (json_extract_string(dp.config_json, '$.verify.command') IS NULL
       OR json_extract_string(dp.config_json, '$.verify.command') = '')
ORDER BY g.project_id, g.goal_id
