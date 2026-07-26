---
name: sdlc-loop
description: Run the autonomous park-and-continue SDLC loop over the .sdlc/goals backlog. Use when the user runs /sdlc-loop or asks to run goals autonomously / overnight / unattended.
allowed-tools: Bash(python3 *), Bash(gh issue view *)
---

# sdlc-loop

Drive the backlog autonomously. The Python helpers own state/budget + the backlog source; you run
each goal. The source is config-selected (`.sdlc/config.json` → `discovery.source`): **local goal
files** (default) or **GitHub issues** (`source: github`, needs an authenticated `gh`). You run the
loop the same way either way — the helper handles where goals come from and how status is recorded.

First, reset the per-run budget: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" start .sdlc`

Then repeat until the helper says stop:

1. `goal=$(python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" next .sdlc)`
2. If output is `DONE` (backlog empty) or `BUDGET` (a per-run budget hit: iterations always;
   wall-clock minutes / reported tokens when `config.json` sets them) → STOP. If the host surfaces
   token usage to you, report it between goals — `loop.py spend .sdlc <tokens>` — so
   `budget.max_tokens` can actually enforce; never guess a number (no report = no token cap).
3. Otherwise: first **recall prior art** — if the knowledge graph is enabled, run the `sdlc-context`
   pre-flight to pull a cited brief from the graph + past issues + conventions, so the goal starts
   informed by history instead of a flushed window (no-op when the KG is off).
   **Match the model to the goal** — run `python3 "${CLAUDE_SKILL_DIR}/../sdlc-model/scripts/predict.py"
   resolve "$goal" .sdlc`. If it prints a tier (`haiku`/`sonnet`/`opus`/`fable`), that tier is the GOAL's
   ceiling — run its phases inside a **subagent with that `model`** (the Task tool's model override —
   the session can't switch its own model); if it prints `off` (the default, or you're off-Claude),
   run the phases inline as usual. **Per-STEP downgrade:** once the plan exists, resolve each plan
   step too — `python3 "${CLAUDE_SKILL_DIR}/../sdlc-model/scripts/predict.py" resolve-step "<step
   text>" .sdlc` prints `model=<tier> effort=<low|medium|high>` — and run a MECHANICAL step (run the
   tests, a watcher/poll, lint) in a subagent at ITS cheaper tier/effort instead of the goal
   ceiling. Where the host's subagent API takes a reasoning-effort parameter, pass the effort;
   otherwise the tier alone. Never run a step ABOVE the goal ceiling. Then read the goal and
   run it through the full SDLC (research → plan → plan-review →
   implement → review) — each phase via its **executor** (the `superpowers`/`code-review` companion on
   Claude if installed, else LoopSmith's portable `sdlc-brainstorm`/`sdlc-plan`/`sdlc-implement`/
   `sdlc-review`/`sdlc-verify`; each skill's resolution header picks). `$goal` is a **file path** in local mode (read the file) or a **GitHub issue
   number** in github mode (`gh issue view "$goal"` to read it). **Park instead of forcing through**
   if you hit any of:
   - a hard checkpoint / a decision only the user can make,
   - an **irreversible or expensive action** (deploy, delete, overwrite, spend, migrate) — NEVER
     run one unattended,
   - a failure you cannot resolve — record THIS one as `failed` (see step 6): parked means
     "needs a human decision", failed means "needs a fix"; the queue separates the two.

   **3a. Cut this goal's worktree BEFORE you edit anything.** With `config.work.enabled` on:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/work.py" start .sdlc "$goal"`. It cuts a fresh worktree and
   branch from `<remote>/<base>` — which **is** the goal-start rebase: nothing to replay, so it
   cannot conflict or strand a half-applied tree in an unattended run. Do **every edit for this goal
   inside that worktree**; the human's checkout must never move, and never change branch (it would
   rewrite `.sdlc/goals/` underneath you). Bookkeeping is the exception and stays in the MAIN
   checkout — keep passing `loop.py`/`ledger.py` the same `.sdlc` path as always, never the
   worktree's stale copy of it. `loop.py verify` finds the worktree by itself. Feature off → work in
   the checkout exactly as before.

   **3b. Independent slices? Run the wave — don't queue it.** Once the plan exists, if it declared
   slices in `.sdlc/plans/<goal-stem>.slices.json`, compute the dispatch plan:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/slices.py" plan .sdlc "$goal"`. It groups the runnable slices
   into **waves** — each wave mutually non-conflicting by declared files, capped at
   `parallel.max_concurrent`. Do ONE wave at a time: dispatch each of its slices as a **subagent**
   (fresh context), with **`isolation: worktree`** for every slice the plan marks that way, so two of
   them cannot fight over one checkout. Land the wave, then re-run `plan` for the next. **Never**
   dispatch a slice with an unattended `claude -p` — uncapped spend, and a second worker on one
   `.sdlc` breaks every state file here. A slice the plan marks `dispatch: session` will not fit one
   subagent's context: **print its exact `claude --worktree <name>` line and let the human start it**
   — never start it yourself. No manifest, or `parallel.enabled` off → run the goal as one unit,
   exactly as before.

   **Blocked on someone else's AREA? Hand it off before you park.** A dependency in code another
   person owns is not a decision for the user — parking it silently tells nobody and the work
   stalls until a human happens to notice. Instead:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/handoff.py" open .sdlc "$goal" --area <area> --why "<what
   is needed>" [--priority P0|P1|P2]`. It resolves the owner from the repo's CODEOWNERS, opens an
   issue in their area **assigned to them and carrying the goal label** — so their own loop picks
   it up — records it in the team ledger addressed to them, and links it from this issue. THEN park
   this goal as normal. Degrades honestly: with no owner, no `gh`, or a local backlog it still
   records the ledger entry.

   As you complete each phase, **record it** so the issue timeline is the audit trail:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" note .sdlc "$goal" "<phase>: <key findings / decisions>"`.
   For a decision/finding/fix worth keeping, record a 🔒 Critical Insight (the
   `.github/CRITICAL_INSIGHT_TEMPLATE.md` format) the same way. This comments the issue in github mode
   and appends to `.sdlc/journey/<goal>.md` in local mode; it's fail-open (never breaks the run).
