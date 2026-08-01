-- name: Unanswered handoffs
-- question: What has nobody even looked at?
-- personas: manager, cross-functional
-- reliability_class: 1
-- guardrail: reads fact_handoff's own already-merged ack_ts/ack_state/settled_ts columns (ledger_writer.py's _apply_ack, lines 238-274) rather than replaying raw ledger/handoff/ack entries in SQL -- a hand-off is "unanswered" here iff settled_ts IS NULL AND ack_ts IS NULL AND ack_state IS NULL, i.e. no ack of any kind has ever been recorded against it. For issue-keyed hand-offs specifically (WHERE issue IS NOT NULL, see the separate BLOCKING-finding note at the end of this guardrail) that is an EXACT mirror of ledger.py's own unanswered()/handoff_states() last-write-wins semantics -- SCOPED, NOT UNCONDITIONAL, per post-review correction: any issue with any ack at all -- settled or not, deferred or not -- is excluded from "unanswered" under both readings, so the divergence named below has no observable effect here. NAMED ANYWAY, FOR A READER OF THIS FILE ALONE (Decision B, post-review): ledger_writer.py's settled_ts is last-write-wins per ack (a later, de-escalating re-ack such as resolved-then-deferred CLEARS an earlier ack's settled_ts), while ledger.py's own outstanding() treats settlement as a one-way RATCHET -- once any ack for an issue ever reaches declined/resolved, that issue is settled forever regardless of what a later ack says. That divergence is real, operationally reachable (the ack CLI has no guard against a de-escalating re-ack), and matters for metric 34 (deferred-handoff age), not for this metric -- see 34.sql's own guardrail and .sdlc/plans/112.md Design decision B for the full accounting, including the named, out-of-scope follow-up (a ratchet fix to ledger_writer.py, or a separate "ever settled" column) that would close it at the source. SEPARATE, BLOCKING FINDING FROM AN INDEPENDENT REVIEW, FIXED HERE (issue IS NOT NULL added to the WHERE clause below): fact_handoff has no goal_id column (store.py:126-138), so an issue-less hand-off (handoff.py's own hand_off() falls back to issue=None whenever the backlog source has no create_dependency) can never be durably re-matched to its own later ack -- the ack lands instead as a second, orphaned fact_handoff row (issue NULL, only ack_ts/ack_state populated, every other column NULL). Without this filter, a goal-only hand-off that was genuinely accepted or resolved would still surface here as permanently "unanswered" (its own row never gets an ack merged into it), which is not a divergence this metric can afford to call exact; issue IS NOT NULL excludes both that hand-off and its orphaned ack from this view entirely -- a disclosed, real coverage gap, not a silent miscount; the real fix is a goal_id column on fact_handoff or a durable key in ledger_writer.py, out of scope here per Decision K.
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
WHERE issue IS NOT NULL AND settled_ts IS NULL AND ack_ts IS NULL AND ack_state IS NULL
