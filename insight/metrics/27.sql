-- name: Decision-gate denials
-- question: Are the invariants earning their keep?
-- personas: manager, leadership
-- reliability_class: 2
-- data_status: dark
-- guardrail: DEEP-DARK (#147 Design decision 1, THEN #243): no `why` column existed on fact_event before this story -- #243 alone (which only fixes DROPPED columns on an already-existing schema) is insufficient here; a writer for this new column is ALSO needed, both deferred. WHY IS BUILT AS: decision_gate.py's _emit_decision_event prefixes the edited file's repo-relative path, then per violating decision joins `f"{id} {title}".strip(): {name}={actual!r} violates ..."`, multiple violations joined by " | ", with one fixed trailing INVARIANT suffix sentence appended once. EXTRACTION IS `regexp_extract(why, '^[^:]*:\s*([^\s:]+)', 1)`, NULLIF'd against '' -- anchored at the START of `why` (the one structural guarantee the writer gives: a file-path prefix, then exactly one ": " before the real denial text), capturing the leading non-whitespace-non-colon run right after that prefix colon, i.e. the first token of `ident`, which is the id by construction. RE-VERIFIED SEVEN-CASE CONTRACT (DuckDB 1.4.5, this pattern is the FIX for a blocking finding, not the original draft): (1) a normal id+title extracts correctly; (2) a title containing its own colon still extracts correctly, the anchored prefix-strip never re-enters the string past the first colon; (3) multiple " | "-joined violations extract ONLY the first id, silently dropping every later one -- named, unfixed, a second extraction pass is out of scope; (4) a title-less decision with NO rationale now extracts correctly (the draft pattern used to fail this case outright); (5) THE BLOCKING FINDING, FIXED -- a title-less decision WITH a rationale (id DEC-002, title "", rationale present): the draft pattern returned the silently-wrong non-NULL 'DEC-002:' (a colon is non-whitespace, so its bare (\S+) swallowed it whole); the fixed pattern correctly returns 'DEC-002'; (6) an id containing internal whitespace ("DEC 001") still silently truncates to its first word ("DEC") -- UNCHANGED by the fix and now the SINGLE SHARPEST LIMITATION REMAINING, since a multi-token id is syntactically indistinguishable from a real single-word one and no id-format contract exists to close it from the read side; (7) an empty-id registry-authoring shape ("id": "") -- validate()-flagged but evaluate()-reachable since validate() is advisory-only -- collapses `ident` to "", so the capture group has nothing to match immediately after the prefix colon+space and regexp_extract genuinely returns '', landing FALSE -- this is the load-bearing proof the FALSE state is real and reachable post-fix. A SEPARATE, STILL-OPEN, NON-BLOCKING RESIDUAL OF THE SAME FAILURE CLASS -- STATED AT ITS REAL, WIDER SCOPE AFTER A PR-REVIEW FINDING NARROWED-CLAIM CORRECTION: ANY `rel` value that itself contains a colon before the intended ": " separator anchors the `^[^:]*:` prefix-strip at the WRONG colon, landing a silently-wrong non-NULL id in the TRUE bucket. This is NOT Windows-only and NOT limited to files outside root, as this guardrail originally claimed: _rel()'s PRIMARY in-root relative_to() branch is equally vulnerable, so a legally-named POSIX file (e.g. "notes:draft.py") reproduces it with no OS or out-of-root precondition -- verified live, regexp_extract('weird:file.py: DEC-009 Title: ...') returns 'file.py'. The Windows drive-letter path from _rel()'s Path(path).as_posix() fallback (e.g. "C:/Users/x/file.py" -> "/Users/x/file.py") is one INSTANCE of this class, not its boundary. Not fixed here: closing it needs a hooks/decision_gate.py change (a delimiter the path cannot contain), out of this issue's insight/-only footer. RELATEDLY, THE FREE_TEXT_CAP=200 TRUNCATION IS ALSO WIDER THAN "a joined second id": a sufficiently long `rel` prefix (~170+ chars, i.e. a deep repo-relative path) pushes the PRIMARY id itself past the cap, so ledger.py's plain text[:cap] can truncate the first and only violation's id to a fragment -- e.g. a 15-char id cut to one character -- which then extracts as a silently-wrong non-NULL id in the ordinary single-violation case, not only in the " | "-joined case named above. Low probability, real, and a property of the WRITE path, not of this extraction. decision_id_extracted IS A TRI-STATE, COMPUTED ONCE, REUSED AS A GROUP BY KEY: NULL when why IS NULL (no denial text at all -- today's real shape, pre-#243-writer), FALSE when why is present but the regex found nothing (case 7), TRUE when extraction succeeded -- decision_id_best_effort is NULL in BOTH the NULL and FALSE states, so the tri-state column, not the id column alone, is what keeps "no data" and "data present but unparseable" from being conflated; sample_why (arg_max(why, ts)) exposes a real example of whichever shape a FALSE-state bucket saw. SENTINEL goal_id RISK: every real denial across every project carries the byte-identical literal goal_id "(decision-gate)" -- unlike 16/22's coincidentally-colliding real goal ids, here EVERY row from EVERY project shares the same fake id by construction, making a project_id-dropping GROUP BY bug easy to introduce silently; goal_id is deliberately NOT projected anywhere for exactly this reason. view_rows[0] CATALOG RESIDUE (#253, the same inherited debt 15/16/17/18/19/22.sql already name): this view emits multiple rows per project, one per id group. CLASS 2: decision_gate.py's _emit_decision_event writes via stream=ledger.EVENTS literally in the call, tagged class 2 by directory alone -- no reliability_class = 1 filter anywhere in this file.
WITH decision_denials AS (
    SELECT project_id, ts, why, reliability_class,
        NULLIF(regexp_extract(why, '^[^:]*:\s*([^\s:]+)', 1), '') AS decision_id_best_effort
    FROM fact_event
    WHERE kind = 'gate' AND gate = 'decision' AND verdict = 'block' AND project_id IS NOT NULL
),
classified AS (
    SELECT *,
        CASE WHEN why IS NULL THEN NULL
             WHEN decision_id_best_effort IS NOT NULL THEN TRUE
             ELSE FALSE END AS decision_id_extracted
    FROM decision_denials
),
totals AS (
    SELECT
        project_id,
        count(*) AS total_denial_count,
        count(*) FILTER (WHERE decision_id_best_effort IS NOT NULL) AS identified_denial_count,
        count(*) FILTER (WHERE reliability_class = 1) AS class1_count,
        count(*) FILTER (WHERE reliability_class = 2) AS class2_count,
        count(*) AS total_count,
        ROUND(count(*) FILTER (WHERE reliability_class = 1) * 1.0 / NULLIF(count(*), 0), 4)
            AS coverage_pct
    FROM classified
    GROUP BY project_id
),
id_breakdown AS (
    SELECT project_id, decision_id_best_effort, decision_id_extracted, count(*) AS denial_count,
        arg_max(why, ts) AS sample_why
    FROM classified
    GROUP BY project_id, decision_id_best_effort, decision_id_extracted
)
SELECT
    totals.project_id,
    id_breakdown.decision_id_best_effort,
    id_breakdown.decision_id_extracted,
    id_breakdown.denial_count,
    id_breakdown.sample_why,
    totals.total_denial_count,
    totals.identified_denial_count,
    totals.class1_count, totals.class2_count, totals.total_count, totals.coverage_pct
FROM totals
LEFT JOIN id_breakdown ON id_breakdown.project_id = totals.project_id
ORDER BY totals.project_id, id_breakdown.denial_count DESC
