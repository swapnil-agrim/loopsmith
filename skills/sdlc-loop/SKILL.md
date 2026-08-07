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

1. `goals=$(python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" next-batch .sdlc)` — one goal per line, or a
   lone `DONE`/`BUDGET`. Off by default (`parallel.goals.enabled`, config-gated): with it off, or only
   one goal available, this is byte-identical to plain `next` — exactly one line — and everything below
   runs inline exactly as documented, for that one goal (`goal=$goals`).

   **1a. More than one line? Dispatch the batch, don't queue it** (F10.5-3/#375) — mirrors 3b's
   slice-wave shape one level up: instead of ONE goal's implementation slices running concurrently,
   MULTIPLE goals run concurrently, each all the way through its own steps 2-7. For each goal in the
   batch, dispatch a **subagent** (fresh context) that runs this skill's steps 2 through 7 for that ONE
   goal (log the dispatch: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" agent_dispatch
   --role goal-slot`) exactly as documented — it resolves its own model tier, cuts its own worktree (3a already
   isolates goals from each other: separate worktree + branch + PR per goal, so no extra
   `isolation: worktree` bookkeeping is needed beyond what `work.py start` already does for a single
   goal), runs its own review, and records its own outcome. **Never** dispatch with an unattended
   `claude -p` (same reason as 3b: uncapped spend, and a second unmanaged worker on one `.sdlc` breaks
   state) — and **never** hand a subagent's own scratch/comparison work a `cp`/`rsync` of another
   goal's live worktree; a worktree's `.git` is a file pointing at shared metadata, and copying it can
   corrupt the real one.

   Track which goals are currently live in your other slots — you already know this, you dispatched
   them. As EACH subagent finishes (done/parked/failed) — not the whole batch — log it first:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" agent_done --role goal-slot --result
   <done|parked|failed>` for the goal that just finished, THEN immediately refill
   just that ONE freed slot: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" next .sdlc --skip
   <comma-separated other still-live goals>`. **The `--skip` list is not optional for a refill.**
   `next`/`next-batch`'s own claim-liveness check (F10.5/#374) can only tell whether the SHORT-LIVED
   `loop.py` process that wrote a claim is still literally running — and that process has already
   exited by the time it returns the goal to you, regardless of whether a subagent is still actively
   working it minutes later. Nothing else stands in for that once the picking call itself is gone: a
   goal's own `in_progress` status alone does **not** exclude it from a later pick. Omitting `--skip`
   on a refill can re-dispatch a goal a sibling slot already holds — two subagents doing the same
   work, worse than not parallelizing at all. Continue refilling one slot at a time until a call
   returns `DONE` or `BUDGET`. Unlike 3b's slices, goals carry no declared file-conflict graph — each
   gets its own worktree and PR, so overlap surfaces later as an ordinary PR-rebase (this plugin's own
   history already handles that routinely), not a silent lost edit. Note also that with multiple slots
   live, it is the ORCHESTRATING pass — the one calling `next`/`next-batch` for refills — that sees any
   **LEDGER INBOX** block (step 6 below), not a subagent mid-goal; handle it there, between refills,
   the same way you would between goals in the single-goal path.
2. If output is `DONE` (backlog empty) or `BUDGET` (a per-run budget hit: iterations, wall-clock
   minutes, or reported tokens — each only when `config.json` sets it) → STOP. If the host surfaces
   token usage to you, report it between goals — `loop.py spend .sdlc <tokens>` — so
   `budget.max_tokens` can actually enforce; never guess a number (no report = no token cap). If
   the host breaks that usage down per phase (rare), attribute it instead of the plain form:
   `loop.py spend .sdlc <tokens> "$goal" --phase <phase> --tokens_in N --tokens_out N` —
   otherwise keep the two-argument form; never fabricate a per-phase number you don't have.
3. Otherwise: first **cross-check the pick** (opt-in, `backlog_check.enabled`) — before spending a
   token, run `result=$(python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" precheck .sdlc "$goal")`. It
   prints `OFF` (a no-op — feature disabled) or, when on, refreshes the board mirror and cross-checks the
   goal against the rest of the backlog + the team ledger at **zero LLM cost** (also checking the
   goal's own recent comments for a human-authored dependency marker your loop never wrote), then
   either:
   - prints **`PARKED <reason>`** — the goal was a confident DUPLICATE / OBSOLETED-BY-completed-work /
     BLOCKED-BY item and has already been parked-with-proof (the evidence is on the issue). **Do not
     research it: loop back to step 1** and take the next goal. (The park counts as one iteration.)
   - prints **`PROCEED`** (optionally `(advisory)`, having annotated a weak match) → carry on below.

   Then **check for an oversized goal** (opt-in, `goal_decompose.enabled`) — before spending a
   token, run `result=$(python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" decompose-check .sdlc
   "$goal")`. It prints `OFF` (a no-op — feature disabled) or, when on, classifies the goal's own
   body at **zero LLM cost** (a goal already marked as a decomposition child or meta-goal is exempt
   by construction), then either:
   - prints **`PARKED <reason>`** — the goal reads like an epic (oversized per the classifier) and
     has already been parked for a human to split it. **Do not research it: loop back to step 1**
     and take the next goal. (The park counts as one iteration.)
   - prints **`PROCEED`** (optionally `(flagged: <reason>)`, `log` mode having only annotated) →
     carry on below.

   Placed AFTER the cross-check above so a duplicate parks as a duplicate — a goal that is both a
   dup and an epic never reaches this check.

   Then **recall prior art** — if the knowledge graph is enabled, run the `sdlc-context`
   pre-flight to pull a cited brief from the graph + past issues + conventions, so the goal starts
   informed by history instead of a flushed window (no-op when the KG is off).
   **Match the model to the goal** — run `python3 "${CLAUDE_SKILL_DIR}/../sdlc-model/scripts/predict.py"
   resolve "$goal" .sdlc`. If it prints a tier (`haiku`/`sonnet`/`opus`/`fable`), that tier is the GOAL's
   ceiling — log it (`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" model_choice --model
   <tier>`), then run **each phase as its own subagent** with that `model` (the Task tool's model override —
   the session can't switch its own model). Bracket each phase-subagent dispatch itself:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" agent_dispatch --role phase --phase
   <phase>` right after dispatching it, `agent_done --role phase --phase <phase> --result <...>` right
   when it returns. One subagent PER PHASE, not one for the whole goal: that is
   what keeps a reviewer phase a **sibling** of the maker phase it checks (fresh context, no nesting),
   never a continuation of it — see the maker≠checker rule below. Artifacts pass between phases through
   the filesystem (`.sdlc/plans/`, the worktree diff, the issue timeline), not shared context. If it
   prints `off` (the default, or you're off-Claude), run the phases inline as usual. **Per-STEP downgrade:** once the plan exists, resolve each plan
   step too — `python3 "${CLAUDE_SKILL_DIR}/../sdlc-model/scripts/predict.py" resolve-step "<step
   text>" .sdlc` prints `model=<tier> effort=<low|medium|high>` — log it too (`python3
   "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" model_choice --model <tier> --effort <effort>
   --phase <phase>`) — and run a MECHANICAL step (run the
   tests, a watcher/poll, lint) in a subagent at ITS cheaper tier/effort instead of the goal
   ceiling. Where the host's subagent API takes a reasoning-effort parameter, pass the effort;
   otherwise the tier alone. Never run a step ABOVE the goal ceiling. Then read the goal and
   run it through the full SDLC (research → plan → plan-review →
   implement → review) — each phase via its **executor** (the `superpowers`/`code-review` companion on
   Claude if installed, else LoopSmith's portable `sdlc-brainstorm`/`sdlc-research`/`sdlc-plan`/
   `sdlc-implement`/`sdlc-review`/`sdlc-verify`; each skill's resolution header picks). `$goal` is a **file path** in local mode (read the file) or a **GitHub issue
   number** in github mode (`gh issue view "$goal"` to read it).

   **The maker is never the checker (`config.review.independent`, default on).** Every review gate —
   plan-review, the pre-PR code review, and the post-PR review at step 6 — runs as a **fresh subagent
   that never saw the maker's context**. Give it the PROJECT, not the author:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/review_context.py" brief .sdlc "$goal" --for
   plan-review|code-review|pr-review [--artifact <path|PR#>]` assembles the pack — north-star +
   conventions + contracts + the goal + a pointer to the artifact — and you hand the subagent **only
   that**. So it re-derives blast radius from the whole repo and can *disagree*, instead of
   rubber-stamping the plan/diff it just wrote (which a lower-tier maker does — that is where a
   self-review adds nothing). A diff-only reviewer cannot see what a small change breaks two files
   away; the brief's whole-repo grounding is the point. Where the host has no subagents, degrade
   honestly: run the review inline but **reload the brief fresh and take the reviewer's stance**. With
   `review.independent: false` reviews run inline as before (the maker reviews its own work — only for a
   trivial solo repo where the ceremony isn't worth it).
   **Match the ceremony to the goal** — after Research, resolve the lane it measured. In **local mode**
   run `python3 "${CLAUDE_SKILL_DIR}/scripts/discovery.py" lane "$goal"`; in **github mode** the goal is
   an issue number with no frontmatter, so read the lane from Research's phase note on the issue
   timeline you already fetched. Either way an unsized goal is **`medium`** — unknown gets more rigour,
   not less. On **small**, plan in a few lines and keep the retro to one; on **large**, work the design
   out before planning and consider splitting it into several goals. **Plan-Review runs in full at every lane** — small goals are exactly
   where an unreviewed plan ships, because nobody looks twice at an obvious-seeming change.
   The lane is a *starting* call: if implementation shows the goal is bigger, escalate and say so in the
   phase note. **Park instead of forcing through**
   if you hit any of:
   - a hard checkpoint / a decision only the user can make,
   - an **irreversible or expensive action** (deploy, delete, overwrite, spend, migrate) — NEVER
     run one unattended,
   - a failure you cannot resolve — record THIS one as `failed` (see step 6): parked means
     "needs a human decision", failed means "needs a fix"; the queue separates the two.

   **3a. Cut this goal's worktree BEFORE you edit anything.** With `config.work.enabled` on:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/work.py" start .sdlc "$goal"`. It cuts a fresh worktree and
   branch from `<remote>/<base>` — which **is** the goal-start rebase: nothing to replay, so it
   cannot conflict or strand a half-applied tree in an unattended run. Then register that you're the
   one driving this goal right now: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" agent-start .sdlc
   "$goal" --pid $PPID` — `$PPID` is YOUR OWN stable process id, captured once (same contract
   `--session-pid` already documents), not any individual command's own. Best-effort and always safe
   to call — a no-op unless `agent_watch.enabled`. Do **every edit for this goal
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
   them cannot fight over one checkout. For each dispatched slice: log the dispatch
   (`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" agent_dispatch --thread
   <slice-id> --role slice --phase implement`) and register it for the death-watch the same way 3a
   does, one level down (`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" agent-start .sdlc "$goal"
   --pid $PPID --thread <slice-id>` — giving intra-goal slice parallelism its own
   independently-tracked pid per thread). Land the wave — logging each landed slice first (`agent_done
   --thread <slice-id> --role slice --result <...>`) — then re-run `plan` for the next. **Never**
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

   **Found something worth tracking that isn't a cross-area blocker? Never call `gh issue create`
   directly.** A same-area follow-up, a review finding, anything worth its own issue but not a
   decision for the user right now, still needs a label and an assignee or it is orphaned — the
   majority-real-world shape of this bug, worse than the cross-area case above because nothing
   documented the right command for it until now. Use `handoff.py track` the same way a cross-area
   blocker already goes through `handoff.py open`:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/handoff.py" track .sdlc "$goal" --area <area> --why "<what
   you found>" --queue actionable|queued --assignee same-area|cross-area --blocks yes|no [--priority
   P0|P1|P2] [--label sdlc:followup]`. All three value-flags are required, with no default, on
   purpose. Choose `--queue queued` for anything not urgent enough to jump the backlog (the usual
   case); reserve `--queue actionable` for something that genuinely should be picked up next. Choose
   `--assignee same-area` to file it to yourself — you're already working this area; `--assignee
   cross-area` routes it through CODEOWNERS like `open` does. Choose `--blocks yes` ONLY when the
   *current* goal truly cannot proceed until the new issue lands — getting this wrong incorrectly
   parks unrelated work; a merely-related finding is `--blocks no`. Recommend `--label sdlc:followup`
   on a non-blocking review finding so it is greppable as a class, distinct from `sdlc:dependency`.

   As you complete each phase, **record it** so the issue timeline is the audit trail:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" note .sdlc "$goal" "<phase>: <key findings / decisions>"`.
   Mark phase boundaries too (optional, `telemetry.enabled`):
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" emit .sdlc "$goal" phase --phase <goal|research|plan|plan_review|implement|review|retro> --state start`
   at the start of each phase, `--state end` when it finishes. Best-effort telemetry — never gates
   progress, skip it rather than guess the phase name. Do not invent `ms`, `tokens_in`, or
   `tokens_out` for a `phase` event — the loop cannot measure per-phase timing or spend from prose.
   The first time in a phase you create, edit, or delete a file not already logged this phase, log
   that too: `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" log .sdlc "$goal" file --path <path> --op
   create|edit|delete` — do not log a re-edit of an already-logged file, and do not log reads.
   For a decision/finding/fix worth keeping, record a 🔒 Critical Insight (the
   `.github/CRITICAL_INSIGHT_TEMPLATE.md` format) the same way. This comments the issue in github mode
   and appends to `.sdlc/journey/<goal>.md` in local mode; it's fail-open (never breaks the run).
4. Entering the **review** phase? Move the board card to QC:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" qc .sdlc "$goal"` (github-project board only — a no-op for local/issues).
5. **Retrospective (Learn)** — after Review, run the **`sdlc-retro`** executor (advisory): reflect on
   the structural + product debt the fix left behind and grade intent-vs-shipped. Under
   `config.review.independent` this too runs as a **fresh, author-blind subagent**
   (`python3 "${CLAUDE_SKILL_DIR}/scripts/review_context.py" brief .sdlc "$goal" --for retro`) — the
   context that just argued the work was done shouldn't also grade whether it met the intent. Autonomous
   mode → **write only the audit-trail notes**, and **park** any north-star / standing-rule proposal to
   the review queue for a human; never edit a standing doc unattended. Fail-open — it never breaks the run.
6. Record the outcome:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" record .sdlc "$goal" done` (or `parked "reason"` / `failed "reason"`).
   With `config.verify.enforce` on, a `done` needs FRESH machine evidence first —
   `python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" verify .sdlc "$goal"` runs the goal's proving
   command (frontmatter `verify_command`, else `verify.command`) and records it; `record done` is
   REFUSED without a passing, this-run verify.
   **Landing the work** (`config.work.enabled` on) — once verify is green, and never with a bare
   `git` command (the loop has no general git tool on purpose):
   `work.py commit .sdlc "$goal" --message "<type: what changed>"` → `work.py pr .sdlc "$goal"`.

   **Then REVIEW the PR you just opened, if `config.work.require_review` is set** — a real review AFTER
   the PR. Self-review before the PR is never enough; this is a **fresh, adversarial pass over the PR's
   real, mergeable diff** (post-commit, post-CI). It **MUST run as a fresh, author-blind subagent** —
   the maker never clears its own PR — fed the reviewer brief for this gate:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/review_context.py" brief .sdlc "$goal" --for pr-review
   --artifact <PR#>` (see the maker≠checker rule above), running `/code-review` on the PR, else
   `/sdlc-review` in diff mode. That subagent decides the verdict below. **No human approves — the loop reviews and clears its own PR:**
   - **No blocking issues** → `work.py post-review .sdlc "$goal" --verdict approve` (posts `loopsmith:approve`).
   - **Blocking issues** → `work.py post-review .sdlc "$goal" --verdict block --reason "<the issues>"`, then
     **fix them in the worktree** (back to Implement), re-run `loop.py verify`, then
     `work.py commit` **AND `work.py pr` — the push is not optional**, and **re-review**. Repeat
     until clean, then post `--verdict approve`.
     **`commit` is LOCAL; only `pr` pushes.** Skipping it leaves the PR head at the pre-fix commit,
     so GitHub's checks and reviews all pass — correctly — about code nobody approved, and an armed
     auto-merge squashes that. This shipped defects to a protected `main` three times before
     `gate()` grew a STALE HEAD refusal; the guard is the backstop, this line is the intent. **The cycle is hard-capped:** `post-review` counts the
     block cycles and, once they hit `work.max_review_cycles` (default **3**), returns a `PARK: …` line
     instead of asking for another fix — the review genuinely didn't converge, so `record parked "<why>"`
     for a human. You never have to count the cycles yourself; the cap is enforced in code.

   Then `work.py merge .sdlc "$goal"`. The gate is clean **and** safe: it needs THIS run's passing verify
   evidence *and* GitHub's `mergeable` + `mergeStateStatus CLEAN`, **plus — with `require_review` on — the
   review verdict you just posted** (it will not merge a PR that isn't `loopsmith:approve`d, or that has a
   `loopsmith:block` / an unresolved thread). It rebases once if the PR is `BEHIND`, and arms GitHub's own
   `--auto` so the last word is an atomic re-check at merge time rather than a stale read.
   **Read its first word and record accordingly — never merge past it by hand:**
   - `PARK: …` → `record parked "<that reason>"` (a conflict, a stale read, no evidence). A **failing
     required check** is a fix, not a decision → `record failed "<the check>"` instead.
   - `PR #N opened — …` → **`record done`**. This is the open-source path: a fork PR, or a repo you
     only have read access to, can never be merged by you, so the PR *is* the deliverable and the loop
     has done everything it can. It is not a park — nothing about it wants a human here.
   - `auto-merge armed …` / `clean and safe …` → `record done`.

   `work.auto_merge` is `off` | `protected` | `always`, default **off**. `protected` merges only where
   the base branch genuinely REQUIRES checks or reviews — autonomy proportional to the guardrails that
   actually exist. After a `done`, release the checkout: `work.py finish .sdlc "$goal"` — it
   deliberately KEEPS a worktree that still holds uncommitted work, so a parked goal stays intact for
   whoever picks it up.
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
