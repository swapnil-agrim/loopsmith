-- name: Aging WIP
-- question: What is quietly rotting?
-- personas: manager
-- reliability_class: 1
-- guardrail: explicit exception to the individual-grain guardrail (spec: "aging WIP (#10) ... about unblocking a person, not ranking one"). Returns claimed_ts, NOT a computed age -- a static .sql view has no runtime "now", so age = today - claimed_ts is the consumer's job at render time, keeping this view deterministic and fixture-testable. DARK CLASSIFICATION UNRESOLVED (#188): fact_event IS now populated by insight ingest as of #105 -- measured 70 rows on this repo's own ledger -- so the old 'fact_event holds 0 rows in every real ingest' rationale is dead. But this view still returns 0 rows, because it needs OPEN claims and this repo currently has none. Empty-result is not the same as empty-source, and re-classifying all eight dark metrics needs one measured pass, not a guess per file. Left 'dark' until then. ANSWERED by #105 (which absorbed #180): `fact_event.kind` carries the ENTRIES-stream lifecycle string verbatim (claimed/done/parked/failed/merged/handoff/ack/release/note), NOT spec A.3's events-stream vocabulary. Measured on this repo's own ledger: 70 rows, all reliability_class=1. This view is no longer dark. RELIABILITY-CLASS ENFORCEMENT (#114, spec line 563: "a NOW metric must not read any reliability_class=2 row"): the events CTE now filters `AND reliability_class = 1`, a bare equality not `!= 2` -- a NULL reliability_class is excluded identically to a 2, not grandfathered as trusted, because a real ingested fact_event row is NEVER NULL (ledger_writer.py always tags it from ledger_reader.read_all_with_reliability); a NULL seen here is evidence of an ingest-path regression, not a legitimate legacy row.
-- data_status: dark
-- LAST-EVENT-WINS, EPISODE-SCOPED (post-review BLOCKING fix, round 4): rounds 2 and 3 each modeled a terminal event as an UNSCOPED resource able to close ANY claim episode with claimed_ts <= its own ts, rather than being scoped to the one episode it actually released -- a set-based join reconstructing a sequential state machine, which diverges whenever the true sequence has a tie (round 4's own falsifying case: a terminal releasing actor X's claim and a brand-new claim by actor Y sharing the identical instant -- the unscoped join let the shared timestamp satisfy BOTH the old episode's closing condition and the new episode's opening condition simultaneously, losing the goal's open state entirely). DERIVED FROM skills/sdlc-loop/scripts/ledger.py's open_claims(), re-read line-by-line this round: `held[goal] = (actor, ts)` on 'claimed' and `held.pop(goal, None)` on a terminal are each a TOTAL OVERWRITE/CLEAR, never cumulative -- so the state after replaying any prefix of a goal's events is entirely determined by that prefix's OWN LAST event, full stop; every earlier event for that goal is irrelevant to the current state (not merely superseded -- structurally unable to influence it). This collapses episode-scoping into a single per-goal lookup: the CURRENT holder of a goal is whichever actor's 'claimed' event is the goal's single most-recent event. Tie-break, three keys deep, matching open_claims()'s own read_all() sort key (ts, actor, seq) as closely as this schema allows: (1) ts DESC; (2) actor_id DESC -- two DIFFERENT actors sharing an instant are ordered by open_claims()'s own (ts, actor) key, so the alphabetically-LAST actor wins; (3) is_claim ASC -- for the SAME actor at the SAME instant, a real ledger's per-actor seq always has the claim strictly precede its own release, so the terminal is always the later of the two and must win the tie -- caught live this round: without this third key, a same-second claim-then-done (round 3's own fix target) silently regressed again, because DuckDB's tie-break for two same-(ts,actor) rows with no further ORDER BY key is unstable/arbitrary, not "whichever was inserted last." ACCEPTED, DOCUMENTED LIMITATION, narrowed from round 3's broader claim: three OR MORE same-actor, same-instant events for one goal remain genuinely ambiguous without a seq column this schema does not carry; two-event same-actor ties (the realistic shape -- a fast automated claim-then-done) are now handled exactly.
WITH events AS (
    SELECT project_id, goal_id, actor_id, ts,
           CASE WHEN kind = 'claimed' THEN 1 ELSE 0 END AS is_claim
    FROM fact_event
    WHERE kind IN ('claimed', 'done', 'parked', 'failed')
      AND reliability_class = 1
),
latest_event AS (
    SELECT project_id, goal_id, actor_id, ts, is_claim,
           ROW_NUMBER() OVER (
               PARTITION BY project_id, goal_id ORDER BY ts DESC, actor_id DESC, is_claim ASC
           ) AS rn
    FROM events
),
current_holder AS (
    -- The goal's CURRENT state is entirely determined by its single latest event (see the
    -- guardrail above) -- open, held by that event's own actor, only if that latest event is
    -- itself a 'claimed' event.
    SELECT project_id, goal_id, actor_id, ts AS claimed_ts
    FROM latest_event
    WHERE rn = 1 AND is_claim = 1
)
SELECT project_id, actor_id, goal_id, claimed_ts
FROM (
    SELECT project_id, actor_id, goal_id, claimed_ts,
           ROW_NUMBER() OVER (PARTITION BY project_id, actor_id ORDER BY claimed_ts ASC) AS rn
    FROM current_holder
) ranked
WHERE rn = 1
