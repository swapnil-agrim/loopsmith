---
name: sdlc-release-check
description: The pre-flight checklist run immediately before a non-trivial change ships to production — the final gate after Review. Triggers on "ready to ship", "deploy", "release", "merge to main", "go live", "pre-flight". A conditional-risk review orthogonal to sdlc-review; always LoopSmith's own (no companion equivalent). Use before shipping, or when the user runs /sdlc-release-check.
allowed-tools: Bash, Read, Grep
---

# sdlc-release-check

> The pre-flight checklist. Run before anything goes to prod.

Run this **after** `sdlc-review` and any risk reviews the change tripped (`sdlc-migration-check`,
`sdlc-contract-check`, `sdlc-security-review`). Ground yourself in the plan and the review artifacts in
`.sdlc/reviews/` before judging readiness.

> **Note for the autonomous loop:** LoopSmith never deploys or releases on its own — the loop parks an
> irreversible action (deploy/release) for a human rather than running it (enforced by `/sdlc-loop`). This
> skill produces the go/no-go artifact a human release captain reads; it does not perform the release.

## Goal
Confirm a change is genuinely ready to ship, and produce the artifact a release captain reads.

## Steps
1. Run the eleven-point checklist. Each item: `pass` / `fail` / `n/a` + one line.
2. Identify the canary path: who / what / where gets it first, and the verification metric.
3. Identify the rollback path: exact steps, timing (target ≤ 5 min).
4. Confirm the right people are notified (on-call, dependent teams).
5. State the release window (now / scheduled / off-peak).

## Eleven-point checklist
1. Tests green — full suite, not just affected files.
2. Type / lint clean.
3. Plan trace clean (no orphan diff lines).
4. Review says ready (`.sdlc/reviews/` + the loop's own review verdict).
5. Feature flag set correctly (default-off for risky changes).
6. Migrations sequenced; backfills started.
7. Dashboards / alerts present for new surfaces.
8. On-call notified.
9. Dependent teams notified (if a contract changed).
10. Rollback path written and tested in staging.
11. Release notes / changelog updated.

## Gates
- Every checklist item has `pass` / `fail` / `n/a` + a reason.
- Rollback path includes timing ("≤ 5 min").
- Canary path includes a named verification metric.

## Stop when
- Any item is `fail` → do NOT ship; fix and re-run this skill.
- A migration is involved but `sdlc-migration-check` was not run → run it first.
- A contract changed but consumers were not notified → **park for a human**.

## Output → render the report, and persist it if you want it retained
Write to `.sdlc/reviews/release-check-<slug>.md` (NOT under `.sdlc/knowledge/`, which is gitignored).

```markdown
# release check · <slug>

## checklist
1. tests · <pass/fail/n-a> · <one line>
2. type/lint · <pass/fail/n-a> · <one line>
3. plan trace · <pass/fail/n-a> · <one line>
4. review ready · <pass/fail/n-a> · <one line>
5. feature flag · <pass/fail/n-a> · <one line>
6. migrations · <pass/fail/n-a> · <one line>
7. dashboards · <pass/fail/n-a> · <one line>
8. on-call notified · <pass/fail/n-a> · <one line>
9. dependent teams · <pass/fail/n-a> · <one line>
10. rollback tested · <pass/fail/n-a> · <one line>
11. release notes · <pass/fail/n-a> · <one line>

## canary
- target: <who/what>
- verify: <metric, expected range>
- timing: <how long before full rollout>

## rollback
- steps: <ordered list>
- max time to rollback: <X minutes>

## notifications
- on-call: <who, when>
- dependent teams: <list>

## go/no-go
<go / no-go> — <one sentence>
```
