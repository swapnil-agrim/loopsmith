-- name: Ledger says done but linked PR shows no merge
-- class: Consistency
-- metric: ledger_done_pr_open
-- action: merge the PR, or correct the goal's linked pr if it was abandoned or superseded
-- severity: FAIL
-- guardrail: fact_event HAS NO pr COLUMN AND CARRIES NO PR CONCEPT AT ALL -- insight/ingest/ledger_writer.py's own _EVENT_INSERT_SQL (lines 178-181) writes exactly six columns (project_id, goal_id, ts, actor_id, kind, reliability_class); store.py's fact_event DDL agrees -- so this rule joins through fact_goal.pr instead. fact_goal.pr/.issue HAVE ZERO WRITERS UNDER insight/ingest/ TODAY -- artifact_reader.py's own _GOAL_UPSERT_SQL column list (project_id, goal_id, title, lane, source, done_when_present, plan_artifact_present, status, verify_command) has no pr, no issue, the same fact coverage_review_missing.sql's own guardrail already states -- so this rule's population legitimately reads 0 and renders ABSENT on this repo's own real data today, which is CORRECT, not a defect (.sdlc/plans/120.md Design decision 1's own "honestly disclosed" paragraph). A discovery.source: github GOAL IS THE SHAPE MOST LIKELY TO CARRY A REAL LINKED PR, AND IT IS OUT OF SCOPE HERE TOO -- artifact_reader.py's discover_goal_files/goal_record (lines 76-77 and surrounding) only ever write a fact_goal row for a LOCAL file goal read from .sdlc/goals/*.md frontmatter; a github-mode goal lives as a GitHub issue with no frontmatter text and never gets a fact_goal row at all (#102 Design decision E), so this rule's population misses exactly the goals most likely to have a real fact_goal.pr value once a writer exists -- the same local-mode-only limit definition_no_done_when.sql's own guardrail already discloses for the identical reason, extended here to the pr join key specifically. THE MERGE SIGNAL IS fact_pr_review/fact_pr_check.pr_merged_ts, SOURCED FROM gh's OWN mergedAt (gh_reader.py, lines 270/322 propagate pr.get("mergedAt") into both row-builders), NOT fact_merge_lead_time -- that table is git-log-derived (git_reader.py detects a merge only from one of two specific commit-subject shapes) and would misread a PR merged by neither shape as "not merged"; rejected for this specific join, named by table so a future reader does not "fix" this rule to match coverage_review_missing.sql's own table choice without re-reading why it differs (Design decision 1). gh NEVER REQUESTS PR state (gh_reader.py:159-161's own --json field list: number,createdAt,mergedAt,reviews,comments,statusCheckRollup, no state) -- "still open" and "closed without merging" are therefore indistinguishable here, both reading as pr_merged_ts IS NULL, named honestly, not papered over. POPULATION IS BOUNDED TO PRs gh ACTUALLY FETCHED (present in fact_pr_review OR fact_pr_check for that (project_id, pr_number)) -- mirroring coverage_review_missing.sql's own fetch-window guard (gh_reader.py's _PR_FETCH_LIMIT = 50, applied unconditionally): a PR fact_goal.pr names that gh never looked at was never inspected, not confirmed open, and counting it would silently convert "we don't know" into "policy violated". EVERY JOIN KEYS ON (project_id, pr_number), NEVER BARE pr_number -- two projects sharing a store can collide on the same PR number, reproduced live this session (.sdlc/plans/120.md Key facts item 5): p1's open 102 and p5's merged 102 are kept apart. THE LEDGER-DONE SIDE IS AGGREGATED TO MIN(ts) PER (project_id, goal_id) -- a goal with more than one kind='done' fact_event row (nothing in ledger_writer.py/fact_event's schema prevents or dedupes repeated done records) would otherwise contribute one evidence row per event; reproduced live (Key facts item 4): two done events for the same goal produced two rows before this fix, one (the earlier, MIN) after it. MALFORMED-PAYLOAD DISCLOSURE: this rule reads no raw_payload JSON at all (its population and evidence are built entirely from typed columns), so the InvalidInputException hazard the sibling d1/d2 rules below must disclose does not apply to this rule.
-- population: SELECT count(*) FROM (SELECT DISTINCT dg.project_id, dg.goal_id FROM (SELECT DISTINCT project_id, goal_id FROM fact_event WHERE kind = 'done') dg JOIN fact_goal g ON g.project_id = dg.project_id AND g.goal_id = dg.goal_id JOIN (SELECT project_id, pr_number FROM fact_pr_review UNION SELECT project_id, pr_number FROM fact_pr_check) fp ON fp.project_id = g.project_id AND fp.pr_number = g.pr WHERE g.pr IS NOT NULL)
WITH pr_state AS (
    SELECT project_id, pr_number, MAX(pr_merged_ts) AS pr_merged_ts
    FROM (
        SELECT project_id, pr_number, pr_merged_ts FROM fact_pr_review
        UNION ALL
        SELECT project_id, pr_number, pr_merged_ts FROM fact_pr_check
    )
    GROUP BY project_id, pr_number
),
done_goals AS (
    SELECT project_id, goal_id, MIN(ts) AS ledger_done_ts
    FROM fact_event
    WHERE kind = 'done'
    GROUP BY project_id, goal_id
)
SELECT dg.project_id, dg.goal_id, dg.ledger_done_ts, g.pr AS pr_number, ps.pr_merged_ts
FROM done_goals dg
JOIN fact_goal g ON g.project_id = dg.project_id AND g.goal_id = dg.goal_id
JOIN pr_state ps ON ps.project_id = g.project_id AND ps.pr_number = g.pr
WHERE g.pr IS NOT NULL AND ps.pr_merged_ts IS NULL
ORDER BY dg.project_id, dg.goal_id
