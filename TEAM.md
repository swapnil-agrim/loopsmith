# Team ledger

_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_
_`ledger.py render <sdlc-dir> --write`._

## Waiting on someone

_Nothing is blocked on another person._

## Recent activity

| when | who | did | goal | detail |
|---|---|---|---|---|
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
| 2026-07-31T17:15:37Z | swapnil-agrim | claimed | 109 |  |
| 2026-07-31T17:15:08Z | swapnil-agrim | parked | 105 | Re-parked. Blocked on ordering plus one open decision -- see the earlier park comment for the full reasoning; nothing has changed since. Short version: #180 (E1.S9, persist ledger records into fact_event and fact_handoff) is the story that builds the write path this goal's watermark is supposed to gate, and it has not run yet, so a watermark shipped today gates nothing; and fact_collector_pack is append-only by design with three tests asserting it, so this issue's own Done-when ('running ingest twice produces identical row counts') cannot be met without reversing a shipped decision. Both need you. |
| 2026-07-31T17:14:14Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T17:13:58Z | swapnil-agrim | done | 108 |  |
| 2026-07-31T17:13:41Z | swapnil-agrim | merged | 108 | auto-merge (squash) armed on PR #182 |
| 2026-07-31T15:17:44Z | swapnil-agrim | claimed | 108 |  |
| 2026-07-31T15:16:11Z | swapnil-agrim | claimed | 108 |  |
| 2026-07-31T15:15:48Z | swapnil-agrim | parked | 105 | Blocked on ordering, not on a decision: #180 (E1.S9, persist ledger records into fact_event and fact_handoff) already exists and is the story that builds the write path this goal's watermark is supposed to gate. Verified in Research: spec B.2 step 2 was never built, ledger_reader.py is read-only and is not wired into ingest, and no fact_event/fact_handoff write exists -- so a watermark shipped today gates nothing. #105 should run AFTER #180. Second, independent question still needing your call: fact_collector_pack is append-only BY DESIGN -- measured going 5 to 10 rows across two ingest runs -- with three shipped tests asserting exactly that, so this issue's own Done-when, 'running ingest twice produces identical row counts', cannot be met without reversing a deliberate earlier decision and building a schema/PK migration path this codebase has never needed. Also for your call: the third dimension of the (project, actor, stream) key is inert, since 'stream' is proposed in spec A.1 but not implemented in the shipped ledger.py. Research is complete and the dossier stands at .sdlc/research/105-incremental-resume.md. |
| 2026-07-31T15:15:04Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T15:14:47Z | swapnil-agrim | done | 106 |  |
| 2026-07-31T15:14:28Z | swapnil-agrim | merged | 106 | auto-merge (squash) armed on PR #181 |
| 2026-07-31T13:23:53Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T13:22:45Z | swapnil-agrim | claimed | 106 |  |
| 2026-07-31T13:20:36Z | swapnil-agrim | claimed | 106 |  |
