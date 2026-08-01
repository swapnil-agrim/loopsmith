# Team ledger

_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_
_`ledger.py render <sdlc-dir> --write`._

## Waiting on someone

_Nothing is blocked on another person._

## Recent activity

| when | who | did | goal | detail |
|---|---|---|---|---|
| 2026-08-01T06:17:40Z | swapnil-agrim | claimed | 118 |  |
| 2026-08-01T06:17:25Z | swapnil-agrim | done | 117 |  |
| 2026-08-01T06:17:10Z | swapnil-agrim | merged | 117 | auto-merge (squash) armed on PR #198 |
| 2026-08-01T04:53:50Z | swapnil-agrim | claimed | 117 |  |
| 2026-08-01T04:53:36Z | swapnil-agrim | done | 195 |  |
| 2026-08-01T04:53:08Z | swapnil-agrim | merged | 195 | auto-merge (squash) armed on PR #196 |
| 2026-08-01T04:34:39Z | swapnil-agrim | claimed | 117 |  |
| 2026-08-01T04:34:25Z | swapnil-agrim | done | 116 |  |
| 2026-08-01T04:34:09Z | swapnil-agrim | merged | 116 | auto-merge (squash) armed on PR #194 |
| 2026-08-01T03:14:25Z | swapnil-agrim | claimed | 116 |  |
| 2026-08-01T03:14:10Z | swapnil-agrim | done | 114 |  |
| 2026-08-01T03:13:55Z | swapnil-agrim | merged | 114 | auto-merge (squash) armed on PR #193 |
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
