# Team ledger

_Generated from `.sdlc/ledger/entries/*.jsonl`. Do not hand-edit — regenerate with_
_`ledger.py render <sdlc-dir> --write`._

## Waiting on someone

_Nothing is blocked on another person._

## Recent activity

| when | who | did | goal | detail |
|---|---|---|---|---|
| 2026-08-01T08:31:28Z | swapnil-agrim | claimed | 120 |  |
| 2026-08-01T08:31:12Z | swapnil-agrim | parked | 119 | The issue's done_when is not achievable as written. 'Fires when a metric crosses its OWN trailing p85' plus 'no hardcoded constants' is self-defeating: ~15% of any series' points exceed its own p85 by construction, so measured over 500 trials per shape a HEALTHY stationary project fires a false WARN 91-99% of the time (naive criterion) or 78-93% (with a derived p85+(p85-p50) materiality margin). Sensitivity is fine - a 4x sustained step-up and a 10,000x spike both fire - so the rule can detect real regressions; it cannot stay quiet on healthy ones. A minimum-history cutoff was measured and rejected (>=40 prior points still leaves 76.2% firing). Needs a human product decision among: accept a magnitude constant and amend the done_when; latest-point-only plus a separate anti-amnesia mechanism; require a consecutive run of breaches; threshold a coarser rolling statistic; or drop the story. Analysis + live numbers in .sdlc/plans/119.md. |
| 2026-08-01T07:27:40Z | swapnil-agrim | claimed | 119 |  |
| 2026-08-01T07:06:57Z | swapnil-agrim | claimed | 119 |  |
| 2026-08-01T07:06:42Z | swapnil-agrim | done | 118 |  |
| 2026-08-01T07:06:27Z | swapnil-agrim | merged | 118 | auto-merge (squash) armed on PR #199 |
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
