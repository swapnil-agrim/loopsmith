-- name: Unanswered handoffs
-- question: What has nobody even looked at?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: reads fact_handoff's own already-merged ack_ts/ack_state/settled_ts columns (ledger_writer.py's _apply_ack, lines 238-274) rather than replaying raw ledger/handoff/ack entries in SQL -- a hand-off is "unanswered" here iff settled_ts IS NULL AND ack_ts IS NULL AND ack_state IS NULL, i.e. no ack of any kind has ever been recorded against it. For THIS metric specifically that is an EXACT mirror of ledger.py's own unanswered()/handoff_states() last-write-wins semantics: any issue with any ack at all -- settled or not, deferred or not -- is excluded from "unanswered" under both readings, so the divergence named below has no observable effect here. NAMED ANYWAY, FOR A READER OF THIS FILE ALONE (Decision B, post-review): ledger_writer.py's settled_ts is last-write-wins per ack (a later, de-escalating re-ack such as resolved-then-deferred CLEARS an earlier ack's settled_ts), while ledger.py's own outstanding() treats settlement as a one-way RATCHET -- once any ack for an issue ever reaches declined/resolved, that issue is settled forever regardless of what a later ack says. That divergence is real, operationally reachable (the ack CLI has no guard against a de-escalating re-ack), and matters for metric 34 (deferred-handoff age), not for this metric -- see 34.sql's own guardrail and .sdlc/plans/112.md Design decision B for the full accounting, including the named, out-of-scope follow-up (a ratchet fix to ledger_writer.py, or a separate "ever settled" column) that would close it at the source.
-- data_status: dark
SELECT
    project_id,
    issue,
    area,
    priority,
    from_actor,
    to_actor,
    opened_ts
FROM fact_handoff
WHERE settled_ts IS NULL AND ack_ts IS NULL AND ack_state IS NULL
