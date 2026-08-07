Follow-up filed by #310 [E19.S2], Decision 3 -- not solved there, tracked here as a distinct,
not-yet-scoped provisioning/design question.

## The gap

Two independently-provisioned identity namespaces exist in this codebase, and nothing ties them
together:

- **Session side**: `insight/web/src/auth.ts`'s `authorize()` sets the session's actor identity to
  exactly the string typed at `insight users add <username>` (`insight/accounts/store.py`).
- **Analytics side**: `fact_event.actor_id` / `fact_handoff.from_actor` / `fact_handoff.to_actor` /
  `fact_pr_review.actor` are populated at ingest time from the SDLC loop's own `ledger.actor`
  config key, else `gh api user -q .login`, else `$USER` (`skills/sdlc-loop/scripts/ledger.py`'s
  `actor()`).

`insight/dash/actor.py`'s own module docstring already names this exact failure shape for the
offline CLI equivalent, and `insight/web/src/lib/auth/actor.ts`'s `resolveActor()` (issue #310)
repeats the same honesty for the web path.

Two failure shapes follow, both silent:
1. **Safe but useless** -- the two names differ, and an operator's own `/ic` view renders entirely
   absent even though real data about them exists under a different key
   (`insight.dash.ic._actor_ever_appeared` detects and banners this today, but only the symptom).
2. **Unsafe** -- a web username collides with a DIFFERENT real person's `actor_id` (a reused
   handle, a templated account). Scoping is then correct by construction in SQL and still
   resolves to the wrong human's data.

## Open questions for a future scoping pass

- Should `insight users add` require or validate that its `--username` matches `ledger.actor` for
  the project(s) it will be used against?
- Should there be a runtime cross-check (e.g. a warning banner when the session identity has never
  appeared in the store at all -- already partially covered by `_actor_ever_appeared`)?
- Does this belong in provisioning tooling, documentation-only operator responsibility (the
  precedent `insight/web/README.md` already accepts for `role`), or a stronger runtime guard?

## Related

- Decision 1 of #310 also named a distinct, not-yet-solved limit worth folding into the same
  future pass when it's scoped: `/ic`'s current CLI-bridge data-access transport (a `python3 -m
  insight web ic` shell-out) is a deliberate interim architecture, diverging from spec section 6's
  eventual "queries and guardrails move to the API" end state. Once E22.S1 wires real internal
  Next-to-FastAPI network transport, `/ic`'s data path should migrate from the CLI bridge to a
  real `insight/api/` endpoint calling the same `insight.dash.ic.collect_ic_payload()` the bridge
  already reuses.

Not `sdlc:goal`-labelled by default -- this is a provisioning/design question, not a
ready-to-implement goal.