4. Entering the **review** phase? Move the board card to QC:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" qc .sdlc "$goal"` (github-project board only — a no-op for local/issues).
5. **Retrospective (Learn)** — after Review, run the **`sdlc-retro`** executor (advisory): reflect on
   the structural + product debt the fix left behind and grade intent-vs-shipped. Autonomous mode →
   **write only the audit-trail notes**, and **park** any north-star / standing-rule proposal to the
   review queue for a human; never edit a standing doc unattended. Fail-open — it never breaks the run.
6. Record the outcome:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" record .sdlc "$goal" done` (or `parked "reason"` / `failed "reason"`).
   With `config.verify.enforce` on, a `done` needs FRESH machine evidence first —
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" verify .sdlc "$goal"` runs the goal's proving
   command (frontmatter `verify_command`, else `verify.command`) and records it; `record done` is
   REFUSED without a passing, this-run verify.
   **Landing the work** (`config.work.enabled` on) — once verify is green, and never with a bare
   `git` command (the loop has no general git tool on purpose):
   `work.py commit .sdlc "$goal" --message "<type: what changed>"` → `work.py pr .sdlc "$goal"` →
   `work.py merge .sdlc "$goal"`. The gate is clean **and** safe: it needs THIS run's passing verify
   evidence *and* GitHub's `mergeable` + `mergeStateStatus CLEAN` (required checks and reviews folded
   in), it rebases once if the PR is `BEHIND`, and it arms GitHub's own `--auto` so the last word is
   an atomic re-check at merge time rather than a stale read. Any line it prints beginning `PARK:` is
   exactly that: `record parked "<that reason>"` and move on — **never** merge past it by hand. With
   `work.auto_merge` off (the default) it stops at "clean and safe" and leaves the PR for a human.
   After a `done`, release the checkout: `work.py finish .sdlc "$goal"` — it deliberately KEEPS a
   worktree that still holds uncommitted work, so a parked goal stays intact for whoever picks it up.
   Declared a pipeline (`.sdlc/pipeline.json`)? Run the
   bidirectional report card between goals — `python3 "${CLAUDE_SKILL_DIR}/scripts/pipeline.py" card
   .sdlc` — and treat its findings as inputs, not gates. `pipeline.py propose .sdlc` turns the card's
   FAILING signals into `proposed` goal files (with the failing check wired as `verify_command`);
   the loop NEVER runs a `proposed` goal — a human promotes it to `pending` first.
   With `config.ledger.enabled` on, the claim and the outcome are mirrored to the **team ledger**
   automatically — never record those by hand. Record anything a TEAMMATE needs to see with
   `python3 "${CLAUDE_SKILL_DIR}/scripts/ledger.py" append .sdlc note "$goal" --to <login> --why "…"`.
   `loop.py next` prints a **LEDGER INBOX** block on stderr when a teammate needs you: read it,
   answer each item with `handoff.py ack .sdlc --issue <n> --state accepted|deferred|declined|resolved`,
   and take a `P0` next rather than interrupting the goal you are in.
7. Loop.

**Self-improving (optional, gated):** when the backlog is empty (`next` → `DONE`) but the knowledge
graph is enabled and `kg.py gap list .sdlc` shows open gaps **and** budget remains, you may close the
loop instead of stopping: take the oldest gap, research it, write the finding to
`.sdlc/knowledge/analysis/`, refresh the graph (`/sdlc-kg`), then mark it filled —
`python3 "${CLAUDE_SKILL_DIR}/../sdlc-kg/scripts/kg.py" gap resolve "<the gap>" .sdlc`. **One gap per
spare iteration, only within budget, and park (never force)** anything that needs a human. This is how
the graph fills what it didn't know — turn it off by leaving the KG disabled.

At STOP, print one machine-readable line FIRST — `LOOP STOP: backlog-empty` or `LOOP STOP: budget` — then report: N done, M parked, K failed. (Unattended overnight? The user can wrap this loop
in `scripts/supervise.sh` — it relaunches through usage-limit resets and crashes with zero
polling; you never invoke it yourself mid-session.) If anything parked or failed, point the user to the items —
`.sdlc/state/review-queue.md` in local mode, or the issues labelled `sdlc:parked` (the **Blocked**
column on the board) in github mode.
Parking is always correct over forcing an irreversible action to "finish" a goal.
