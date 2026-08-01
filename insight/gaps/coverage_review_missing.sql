-- name: Merged PR missing a required review
-- class: Coverage
-- metric: pr_review_coverage
-- action: get an approving review recorded, or turn off work.require_review if review is not actually required
-- severity: FAIL
-- guardrail: EVIDENCE IS PER-PR (project_id, pr_number), NEVER goal_id -- fact_goal.pr has ZERO writers anywhere under insight/ingest/ today (grep-confirmed this session: the only writer touching fact_goal is artifact_reader.py's _GOAL_UPSERT_SQL, whose column list has no `pr`, no `issue`), so no live join from a PR back to the goal that opened it exists (Design decision 3, .sdlc/plans/117.md). THE PR ROSTER IS fact_merge_lead_time.pr_number, NOT A DEDICATED PR TABLE (none exists) -- 42.sql's own guardrail already treats this table as the live PR roster; reused here. THE POPULATION IS BOUNDED BY GH'S OWN FETCH WINDOW, NOT JUST BY REVIEW POLICY -- insight/ingest/gh_reader.py:59's `_PR_FETCH_LIMIT = 50`, applied unconditionally regardless of --gh-window-days, means fact_pr_review/fact_pr_check only ever cover the ~50 most-recently-updated PRs, while fact_merge_lead_time (git-derived) carries the full, uncapped merge history; a PR merged outside that fetch window was never inspected by gh at all and must not be judged -- this rule's population additionally requires (project_id, pr_number) to appear in fact_pr_review OR fact_pr_check before counting a merged PR (verified live this session against this repo's own real ingest: PRs #1-65 have zero rows in either gh table, not because they were reviewed and rejected, but because gh was never asked). THE POPULATION REQUIRES work.require_review TO MEAN APPROVAL, NEVER 'changes' OR A MISSING/OFF KEY, AND IT NORMALISES THE VALUE THE SAME WAY THE CODE THAT DEFINES IT DOES -- config_json is stored VERBATIM at ingest with no normalisation, but work.py:315-321's review_mode() maps `true` to approval outright and lowercases/strips every string, and doctor.py's _review_gate_state() agrees; a bare `= 'approval'` here would therefore read a project configured `require_review: true` or "Approval" as population 0 and render a permanent, silent ABSENT for a policy that is genuinely ACTIVE -- a false 'nothing to check' rather than a false FAIL, but wrong in the same family, so this rule matches lower(trim(...)) IN ('approval','true'). Anything unrecognised falls out, matching review_mode()'s own 'a review gate you didn't ask for must never block a merge'. On the vocabulary itself: skills/sdlc-loop/scripts/work.py:374-378's own three-way vocabulary: off is no gate; changes parks only on a CHANGES_REQUESTED review, an unresolved thread, or a loopsmith:block comment, and NO APPROVAL IS EVER REQUIRED; approval is the above, AND requires an actual approving verdict (work.py:410 is the only branch in the whole function gated on an approving verdict, and changes mode never reaches it). Counting 'changes' here would flag PRs under a policy that never asked for approval in the first place -- a missing/never-ingested config key and 'off' are excluded the same way 42.sql's "missing key is not a disabled flag" rule already excludes them, extended symmetrically to 'changes' too. POPULATION AND EVIDENCE BOTH COUNT DISTINCT (project_id, pr_number), NEVER BARE pr_number -- GitHub PR numbers are per-repo and two projects sharing a store (--repos, #106) can collide on the same number. BOTH VERDICT SPELLINGS ('APPROVED' native GitHub, 'APPROVE' this project's own loopsmith:approve PR-comment marker) COUNT AS APPROVAL, matching 42.sql's own reasoning verbatim. AGAINST THIS REPO'S OWN REAL, LIVE CONFIG (work.require_review: "changes", not "approval"), this rule's population is 0 -- ABSENT -- by design: this repo has never opted into the approval policy this rule checks, so it has nothing for the rule to check. The changes-mode question ("was a PR ever blocked and left unresolved?") is a genuinely different, unbuilt check, not this rule.
-- population: SELECT count(*) FROM (SELECT DISTINCT flt.project_id, flt.pr_number FROM fact_merge_lead_time flt JOIN dim_project dp ON dp.project_id = flt.project_id WHERE flt.pr_number IS NOT NULL AND lower(trim(COALESCE(json_extract_string(dp.config_json, '$.work.require_review'), ''))) IN ('approval', 'true') AND (EXISTS (SELECT 1 FROM fact_pr_review r WHERE r.project_id = flt.project_id AND r.pr_number = flt.pr_number) OR EXISTS (SELECT 1 FROM fact_pr_check c WHERE c.project_id = flt.project_id AND c.pr_number = flt.pr_number)))
WITH require_review_projects AS (
    SELECT project_id
    FROM dim_project
    WHERE lower(trim(COALESCE(json_extract_string(config_json, '$.work.require_review'), ''))) IN ('approval', 'true')
),
fetched_prs AS (
    SELECT DISTINCT project_id, pr_number FROM fact_pr_review
    UNION
    SELECT DISTINCT project_id, pr_number FROM fact_pr_check
),
merged_prs AS (
    SELECT DISTINCT flt.project_id, flt.pr_number
    FROM fact_merge_lead_time flt
    JOIN require_review_projects rrp USING (project_id)
    JOIN fetched_prs fp USING (project_id, pr_number)
    WHERE flt.pr_number IS NOT NULL
),
approved_prs AS (
    SELECT DISTINCT project_id, pr_number
    FROM fact_pr_review
    WHERE UPPER(verdict) IN ('APPROVED', 'APPROVE')
)
SELECT mp.project_id, mp.pr_number
FROM merged_prs mp
LEFT JOIN approved_prs ap USING (project_id, pr_number)
WHERE ap.pr_number IS NULL
ORDER BY mp.project_id, mp.pr_number
