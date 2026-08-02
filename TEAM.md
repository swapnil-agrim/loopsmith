# Team ledger

_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_
_`ledger.py render <sdlc-dir> --write`._

## Waiting on someone

_Nothing is blocked on another person._

## Recent activity

| when | who | did | goal | detail |
|---|---|---|---|---|
| 2026-08-02T01:42:35Z | swapnil-agrim | merged | 132 | auto-merge (squash) armed on PR #227 |
| 2026-08-02T01:08:21Z | swapnil-agrim | claimed | 132 |  |
| 2026-08-02T01:08:07Z | swapnil-agrim | done | 131 |  |
| 2026-08-02T01:03:05Z | swapnil-agrim | merged | 131 | auto-merge (squash) armed on PR #223 |
| 2026-08-02T00:10:40Z | swapnil-agrim | claimed | 131 |  |
| 2026-08-02T00:10:25Z | swapnil-agrim | done | 129 |  |
| 2026-08-02T00:05:52Z | swapnil-agrim | merged | 129 | auto-merge (squash) armed on PR #221 |
| 2026-08-01T22:44:56Z | swapnil-agrim | claimed | 129 |  |
| 2026-08-01T22:44:39Z | swapnil-agrim | done | 128 |  |
| 2026-08-01T22:41:02Z | swapnil-agrim | merged | 128 | auto-merge (squash) armed on PR #219 |
| 2026-08-01T21:51:32Z | swapnil-agrim | claimed | 128 |  |
| 2026-08-01T21:47:34Z | swapnil-agrim | claimed | 128 |  |
| 2026-08-01T21:47:19Z | swapnil-agrim | done | 127 |  |
| 2026-08-01T21:47:01Z | swapnil-agrim | merged | 127 | auto-merge (squash) armed on PR #216 |
| 2026-08-01T20:45:00Z | swapnil-agrim | claimed | 127 |  |
| 2026-08-01T20:44:45Z | swapnil-agrim | done | 126 |  |
| 2026-08-01T20:44:29Z | swapnil-agrim | merged | 126 | auto-merge (squash) armed on PR #215 |
| 2026-08-01T19:43:26Z | swapnil-agrim | claimed | 126 |  |
| 2026-08-01T19:43:12Z | swapnil-agrim | done | 125 |  |
| 2026-08-01T19:42:55Z | swapnil-agrim | merged | 125 | auto-merge (squash) armed on PR #214 |
| 2026-08-01T18:23:38Z | swapnil-agrim | done | 121 |  |
| 2026-08-01T18:23:18Z | swapnil-agrim | merged | 121 | auto-merge (squash) armed on PR #213 |
| 2026-08-01T17:26:05Z | swapnil-agrim | claimed | 125 |  |
| 2026-08-01T17:25:50Z | swapnil-agrim | parked | 121 | Half the done_when has no instrument, and choosing what to do about it is a product call — the same class of decision #119 was parked for. Clause 1 (rising discovery-scan inventory) is LIVE; clause 2 (unanswered knowledge/gaps.md queries) is ABSENT with NO SCHEMA AT ALL — kg.py:28-67 is the only code touching gaps.md, it records no per-entry timestamp, and nothing in insight/ references it. The story's three Tasks name only the scan snapshots, so Tasks and done_when disagree about scope. Issue #210 has since been filed for the gaps.md ingest surface, which makes 'ship clause 1 and defer clause 2' a concrete option a human can simply approve. Two blocking defects must also be fixed first, both on the issue: the plan's false claim that this closes epic #115, and a measured hole where ~3 prior snapshots make the trailing p85 land exactly on the elevated value so strict > caps the run at 2 and a real sustained 2x step-up is missed entirely. Nothing implemented; the plan, data audit and measurements are the deliverable in .sdlc/plans/121.md. |
| 2026-08-01T17:25:13Z | swapnil-agrim | claimed | 121 |  |
