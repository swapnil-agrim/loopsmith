# Team ledger

_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_
_`ledger.py render <sdlc-dir> --write`._

## Waiting on someone

_Nothing is blocked on another person._

## Recent activity

| when | who | did | goal | detail |
|---|---|---|---|---|
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
| 2026-08-01T17:24:52Z | swapnil-agrim | done | 124 |  |
| 2026-08-01T17:24:32Z | swapnil-agrim | claimed | 121 |  |
| 2026-08-01T17:24:17Z | swapnil-agrim | done | 124 |  |
| 2026-08-01T17:24:00Z | swapnil-agrim | merged | 124 | auto-merge (squash) armed on PR #212 |
| 2026-08-01T15:14:28Z | swapnil-agrim | claimed | 124 |  |
| 2026-08-01T15:14:08Z | swapnil-agrim | parked | 121 | Half the done_when has no instrument, and choosing what to do about it is a product call — the same class of decision #119 was parked for, so it is not being made unattended. Clause 1 (rising discovery-scan inventory) is genuinely LIVE: a real collector at ingest/collectors.py:52-53 writing fact_collector_pack, with shipped metric 30 as prior art. Clause 2 (unanswered knowledge/gaps.md queries) is ABSENT with NO SCHEMA AT ALL — one level darker than #120's fact_goal.pr, which at least had a column. skills/sdlc-kg/scripts/kg.py:28-67 is the only code touching gaps.md, it records no per-entry timestamp ever, and nothing in insight/ references it. The story's own three Tasks name only the scan snapshots, so Tasks and done_when disagree about scope.  The plan proposed descoping clause 2. Its engineering argument is right — fabricating a never-written schema string so a population query reads 0 would be inventing an instrument rather than disclosing a missing one. But that only rules out one option; it does not authorize picking descope-and-close unilaterally. The real choice — build the gaps.md ingest surface now, ship clause 1 alone and amend the done_when, or drop clause 2 — belongs to whoever owns the backlog. Issue #210 has since been filed for the ingest surface, which makes 'ship clause 1 and defer clause 2' a concrete option a human can now simply approve.  Two independent blocking defects must also be fixed before this ships, both recorded on the issue: (a) the plan asserts this story CLOSES epic #115, which is false — #115 tracks seven stories and #122 was open and untouched (it has since shipped); (b) two of the plan's five 'verified live' scenarios do not reproduce against its own byte-identical SQL, because with only ~3 prior snapshots the trailing p85 lands exactly on the elevated value and strict > caps the breach run at 2 — so a real sustained 2x step-up is MISSED ENTIRELY on a short history, which is exactly the newly-onboarded project a rising-debt alert exists for.  No code was written. The plan, the data audit, the reviewer's reproduction and the measured numbers are the deliverable, in .sdlc/plans/121.md. |
| 2026-08-01T15:13:26Z | swapnil-agrim | claimed | 121 |  |
| 2026-08-01T15:13:12Z | swapnil-agrim | done | 122 |  |
| 2026-08-01T15:12:57Z | swapnil-agrim | merged | 122 | auto-merge (squash) armed on PR #211 |
| 2026-08-01T13:34:52Z | swapnil-agrim | claimed | 122 |  |
| 2026-08-01T13:34:34Z | swapnil-agrim | parked | 121 | Half the done_when has no instrument, and choosing what to do about it is a product call — the same class of decision #119 was parked for, so it is not being made unattended. VERIFIED AT RESEARCH, NOT DISCOVERED LATE: clause 1 ('rising discovery-scan inventory') is genuinely LIVE — a real collector (ingest/collectors.py:52-53) writing fact_collector_pack, with metric 30 as shipped prior art. Clause 2 ('unanswered knowledge/gaps.md queries') is ABSENT with NO SCHEMA AT ALL — one level darker than #120's fact_goal.pr, which at least had a column. skills/sdlc-kg/scripts/kg.py:28-67 is the only code that touches gaps.md, it records no per-entry timestamp ever, and nothing in insight/ references it (zero grep hits). The story's own three Tasks name only the scan snapshots, so the Tasks and the done_when disagree about scope.  The plan proposed descoping clause 2 with a follow-up issue. Its engineering argument is right as far as it goes — fabricating a never-written schema string just so a population query reads 0 would be inventing an instrument rather than disclosing a missing one, and I agree that is not acceptable. But 'do not fabricate' only rules out one option; it does not authorize picking 'descope and close' unilaterally. The real choice is a cost/scope tradeoff — build the gaps.md ingest surface now, ship clause 1 alone and amend the done_when, or drop clause 2 outright — and that belongs to whoever owns the backlog.  Two independent blocking defects also have to be fixed before this ships, and both are recorded on the issue: (a) the plan asserts this story CLOSES epic #115, which is false — #115 tracks seven stories and #122 [E3.S7] is open and untouched, verified directly against the epic body, and epic-closure was part of the plan's own descope argument; (b) two of the plan's five 'verified live' scenarios do not reproduce against its own byte-identical SQL, and the cause is a genuine undisclosed hole — with only ~3 prior snapshots the trailing p85 lands exactly on the elevated value and strict > caps the run at 2, so a real sustained 2x step-up is MISSED ENTIRELY on a short history, which is exactly the newly-onboarded project a rising-debt alert is for.  Nothing was implemented and no code was written. The plan, the data audit, the reviewer's reproduction and the measured numbers are the deliverable, in .sdlc/plans/121.md. |
| 2026-08-01T12:56:57Z | swapnil-agrim | claimed | 121 |  |
| 2026-08-01T12:56:37Z | swapnil-agrim | done | 120 |  |
| 2026-08-01T12:56:15Z | swapnil-agrim | merged | 120 | auto-merge (squash) armed on PR #203 |
