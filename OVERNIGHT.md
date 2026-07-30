# Starting the unattended overnight run

**Status: blocked on one 30-second step only you can do.**

## The blocker

`supervise.sh` relaunches headless `claude -p /sdlc-loop` sessions. The CLI is not logged in:

```
$ claude auth status
{ "loggedIn": false, "authMethod": "none", "apiProvider": "firstParty" }
```

There is no `~/.claude/.credentials.json` and no `ANTHROPIC_API_KEY` in the environment. The
interactive session you were talking to is authenticated by the **host app**, not by a CLI
credential store, so a headless subprocess has nothing to inherit. Started blind, the supervisor
would have burned all 48 relaunches failing on auth and produced nothing.

## The fix — pick either, then start the supervisor

**Option A — log the CLI in (recommended).** In a normal terminal:

```bash
claude login
```

Confirm it took, then start the run:

```bash
claude auth status          # expect "loggedIn": true
cd /Users/swapnildubey/Documents/loopsmith/sdlc-kit
caffeinate -is bash skills/sdlc-loop/scripts/supervise.sh .sdlc
```

**Option B — use an API key.** Export it in the shell you launch from (do not paste it into chat):

```bash
export ANTHROPIC_API_KEY=...
cd /Users/swapnildubey/Documents/loopsmith/sdlc-kit
caffeinate -is bash skills/sdlc-loop/scripts/supervise.sh .sdlc
```

`caffeinate -is` matters — a sleeping Mac stops everything, and the supervisor's whole design is to
sleep between relaunches rather than poll.

## Watching it

```bash
tail -f .sdlc/state/supervisor.log      # relaunch decisions
tail -f .sdlc/state/supervisor.tail     # the live session's output
python3 skills/sdlc-loop/scripts/ledger.py summary .sdlc
```

Stop it any time:

```bash
touch .sdlc/state/supervisor.stop
```

## What it will pick up

`loop.py next` orders by **issue number**, not by priority label — nothing reads `priority:P*`.
Later tranches are held out of view with `sdlc:parked` instead, which is what actually enforces
order. Live queue:

| | |
|---|---|
| #99–#106 | E1 Ingest (8 goals) |
| #108–#114 | E2 Metric layer (7 goals) |
| **#162** | `[E0.S5]` Root `LICENSE` still grants MIT over `insight/` — **P0, but sorts LAST** |

**⚠ #162 is the one ordering wart left.** It is a P0 licensing hole — the root `LICENSE` grants MIT
over "the Software" with no carve-out, so a recipient reading only that file sees an unqualified
MIT grant covering `insight/`. Because it was filed late it has a high issue number, so the loop
will take it *after* all fifteen E1/E2 goals. If you want it first, park E1+E2 before starting:

```bash
for i in 99 100 101 102 103 104 105 106 108 109 110 111 112 113 114; do
  gh issue edit $i --repo swapnil-agrim/loopsmith --add-label sdlc:parked
done
```

…then remove the label again once #162 lands. Note #162 also requires relaxing
`test_plugin_licence_is_still_mit`, which currently asserts `"Business Source" not in` the root
LICENSE — that assertion would reject the very carve-out the fix needs. The issue says so.

Parked for later tranches: E3, E5–E9, plus #163 and #168. Unpark by removing the `sdlc:parked`
label when E1/E2 land.

## Config as tuned for unattended running

* `work.auto_merge: protected` — and `main` now genuinely requires all four checks
  (`test (3.10)`, `test (3.12)`, `insight (3.10)`, `insight (3.12)`, `strict: true`).
* `work.require_review: changes` — the loop reviews its own PR and blocks itself.
* `work.max_review_cycles: 5` — raised from 3 on measured evidence: goal #94 needed exactly 3
  cycles to converge, so the old cap would have parked work that was converging.
* `verify.command: pytest -q tests/ && pytest -q insight/tests/` — stdlib-only and offline-safe on
  purpose. Do **not** re-add a `pip install` step until `insight/` actually imports duckdb; build
  isolation needs the network on every run and a non-venv install errors under PEP 668.
* `model_selection: auto`, `review.independent: true`, `parallel.enabled: false` (no slice
  manifests yet).
* `budget`: 60 iterations / 1440 min / 8M tokens **per run**; the supervisor relaunches after each.

## Already done (so you can tell progress from a fresh start)

Five goals merged, all through the full spine with independent review at every gate:

| | |
|---|---|
| #94 → [#161](https://github.com/swapnil-agrim/loopsmith/pull/161) | BUSL licence, folder boundary, non-vacuous marker guard |
| #95 → [#164](https://github.com/swapnil-agrim/loopsmith/pull/164) | Package skeleton, `pyproject.toml`, CLI |
| #96 → [#166](https://github.com/swapnil-agrim/loopsmith/pull/166) | Separate `insight` CI job, coverage split, widened verify |
| #97 → [#169](https://github.com/swapnil-agrim/loopsmith/pull/169) | Import boundary, both directions, by AST |
| #165 → [#171](https://github.com/swapnil-agrim/loopsmith/pull/171) | Distribution renamed `loopsmith-insight` |

`#167` (required checks) is closed — `main` now enforces all four contexts.

New issues the reviews surfaced, all filed and labelled: **#162** (root LICENSE carve-out, P0),
**#163** (marker beyond `.py`, parked), **#168** (coverage scoping + build residue, parked),
**#170** (the import guard misses bare `sys.path` imports, parked).

## Anything that parks

Lands in the **Blocked** column of [Project #6](https://github.com/users/swapnil-agrim/projects/6)
and carries the `sdlc:parked` label with the reason on the issue. Parking is the correct outcome for
anything needing a decision — read those first in the morning.
