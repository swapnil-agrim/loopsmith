# Team ledger

_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_
_`ledger.py render <sdlc-dir> --write`._

## Waiting on someone

_Nothing is blocked on another person._

## Recent activity

| when | who | did | goal | detail |
|---|---|---|---|---|
| 2026-07-31T15:17:44Z | swapnil-agrim | claimed | 108 |  |
| 2026-07-31T15:16:11Z | swapnil-agrim | claimed | 108 |  |
| 2026-07-31T15:15:48Z | swapnil-agrim | parked | 105 | Blocked on ordering, not on a decision: #180 (E1.S9, persist ledger records into fact_event and fact_handoff) already exists and is the story that builds the write path this goal's watermark is supposed to gate. Verified in Research: spec B.2 step 2 was never built, ledger_reader.py is read-only and is not wired into ingest, and no fact_event/fact_handoff write exists -- so a watermark shipped today gates nothing. #105 should run AFTER #180. Second, independent question still needing your call: fact_collector_pack is append-only BY DESIGN -- measured going 5 to 10 rows across two ingest runs -- with three shipped tests asserting exactly that, so this issue's own Done-when, 'running ingest twice produces identical row counts', cannot be met without reversing a deliberate earlier decision and building a schema/PK migration path this codebase has never needed. Also for your call: the third dimension of the (project, actor, stream) key is inert, since 'stream' is proposed in spec A.1 but not implemented in the shipped ledger.py. Research is complete and the dossier stands at .sdlc/research/105-incremental-resume.md. |
| 2026-07-31T15:15:04Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T15:14:47Z | swapnil-agrim | done | 106 |  |
| 2026-07-31T15:14:28Z | swapnil-agrim | merged | 106 | auto-merge (squash) armed on PR #181 |
| 2026-07-31T13:23:53Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T13:22:45Z | swapnil-agrim | claimed | 106 |  |
| 2026-07-31T13:20:36Z | swapnil-agrim | claimed | 106 |  |
| 2026-07-31T13:20:18Z | swapnil-agrim | parked | 105 | Two scope decisions only you can make, both verified in Research rather than reasoned. (1) LEDGER PERSISTENCE: spec B.2 step 2 -- read ledger entries/events into raw tables -- was never built. ledger_reader.py is read-only and is not wired into ingest, and no fact_event/fact_handoff exists, so the watermark this story asks for has no write path to gate. Either #105 absorbs building that write path (a whole unopened story's worth of work, well past E1.S7's stated scope), or the watermark ships inert. (2) fact_collector_pack IS APPEND-ONLY BY DESIGN: ingest run twice was measured going 5 -> 10 rows, and three shipped tests assert exactly that behaviour. The issue's own Done-when -- 'running ingest twice produces identical row counts' -- therefore cannot be met without reversing a deliberate, tested decision from an earlier story, which needs a schema/PK migration path this codebase has never built. Both readings lead to materially different work, so guessing would either ship dead code or silently overturn a prior design decision. Also worth your call while you are here: the third dimension of the (project, actor, stream) key is inert -- 'stream' is proposed in spec A.1 but not implemented in the shipped ledger.py. Research is complete and the dossier stands; the goal needs only a scope answer to proceed. |
| 2026-07-31T13:10:40Z | swapnil-agrim | claimed | 105 |  |
| 2026-07-31T13:10:18Z | swapnil-agrim | done | 104 |  |
| 2026-07-31T13:10:03Z | swapnil-agrim | merged | 104 | auto-merge (squash) armed on PR #179 |
| 2026-07-31T12:55:46Z | swapnil-agrim | claimed | 104 |  |
| 2026-07-31T11:24:56Z | swapnil-agrim | claimed | 104 |  |
| 2026-07-31T11:24:33Z | swapnil-agrim | done | 103 |  |
| 2026-07-31T11:23:23Z | swapnil-agrim | merged | 103 | auto-merge (squash) armed on PR #178 |
| 2026-07-31T10:29:37Z | swapnil-agrim | claimed | 103 |  |
| 2026-07-31T09:21:37Z | swapnil-agrim | claimed | 103 |  |
| 2026-07-31T09:21:18Z | swapnil-agrim | done | 102 |  |
| 2026-07-31T09:21:04Z | swapnil-agrim | merged | 102 | auto-merge (squash) armed on PR #177 |
| 2026-07-31T08:26:47Z | swapnil-agrim | claimed | 102 |  |
| 2026-07-31T08:25:07Z | swapnil-agrim | done | 101 |  |
| 2026-07-31T08:24:53Z | swapnil-agrim | merged | 101 | auto-merge (squash) armed on PR #174 |
| 2026-07-31T08:06:02Z | swapnil-agrim | claimed | 101 |  |
