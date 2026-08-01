-- name: Deferred-handoff age
-- question: The silent killer -- how long has a deferred hand-off been sitting?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: age of ack_state='deferred' hand-offs, deliberately never settling by construction. Returns ack_ts (when the deferral decision was made), NOT a computed age -- a static .sql view has no runtime "now", so age = today - ack_ts is the consumer's job at render time, matching 10.sql's own claimed_ts precedent and metric 35's own claimed_ts (Decision H). settled_ts IS NULL is included defensively even though it is always true for ack_state='deferred' today by ledger_writer.py's own construction -- belt-and-suspenders, not load-bearing. DISCLOSED, ACCEPTED LIMITATION, BLOCKING FINDING FROM AN INDEPENDENT REVIEW (Decision B): this view CANNOT distinguish "a hand-off that was deferred and never resolved" from "a hand-off that was once resolved/declined and later re-acked as deferred" -- fact_handoff's own schema (ledger_writer.py's _apply_ack, lines 238-274) carries only the LATEST ack, last-write-wins, while ledger.py's own outstanding() (lines 251-267) treats "ever reached a settling state" as a permanent, ONE-WAY RATCHET: once any ack for an issue reaches declined/resolved, outstanding()/unanswered() treat it as settled forever, regardless of a later ack. A resolved-then-deferred re-ack (operationally reachable -- the ack CLI has no guard against a de-escalating re-ack) therefore appears in THIS view identically to a genuinely never-resolved deferred hand-off, even though ledger.py's own team view (render/summary) would treat it as closed forever and never surface it. This is a named, accepted, disclosed gap inherited from ledger_writer.py -- out of scope to fix here (no edits to insight/ingest/* per Decision K); the named follow-up is either a ratchet fix to _apply_ack's settled_ts, or a separate durable "ever settled" column, decided by whoever owns insight/ingest/* next.
-- data_status: dark
SELECT
    project_id,
    issue,
    area,
    priority,
    from_actor,
    to_actor,
    opened_ts,
    ack_ts
FROM fact_handoff
WHERE ack_state = 'deferred' AND settled_ts IS NULL
