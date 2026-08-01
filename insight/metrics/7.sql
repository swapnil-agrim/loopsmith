-- name: Flow load (WIP)
-- question: How much is in flight?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: no metric renders at individual grain in the manager or leadership views (spec Guardrails). DARK CLASSIFICATION UNRESOLVED (#188): fact_event IS now populated by insight ingest as of #105 -- measured 70 rows on this repo's own ledger -- so the old 'fact_event holds 0 rows in every real ingest' rationale is dead. But this view still returns 0 rows, because it needs OPEN claims and this repo currently has none. Empty-result is not the same as empty-source, and re-classifying all eight dark metrics needs one measured pass, not a guess per file. Left 'dark' until then. ANSWERED by #105 (which absorbed #180): `fact_event.kind` carries the ENTRIES-stream lifecycle string verbatim (claimed/done/parked/failed/merged/handoff/ack/release/note), NOT spec A.3's events-stream vocabulary. Measured on this repo's own ledger: 70 rows, all reliability_class=1. This view is no longer dark.
-- data_status: dark
-- LAST-EVENT-WINS, EPISODE-SCOPED (post-review BLOCKING fix, round 4): rounds 2 and 3 modeled a terminal event as an UNSCOPED resource able to close ANY claim episode with claimed_ts <= its own ts, rather than being scoped to the one episode it actually released -- a set-based join reconstructing a sequential state machine, which diverges wherever the true sequence has a tie. Round 3's own guardrail claimed count(DISTINCT goal_id) was "PROVABLY still correct" after its >= fix -- FALSIFIED this round: a terminal releasing one actor's claim and a brand-new claim by a different actor sharing the identical instant made the unscoped closing condition satisfy BOTH the old episode's close and the new episode's open at once, losing the goal's open state for every subsequent week (reproduced: wip_count=0 in every week when the true state per open_claims() is open). That claim is retracted, not repeated. DERIVED FROM skills/sdlc-loop/scripts/ledger.py's open_claims(): `held[goal] = (actor, ts)` and `held.pop(goal, None)` are each a TOTAL OVERWRITE/CLEAR, never cumulative -- the state after replaying any prefix of a goal's events is entirely determined by that prefix's OWN LAST event; every earlier event for that goal is irrelevant to the CURRENT state, not merely superseded. This view now computes, for each week boundary, the single most-recent event at-or-before that boundary PER GOAL and counts a goal as in-flight iff that single latest event is a 'claimed' event. Tie-break, three keys deep, matching open_claims()'s own read_all() sort key (ts, actor, seq) as closely as this schema allows: (1) ts DESC -- the primary order; (2) actor_id DESC -- two DIFFERENT actors sharing an instant are ordered by open_claims()'s own (ts, actor) key, so the alphabetically-LAST actor is the one "processed last" and wins; (3) is_claim ASC -- for the SAME actor at the SAME instant (e.g. a fast automated claim-then-done, round 3's own fix target), a real ledger's per-actor seq always has the claim strictly precede its own release (you cannot release before you claim), so the terminal is always the later of the two and must win the tie, not an arbitrary row-order pick -- caught live this round: without this third key, DuckDB's own tie-break for two same-(ts,actor) rows was unstable/arbitrary and silently regressed round 3's own same-second-close fix. ACCEPTED, DOCUMENTED LIMITATION, narrowed from round 3's broader claim: three OR MORE same-actor, same-instant events for one goal (e.g. claim-done-reclaim all stamped identically) remain genuinely ambiguous without a seq column this schema does not carry; two-event same-actor ties (the realistic shape) are now handled exactly.
WITH events AS (
    SELECT project_id, goal_id, actor_id, ts,
           CASE WHEN kind = 'claimed' THEN 1 ELSE 0 END AS is_claim
    FROM fact_event
    WHERE kind IN ('claimed', 'done', 'parked', 'failed')
),
weeks AS (
    SELECT CAST(unnest(generate_series(
        date_trunc('week', (SELECT min(ts) FROM events WHERE is_claim = 1)),
        date_trunc('week', (SELECT max(ts) FROM events)),
        INTERVAL 7 DAY
    )) AS DATE) AS week_start
),
candidate AS (
    -- Every event at or before each week boundary, ranked per (week, project, goal) so only the
    -- single most-recent event (the one that actually determines the goal's state as of that
    -- boundary) survives -- mirroring open_claims()'s own "last write wins" semantics exactly,
    -- not an approximation of it.
    SELECT w.week_start, e.project_id, e.goal_id, e.is_claim,
           ROW_NUMBER() OVER (
               PARTITION BY w.week_start, e.project_id, e.goal_id ORDER BY e.ts DESC, e.actor_id DESC, e.is_claim ASC
           ) AS rn
    FROM weeks w
    LEFT JOIN events e ON e.ts <= w.week_start
    -- LEFT JOIN, not JOIN: a week boundary before ANY event has happened yet must still
    -- produce a row (wip_count=0), not silently vanish from the output because the inner
    -- join found nothing to match -- caught live while verifying this fix (see this file's
    -- own test for the exact repro).
)
SELECT week_start, count(*) FILTER (WHERE rn = 1 AND is_claim = 1) AS wip_count
FROM candidate
GROUP BY week_start
