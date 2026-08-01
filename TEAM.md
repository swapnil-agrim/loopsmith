# Team ledger

_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_
_`ledger.py render <sdlc-dir> --write`._

## Waiting on someone

_Nothing is blocked on another person._

## Recent activity

| when | who | did | goal | detail |
|---|---|---|---|---|
| 2026-08-01T02:16:31Z | swapnil-agrim | claimed | 114 |  |
| 2026-08-01T02:16:13Z | swapnil-agrim | done | 113 |  |
| 2026-08-01T02:15:58Z | swapnil-agrim | merged | 113 | auto-merge (squash) armed on PR #192 |
| 2026-08-01T01:44:08Z | swapnil-agrim | claimed | 113 |  |
| 2026-08-01T01:17:13Z | swapnil-agrim | claimed | 113 |  |
| 2026-08-01T01:16:55Z | swapnil-agrim | done | 112 |  |
| 2026-08-01T01:16:41Z | swapnil-agrim | merged | 112 | auto-merge (squash) armed on PR #190 |
| 2026-08-01T00:01:58Z | swapnil-agrim | claimed | 112 |  |
| 2026-08-01T00:01:21Z | swapnil-agrim | done | 105 |  |
| 2026-08-01T00:01:03Z | swapnil-agrim | merged | 105 | auto-merge (squash) armed on PR #187 |
| 2026-07-31T22:59:03Z | swapnil-agrim | parked | 105 | Re-parked (fifth time this run). The decision has never changed and is recorded in full in the earlier park comments: fact_collector_pack is append-only by design with three shipped tests asserting it, so this issue's Done-when cannot be met without reversing a deliberate earlier decision. The ordering half resolved itself when #180 was folded in here. NEW AND RELEVANT TO SEQUENCING: #111's retrospective identified insight/metrics/10.sql (Aging WIP, shipped #109) as the highest-severity latent bug in the catalog -- ROW_NUMBER OVER (PARTITION BY actor_id) with no project_id, so one actor's open claim in a second project does not render wrong, it VANISHES from the view. It is inert ONLY because fact_event has zero rows, and THIS goal is what populates fact_event. So the retro's recommendation is to fix 10.sql as a precondition of this goal landing, rather than as an independent backlog item. That makes your decision here gate a real bug fix, not just this story. |
| 2026-07-31T22:58:22Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T22:58:04Z | swapnil-agrim | done | 111 |  |
| 2026-07-31T22:50:56Z | swapnil-agrim | merged | 111 | auto-merge (squash) armed on PR #186 |
| 2026-07-31T21:15:46Z | swapnil-agrim | claimed | 111 |  |
| 2026-07-31T21:15:29Z | swapnil-agrim | parked | 105 | Re-parked (fourth time this run). Unchanged and still needing your decision: fact_collector_pack is append-only by design with three shipped tests asserting it, so this issue's Done-when ('ingest twice produces identical row counts') cannot be met without reversing a deliberate earlier decision and building a schema/PK migration path this codebase has never needed. The ordering half resolved itself -- #180 was folded into this issue, so there is nothing left to sequence behind. NOTE ON THE REPEAT PARKS: loop.py next re-claims this goal on every iteration, re-adding sdlc:goal and sdlc:in-progress and stripping sdlc:parked, so the park does not survive a single loop cycle. That is a helper bug, recorded on this issue earlier, not indecision by the loop -- the decision has been the same each time. |
| 2026-07-31T21:14:36Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T21:14:17Z | swapnil-agrim | done | 110 |  |
| 2026-07-31T21:14:04Z | swapnil-agrim | merged | 110 | auto-merge (squash) armed on PR #184 |
| 2026-07-31T19:58:23Z | swapnil-agrim | claimed | 110 |  |
| 2026-07-31T19:58:06Z | swapnil-agrim | parked | 105 | Re-parked (third time). Ordering half of the original park is resolved -- #180 was folded into this issue, so there is nothing to wait for. What still needs YOUR decision is unchanged: fact_collector_pack is append-only by design with three shipped tests asserting it, so this issue's Done-when ('ingest twice produces identical row counts') cannot be met without reversing a deliberate earlier decision and building a schema/PK migration path this codebase has never needed. See the two prior park comments and the status-correction note for the full reasoning. |
| 2026-07-31T19:57:24Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T19:57:07Z | swapnil-agrim | done | 109 |  |
| 2026-07-31T19:56:43Z | swapnil-agrim | merged | 109 | auto-merge (squash) armed on PR #183 |
| 2026-07-31T17:16:49Z | swapnil-agrim | claimed | 109 |  |
