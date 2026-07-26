# LoopSmith

[![CI](https://github.com/swapnil-agrim/loopsmith/actions/workflows/ci.yml/badge.svg)](https://github.com/swapnil-agrim/loopsmith/actions/workflows/ci.yml)

**Guardrails + an overnight autopilot for your AI coding agent — one that plans before it codes, won't ship work that fights your strategy, and gets sharper every run.**

Drop it into any repo and every non-trivial prompt is held to a disciplined **7-phase SDLC** — Goal →
Research → Plan → Plan-Review → Implement → Review → Retrospective — so the agent stops jumping
straight to code. Then queue a backlog and let it **run autonomously**: each goal is driven to a
*verified* finish, moved across a **GitHub Projects board**, and recorded with a full audit trail.
Start from an existing repo **or** a product vision; LoopSmith grounds the work in your strategy and
remembers what it learns in a **self-improving knowledge graph**.

> **One promise: best-quality output, minimum effort.** Zero runtime deps (bash + python3 stdlib) and
> **zero hard plugin dependencies** — it installs seamlessly with or without anything else. If the
> `superpowers` + `code-review` companions are **already installed**, LoopSmith uses them
> automatically; if not, the portable `sdlc-*` executors run the same phases — **you install
> nothing**, on any host.

---

## Two ways to run: interactive or autonomous

Both modes drive the **same seven phases** per goal — they differ in who's in the loop and what
happens at a checkpoint. The repo-scoped prompt hook underpins both.

### `/sdlc-goal <goal>` — interactive

One goal through the engine, **pausing for your approval at each gate**. Take a goal from
`.sdlc/goals/` (preferred — so it's tracked) or inline text, then walk Goal → Research → Plan →
**Plan-Review** (via `sdlc-plan-review`, never skipped) → Implement (test-first) → Review (evidence
before "done"). It does **not** auto-proceed past checkpoints — you approve each one. The outcome is
recorded to `.sdlc/` (`done`, or `parked` with a reason) so it shows in `/sdlc-status`.

### `/sdlc-loop` — autonomous

Pulls the backlog — local `.sdlc/goals/` files or [GitHub issues](#your-backlog-local-files-or-github-issues) —
and runs **each goal autonomously** through the same phases. Anything
that needs a human is **parked to `.sdlc/state/review-queue.md`** and the loop continues — it parks,
it does not force. It parks on:

- a hard checkpoint / a decision only you can make,
- an **irreversible or expensive action** (deploy, delete, overwrite, spend, migrate) — never run
  unattended,
- a hard failure it cannot resolve — recorded as **`failed`** (needs a fix), distinct from
  parked (needs a decision), so the review queue separates the two.

It halts on the **per-run budgets** (`config.json` → `budget`): `max_iterations` always, plus —
when set — `max_minutes` (wall-clock from the run's start) and `max_tokens` (against spend the host
reports via `loop.py spend`; no reports means no token enforcement). All reset each invocation and
are resume-safe (a budget stop, re-run, picks up where it left off). Run
**Overnight without babysitting:** `bash skills/sdlc-loop/scripts/supervise.sh .sdlc` wraps the
loop in a zero-polling supervisor — blocked while a session runs, and on exit it classifies the
tail: loop finished → stop; per-run budget → relaunch; **usage-limit exhaustion → sleeps until the
stated reset time (+ jitter) and relaunches**; unknown crash → capped escalating backoff. Stop it
any time with `touch .sdlc/state/supervisor.stop`. (Sleeping *machine* ≠ sleeping process — on a
macOS laptop run it under `caffeinate -is`.) Run
**`/sdlc-status`** any time for backlog counts (pending / in-progress / done / parked / failed) + whether the
review queue needs attention.

| | `/sdlc-goal` (interactive) | `/sdlc-loop` (autonomous) |
|---|---|---|
| Scope | one goal | the whole `.sdlc/goals/` backlog |
| At a checkpoint | pauses for you | parks to the review queue, continues |
| Approval | every gate | only what it parks |
| Stops on | goal complete / you stop | backlog empty or per-run budget |
| Irreversible action | asks you | always parks — never runs it |

---

## The seven phases

Each phase runs via an **executor**, resolved per host: on Claude with the companion installed, the
`superpowers` / `code-review` skill; otherwise LoopSmith's **portable `sdlc-*` executor** — each with a
committed [parity review](docs/executor-parity/) showing it's at-par-or-better. Phase 4 is always
LoopSmith's own; no companion ships it.

1. **Goal** — restate the objective as one concrete, checkable goal. For feature/creative work, this
   is where you explore intent and requirements first.
   → *executor:* `superpowers:brainstorming` · portable `sdlc-brainstorm`.
2. **Research** — map the blast radius: affected files, existing patterns, constraints, prior art.
   → *agent practice; no dedicated skill.*
3. **Plan** — write the plan: steps, files, tests, and a definition-of-done. Size it against real
   throughput with **`/sdlc-velocity`** (measured git pace), not "this feels like weeks."
   → *executor:* `superpowers:writing-plans` · portable `sdlc-plan`.
4. **Plan-Review** — adversarially review the plan **before** any edit: verify each claim against the
   real code, stress-test what breaks after it ships, check scope/fit, and (vision-first) check it
   against your strategy. Never skipped. This is the gate `superpowers` doesn't provide, so LoopSmith
   ships it.
   → *owned by* **`sdlc-plan-review`** (always LoopSmith's — no companion equivalent).
5. **Implement** — build test-first and execute the plan step by step.
   → *executor:* `superpowers:test-driven-development` + `executing-plans` · portable `sdlc-implement`.
6. **Review** — code-review the diff for real findings, then verify every claim with evidence before
   declaring anything done.
   → *executor:* `code-review` + `superpowers:requesting-code-review` + `verification-before-completion`
   · portable `sdlc-review` + `sdlc-verify`.
7. **Retrospective** — surface the structural + product debt the fix left behind, grade
   intent-vs-shipped, and route each durable lesson to the right store (advisory).
   → *executor:* **`sdlc-retro`** (always LoopSmith's own — no companion equivalent).

---

## Quickstart

```
/plugin marketplace add <git-url-or-local-path>
/plugin install loopsmith
/sdlc-init --demo     # scaffolds a small, safe, runnable demo goal
/sdlc-loop            # watch it run Goal → Research → … → Review end-to-end
```

That installs the plugin machine-wide, but the hook only speaks in repos that adopt the spine
(scoped to `.sdlc/` presence); `/sdlc-init` scaffolds
each repo's `.sdlc/` layer and is safe to re-run. If the `superpowers` + `code-review` companions are
**already** in your plugin list, LoopSmith uses them automatically; if not, the portable `sdlc-*`
executors run the same phases ([details](#companions-optional-enhancement)) — **nothing to install
either way**.

- Add **`--github`** to `/sdlc-init` to also set up the GitHub Projects board + issue templates and
  run the demo on a real board ([Your backlog](#your-backlog-local-files-or-github-issues)).
- Add **`--vision`** (or run **`/sdlc-vision`**) to start from a product vision instead
  ([Two ways to start](#two-ways-to-start-drop-in-or-vision-first)).

See the **[worked walkthrough](examples/hello-sdlc/)** for a runnable end-to-end example. Forking the
kit to publish it? See [EXTRACT.md](EXTRACT.md).

---

## What you get

Every option LoopSmith provides, at a glance:

| Capability | What it gives you | Command / component |
|---|---|---|
| **Repo-scoped guardrail** | Every prompt in an adopted repo held to the 7-phase spine — no jumping to code | `hooks/sdlc_gate.sh` (automatic) |
| **Plan-review gate** | Adversarial review of the plan *before* any edit — the gate `superpowers` doesn't ship | `sdlc-plan-review` |
| **Strategy-alignment gate** | A plan that contradicts your stated strategy / non-goals is blocked (FIX-FIRST) | `sdlc-plan-review` + north-star |
| **Two ways to start** | **Drop-in** (existing repo) or **vision-first** (start from a product vision) | `/sdlc-init`, `/sdlc-vision` |
| **Two ways to run** | **Interactive** (approve each gate) or **autonomous** (park-and-continue over a backlog) | `/sdlc-goal`, `/sdlc-loop` |
| **Hard plan-gate (opt-in)** | With `gates.hard_plan_gate.enabled`, a source edit is mechanically DENIED until a fresh plan exists under `.sdlc/plans/` (`touch .sdlc/.allow-direct-edits` for a deliberate bypass) | `hooks/plan_gate.sh` |
| **Machine-checked done** | With `verify.enforce`, "done" is refused until the goal's proving command passes THIS run | `loop.py verify` |
| **Bidirectional report card** | Declare your pipeline's stages once; every stage gets a forward (nothing dropped) + reverse (nothing invented) lane — uninstrumented lanes read ABSENT, never green — with a recurrence delta across runs | `.sdlc/pipeline.json` + `pipeline.py card` |
| **Model + effort auto-selection (opt-in)** | Per-goal ceiling AND per-step downgrade: mechanical steps run on a cheaper tier/effort (`model_selection: "auto"`, default off) | `predict.py resolve / resolve-step` |
| **Findings become work** | The card's failing signals become `proposed` goals (proof-of-fix pre-wired); the loop never runs one until you promote it | `pipeline.py propose` |
| **Team ledger (opt-in)** | A committed, append-only record of what the loop did — **one file per person**, so concurrent appends can't conflict; the team view is their union | `ledger.py`, `ledger.enabled` |
| **Cross-area hand-off** | Blocked on someone else's code? It resolves the owner from CODEOWNERS, opens an issue **assigned to them** (so their loop picks it up), and records it — instead of parking into silence | `handoff.py open` / `ack` |
| **Ledger watcher** | Pulls the ledger's own ops branch on an interval — never your working tree — and surfaces what needs you between goals, deduped | `watch.sh`, `sync.py` |
| **Slice parallelism (opt-in)** | Declare a goal's slices and the files each touches; independent ones run as concurrent subagents in **waves** (own worktree each), instead of burning one session's context in sequence | `slices.py plan`, `parallel.enabled` |
| **Pluggable backlog** | Local goal files, GitHub issues, or a GitHub **Projects v2 board** | `discovery.source` |
| **Board + audit trail** | Cards flow Backlog → In Progress → QC → Done → Blocked; every phase recorded on the issue | `/sdlc-init --github` |
| **Self-improving knowledge graph** | Captures research + lessons, **tracks what it doesn't know**, prunes itself, and fills gaps | `/sdlc-kg` |
| **Context recall** | Pulls the relevant slice of project memory into context before each goal | `/sdlc-context` |
| **Velocity calibration** | Size work from real git throughput, not "this feels like weeks" | `/sdlc-velocity` |
| **Proactive research scout** | Sweep the backlog for new SOTA, dedup, write a ranked digest (dry-run) | `/sdlc-radar` |
| **Model auto-selection** | Predict the tier a goal deserves (haiku/sonnet/opus/fable); the loop runs it there | `/sdlc-model`, `model_selection: auto` |
| **Quality-drift gate** | A behavioral corpus scored on every change; the build fails if a discipline signal regresses | `evals/run.py` |
| **Retrospective / learning loop** | After each goal: structural + product debt, intent-vs-shipped, lessons routed to the right store (advisory) | `/sdlc-retro` |
| **Cursor adapter** *(experimental)* | Scaffolds the SDLC discipline as an always-applied Cursor rule — *not yet verified in a live Cursor session* | `/sdlc-init --cursor` |
| **Status at a glance** | Backlog counts + whether the review queue needs you | `/sdlc-status` |
| **Setup check-up** | Audits the setup and hands you the exact fix for anything missing — no silent failures | `/sdlc-doctor` |

---

## Why LoopSmith

What you don't get anywhere else, in one kit:

- **Automatic model selection.** It predicts the right tier per goal — `haiku · sonnet · opus · fable` —
  and runs that goal's phases there, so a rename won't burn Opus and a migration won't crawl on Haiku — you set nothing.
- **A plan-review gate before any edit.** The plan is adversarially reviewed against the real code first,
  and the prompt hook won't let the agent skip straight to coding. Discipline is automatic, not remembered.
- **Your strategy has teeth.** A plan that contradicts your stated strategy or advances a non-goal is blocked
  **FIX-FIRST** against your north-star — so the agent can't quietly build the wrong thing.
- **An overnight autopilot, not a one-shot.** It drives a whole backlog unattended, parks anything that needs you,
  and never runs an irreversible action alone — you wake up to verified work plus a full audit trail.
- **A knowledge graph that improves itself.** It captures research and lessons, tracks what it *doesn't* know,
  prunes stale notes, and fills its own gaps — each run is sharper, not noisier.
- **No lock-in.** Every phase runs via a companion on Claude or a parity-reviewed **portable executor** elsewhere —
  zero hard dependencies, so the same spine runs on any host.
- **Quality that can't silently regress.** A behavioral eval corpus is scored on every change and fails the build
  if a discipline signal drops — drift is caught before you ship it.

---

## Feature flags at a glance

Everything optional ships OFF — `/sdlc-doctor` prints this dashboard live (`doctor.py features`):

| Flag | Default | What it turns on |
|---|---|---|
| `model_selection: "auto"` | off | per-goal model ceiling + per-step model/effort downgrade |
| `verify: {"enforce": true}` | off | `record done` refused without fresh machine evidence (`loop.py verify`) |
| `gates.hard_plan_gate.enabled` | off | source edits mechanically denied without a fresh `.sdlc/plans/*.md` |
| `.sdlc/pipeline.json` | absent | the bidirectional report card + `propose` (findings → groomable goals) |
| `ledger: {"enabled": true}` | off | the committed team ledger — claims and outcomes recorded per author, plus cross-area hand-off |
| `ledger.watch.interval_seconds` | 900 | how often `watch.sh` pulls the ledger ops branch and refreshes the inbox |
| `parallel: {"enabled": true}` | off | a goal's independent slices run concurrently in waves (`max_concurrent`, default 3) from `.sdlc/plans/<goal>.slices.json` |
| `work: {"enabled": true}` | off | one worktree + branch + PR per goal; your checkout never moves, and `verify_command` runs in the goal's own tree |
| `work.auto_merge` | off | arm GitHub auto-merge when the PR is clean **and** safe; anything else parks with the reason |
| `budget.max_minutes` / `max_tokens` | unset | wall-clock / host-reported token ceilings (iterations always enforce) |
| `knowledge_graph.enabled` | off | research capture + the self-improving graph |
| `LOOPSMITH_GATE_GLOBAL=1` (env) | unset | restores the pre-0.6 always-on prompt gate |

## How it works

LoopSmith installs one hook (`hooks/sdlc_gate.sh`, wired as a `UserPromptSubmit` hook). It is
**scoped per repo**: it only speaks in a project that has adopted the spine (an `.sdlc/` directory
exists — i.e. you ran `/sdlc-init`); in any other repo it is a silent no-op, so installing the
plugin machine-wide never injects policy into unrelated projects. Set `LOOPSMITH_GATE_GLOBAL=1`
to restore the old always-on behavior everywhere. In an adopted repo, on every prompt it
classifies intent with fast, deterministic regex — **no LLM** — and injects the matching SDLC
directive:

- **code change / implementation** → "do NOT jump to editing; run the full spine from the GOAL and
  pass PLAN-REVIEW before any edit."
- **read-only / conversational** → "answer directly (say so) — but the moment it becomes a code
  change, switch to the spine."
- **anything else** → the standard 7-phase policy.

The hook is *advisory and fail-safe*: a false positive over-reminds, a false negative falls back to
the standard policy, and it always emits valid JSON (even on garbage or empty stdin). It never calls
out, never blocks — it shapes what the agent does next.

---

## Architecture & flow

### The pieces

```mermaid
flowchart TB
    PROMPT(["Prompt / queued goal"]) --> HOOK["Always-on intent hook"]
    HOOK --> ORCH["Orchestrator<br/>/sdlc-goal (interactive) · /sdlc-loop (autonomous)"]
    ORCH --> SPINE["7-phase SDLC spine<br/>two gates: Plan-Review + Strategy-Alignment"]
    ORCH -.->|"model_selection: auto"| MDL["Model per goal<br/>haiku · sonnet · opus · fable"]
    SPINE --> OUT(["Verified change + audit trail"])
    SPINE <--> SRC["Backlog source<br/>local files · GitHub issues · Projects board"]
    SPINE <--> KG["Self-improving KG (optional)<br/>write · recall · track→prune→fill gaps"]
    SPINE -.->|"each phase via an executor"| COMP["Companion on Claude<br/>(superpowers / code-review)<br/>· else portable sdlc-* executor"]
```

### How a prompt falls through the phases

A prompt enters through the repo-scoped prompt hook, which routes by intent. Code work then falls through the
seven phases — with two **gates** that can send it back, and a **park** exit for anything that needs you:

```mermaid
flowchart TD
    P(["Your prompt"]) --> H{"Hook classifies intent"}
    H -->|"read-only / conversational"| ANS(["Answer directly"])
    H -->|"code change / non-trivial"| G["1. Goal"]
    G --> RS["2. Research"]
    RS --> PL["3. Plan"]
    PL --> PR{"4. Plan-Review<br/>+ strategy alignment"}
    PR -->|"FIX-FIRST"| PL
    PR -->|"SOUND"| IM["5. Implement (TDD)"]
    IM --> RV{"6. Review + verify"}
    RV -->|"unverified"| IM
    RV -->|"evidence passes"| RT["7. Retrospective"]
    RT --> DN(["Done"])
    PR -.->|"blocked"| PK(["Park"])
    IM -.->|"irreversible / stuck"| PK
    RV -.->|"blocked"| PK
```

### How the autonomous loop runs the backlog

The loop runs the backlog **park-and-continue** — it parks whatever needs you and keeps going:

```mermaid
flowchart TD
    ST(["/sdlc-loop — reset run budget"]) --> NX{"Next pending goal?"}
    NX -->|"backlog empty"| SD(["Stop — all done"])
    NX -->|"budget reached"| SB(["Stop — budget"])
    NX -->|"goal"| RUN["Run it through the 7-phase pipeline"]
    RUN -->|"done + verified"| CMP["Mark done"]
    RUN -->|"needs you / irreversible / unresolved"| PRK["Park to review queue"]
    CMP --> NX
    PRK --> NX
```

---

## Two ways to start: drop-in or vision-first

LoopSmith meets you where you are — both on the **same spine**, so you can move between them anytime.

### Drop-in (default)
Install, `/sdlc-init`, and start running goals against your existing repo. A thin `.sdlc/project.md`
(stack + verify command) is all the context you need. Near-zero setup; nothing to author up front.

### Vision-first (opt-in)
Starting a new product, or want top-down grounding? Run **`/sdlc-vision`** (or `/sdlc-init --vision`)
to externalize a tiered **north-star** into `.sdlc/context/north-star.md` — **Vision → Strategy
(+ non-goals) → Design → Architecture**. Then every goal is grounded in it: `/sdlc-context` recalls
the north-star first, and `sdlc-plan-review`'s **alignment gate** blocks any plan that contradicts
your strategy or advances a stated non-goal (**FIX-FIRST**). The agent can't build something that
fights your direction. The one-pass draft is the lean default; when you want to externalize a tier
properly, `/sdlc-vision` loads an optional **deep-elicitation guide** per tier (on demand, never bloat)
— and the **Architecture** tier drafts its rules straight from the codebase for you to approve.

> **Progressive disclosure is the seam:** a drop-in project can add a north-star later; a vision-first
> project just starts running goals once the tiers are filled. No north-star → the alignment gate is a
> no-op, and drop-in behaves exactly as before.

---

## Match the model to the goal (optional)

A one-line rename doesn't need Opus; a schema migration shouldn't run on Haiku. Set
**`model_selection: auto`** in `.sdlc/config.json` and `/sdlc-loop` **predicts a tier per goal** —
`haiku · sonnet · opus · fable` — from the goal text (deterministic regex, zero-dep), then runs that
goal's phases at it inside a subagent (the session can't switch its own model). Conflicts resolve
**upward**, so a hard goal is never under-powered. Off by default; run **`/sdlc-model "<goal>"`** any
time to see the recommended tier. `/sdlc-goal` (interactive) surfaces the tier as advice rather than
auto-switching.

---

## Your backlog: local files or GitHub issues

Goals live in a backlog — you choose **where**, once, in `.sdlc/config.json` → `discovery.source`.
The loop runs the **same** way for both; only the source of goals and how status is recorded differ.

### Local goal files — default, zero-dep

Goals are markdown files under `.sdlc/goals/NNNN-slug.md`; the loop advances each file's frontmatter
`status: pending → in_progress → done | parked`, in filename order.

- **Add a goal:** copy `0001-example.md`, bump the number, fill `done_when` (a *checkable* condition).
- **Commit** `.sdlc/goals/`, `.sdlc/project.md`, `.sdlc/config.json`; **gitignore** `.sdlc/state/`
  (machine-written loop state — `/sdlc-init` prints this tip).
- **Parked** goals collect in `.sdlc/state/review-queue.md` — your "needs a human" list.

Everything stays in your repo; nothing leaves your machine. This is the zero-dependency path.

### GitHub issues — opt-in, needs the `gh` CLI

Treat **GitHub Issues as the backlog** so planning and triage live where your team already works:

```json
"discovery": {
  "source": "github",
  "github": {
    "repo": "",
    "goal_label": "sdlc:goal",
    "project": { "enabled": true, "status_field": "Status" }
  }
}
```

File each goal as an **issue labelled `sdlc:goal`** (the issue body is the goal). The loop maps SDLC
status onto GitHub — both the **issue** and (when the board is enabled) its **Projects card** — so the
backlog mirrors reality:

| SDLC status | On the issue | On the Projects board |
|---|---|---|
| pending (in backlog) | open issue labelled `sdlc:goal` | card set to **Backlog** |
| picked up → Research / Plan / Implement | adds the `sdlc:in-progress` label | card set to **In Progress** |
| Review (the quality cycle) | issue stays open | card set to **QC** |
| done | **closes** the issue with a completion comment | card set to **Done** |
| parked (needs you) | comments the reason, adds `sdlc:parked`, and removes `sdlc:goal` so it leaves the queue | card set to **Blocked** |

So your **review queue = open issues labelled `sdlc:parked`**, and **done = closed issues**;
**re-queue** a parked issue by re-adding the `sdlc:goal` label. The three labels are auto-created on
first run. **Setup:** run `gh auth login` once; leave `repo` empty to auto-detect from the git remote,
or set it to `owner/name`.

**Sharing one board across a team.** Set `discovery.github.assignee` to `"@me"` (or a username) so
each person's loop only picks issues **assigned to them** — several people can run the loop on the same
board without two loops grabbing the same issue. Assign work with GitHub's normal assignee field; the
Projects card sync still shows the whole team's backlog. Empty (the default) = one shared queue, every
open `sdlc:goal` issue in scope.

#### Projects v2 board

With `discovery.github.project.enabled` (on by default for new repos), the loop also drives a **GitHub
Projects v2 board**: on first run it finds-or-creates a board titled `<repo> — SDLC`, adds every
`sdlc:goal` issue as a card, and keeps GitHub's **built-in Status field** in sync
(**Backlog → In Progress → QC → Done**, **Blocked** for parked) as goals move — the table above. The
**QC** card move happens at the Review phase. It needs the `gh` token's **`project`** scope and is
**fail-open**: no scope, or any API error, and the loop simply continues on issues + labels (nothing
breaks). Tune it under `discovery.github.project` — `owner`/`title` (default `<repo> — SDLC`), `number`
(reuse an existing board), `status_field` (the field's name), and `columns` (override the column names
to match an existing board). A `gh`-aware `/sdlc-status` for github mode is still on the roadmap.

> **No manual "group by" step.** The loop drives GitHub's **built-in `Status` field** — it sets that
> field's options to Backlog → In Progress → QC → Done → Blocked via the API (`updateProjectV2Field`),
> so the Board view groups by it **natively**. The one thing GitHub exposes to *no* tool is the view
> *layout* itself: if a new project opens as a Table, switch it to **Board** once (a standard GitHub
> step, not kit-specific). For **zero per-repo setup**, point several repos at **one shared board** via
> `discovery.github.project.number` — configure it once, reuse everywhere.

**Sprint / PM scaffolding.** Run **`/sdlc-init --github`** to also install GitHub project-management
hygiene into `.github/`: **epic** and **task** issue templates (epics decompose into task sub-issues),
a **bug** template, an **auto-add-to-project workflow** that drops every new issue into the board's
Backlog, a **critical-insight** comment template (record findings/decisions on the issue), and a
**label guide** (one `type` + ≥1 `component`/`area`). Enable auto-add by setting the repo variable
`SDLC_PROJECT_URL` and an `ADD_TO_PROJECT_PAT` secret.

**Recording the audit trail.** As the loop runs each phase, it records a journey-log note (and 🔒
critical insights for key decisions) — as a **comment on the task issue** in github mode, so the issue
timeline and the board card hold the full history, or appended to `.sdlc/journey/<goal>.md` in local
mode. Recording is **fail-open** (never breaks a run).

**Which to pick?** **Local** for a self-contained, zero-dependency repo where the backlog ships with
the code. **GitHub** to keep goals visible to your team, triaged in Issues/Projects, and tied to the
PRs the work produces.

---

## The team ledger (optional, off by default)

The review queue answers *"what stopped?"* for one person on one machine — and it's gitignored, so
nobody else ever sees it. Once more than one person runs the loop against a repo, that isn't enough:
you need a **committed** record of what everyone's loop actually did, with a timestamp and a name on
every line.

Turn it on:

```json
"ledger": { "enabled": true }
```

From then on the loop records a `claimed` line when it takes a goal and an outcome line
(`done` · `parked` · `failed`) when it finishes one. Every call is **fail-open** — a ledger problem
can never stop a run.

```
.sdlc/ledger/
├── entries/<actor>.jsonl   one file per person — you only ever write your own
└── TEAM.md                 generated view; regenerate, never hand-edit
```

**Why one file per person.** Two people appending to a shared file race in the filesystem and then
conflict again in git. Owning exactly one file each removes both by construction, and the team view
is simply their union, computed on read. Your handle comes from `ledger.actor`, else the
authenticated account (`gh api user`), else the shell user.

Read it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/sdlc-loop/scripts/ledger.py" summary .sdlc  # counts + open hand-offs
python3 "${CLAUDE_PLUGIN_ROOT}/skills/sdlc-loop/scripts/ledger.py" mine    .sdlc  # addressed to me
python3 "${CLAUDE_PLUGIN_ROOT}/skills/sdlc-loop/scripts/ledger.py" render  .sdlc --write
```

Write anything else explicitly — kinds are
`claimed · done · parked · failed · handoff · ack · release · note`:

```bash
ledger.py append .sdlc note 0007-cache.md --why "spike looks viable"
```

An entry with a `to` is **addressed** to that person: it lands in the team view and in their
`ledger.py mine`. `/sdlc-status` reports the entry count; `/sdlc-doctor` reports whether the ledger
is on.

### Blocked on someone else's area? Hand it off, don't park into silence

Parking is right for a decision only a human can make. It is the wrong answer for a **dependency in
code another person owns** — the queue entry is local and gitignored, the issue comment is
unaddressed, and the work stalls until someone happens to notice.

```bash
handoff.py open .sdlc "$goal" --area engine --why "auto-restart needs an engine feature flag" --priority P0
```

That resolves the owner from the repo's own `.github/CODEOWNERS` (the roster your host already
enforces on every PR — no second list to keep true), opens an issue in their area **assigned to them
and carrying the goal label**, records a `handoff` entry addressed to them, and links it from the
blocked issue. Then the goal parks as usual and the loop moves on.

The delivery needs no new machinery: because the new issue is a goal issue with an assignee, **the
owner's own loop picks it up** through the `discovery.github.assignee` filter. The other half is an
answer — taking it, needing time, declining, or closing it out:

```bash
handoff.py ack .sdlc --issue 61 --state accepted --why "after the current slice"
```

`deferred` deliberately does *not* settle a hand-off — a promise to look later is not a resolution,
so `ledger.py summary` keeps showing it. Override the roster per area with `ledger.owners` when your
directory layout doesn't match your area vocabulary. Every step degrades honestly: no owner, no `gh`,
or a local backlog still writes the ledger entry.

### Sharing it — an ops branch that never touches your working tree

A shared ledger has to be pulled often, and pulling your integration branch mid-task is how people
lose work to a surprise rebase. So the ledger lives on its own branch, and **`.sdlc/ledger/` is a git
worktree checked out to it**:

```bash
sync.py init .sdlc      # create the branch (from the EMPTY tree) + the worktree, once per clone
```

Add `.sdlc/ledger/` to `.gitignore` on your code branch. From then on:

* fetching and rebasing the ledger touches **only** that worktree — your code checkout never moves;
* the ops branch is never merged into the integration branch, so it needs no review and can stay
  unprotected while your code branch stays locked down;
* the branch starts from the empty tree, so it carries the ledger and nothing else.

`sync.py publish` fast-forwards your own entries file onto it; a rejected push fetches, rebases and
retries rather than forcing — and because nobody shares a file, that replay can't conflict.

### The watcher — so a mention actually reaches you

```bash
bash watch.sh .sdlc &        # stop it with: touch .sdlc/state/watch.stop
```

Each tick pulls the ops branch, works out what is addressed to you and hasn't been surfaced yet,
writes `.sdlc/state/inbox.md`, and publishes anything of your own still sitting local. Interval is
`ledger.watch.interval_seconds` (default 900).

**`loop.py next` prints that inbox on stderr before it hands over the next goal.** That boundary is
deliberate and it is the honest one: nothing can inject a message into a running session, and
interrupting a goal mid-flight is how half-finished work gets lost. Worst-case latency is one goal.
A `P0` should be taken *next*, not *now*.

Two independent suppressions keep it quiet: a per-author cursor so history isn't re-read every tick,
and a `kind:issue:state` signature so a colleague's rebase can't replay old mentions at you. A
*state change* on the same issue is news and does fire.

---

## Slice parallelism (optional, off by default)

A goal is planned as slices, and the loop runs them one after another — even when three of them touch
nothing in common. That isn't only slow: a long goal spends **one session's context** on work that had
no reason to share a window, so the last slice starts on a flushed one.

Turn it on:

```json
"parallel": { "enabled": false, "max_concurrent": 3 }
```

Then declare the slices beside the goal's plan, in `.sdlc/plans/<goal-stem>.slices.json`:

```json
[
  {"id": "s0", "title": "extract the config reader", "files": ["config/**"]},
  {"id": "s1", "title": "rewrite the loader", "needs": ["s0"], "files": ["engine/loader.py"]},
  {"id": "s2", "title": "new CLI flag",       "needs": ["s0"], "files": ["cli/**"]},
  {"id": "s3", "title": "migrate the schema", "needs": ["s1"], "files": ["db/**"], "size": "large"}
]
```

`needs`, `files`, `size` (`small` · `large`) and `status` (`pending` · `done`) are all optional.

```bash
slices.py plan     .sdlc "$goal" [--max N]   # the dispatch plan, wave by wave
slices.py frontier .sdlc "$goal"             # what is runnable right now, one id per line
slices.py check    .sdlc "$goal"             # validate only — exit 1 with the problems listed
```

**Waves, not a thread pool.** `plan` takes the runnable frontier (not done, every `needs` already
done), packs a **wave** of mutually non-conflicting slices capped at `max_concurrent`, and repeats.
Widest fan-out goes first, so a wave is never spent on leaves while the critical path waits, and the
ordering is fully deterministic — you can read the plan before anything is dispatched. `check` reports
unknown dependencies, duplicate ids, and **dependency cycles by their members** (you have to know
which edge to break).

**The conflict rule is deliberately paranoid.** Two slices conflict when their `files` globs *can*
overlap — `fnmatch` in both directions plus a literal-prefix check, so `engine/**` and
`engine/graph.py` are correctly seen as the same blast radius. **A slice that declares no files
conflicts with everything** and runs alone: an unknown blast radius is not something you may
parallelise, and one lost edit costs far more than one extra wave. Every slice that *does* declare
files is dispatched with **`isolation: worktree`**, so concurrent siblings can't see or stomp each
other's half-finished work.

**Where this stops, honestly.** `/sdlc-loop` runs a wave's slices as **subagents** — fresh isolated
context each, which is a documented, dependable primitive. It does **not** drive the Claude desktop
app's "chips": that mechanism is app-internal and **not a public API for plugins**, so building on it
would be building on something that can move without notice. So a slice marked `"size": "large"` — too
big for one subagent's context — is dispatched as `session`, and the plan **prints the exact command
for you to start**:

```
claude --worktree 0007-cache-s3
```

The loop never runs it for you. It also never shells out to an unattended `claude -p`: that's uncapped
spend, and it would put a second worker on one `.sdlc`, which every state file in the kit assumes never
happens. Leave `parallel` off, or ship no manifest, and every goal runs as one unit exactly as before.

---

## One worktree, one branch, one PR per goal (optional, off by default)

Turn it on with `work: {"enabled": true}`. Until you do, the loop writes to git exactly as little as
it did before — this is the only feature that lets it commit at all.

```json
"work": { "enabled": true, "base": "", "remote": "origin", "auto_merge": false }
```

**Why a worktree and not a branch.** The moment the loop touches git, an in-place `checkout -b`
breaks two things silently: it moves the working copy out from under whatever you left open, and —
because `sdlc-init` has you commit `.sdlc/goals/` — every branch switch rewrites the backlog the loop
is in the middle of reading. A worktree avoids both. Your checkout never moves and never changes
branch; bookkeeping keeps resolving to the one real `.sdlc`.

**Cutting fresh IS the goal-start rebase.** The worktree is created from `<remote>/<base>` at the
moment the goal starts, so there is nothing to replay. That matters more than it sounds: a real
`git rebase` that hits a conflict at 3am leaves a half-applied tree, and every later goal in that run
then builds on it. The only rebase that ever runs is the reactive one below, and it aborts on
conflict rather than leave the tree wedged.

**`verify_command` runs in the worktree.** It has to — the main checkout doesn't contain the change.
A proving command resolved against the wrong tree is a green that proves nothing, and `record done`
would accept it.

### The merge gate: clean *and* safe

`work.py merge` will not merge unless all three hold:

| Leg | Source | What it catches |
|---|---|---|
| fresh local evidence | `loop.py verify`, this run | a green from yesterday, or none at all |
| `mergeable` | GitHub | textual conflicts with the base |
| `mergeStateStatus == CLEAN` | GitHub | failing required checks, missing reviews, a stale branch |

Then it arms GitHub's own `--auto` rather than merging on what it just read, so the final decision is
an atomic re-check at merge time. A `BEHIND` branch is rebased once and re-checked. Everything else
prints `PARK: <reason>` and the loop records exactly that and moves on — the "human intervention"
path is the existing review queue, not a new one.

Three honest limits. `CLEAN` is only worth what your **branch protection** is worth: on a repo with
no required checks GitHub objects to nothing, so the gate says so out loud instead of letting it read
as "reviewed". A fresh worktree has no `node_modules`/`.venv`/build cache, so a heavy
`verify_command` pays that cost per goal — part of why this ships off. And `work.py commit` stages
with `git add -A`, so **anything your `verify_command` leaves behind must be gitignored** or it rides
along into the PR (`.coverage`, `.pytest_cache/`, build output).

---

## Self-improving knowledge graph (optional, off by default)

LoopSmith can accumulate a **knowledge graph** of what it learns, so research and analysis compound
across runs instead of evaporating — and it gets *sharper* over time, not noisier. It's **opt-in**
(`knowledge_graph.enabled: false` by default) and built by an external tool (default **graphify**,
`pip install graphifyy`) — the core stays zero-dep.

**Write side — what feeds it** (two objectives: *enhance the learnings* and *build a knowledge base
around the code*):
- **External research** — every `WebSearch` / `WebFetch` is auto-captured to
  `.sdlc/knowledge/research/web/` by a fail-open hook (only when KG is enabled; a hard no-op otherwise).
- **Internal analysis** — durable findings and Retrospective **lessons** you write to
  `.sdlc/knowledge/analysis/`.
- **The code** — graphed too, but only at `scope: full`.

Turn it on in `.sdlc/config.json`:
```json
"knowledge_graph": {
  "enabled": true,
  "scope": "full",
  "builder": "graphify",
  "auto_refresh": false
}
```
`scope` is **`full`** (code + external research + internal analysis) or **`research`** (skip code —
internal analysis + external research only). `auto_refresh: true` rebuilds the graph at the end of each
Retrospective. Then **`/sdlc-kg`** builds, refreshes, and queries it; querying via graphify **saves the
answer back into the graph**, so each query makes the next one better. The builder is a **soft
dependency**: if it isn't installed, `/sdlc-kg` says so and the rest of the SDLC runs unaffected.

### The self-improving loop

A graph that only grows rots into noise. LoopSmith closes the loop so it stays useful:

- **Find gaps** — a query (or a recall) that comes up empty is logged as a **gap** (`kg.py gap log`,
  done automatically by `/sdlc-context`). The graph tracks **what it doesn't know yet**; review it with
  `kg.py gap list`.
- **Prune itself** — `kg.py maintain` reports **stale** notes (citing a repo path that no longer
  exists), **duplicates**, and corpus size vs a threshold. Report-only, **archive-not-delete**,
  destructive trims need your approval — so the corpus self-cleans instead of bloating.
- **Fill the gaps** — when the backlog empties but gaps remain (and budget allows), `/sdlc-loop` can
  promote the oldest gap into a fill-goal (research → write analysis → refresh → `gap resolve`),
  budget-gated and parking anything that needs you. The graph **fills what it didn't know.**

The cycle: **enrich → find gaps → prune → fill → repeat** — cleaner and denser every run.

### Context recall — never lose the thread

The **read side** is **`/sdlc-context`**: a pre-flight that, before a goal runs, pulls the **relevant
slice** of project memory back into context — retrieval by **relevance, not recency** — so a crucial
earlier finding isn't missed just because the context window flushed. It's gated on the KG (a no-op
when disabled), and `/sdlc-loop` + `/sdlc-goal` run it automatically at the start of each goal. It
assembles a short, **cited** brief from the **north-star** (vision-first) + the **graph**
(`graphify query`) + **past issues / 🔒 Critical Insights** + the **conventions** (`.sdlc/project.md` +
governing `CLAUDE.md`).

For on-demand pull *during* a run, expose the graph as a live tool — run **`graphify --mcp`** (or add
the graphify MCP server to your Claude Code config, pointed at `graphify-out/graph.json`) — so the
agent can query it whenever it hits unfamiliar code, keeping the working window small while the full
history stays a query away. The full closed loop: **record** (issues / journey) → **ingest**
(`/sdlc-kg`) → **recall** (`/sdlc-context` + MCP) → run.

> Keep `.sdlc/knowledge/research/` and the builder's output (`graphify-out/`) out of git — they're
> machine-accumulated. Commit `.sdlc/knowledge/analysis/` to version your curated learnings.

---

## Companions (optional enhancement)

LoopSmith ships the *spine* **and** a portable executor for every phase, so it has **zero hard plugin
dependencies** — `/plugin install loopsmith` is seamless whether or not anything else is present, and
the kit is **never disabled** waiting on another plugin. The *execution muscle* for Phases 1, 3, 5,
and 6 runs *best on Claude* through two companion plugins:

- **`superpowers`** — `brainstorming`, `writing-plans`, `test-driven-development`, `executing-plans`,
  `requesting-code-review`, `verification-before-completion`.
- **`code-review`** — the `/code-review` skill.

**Zero action required — LoopSmith auto-detects them.** If a companion is **already in your plugin
list**, each phase uses its richer skill; if it isn't, that phase falls to LoopSmith's **portable
`sdlc-*` executor**. You **install nothing** to get a working, disciplined spine — the portable
executors each carry a committed [parity review](docs/executor-parity/) showing they're
at-par-or-better, so absence is never a downgrade you have to fix.

**How resolution works, per phase:** each phase skill carries a host-aware resolution header — on
Claude *with the companion installed* it prefers the companion's richer skill; **otherwise** (companion
absent / Cursor / any other host) it uses the portable `sdlc-*` executor. Either way the always-on hook
still injects the 7-phase policy. Run `/sdlc-doctor` to see which companions are present (absent is
reported as "portable executor used" — never an error).

*If you happen to want the companions and don't have them,* they live in the official
`claude-plugins-official` marketplace — but this is a preference, never a setup step.

## Status (honest)

LoopSmith is **built on and validated only on Claude Code** — the always-on hook, one-command plugin
install, and the `superpowers`/`code-review` companions. The phase executors are written to be
**portable**; an experimental Cursor adapter exists but **isn't verified in a live session yet** — see
[Other platforms supported](#other-platforms-supported).

## Quality & drift (`evals/`)

The kit's "output" is agent *behavior*, so quality is guarded in two tiers, re-run on every change to
catch drift (see [`evals/README.md`](evals/README.md)):

- **Tier 1 — deterministic behavioral gate (free, in CI):** `python3 evals/run.py` runs the intent hook
  over a behavioral corpus (`evals/fixtures.json`) — a deterministic proxy for *"the agent got the right
  discipline signal"* — scores it, and **fails the build if the score drops below `evals/baseline.json`.**
  That drop is the drift signal.
- **Tier 2 — LLM-judge behavioral evals (opt-in, parked):** run the agent on each fixture goal and have
  an LLM judge score the transcript against its rubric. The runner + injectable `agent`/`judge` seam are
  built and tested; the real LLM wiring is withheld until the API budget is greenlit, so `--live` prints
  a parked notice instead of spending.

## Requirements

- **Runtime:** bash + python3 (stdlib) — zero dependencies. The optional **GitHub backlog source**
  additionally needs the [`gh`](https://cli.github.com) CLI, authenticated (`gh auth login`); the
  default local source stays zero-dep.
- **Knowledge graph (optional):** the graph builder — default `graphify` (`pip install graphifyy`);
  off unless `knowledge_graph.enabled` is set.
- **Companions (optional):** `superpowers` + `code-review` — **auto-used when already installed**,
  otherwise the **parity-reviewed portable `sdlc-*` executors run the phases**. Never required; you
  install nothing either way.
- **Dev/test:** `pip install pytest pytest-cov`, then `pytest tests/ -v`. **CI** (GitHub Actions) runs
  the full suite — including the **leakage gate**, the **hook behavioral-spec**, and the **Tier-1
  quality gate** (`evals/run.py`) — with an **85% coverage floor** on every push/PR, on Python 3.10 + 3.12.

## Other platforms supported

LoopSmith is built on and validated only on **Claude Code**. Beyond it, the phase executors are
portable, so other hosts can run the same spine — but **only Cursor is scaffolded so far, and it is not
yet verified in a live session.**

### Cursor (experimental)

> **Not yet verified in a live Cursor session.** The scaffolding is built and unit-tested, but
> LoopSmith has **not been run end-to-end inside Cursor.** Treat this as experimental — the `.mdc` rule
> format follows Cursor's documented convention, but real-session behavior is unverified.

Cursor has no plugin system, `UserPromptSubmit` hook, or `superpowers`/`code-review`. From your
LoopSmith checkout:

```
python3 <loopsmith>/skills/sdlc-init/scripts/sdlc_init.py . --cursor --demo
```

That writes **`.cursor/rules/sdlc.mdc`** — intended as an *always-applied* Cursor rule carrying the full
7-phase discipline (Cursor's analog of the Claude hook) — scaffolds the `.sdlc/` layer, and pins
`companions: off` so each phase would run via the **portable `sdlc-*` executors** instead of the
Claude-only companions. The loop, model-selection, status and KG **helpers are plain zero-dep
`python3`** — run them from Cursor's terminal (e.g.
`python3 <loopsmith>/skills/sdlc-loop/scripts/loop.py next .sdlc`). Once verified in a live session, the
goal is the same spine, executors, and audit trail without Claude — **help testing this is welcome.**

### Other hosts

Codex and others could follow the same shape — a host rules file + `companions: off` — but aren't
scaffolded yet.

## Credits & acknowledgements

LoopSmith stands on other people's work.

- **[superpowers](https://github.com/obra/superpowers)** by **Jesse Vincent ([@obra](https://github.com/obra))** — supplies the per-phase execution skills (brainstorming, writing-plans, test-driven-development, executing-plans, requesting-code-review, verification-before-completion). Optional companion.
- **[code-review](https://github.com/anthropics/claude-plugins-official)** by **Anthropic** — the `/code-review` skill used in the Review phase. Optional companion.

Both companion plugins are optional and install from the official **`claude-plugins-official`** marketplace ([how](#companions-optional-enhancement)).

## License

MIT. The companion plugins are each under their own licenses (superpowers and code-review are both MIT at the time of writing).
