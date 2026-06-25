# LoopSmith

**Portable Goal-Based SDLC for any repo.** Install once, and every prompt is held to a 7-phase
development spine — Goal → Research → Plan → Plan-Review → Implement → Review → Retrospective — so the
agent stops jumping straight to code. Run it two ways: **goal-mode** (interactive, intervention-driven) or **loop-mode**
(autonomous, park-and-continue).

The discipline borrows from [loop-maker](https://github.com/EricTechPro/loop-maker)'s loop
engineering — a checkable goal, durable-vs-changing state, a separate verifier, a mandatory budget,
and a non-skippable human gate — wrapped around the SDLC as the per-item engine.

> **Status (v0.4):** all commands shipped — the always-on **SDLC hook**, install paths,
> **`/sdlc-init`** (scaffold), **`/sdlc-goal`** (interactive mode), **`/sdlc-loop` +
> `/sdlc-status`** (autonomous loop driver), and a generic **`sdlc-plan-review`** (the Phase-4 gate
> `superpowers` doesn't provide). LoopSmith now **auto-installs** its companion plugins (see
> [Dependencies](#dependencies-auto-installed-companions)).

---

## The pipeline

LoopSmith installs one always-on hook (`hooks/sdlc_gate.sh`, wired as a `UserPromptSubmit` hook).
On every prompt it classifies intent with fast, deterministic regex — **no LLM** — and injects the
matching SDLC directive:

- **code change / implementation** → "do NOT jump to editing; run the full spine from the GOAL and
  pass PLAN-REVIEW before any edit."
- **read-only / conversational** → "answer directly (say so) — but the moment it becomes a code
  change, switch to the spine."
- **anything else** → the standard 7-phase policy.

The hook is *advisory and fail-safe*: a false positive over-reminds, a false negative falls back to
the standard policy, and it always emits valid JSON (even on garbage or empty stdin). It never calls
out, never blocks — it shapes what the agent does next.

### How it falls through

A prompt enters through the always-on hook, which routes by intent. Code work then falls through the
seven phases — with two **gates** that can send it back, and a **park** exit for anything that needs you:

```mermaid
flowchart TD
    P(["Your prompt"]) --> H{"Hook classifies intent"}
    H -->|"read-only / conversational"| ANS(["Answer directly"])
    H -->|"code change / non-trivial"| G["1. Goal"]
    G --> RS["2. Research"]
    RS --> PL["3. Plan"]
    PL --> PR{"4. Plan-Review"}
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

### The seven phases

1. **Goal** — restate the objective as one concrete, checkable goal. For feature/creative work, this
   is where you explore intent and requirements first.
   → *owned by* `superpowers:brainstorming`.
2. **Research** — map the blast radius: affected files, existing patterns, constraints, prior art.
   → *agent practice; no dedicated skill.*
3. **Plan** — write the plan: steps, files, tests, and a definition-of-done.
   → *owned by* `superpowers:writing-plans`.
4. **Plan-Review** — adversarially review the plan **before** any edit: verify each claim against the
   real code, stress-test what breaks after it ships, check scope/fit. Never skipped. This is the gate
   `superpowers` doesn't provide, so LoopSmith ships it.
   → *owned by* **`sdlc-plan-review`** (ships with LoopSmith).
5. **Implement** — build test-first and execute the plan step by step.
   → *owned by* `superpowers:test-driven-development` + `superpowers:executing-plans`.
6. **Review** — code-review the diff for real findings, then verify every claim with evidence before
   declaring anything done.
   → *owned by* `code-review` (`/code-review`) + `superpowers:requesting-code-review` +
   `superpowers:verification-before-completion`.
7. **Retrospective** — capture lessons; lock the critical insights so the next loop is better.
   → *agent practice; no dedicated skill.*

### Phase → owning skill

| # | Phase | What it does | Owning skill | Source |
|---|-------|--------------|--------------|--------|
| 1 | Goal | Restate the objective as one concrete, checkable goal | `brainstorming` | superpowers |
| 2 | Research | Map blast radius — files, patterns, constraints | *(agent practice)* | — |
| 3 | Plan | Write steps / files / tests / definition-of-done | `writing-plans` | superpowers |
| 4 | Plan-Review | Adversarially verify the plan against real code before any edit | **`sdlc-plan-review`** | **LoopSmith (ships)** |
| 5 | Implement | Build test-first; execute the plan | `test-driven-development`, `executing-plans` | superpowers |
| 6 | Review | Code-review the diff; verify claims with evidence before "done" | `/code-review`, `requesting-code-review`, `verification-before-completion` | code-review + superpowers |
| 7 | Retrospective | Capture lessons; lock critical insights | *(agent practice)* | — |

### What LoopSmith ships vs relies on

**Ships in this kit** (`skills/` + `hooks/`) — zero runtime deps, bash + python3 stdlib only:

| Skill / component | Role |
|---|---|
| `hooks/sdlc_gate.sh` | The always-on, intent-aware hook that injects the 7-phase policy on every prompt |
| **`sdlc-plan-review`** | Phase-4 gate: adversarial plan review (the one phase superpowers doesn't cover) |
| **`/sdlc-init`** | Scaffold the per-project `.sdlc/` layer (project stub, goals, config, state) |
| **`/sdlc-goal`** | Interactive orchestrator: drive ONE goal through all 7 phases, pausing at each gate |
| **`/sdlc-loop`** | Autonomous orchestrator: drive the backlog through all 7 phases, park-and-continue |
| **`/sdlc-status`** | Report backlog counts + whether the review queue needs attention |

**Relies on** (auto-installed companions — see [Dependencies](#dependencies-auto-installed-companions)):

| Plugin | Skills used | Phases |
|---|---|---|
| `superpowers` | `brainstorming`, `writing-plans`, `test-driven-development`, `executing-plans`, `requesting-code-review`, `verification-before-completion` | 1, 3, 5, 6 |
| `code-review` | `/code-review` | 6 |

> The orchestrators (`/sdlc-goal`, `/sdlc-loop`) walk a goal through **all seven** phases; `/sdlc-init`
> and `/sdlc-status` set up and report on the work. The phase owners above are *who does the work* at
> each step — superpowers and code-review supply the execution muscle, LoopSmith supplies the spine,
> the Phase-4 gate, and the orchestration.

---

## The two modes

Both modes drive the **same seven phases** per goal — they differ in who's in the loop and what
happens at a checkpoint. The always-on hook underpins both.

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
- a failure it cannot resolve.

It halts on a **per-run iteration budget** (`config.json` → `budget.max_iterations`), which resets
each invocation and is resume-safe (a budget stop, re-run, picks up where it left off). Run
**`/sdlc-status`** any time for backlog counts (pending / in-progress / done / parked) + whether the
review queue needs attention.

The autonomous loop runs the backlog **park-and-continue** — it parks whatever needs you and keeps going:

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

| | `/sdlc-goal` (interactive) | `/sdlc-loop` (autonomous) |
|---|---|---|
| Scope | one goal | the whole `.sdlc/goals/` backlog |
| At a checkpoint | pauses for you | parks to the review queue, continues |
| Approval | every gate | only what it parks |
| Stops on | goal complete / you stop | backlog empty or per-run budget |
| Irreversible action | asks you | always parks — never runs it |

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
  "github": { "repo": "", "goal_label": "sdlc:goal" }
}
```

File each goal as an **issue labelled `sdlc:goal`** (the issue body is the goal). The loop maps SDLC
status onto GitHub so the board mirrors reality:

| SDLC status | What the loop does on GitHub |
|---|---|
| picked up | adds the `sdlc:in-progress` label |
| done | **closes** the issue with a completion comment |
| parked (needs you) | comments the reason, adds `sdlc:parked`, and removes `sdlc:goal` so it leaves the queue |

So your **review queue = open issues labelled `sdlc:parked`**, and **done = closed issues**;
**re-queue** a parked issue by re-adding the `sdlc:goal` label. The three labels are auto-created on
first run. **Setup:** run `gh auth login` once; leave `repo` empty to auto-detect from the git remote,
or set it to `owner/name`.

> Issues + labels are the v1 surface. GitHub **Projects** boards and a `gh`-aware `/sdlc-status` are on
> the roadmap — for now, github-mode status is one `gh issue list --label sdlc:goal` away.

**Which to pick?** **Local** for a self-contained, zero-dependency repo where the backlog ships with
the code. **GitHub** to keep goals visible to your team, triaged in Issues/Projects, and tied to the
PRs the work produces.

---

## Install (plugin — recommended)

```
/plugin marketplace add <git-url-or-local-path>
/plugin install loopsmith
```

Installs the durable spine globally — the SDLC hook then fires in **every** project — and
**auto-installs the `superpowers` + `code-review` companions** (see below). Then run `/sdlc-init` in
each repo to scaffold its per-project `.sdlc/` layer (project stub, `goals/`, `config.json`, loop
`state/`). Re-running `/sdlc-init` is safe — it never clobbers existing state.

## Dependencies (auto-installed companions)

LoopSmith ships the *spine*; the *execution muscle* for Phases 1, 3, 5, and 6 lives in two companion
plugins it now declares as native dependencies:

- **`superpowers`** — `brainstorming`, `writing-plans`, `test-driven-development`, `executing-plans`,
  `requesting-code-review`, `verification-before-completion`.
- **`code-review`** — the `/code-review` skill.

When you `/plugin install loopsmith`, Claude Code **resolves and installs both automatically** and
lists them at the end of the install output. They're declared **unversioned**, so they track the
latest release in the official marketplace (no pinned git tags to resolve).

**Requirement:** you must have the **`claude-plugins-official`** marketplace added. It's the official
marketplace and ships **pre-registered** in current Claude Code, so this is normally already true.
LoopSmith's own `marketplace.json` allowlists it via `allowCrossMarketplaceDependenciesOn` — that
allowlist is what lets a dependency in *another* marketplace resolve.

**If a companion is missing** (e.g. the marketplace isn't registered, or it was disabled), Claude Code
marks LoopSmith with a **`dependency-unsatisfied`** error and disables it until resolved. Fixes, in
order of convenience:

1. Run the `claude plugin install …` command shown in the error, e.g.
   `claude plugin install superpowers@claude-plugins-official` (and/or `code-review@…`).
2. If the marketplace isn't registered, add it and Claude Code resolves the dependency automatically:
   ```
   claude plugin marketplace add anthropics/claude-plugins-official
   ```
   then `/reload-plugins`.
3. If a companion is merely disabled, enable it.

**Graceful degradation:** even with the companions absent, the always-on hook still injects the
7-phase policy and the phase *names* guide the work — LoopSmith **degrades, it does not break**. The
named superpowers/code-review skills are how each phase is executed *best*, not a hard runtime
requirement of the spine. (Ref: [plugin dependencies](https://code.claude.com/docs/en/plugin-dependencies).)

## Install (fallback — no plugin system)

```
git clone <git-url> && cd loopsmith && ./install.sh
```

`install.sh` copies the spine into `~/.claude/skills/loopsmith/` and **prints** the `settings.json`
hook snippet for you to paste (it never edits your settings — malformed JSON silently disables
hooks). Parse-check `settings.json` after pasting. The fallback installs **only** LoopSmith's own
spine — install `superpowers` and `code-review` yourself to get the phase-execution skills.

---

The always-on 7-phase hook underpins both modes. **See the [worked walkthrough](examples/hello-sdlc/)**
for a runnable end-to-end example. Publishing the kit as its own repo? See [EXTRACT.md](EXTRACT.md).

## Status (honest)

v0.4 is **Claude Code only.** The core is plain markdown + shell, structured to be host-portable, but
a second-host (Codex/etc.) adapter is not yet shipped.

## Requirements

- **Runtime:** bash + python3 (stdlib) — zero dependencies. The optional **GitHub backlog source**
  additionally needs the [`gh`](https://cli.github.com) CLI, authenticated (`gh auth login`); the
  default local source stays zero-dep.
- **Companions:** `superpowers` + `code-review` (auto-installed via the plugin path; manual on the
  fallback path).
- **Dev/test:** `pip install pytest`, then `pytest tests/ -v`.

## Credits & acknowledgements

LoopSmith stands on other people's work.

- **[loop-maker](https://github.com/EricTechPro/loop-maker)** by **Eric Tech ([@EricTechPro](https://github.com/EricTechPro))** — the design foundation. LoopSmith's loop engineering is built on loop-maker's ideas: a *checkable* goal, *durable-vs-changing* state, a *separate verifier*, a mandatory stop condition (*budget*), and a *non-skippable* human gate. That vocabulary lives on in LoopSmith's config schema (`discovery` / `verify` / `gates` / `budget`) and its portable-kit packaging (`install.sh` + `SKILL.md` + `templates/`). No code or text was copied — LoopSmith's scripts are original and self-contained — but the concepts and shape are its lineage. MIT-licensed, like this kit.
- **[superpowers](https://github.com/obra/superpowers)** by **Jesse Vincent ([@obra](https://github.com/obra))** — supplies the per-phase execution skills (brainstorming, writing-plans, test-driven-development, executing-plans, requesting-code-review, verification-before-completion). Auto-installed as a dependency.
- **[code-review](https://github.com/anthropics/claude-plugins-official)** by **Anthropic** — the `/code-review` skill used in the Review phase. Auto-installed as a dependency.

Both companion plugins install from the official **`claude-plugins-official`** marketplace.

## License

MIT. The companion plugins and loop-maker are each under their own licenses (superpowers, code-review, and loop-maker are all MIT at the time of writing).
