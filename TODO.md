# TODO / parked

Deliberately deferred work, with enough context to pick it up cold.

## Tier-2 LLM-judge behavioral evals — parked (needs an API budget)

The quality pipeline's second tier (`evals/`): run the agent on each fixture goal, then have an LLM
judge score the transcript against that fixture's `tier2_rubric` (did it plan before editing? did
plan-review catch the planted flaw? did review find the planted bug? did it park on the irreversible
action?); track scores over time and fail on a drop below baseline.

- **Status:** the runner + its injectable `agent`/`judge` seam are already built and tested
  (`run_tier2`, `test_tier2_seam_hermetic`). `python3 evals/run.py --live` prints a parked notice and
  spends nothing.
- **Why parked:** it costs API tokens per run and needs a judge-model + rubric decision — a spend
  call, not one to make unattended.
- **To wire when greenlit:** implement the real `agent(prompt)` (run the agent headless on the fixture
  goal) and `judge(output, rubric)` (an LLM scoring 0..1 per rubric line) in `evals/run.py`, gate both
  behind `--live` + an API key, and run it nightly / pre-release — not per-PR (cost + non-determinism).
  See [`evals/README.md`](evals/README.md) for the two-tier design.

## Verify the Cursor adapter in a live session — before claiming Cursor support

`sdlc-init --cursor` scaffolds `.cursor/rules/sdlc.mdc` + pins `companions: off`, and this is
unit-tested — but LoopSmith has **not been run end-to-end inside Cursor.** Until it is, the README
marks Cursor **experimental** and makes no working-support claim. To close this: open a real Cursor
project, run the `--cursor` scaffold, confirm the `.mdc` rule actually loads as always-applied context
and the agent follows the spine + runs the portable executors + the `python3` helpers. Then drop the
"experimental" caveats.

## Other deferred (see the roadmap for context)

- **research-radar Phase B/C** — findings → gap log → the loop fills them; opt-in guard-railed GitHub
  filing. Deferred until the dry-run digest (`/sdlc-radar`) proves useful. **Deliberately still
  deferred** after a second look: filing issues nobody asked for is negative value until the digest
  earns its keep, and dormant machinery for a feature that may never ship is just carrying cost.
  When it *is* greenlit, the reference repo has a working, tested implementation of the safety
  layer — `guardrails.py` (label hygiene, per-run write caps, a bot signature) plus a
  content-keyed dedup `ledger.py` — under its `.claude/skills/research-radar/lib/`. Port from there
  rather than re-deriving; the caps and the signature are the part that makes unattended writes
  defensible. (Its `agenda.py` also scores by priority/recency where `radar.py agenda` rotates by
  index — a Phase A upgrade, worth taking only if round-robin proves to starve something.)
- **Second-host adapters beyond Cursor** — once Cursor is verified, a Codex/other adapter can follow
  the same shape (a host rules file + `companions: off`).
