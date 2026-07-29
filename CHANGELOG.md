# Changelog

## Unreleased

### Loop-created issues stop being silently blank on a board's custom fields (0.9.6)
- **The gap:** the only path that autonomously creates an issue mid-run — `handoff.py` opening a
  cross-area dependency — set labels, an assignee, and the built-in `Status` field, and *nothing else*.
  On a board with any custom Projects-v2 single-select field (a `Priority`, a `Section`, an effort
  estimate), a loop-created issue came out **quietly inconsistent with every human-made issue** — no
  error, no park, just wrong data a team eventually stops trusting the board over. (And a `priority:<n>`
  *label* is a different mechanism from a Projects-v2 `Priority` *field* — easy to think it's covered.)
- **`discovery.github.project.custom_fields`** (default `{}`) maps a custom single-select field name to
  an option name, e.g. `{"Priority": "Medium", "Section": "Task"}`; `create_dependency` stamps them on
  the issue it opens (mirrors how `project.columns` maps `Status`). A field the board lacks or a value
  that isn't one of its options is skipped, never guessed. Fail-open; empty `{}` = the prior behavior.
- **`/sdlc-doctor` enumerates the board's real fields** and flags any single-select field beyond `Status`
  that isn't mapped — turning a silent, discovered-months-later data-quality bug into a one-time setup
  warning (the pattern the ledger/verify-trap checks already use). Reads the board fields live; a
  can't-read (no `project` scope, no board yet) reports nothing rather than a false all-clear.

### The maker is never the checker — independent, project-informed review across every gate (0.9.5)
- **Every review gate now runs as a fresh, author-blind subagent.** Plan-review ran *inline in the
  context that wrote the plan* (zero separation, the highest-leverage gate); code review only got a
  fresh reviewer on the companion path; the post-PR review was "best run as a subagent" (a suggestion);
  and `model_selection: auto` **buried the whole goal in one subagent**, so every phase shared one mind.
  A maker reviewing its own work rationalizes rather than refutes — on a lower tier that amplifies
  hallucination exactly where the review should catch it.
- **`review_context.py` assembles the reviewer's brief** (`brief .sdlc <goal> --for
  plan-review|code-review|pr-review`): the north-star + conventions + contracts + the goal + a pointer
  to the artifact — the PROJECT, never the maker's transcript. The maker context is excluded *by
  construction*, so the reviewer re-derives blast radius from the whole repo and can disagree. A
  diff-only reviewer can't see what a small change breaks two files away; the whole-repo grounding is
  the point. Fail-open, ASCII-only, zero-dep.
- **`config.review.independent` (default on).** `/sdlc-loop` dispatches one fresh subagent **per phase**
  (not one per goal) so a reviewer is a *sibling* of the maker it checks — plan-review, code review, the
  post-PR review (which MUST run author-blind), and the retrospective grade; `sdlc-model` and `sdlc-goal`
  reconciled from "whole goal in one subagent" to per-phase. Where the host has no subagents it degrades
  honestly to an inline reviewer that still loads the brief fresh. `/sdlc-doctor` reports whether
  independent review is on or the maker is reviewing its own work (INLINE). `plan-review`, `sdlc-review`,
  and `sdlc-retro` skills now open with the independent-reviewer stance + whole-repo blast-radius rule.

### The loop's review→fix loop is hard-capped (0.9.4)
- **`work.max_review_cycles` (default 3) caps the autonomous review cycle.** When the loop reviews its own
  PR and requests changes, it fixes + re-reviews — and if the review kept finding new problems, that could
  churn until the whole run's budget ran out (the per-goal loop isn't bounded by `max_iterations`, which
  counts goals, not review passes). Now `work.py post-review` **counts the block cycles in the goal's work
  record and, at the cap, returns a `PARK:` line** instead of asking for another fix — the review didn't
  converge, so a human takes it. Enforced in code, not left to the SKILL prose. `0` disables the cap.

### The loop reviews its own PR and posts the verdict — no human in the loop (0.9.3)
- **`require_review` had only a READ side.** It parked a merge until a PR was approved, but *nothing in
  the kit posted the approval* — so with `require_review: "approval"` the loop would open a PR and park
  forever waiting for a `loopsmith:approve` that never came (the same permanent-refusal shape). The gate
  was built for an external reviewer; there was no autonomous author.
- **`work.py post-review` is the WRITE side.** After it opens the PR, the loop runs a **fresh, adversarial
  review of the real mergeable diff** (a review *after* the PR — distinct from the pre-PR self-review) and
  posts the verdict itself: `--verdict approve` → `loopsmith:approve` (the gate merges); `--verdict block
  --reason …` → `loopsmith:block`, and the loop **fixes the issues in the worktree, re-verifies, and
  re-reviews** until clean (bounded to a couple of cycles, then parks as a backstop). Fully autonomous —
  the loop is the reviewer, and a plain comment sidesteps GitHub's block on approving your own PR. A human
  can still use the same markers on a loop PR. `/sdlc-loop`'s SKILL drives the cycle; docs/config/doctor
  reframed from "a human approves" to "the loop reviews its own PR."

### Docs caught up with the 0.9.x features (0.9.2)
- The **"What you get"** table now lists the two 0.9.x headliners it was missing: **one-command adoption**
  (`/sdlc-setup`) and the **PR review gate** (`work.require_review`, incl. the `loopsmith:approve`/`:block`
  comment fallback). The **Feature-flags** table already carried `work.require_review`. `/sdlc-doctor`
  surfaces every one of these live (ledger setup, ignore mechanism, auto-merge policy, the review gate,
  the verify-worktree footgun) — verified against its actual output. Docs-only; version → **0.9.2**.

### The review gate works on a solo account — comment-based approve/block (0.9.1)
- **`require_review` no longer has a signal that can never fire.** GitHub structurally forbids approving
  or requesting-changes on your *own* PR — so on a repo where one identity both opens and reviews (a solo
  maintainer, or an org that pins all automation to one account), the formal `APPROVE`/`CHANGES_REQUESTED`
  signals are permanently dead, and `approval` mode would refuse *every* merge forever (the same shape as
  the old empty-`verify.command` trap).
- **Plain-comment markers are the self-usable channel** (comments have no self-authorship rule): a
  **`loopsmith:block`** comment is honored as a change-request, a **`loopsmith:approve`** comment satisfies
  approval, and **`loopsmith:unblock`** clears a block — latest marker wins, and a block overrides even a
  formal approval. Formal reviews and unresolved threads still count whenever a second real identity is
  around. `/sdlc-doctor` and the README now spell out the asymmetry. First patch release: **0.9.1**.

### `/sdlc-doctor` catches the worktree interpreter-path footgun
- **A relative `.venv`/`node_modules` path in `verify.command` now gets flagged before it bites.** Once
  `work.enabled` is on, verify runs in a *fresh* per-goal worktree that has none of your installed
  dependencies — so a natural `cd backend && .venv/bin/python3 -m pytest` fails `exit=127` on the first
  real run, even though it worked with `work.enabled: false` (which ran against the main checkout). It's
  a direct consequence of the 0.9.0 fix that correctly runs verify *in* the worktree. `/sdlc-doctor` now
  flags a bare relative `.venv`/`venv`/`node_modules` interpreter path (an absolute path is fine), and
  the README documents the requirement: the command must resolve independent of the working directory.

### A real PR review gate, not just self-review — `work.require_review`
- **Auto-merge can now wait for an actual review, independent of branch protection.** Before this, the
  merge gate's "safe" (`mergeStateStatus`) only folded in reviews the *base branch's protection*
  required — so a human's ad-hoc **"Request changes" on an unprotected base** (the common `staging`/`dev`
  shape) was invisible to it, and an unattended `auto_merge` landed straight over it. The kit's "Review"
  phase is a *self-review* of the agent's own diff before the PR even exists; nothing read real review
  feedback after the PR. (Confirmed by grep: zero handling of `reviewDecision` / `reviewThreads` anywhere.)
- **`work.require_review`** adds the gate: `changes` parks on a `CHANGES_REQUESTED` review or an
  unresolved review thread; `approval` also requires an `APPROVED` `reviewDecision` before merging —
  parking until a human approves. It reads the PR's real review state (`gh pr view` + GraphQL threads)
  and parks with a clear reason; a human approves and re-queues. **Off by default**, so existing behavior
  is unchanged, and **fail-open** — an unreadable review state never blocks (the other gates still hold).
  `/sdlc-doctor` reports the gate's state. Costs one extra read per merge; the right default for anything
  unattended on a low-protection base.

### Hooks survive a broken `python3` on the PATH
- **A messy multi-python machine no longer breaks the session on first use.** The hooks shell out to a
  bare `python3` — whatever the user's PATH resolves — and on a machine with pyenv/conda that can be a
  *broken* interpreter (a shim pointing at an uninstalled version, a half-broken base), not just an
  absent one. Since the hooks run on every edit / web-fetch / prompt, that failed the very first tool
  call, and the only fix a real adopter found was removing a Python version.
- **Now it degrades to a no-op instead of erroring.** A new `hooks/_py.sh` preflights the interpreter
  (`python3 -c ''`) and, if it can't run, exits 0 (allow) rather than failing; when it works it execs
  straight through, so stdin/stdout/exit-code still pass and a hook can still deny. `decision_gate.py`
  and `research_capture.py` now run through it. `sdlc_gate.sh`'s preflight catches a *broken* (not just
  missing) `python3`, and its final emit is guarded so a failure there can't fail the prompt hook.

### Adoption-safety guards — the silent states are now loud
For repos configured by hand (not via `/sdlc-setup`), the states that surprised real adopters are now
surfaced instead of silent:
- **`work.enabled: false` no longer looks like success.** A goal used to close, the ledger say `done`,
  and *no branch/commit/PR* ever get created — with no signal; you'd only notice on a stray `git status`.
  Now `loop.py start` prints a heads-up, `record … done` prints a loud note ("changes only in your
  working tree, no PR"), and `/sdlc-doctor`'s dashboard says it plainly.
- **The verify permanent-refusal trap is caught before it bites.** `verify.enforce: true` with an empty
  `verify.command` refuses *every* `done` forever. `/sdlc-doctor` now flags it as a real gap (a per-goal
  `verify_command` also satisfies it), and `loop.py start` warns up front rather than only failing at the
  first `record done`.
- **`/sdlc-doctor` reports which mechanism ignores the runtime dirs** — the tracked `.gitignore`, the
  local `.git/info/exclude`, or neither — so an adopter catches a mismatch with their intent.
- README documents both: the no-PR-when-`work`-off behavior, and that a host repo's own `PreToolUse`
  edit-gate applies to LoopSmith's Implement edits too.

### `/sdlc-setup` — adopt LoopSmith into an existing repo in one pass
- **One command configures a real repo the way a team actually wants it**, instead of ten manual edits
  to `config.json`. It detects the repo from the git remote (ssh / https / host-alias forms), finds the
  board, scaffolds `.sdlc/` if missing, and writes a config with the **safe adoption defaults**: github
  discovery scoped to **`assignee: @me`**, **ledger on**, **work (a PR per goal) on** (`auto_merge: off`
  — a clean PR is left for a human). Then it bootstraps the ledger and runs doctor.
- **It refuses two traps real adoptions hit.** It never sets `verify.enforce: true` without a real
  `verify.command` — that combination refuses *every* `done` forever — and turns an existing
  enforce-without-command back off. And it never clobbers or narrows a git-ignore rule a human already
  set: a blanket `.sdlc/` exclude is left untouched, and a `--scope local` routes runtime-dir ignores to
  `.git/info/exclude` (nothing the team sees) for a local-only adoption. `/sdlc-ledger` and the
  `sdlc-init` tip now use the same safe helper instead of a blind `echo >> .gitignore`.
- The skill also flags the host-hook interaction: if the repo gates source edits behind its own
  `PreToolUse` hook, LoopSmith's Implement-phase edits go through it too.

### One command turns the ledger on — and a `/sdlc-ledger` slash command
- **`sync.py bootstrap` (and `/sdlc-ledger`) sets the whole ledger up in one shot.** Standing the
  ledger up used to be a multi-step dance whose failure mode was silent: `init` created the ops branch
  *locally* and `publish` only pushed once you owned an entries file — so the branch reached the remote
  only after your first goal wrote a claim, and a teammate who ran `init` found nothing to fetch.
  bootstrap does init **+ seeds your (empty) entries file + the `TEAM.md` rollup + pushes**, so the
  branch exists for the whole team the moment the ledger is switched on. Idempotent; each teammate runs
  it once per clone to join.
- **`/sdlc-doctor` sets it up for you.** It now reports `team ledger initialized` as a real setup gap
  (enabled in config but branch not created) and runs the one-command bootstrap — the ledger comes up
  the moment you flip the switch and run doctor.
- **New `/sdlc-ledger` skill** makes every ledger operation a plugin command instead of a raw
  `python3 <path>/ledger.py …` line — set up, read (`mine`/`summary`/`render`), leave a note, hand a
  blocker off, answer a hand-off. Sharing a slash command with the team beats sharing a file path.
  Claiming/recording stays automatic inside `/sdlc-loop`; this is for everything around it.

### The ledger is a claim lease — two loops stop starting the same goal
- **A `claimed` line is now a lock, not just a record.** The team ledger already recorded who started
  what, but nothing read it back, so two people running the loop against one board could both pick the
  same issue (the assignee filter was the *only* thing keeping work apart). `_next` now consults the
  ledger before it commits: a goal another actor holds an **open** claim on — `claimed` with no later
  `done`/`parked`/`failed` — is skipped, and the loop takes the next free one instead.
- **Your own work is never locked against you.** A claim *you* hold is still returned, so a resumed or
  restarted run continues its own goal rather than treating it as taken.
- **A crashed claimer can't freeze a goal forever.** A claim older than `ledger.lease.ttl_hours`
  (default 12; `0` = never expire) is treated as released — the lock self-heals instead of stranding a
  goal on a dead run.
- **Honest about its guarantee.** It's an *advisory* lease over eventually-consistent state (it sees
  claims already pulled to the ops branch, not a distributed mutex), so per-person assignee routing
  stays the first line of defence; the lease closes the double-start gap behind it. **Off by default**
  and **fail-open**: no ledger, or an unreadable one, and selection is byte-identical to before.

### A loop trigger keeps its own watcher alive
- **The loop now starts the ledger watcher itself.** Entries were only ever appended locally; nothing
  pushed them to the ops branch except the watcher, and a team that ran the loop but forgot the watcher
  saw a silent ledger — claims and hand-offs piled up on one laptop and reached nobody. Every loop
  trigger (`loop.py next` and the unattended drain) now ensures the watcher is running before it does
  anything else, so the ledger flows without a separate manual step.
- **Firing it repeatedly is safe.** `watch.sh` is idempotent — a live `watch.pid` makes a second copy
  no-op instead of stacking watchers, while a stale pid from a crashed watcher is ignored so the next
  trigger takes over. The launch is **fail-open**: a watcher that can't start never breaks the run.
  Off entirely unless the ledger is enabled and initialised (`sync.py init` has made the worktree) —
  nothing to publish otherwise.

### A guardrail that isn't a prompt — the decision gate
- **`/sdlc-decide` + a `PreToolUse` hook.** Every other gate in this kit is discipline a model is
  *asked* to follow, and a model can talk itself past discipline — especially unattended, on iteration
  forty. Record an architectural invariant in `.sdlc/decisions.json` and the edit that breaks it is
  **denied** by a script that doesn't negotiate. `invariant` denies; `recipe` asks; `caution_on_touch`
  asks on any edit inside a path that's dangerous to touch at all.
- **Authoring the registry is the opt-in.** No registry, no behavior — installing the plugin changes
  nothing. `gates.decision_gate.enabled: false` turns it off without deleting the file, for refactors
  that intentionally move an invariant.
- **JSON, not YAML** — this kit takes no dependencies, and the registry is not worth breaking that for.
- **Precision is the whole product here.** A gate that cries wolf gets clicked through, and then it
  protects nothing. So: params are scoped to their own decision's paths (a name as common as `timeout`
  can't trip everywhere); only literal assignments are judged, never expressions; comments are
  stripped; and a violating value *quoted inside prose* — `doc = "set timeout: 120 here"` — does not
  fire. That last one was a real false-deny caught in test, and false denies cost more than misses.
- **`decision_gate.py check`** scans code already on disk, because the hook only ever sees edits going
  forward and the first question after authoring a registry is "does my code even comply?"
  **`validate`** catches entries that can never fire — a registry's failure mode is being quietly
  unenforceable, not loudly broken.
- **Editing the registry always asks.** Changing a recorded invariant is a supersession the user makes
  deliberately, never a silent rewrite by the agent about to be bound by it.
- `/sdlc-doctor` counts **active** decisions, not entries — a registry whose decisions are all
  superseded enforces nothing, and reporting that as ON would be the false assurance this gate exists
  to remove. `/sdlc-retro` can now propose an invariant as a fourth learning store.
- **Stated limits, in the skill:** it reads literal assignments only (`timeout = CONFIG.default` is
  invisible), it sees edit text rather than the program, and it fails open. It's a seatbelt against the
  obvious mistake, not a proof of compliance.

### The lane is routed on, not just recorded
- **Both orchestrators now branch on the lane** Research measured. Sizing a goal and then ignoring the
  size is the same producer-without-consumer defect `lane: auto` had before it was implemented: the
  value existed, nothing read it, and a typo fix drew the same seven-phase pass as a schema migration.
  `discovery.py lane <goal>` resolves it in local mode; github mode reads it from Research's note on
  the issue timeline, because an issue number carries no frontmatter.
- **small** plans in a few lines and keeps the retro to one; **large** works the design out before
  planning and asks whether it should be several goals; **medium** is the full pass, unchanged.
- **Plan-Review runs in full at every lane.** Small goals are exactly where an unreviewed plan ships —
  nobody looks twice at a change that seemed obvious — so no lane may skip the gate.
- An unsized goal resolves to **`medium`**, never `small`: guessing low on an unknown goal skips
  ceremony it might need, which is the one direction where being wrong is expensive.

### The ledger records merges
- **A landed PR is now a ledger line.** `work.merge` armed GitHub's auto-merge but recorded nothing, so
  the team view showed goal outcomes (`done`/`parked`) yet never the merges themselves. It now appends a
  **`merged`** entry (a new shared kind) tagged with the PR number when it arms a merge — fail-open, so a
  ledger problem can never turn a successful merge into a failure. Off unless the ledger is enabled,
  exactly like every other entry.

### The Research phase gets an executor, and `lane: auto` starts meaning something
- **`/sdlc-research`** — Research was the only one of the seven phases with no executor behind it
  (the README said "agent practice; no dedicated skill"). It now maps a goal's blast radius, **records
  the exact queries** so Review can re-run them — coverage guaranteed by re-scan, not by trusting that
  one afternoon's list was complete — inventories the debt already in the radius, and sizes the goal
  into a lane from the footprint it *measured*, never from a guessed duration.
- **`lane: auto` was a promise nothing kept.** `sdlc-init` has been scaffolding it into every goal with
  a README saying "auto lets the engine size it"; no code sized anything. Research now writes the lane
  back, which is what lets small goals skip ceremony they never earned.
- The dossier lands in `.sdlc/research/`, deliberately **not** `.sdlc/plans/` — the hard plan-gate
  treats any recent file there as a fresh plan, and a research note is not a plan.

### Plan-review closes its own loop
- A FIX-FIRST verdict sent a plan back with **no record of what happened to each finding**. Every
  finding now gets an explicit disposition — accept / reject / partially accept, each with its own
  `file:line`. This matters most in `/sdlc-loop`, where no human adjudicates and nothing otherwise
  stopped the loop from faithfully implementing a wrong review finding.
- **The review is a hypothesis too.** A finding claiming a file or symbol doesn't exist is checked
  against the filesystem before it's accepted; a plan patched to satisfy a false finding is worse than
  the plan was. Plus a regen threshold: when most findings are substantive, the plan wants
  regenerating, not patching.

### Cumulative drift, and a trigger that can actually fire
- **`/sdlc-align`** — every existing alignment gate reads one unit of work: `sdlc-plan-review` §4 holds
  one plan to the north-star, `sdlc-retro` asks what one goal taught. Neither can see the shape of
  twenty goals, which is where strategy actually drifts. Two lenses only — dominant theme vs stated
  bets, and effort accumulating behind a bet nobody ever declared. No-op without a north-star.
- **`/sdlc-status` reports when it's due.** The old plan was to add this "only if drift proves to slip
  past the other two" — a trigger that cannot fire, since undetected drift is exactly what you can't
  observe without the audit. A count of goals shipped since the last report is something the loop can
  see. The report is its own bookkeeping; no extra state file.

### Both backlog modes reach the same place
- **The alignment counter was blind in github mode.** It tallied `.sdlc/goals/*.md` with `status: done`
  — but when goals are issues that directory stays empty, so the count sat at zero and the audit would
  never have come due for exactly the teams running a shared board. It now takes the larger of the
  local tally and the loop's `iteration` cursor, since neither signal covers both modes alone
  (`iteration` misses interactive `/sdlc-goal` runs; the file tally misses github entirely).
- **Research recorded the lane into frontmatter an issue doesn't have.** In github mode the lane, the
  site count, and the blocking questions now go on the issue timeline, where every other phase already
  records. A research pass that leaves no comment there is invisible to everyone but its author.
- **Plan-review writes down its rejections.** Accepted findings are visible in the revised plan; the
  reasoning for *overruling* a reviewer existed nowhere — and it's the first thing anyone asks when the
  same objection returns a month later.
- No new board columns: Research sits in the same **In Progress** state as Plan and Implement, and
  `/sdlc-align` deliberately files nothing — drift is a question about direction, not about any one
  issue.

### Standing docs stop only ever growing
- **`/sdlc-doctor` scans for rot** — cited paths and links in `.sdlc/project.md` and
  `.sdlc/context/*.md` that no longer resolve. A north-star pointing at a deleted file quietly teaches
  the wrong thing to every phase that reads it. Reported in its own section and kept out of the
  readiness score: "is my setup working?" and "are my docs rotting?" are different questions.
- It only reports references that provably don't resolve — globs and `<placeholders>` are skipped,
  because a check that cries wolf gets ignored along with its true positives.
- **`/sdlc-retro` proposes the retirements.** Adding a rule has an obvious moment; retiring one never
  does. Retro now asks what the goal made redundant — a rule CI now enforces mechanically, a rule whose
  premise moved, a plan whose work shipped — and parks each removal for approval alongside the
  additions, in the same table.

### Merge rights + a protection-aware policy
- **A fork PR or a read-only repo is never merge-attempted.** Permission is checked before anything
  it could gate on, and it is not a config question: on a project you lack write access to, the PR
  *is* the deliverable. The loop opens it, says why it stopped, and records **`done`** — not a park,
  because nothing about it wants a human. Unknown rights fail **closed**.
- **`auto_merge` is now `off` | `protected` | `always`** (default `off`; the old booleans still parse,
  `false`→off and `true`→always). `protected` merges only where the base branch genuinely REQUIRES
  checks or reviews — autonomy proportional to the guardrails that actually exist.
- **Fixes a truthfulness bug shipped in the previous entry.** The "no required checks" warning tested
  whether a check had *run*, not whether one was *required*. A repo can run CI on every PR and require
  none of it — which is exactly the state this repo was in — so the warning stayed silent in the one
  case it existed for. Protection is now read from
  `repos/{owner}/{repo}/branches/{base}/protection` (404 = nothing enforced) and is a real branch in
  the logic rather than a message.
- Outcome mapping is explicit in the loop prose: a failing required check records `failed` (needs a
  fix), a conflict records `parked` (needs a decision), and an opened-but-unmergeable PR records
  `done`. The review queue only fills with things that actually want attention.

### Per-goal worktree + the merge gate
- **One worktree, one branch, one PR per goal** (`work: {"enabled": true}`, default OFF): the loop
  stops sharing your working copy. An in-place `checkout -b` would move the tree out from under
  whatever you left open *and* rewrite `.sdlc/goals/` — which `sdlc-init` tells you to commit —
  underneath the loop that is reading it. Both failures are silent; a worktree removes both.
- **Cutting fresh from `<remote>/<base>` is the goal-start rebase**: nothing to replay, so it cannot
  conflict. The only real rebase is reactive (GitHub reported the PR `BEHIND`) and it `--abort`s on
  conflict rather than strand a half-applied tree that every later goal in the run would build on.
- **`verify_command` now runs in the goal's own worktree**, not the main checkout. It was resolved
  from `sdlc_dir` alone, which with a worktree would have proved the *unchanged* tree green and let
  `record done` accept it. `loop.py verify` resolves the root through `work.root()`, matching the
  injectable `repo_root` `pipeline.py` already had; with the feature off the root is unchanged.
- **A merge gate that is clean AND safe** (`work.auto_merge`, default OFF): needs this run's passing
  verify evidence *and* GitHub's `mergeable` + `mergeStateStatus CLEAN` (required checks and reviews
  folded in), then arms GitHub's own `--auto` so the last word is an atomic re-check at merge time
  instead of a stale read. It retries the lazy first-read `UNKNOWN` rather than treating it as an
  answer, and says so out loud when a repo has no required checks — `CLEAN` there means only that
  GitHub had nothing to object to. Anything else prints `PARK: <reason>` for the existing review
  queue; no new human-intervention path.
- **No general `git` tool for the loop**: it commits through `work.py commit`, which can only ever
  run against this goal's worktree — a broad `Bash(git *)` would hand the unattended loop the exact
  power the feature exists to remove.
- `evidence_path`/`done_refusal` moved from `loop.py` to `state.py` so the merge gate can require the
  same evidence without the two modules importing each other. `loop.py` keeps both names as aliases.

### Team ledger
- **`TEAM.md` now shows `claimed`**: the shared team view records WHO started a ticket and WHEN, not
  only outcomes — it pairs with `done` to read a ticket's start→finish at a glance. `note` stays
  personal (unless addressed `to` someone). Inbox/wake behavior is unchanged (that keys off `to`, not
  the shared-kinds set), so a claim never wakes a teammate.
- **`TEAM.md` is published to the ops branch**: `sync.py publish` now renders and commits the rolled-up
  view alongside your entries file, so a lead can read one file on `sdlc-ledger` instead of cloning and
  rendering locally. It stays conflict-free — TEAM.md is a pure function of the entries, so a push race
  is resolved by rebasing and re-rendering from the merged entries, never by hand.

## 0.7.0 — the coordination release

The theme: LoopSmith stops being a single-player autopilot, and stops running one thing at
a time. A team running it against one repo now leaves a shared, attributed record of what
each loop did; a blocker in someone else's code is handed to that person instead of parked
into silence; the hand-off actually reaches them; and a goal's independent slices run
together instead of queueing. Everything ships opt-in and default-OFF — with no `ledger`
and no `parallel` block in config.json the loop behaves exactly as 0.6.0 did.

### Coordination
- **Team ledger** (`ledger: {"enabled": true}`, default OFF): a committed, append-only record of what
  the loop did — `claimed` when it takes a goal, `done`/`parked`/`failed` when it finishes one — with
  a timestamp and an actor on every line. **One file per person** (`.sdlc/ledger/entries/<actor>.jsonl`):
  you only ever write your own, so concurrent appends can neither race on disk nor conflict in git;
  the team view is their union, computed on read. Kinds `claimed · done · parked · failed · handoff ·
  ack · release · note`; an entry with a `to` is addressed to that person. `ledger.py` renders
  `TEAM.md`, lists what is addressed to you, and summarises outstanding hand-offs; `/sdlc-status`
  reports the entry count and `/sdlc-doctor` reports the flag. Every write from the loop is fail-open
  — a ledger problem can never stop a run. Absent config = byte-compatible with 0.6.0.

- **Cross-area hand-off** — parking with a successor instead of parking into silence. A blocker in
  code someone else owns is not a decision for the user, but until now it parked like one: a
  gitignored queue entry, an unaddressed issue comment, and no code path in the kit had ever set an
  assignee. `handoff.py open` resolves the owner from the repo's own `.github/CODEOWNERS` (override
  per area with `ledger.owners`), opens an issue in their area **assigned to them and carrying the
  goal label** — so their own loop picks it up through the `assignee` filter, no new transport —
  records a `handoff` ledger entry addressed to them, and links it from the blocked issue. Then the
  goal parks as before. `handoff.py ack --state accepted|deferred|declined|resolved` is the answer;
  `deferred` deliberately does not settle it. Degrades honestly with no owner, no `gh`, or a local
  backlog: the ledger entry is still written.

- **Ledger transport + watcher** — a mention nobody reads is worth nothing, so the ledger now shares
  itself and tells you when it needs you. `sync.py init` makes `.sdlc/ledger/` a **git worktree** on a
  dedicated ops branch (default `sdlc-ledger`, created from the EMPTY tree so it carries no code):
  fetching and rebasing the ledger touches only that worktree, so a pull can never disturb your code
  checkout or drag you into a mid-task rebase, and the branch is never merged into the integration
  branch so it needs no review. `publish` fast-forwards your own entries file with a bounded
  fetch-rebase-retry — a race replays, never forces, and can't conflict because nobody shares a file.
  `watch.sh` ticks on `ledger.watch.interval_seconds` (default 900): pull → classify → write
  `.sdlc/state/inbox.md` → publish. **`loop.py next` surfaces that inbox on stderr before handing over
  the next goal** — the only honest delivery point, since nothing can inject a message into a running
  session and interrupting a goal mid-flight loses work; worst case is one goal of latency, and stdout
  stays exactly the goal the caller parses. Deduped twice: a per-author cursor, plus a
  `kind:issue:state` signature so a colleague's rebase can't replay old mentions (a state *change*
  still fires).

### Parallelism
- **Slice-level parallelism** (`parallel: {"enabled": false, "max_concurrent": 3}`, default OFF): a
  goal's independent slices now run concurrently instead of queueing behind each other. Declare them
  beside the plan in `.sdlc/plans/<goal-stem>.slices.json` — `{id, title, needs, files, size, status}`,
  everything but `id` optional — and `slices.py plan` renders a dispatch plan: the runnable frontier
  packed into **waves** of mutually non-conflicting slices, capped at `max_concurrent`, widest fan-out
  first so a wave is never spent on leaves while the critical path waits. Deterministic by
  construction, so the plan is reviewable before anything is dispatched; `slices.py check` reports
  unknown dependencies, duplicate ids, and **cycles by their members** (you cannot pick which edge to
  break without the names). **Conflict is decided from DECLARED files only** — `fnmatch` both
  directions plus a literal-prefix check, so `engine/**` and `engine/graph.py` are one blast radius —
  and a slice that declares **no** files conflicts with everything and runs alone: an unknown radius
  is not something you may parallelise, and one lost edit costs more than one extra wave. Each slice
  that declares files is dispatched with `isolation: worktree`, so concurrent siblings cannot stomp
  each other's half-finished edits.

  The boundary is drawn where the host's guarantees end. A python script cannot spawn a subagent, so
  `slices.py` **computes and advises** and `/sdlc-loop`'s prose dispatches — one subagent per slice,
  one wave at a time. The desktop app's "chips" mechanism is app-internal and **not a public API for
  plugins**, so nothing here is built on it: a slice marked `"size": "large"` (too big for one
  subagent's context) is dispatched as `session` and the plan **prints the exact
  `claude --worktree <goal-stem>-<slice-id>` line for a human to start**. The loop never shells out to
  an unattended `claude -p` — uncapped spend, and a second worker on one `.sdlc` breaks every state
  file in the kit. `/sdlc-doctor` reports the flag. Absent config, or no manifest, and every goal runs
  as one unit exactly as before.

### Fixed
- **A fresh clone of an adopted repo can run the loop** *(bug-fix class, applies with or without the
  ledger)*: `.sdlc/state/` is gitignored by design — it is per-machine runtime — so a teammate who
  clones a repo that has already adopted the spine gets the committed config and goals but **no state
  files at all**, and `loop.py start` / `next` / `record` died on a raw `FileNotFoundError` before
  their first goal. `STATE.md` and `review-queue.md` are now scaffolded on first use. Nothing changes
  for a repo whose state already exists.
- **The ledger worktree path is resolved before it reaches git.** Every git call runs with
  `-C <another directory>`, so a relative `.sdlc` made `git worktree add` create the worktree under
  the project root's own name and the next write landed nowhere.
- **`ledger.py summary` and `TEAM.md` distinguish an unanswered hand-off from one someone has taken.**
  Both are outstanding — the blocker is real until it is `resolved` — but only the first needs
  chasing, and a bare count could not say which was which.

### Backlog routing
- **Per-owner discovery scope**: `discovery.github.assignee` (e.g. `"@me"`) makes the loop pick only
  issues assigned to that user, so several people can run the loop against one shared board/Project
  without grabbing each other's work. Absent/empty = no filter (every open goal issue is in scope) —
  byte-compatible with prior behavior. Applies to discovery only; the board backlog sync still seeds
  the whole team's issues.

## 0.6.0 — the trust-and-feedback release

The theme: everything the loop CLAIMS is now checkable, everything it produces feeds
back into it, and an unattended night no longer ends at the first limit or crash.
Every new capability ships opt-in and default-OFF (one bug-fix exception, noted);
absent config keys behave exactly as 0.5.0.

### Guardrails made real
- **Prompt gate is repo-scoped** *(the one default-ON change — bug-fix class)*: the
  UserPromptSubmit directive now speaks only in repos that adopted the spine
  (`.sdlc/` exists); everywhere else it is a silent no-op, so a machine-wide install
  never injects policy into unrelated projects. `LOOPSMITH_GATE_GLOBAL=1` restores
  the old always-on behavior.
- **Budgets enforce**: `budget.max_minutes` (wall-clock) and `budget.max_tokens`
  (host-reported via the new `loop.py spend` verb) now actually halt the run;
  previously only `max_iterations` did. Absent keys enforce nothing.
- **Opt-in hard plan-gate**: `gates.hard_plan_gate.enabled` mechanically DENIES a
  source edit unless a fresh plan exists under `.sdlc/plans/`
  (`touch .sdlc/.allow-direct-edits` for a deliberate bypass; fail-open throughout).

### Truthful outcomes
- **`failed` is not `parked`**: a goal the loop could not resolve gets its own
  terminal status, review-queue tag ("needs: a fix" vs "needs: human review"),
  counters, and `record failed` verb — the morning queue separates decide-this
  from fix-this.
- **Machine-checked done**: `loop.py verify` runs the goal's proving command
  (frontmatter `verify_command`, else `verify.command`) and records evidence; with
  `verify.enforce` on, `record done` is REFUSED without a fresh passing verify from
  this run.

### The feedback circle
- **Bidirectional pipeline report card**: declare stages once in `.sdlc/pipeline.json`
  and `pipeline.py card` renders every stage in BOTH directions — forward
  (survivorship: nothing dropped) and reverse (provenance: nothing invented) — with
  uninstrumented lanes reading honest-ABSENT, a typed verdict, and `--compare` for
  the regressed / improved / still-failing (recurrence) delta between runs.
- **Findings become work**: `pipeline.py propose` turns failing card signals into
  `proposed` goal files with the failing check pre-wired as their `verify_command`;
  the loop never runs one until a human promotes it to `pending`.

### Smarter, cheaper, visible
- **Per-step model + effort selection** (under the existing `model_selection: "auto"`
  gate): `resolve-step` returns `model=<tier> effort=<low|medium|high>` so mechanical
  steps inside a hard goal (tests, watchers, lint) run below the goal's ceiling;
  `resolve` output stays backward-compatible.
- **Feature dashboard**: `doctor.py features` (also appended to `/sdlc-doctor`
  output) reports every optional capability's LIVE state with its one-line enable.

### Unattended for real
- **Overnight supervisor**: `scripts/supervise.sh` owns the loop's lifetime with
  zero polling — blocked while a session runs; on exit it classifies the output
  (via the pure `supervise_classify.py`): loop finished → stop · budget → relaunch ·
  usage-limit → **sleep until the stated reset time (+ jitter), then relaunch** ·
  unknown → capped backoff. Kill-file stop; per-run output capture; laptop-sleep
  caveat documented.

57 new tests across the arc (281 total, coverage >85% held, Tier-1 eval baseline
unchanged). No runtime dependencies added.
