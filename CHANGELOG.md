# Changelog

## Unreleased

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
