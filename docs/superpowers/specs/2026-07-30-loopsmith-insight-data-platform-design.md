# LoopSmith Insight — data platform design

**Date:** 2026-07-30
**Status:** design, approved in outline; awaiting spec review
**Scope of this spec:** subsystems **A** (event emitter, in `sdlc-kit`), **B** (store + ingest), **C** (metric
catalog + gap engine). Subsystems **D** (analytics dashboards) and **E** (configuration UI) are sketched in
§9 for continuity and get their own specs.

---

## 1. Why this exists

LoopSmith runs a seven-phase SDLC spine with real gates. Today the spine's output is a verified change and a
prose audit trail. What it does **not** produce is a record of *what the gates caught* — and that is the only
data in the system nobody else can sell.

Every engineering-intelligence vendor sells DORA and flow metrics off git and issue trackers. None of them can
say *"plan-review sent 34 plans back before a single edit was made, and here is the rework that did not
happen."* LoopSmith can, because it is the thing running the gate. That is the wedge, and it is the reason this
platform needs an **emitter** and not just a **reader**.

### Product decisions already settled

| Decision | Choice | Consequence |
|---|---|---|
| Repo boundary | **Monorepo — a dedicated `insight/` folder in this repo** (revised 2026-07-30, user's call; supersedes the earlier separate-repo choice) | The plugin surface (`skills/`, `hooks/`) stays stdlib-only / fail-open / default-off — unchanged and still test-guarded. `insight/` is a separate package with its own deps, CI job, and version; see §1.1 for the boundary rules that keep the product separable inside one repo. |
| v1 deployment | **Local-first, one command** | No auth, no hosting, no privacy review before first value. Same event schema promotes to hosted without a rewrite. |
| Personas in scope | **IC, manager, leadership, cross-functional** | Four distinct views, not four filters on one. |

Why the monorepo flip is the right call here: **the dogfooding loop already lives in this repo.** The team
ledger, the board, and `/sdlc-loop` all run against this clone — building `insight/` through the loop makes
the construction of the analytics product the analytics product's own first dataset, with zero cross-repo
plumbing. And `discovery-scan.sh`, `velocity.py`, and the ledger readers are in-repo, so ingest shells into
sibling paths instead of requiring a second checkout.

### 1.1 Monorepo boundary rules

```
sdlc-kit/
├── skills/ hooks/ …        # the plugin — stdlib-only, fail-open, default-off (unchanged)
├── insight/                # the product
│   ├── ingest/  metrics/  gaps/  dash/  tests/
│   ├── pyproject.toml      # its own deps (DuckDB, …) — never installed by the plugin
│   └── VERSION             # versioned independently of plugin.json
└── .claude-plugin/         # marketplace source stays "./" for now
```

1. **The contract between the two halves is file formats, never imports.** `insight/` reads
   `ledger/*/ *.jsonl`, goal frontmatter, `config.json`, `state/*` — the same additive-fields contract
   `ledger.py` already documents ("an older reader ignores a field it does not know"). Nothing under
   `skills/` or `hooks/` may import from `insight/`, and vice versa; a test enforces both directions, the
   same shape as the existing self-containment guard.
2. **Separate CI job, separate coverage gate.** The plugin's `--cov-fail-under=85` gate must not gain
   `insight/` in its denominator, and an `insight/` test failure fails its own job, not the plugin's.
3. **Plugin installs clone the folder.** Marketplace `source` is `"./"`, so every `/plugin install
   loopsmith` pulls `insight/` as dead weight. Accepted while it is small; if it bloats, marketplace
   entries support subdirectory sources and the repo can be re-scoped — **unverified until tried** (§11).
4. **License carve-out — decided.** This repo is **public under MIT**, so `insight/` carries its own
   source-available license (`insight/LICENSE`, BUSL-style), clearly marked in the repo README and in
   `insight/` file headers. The plugin stays MIT. **The LICENSE file is the first commit into `insight/`** —
   nothing else lands before it. (§10 q0 records the decision.)

### The property that makes local-first work

The ledger already lives on an **ops branch checked out as a worktree** (`ledger.branch`, default
`sdlc-ledger`), and `sync.py publish` / `watch.sh` keep it current. **Git is the transport.** So "local-first"
is *already multi-actor for a repo* — any clone sees the whole team's ledger. What local-first genuinely cannot
do is roll up across people who share no repo; v1 leadership altitude is therefore "the projects reachable
from this machine", with multi-repo via glob.

---

## 2. What already exists (verified against code)

| Source | Shape | Committed? |
|---|---|---|
| `.sdlc/ledger/entries/<actor>.jsonl` — [`ledger.py:147`](../../../skills/sdlc-loop/scripts/ledger.py) | `{id, ts, actor, kind, goal, area?, to?, issue?, priority?, why?, state?, ref?, pr?}`; kinds `claimed·done·parked·failed·handoff·ack·release·note·merged` | yes, on the ops branch |
| `.sdlc/goals/*.md` frontmatter | `id, title, lane(small\|medium\|large\|auto), done_when, auto_ok, status, verify_command?, source?` | yes |
| `.sdlc/plans/<stem>.md` + `<stem>.slices.json` | plan artifact; declared slices with the files each touches | yes |
| `.sdlc/journey/<stem>.md` | `## <iso-ts>` + `phase: prose`, written via `loop.py note` | yes |
| `.sdlc/config.json` | every feature flag | yes |
| `.sdlc/state/STATE.md` | `iteration, run_iteration, run_started_at, run_tokens` | **no — gitignored, per-machine** |
| `.sdlc/state/verify/<stem>.json` — [`loop.py:186`](../../../skills/sdlc-loop/scripts/loop.py) | `{command, exit, at, tail[]}` | **no — gitignored** |
| `.sdlc/state/work/<stem>.json` | `{worktree, branch, base, remote, pr}` | **no — gitignored** |
| `.sdlc/state/review-queue.md` | parked items with context | **no — gitignored** |
| `.sdlc/knowledge/gaps.md` | logged KG query misses | gitignored by `/sdlc-setup` |
| git / `gh` | commits, merges, PR reviews, checks | n/a |
| Claude Code Analytics API | per-**user-per-day**: `num_sessions`, `lines_of_code.{added,removed}`, `commits_by_claude_code`, `pull_requests_by_claude_code`, `{edit,multi_edit,write,notebook_edit}_tool.{accepted,rejected}`, `model_breakdown[].{tokens,estimated_cost}` | n/a |

**Gitignored ≠ useless.** A local-first reader on the developer's own machine sees `state/` directly. It just
cannot be *shared* — which is precisely what the emitter fixes for the subset worth sharing.

---

## 3. The reliability boundary — the single most important constraint

`loop.py`'s CLI surface is `start | next | qc | note | record | spend | verify`. The seven-phase spine is driven
by **SKILL.md prose**, not by Python. Python only observes the hooks the agent chooses to call. The repo states
the consequence for exactly this class of data in `loop.py`'s own docstring:

> `max_tokens` enforces against the host-REPORTED spend counter (`loop.py spend <dir> <n>` — the loop never
> measures spend itself; no reports == no enforcement).

So events fall into two reliability classes, and **the platform must never present them as one**:

### Class 1 — deterministic (Python-observed; an agent cannot forget)

| Event | Emitted from | Already computed today? |
|---|---|---|
| verify ran, exit code, duration, command | `loop.py verify_goal` — already persists `{command, exit, at, tail}` | yes, discarded on the next run |
| verify **NO-COMMAND** (`exit 3`) | same | yes |
| goal terminal outcome | `loop.py record` → `state.complete/park/fail` + ledger | yes, to the ledger |
| claim / lease | `ledger.append` | yes |
| PR opened, merge-gate verdict, merge landed | `work.py pr/gate/merge/finish` | computed, only `pr` persisted |
| review-gate read, `loopsmith:approve|block`, **cycle count** | `work.py review_gate/post_review` | computed to enforce `max_review_cycles`, then discarded |
| decision-gate denial | `hooks/decision_gate.py` | denial happens, not recorded |
| slice dispatch plan, waves, declared files | `slices.py plan` | computed per run |
| debt / test-gap inventory | `discovery-scan.sh` — deterministic and re-runnable | yes, consumed then discarded |
| pipeline report card | `pipeline.py card --json` | yes, already a JSON artifact |

### Class 2 — agent-emitted (best-effort; same class as `spend`)

phase start/end · phase tokens · gate verdict + reason · retro grade · park **reason class**.

**Design rule.** Every metric derived from Class 2 renders with a **coverage denominator** beside it:
`flow efficiency 41% — phase traces complete for 62% of goals (38% partial)`. A Class-2 metric with no
coverage figure is a bug, not a number. This is the same doctrine the repo already enforces for reviews
(`ABSENT`, never `PASS`) applied to telemetry.

**Non-goal.** We do not try to make phases Python-observable. That would mean moving the spine from prose into
a Python driver — a rewrite of the orchestrator, and it would break every host that runs the portable
executors. Coverage-qualified honesty is cheaper and truthful.

---

## 4. Subsystem A — the emitter (in `sdlc-kit`)

### A.1 A second stream on the ledger's transport, not a new module

The ledger is already an append-only, per-author, conflict-free JSONL store with actor resolution, monotonic
per-author sequence ids, fail-open appends, and a git-based publish/watch transport. Building a parallel module
would duplicate all of it.

So `ledger.py` gains a **stream** concept:

```python
ENTRIES, EVENTS = "entries", "events"

def entries_dir(sdlc_dir, stream=ENTRIES):
    return ledger_dir(sdlc_dir) / stream
```

* `append(..., stream=EVENTS)` writes `.sdlc/ledger/events/<actor>.jsonl`.
* `read_all(sdlc_dir, stream=...)` reads one stream.
* `KINDS` / `SHARED_KINDS` / `render()` are **untouched** — `render()` reads `entries` only, so `TEAM.md`
  cannot be drowned by telemetry *by construction*. This is the property that makes the shared transport safe;
  the ledger's docstring is explicit that an open vocabulary would make the team view unreadable within a week.
* Sequence ids stay per-`(actor, stream)`, so `watch.sh`'s resume cursor keeps working unchanged.

**The transport half is a real change, not an assumption.** Verified against the code:
[`sync.py publish`](../../../skills/sdlc-loop/scripts/sync.py) commits **only** `entries/<actor>.jsonl` +
`TEAM.md`, and its `GITATTRIBUTES` union-merge rule covers `entries/*.jsonl` only. As written today, an
events stream would be created locally and **never published** — the fourth instance of two halves built
separately and nothing checking they meet. So the emitter change explicitly includes: `publish` also commits
`events/<actor>.jsonl` when present; the gitattributes seed gains `events/*.jsonl merge=union`; `pull`/`watch`
cursors key on `(actor, stream)`. The §A.6 guard grows a case for it: if an `events/` directory exists in a
fixture ledger, a `publish` that leaves it uncommitted fails the test.

**Deliberate non-duplication.** The events stream carries **only** what the entries stream structurally cannot.
Lifecycle (`claimed·done·parked·failed·merged·handoff·ack`) stays in `entries` and is never re-emitted. No
double-write, no reconciliation.

### A.2 Config

```json
"telemetry": {
  "enabled": false,
  "share": true,
  "sample_phases": true
}
```

`enabled: false` by default — installing changes nothing, matching every other feature in this kit.

`share: true` publishes the events stream on the ops branch alongside entries. `share: false` keeps events
local (`.sdlc/events/` on the code branch, gitignored) so a repo can run the dashboard without committing
telemetry. **When the ledger is off entirely, telemetry still writes locally** and the local dashboard reads
it — the two flags are independent, and telemetry degrades to local-only rather than silently doing nothing.

### A.3 Event vocabulary

Common envelope, identical to the entries stream: `{id, ts, actor, kind, goal}`.

| kind | fields | class |
|---|---|---|
| `phase` | `phase`, `state: start\|end`, `ms?`, `tokens_in?`, `tokens_out?` | 2 |
| `gate` | `gate`, `verdict`, `cycle`, `why?` | 2 (plan/align/retro) · 1 (`post_review`, `merge`, `decision`) |
| `verify` | `ok`, `exit`, `ms`, `command_sha256`, `absent?` | 1 |
| `slice` | `slice`, `wave`, `mode: subagent\|session`, `files_declared`, `ms?` | 1 (plan) · 2 (actual) |
| `spend` | `phase?`, `model`, `tokens_in`, `tokens_out`, `cost_cents?` | 2 |
| `retro` | `grade: achieved\|partial\|diverged`, `debt_count`, `lessons_count` | 2 |
| `park` | `reason_class`, `why` | 2 |
| `scan` | `category: tech-debt\|test-gap`, `file`, `count` | 1 |

Controlled vocabularies:

* `phase` ∈ `goal · research · plan · plan_review · implement · review · retro`
* `gate` ∈ `plan_review · code_review · post_review · merge · decision · alignment · verify ·
  risk_security · risk_contract · risk_migration · risk_release · risk_debug`
* `verdict` ∈ **`pass · block · warn · absent`** — four states, borrowed verbatim from
  [`pipeline.py`](../../../skills/sdlc-loop/scripts/pipeline.py) including its severity order
  `{PASS:0, ABSENT:1, WARN:2, FAIL:3}`. `absent` is mandatory: PR #90 established that *a review missing a
  required input reads ABSENT, never PASS.* A store that folds `absent` into `pass` reports false passes,
  which is worse than reporting nothing.
* `reason_class` ∈ `irreversible · needs_decision · merge_conflict · failing_check · no_evidence ·
  dependency · review_cap · budget · unknown`

### A.4 Privacy — enforced, not advised

Events carry **identifiers, counts, durations, verdicts, exit codes, and hashes. Never code, never diffs,
never prose bodies.** Precedent is already set in this repo: research capture scrubs secret-shaped substrings
and stores a short excerpt rather than the page (#80/#81), and `discovery-scan` carries "location + count,
never the marker text, because a TODO can hold a secret".

Concretely: `why` is capped at 200 chars and passed through the existing scrubber; `command_sha256` replaces
the verify command string (a command line can carry a token); `files_declared` is a **count**, not a list, in
the shared stream. A test asserts no event field exceeds the cap and that the emitter rejects a payload
containing a newline. This is what makes the hosted upgrade legal-reviewable rather than a rewrite.

### A.5 Instrumentation sites

Deterministic (Python, each wrapped exactly like `safe_append` — a telemetry failure can never stop a run):

1. `loop.py verify_goal` → `verify` (incl. the `exit 3` ⇒ `absent` case)
2. `loop.py record` → `park` with `reason_class`
3. `work.py post_review` → `gate{post_review, verdict, cycle}` ← **this is where the discarded cycle count becomes durable**
4. `work.py gate` / `merge` / `finish` → `gate{merge, verdict}`
5. `work.py review_gate` → `gate{code_review, verdict}`
6. `hooks/decision_gate.py` → `gate{decision, verdict: block, why: <decision id>}`
7. `slices.py plan` → `slice` per planned slice
8. `discovery-scan.sh` → `scan` per candidate

Agent-emitted, via a new thin CLI verb `loop.py emit <dir> <goal> <kind> [--k v ...]`, instructed in the
orchestrator prose exactly as `spend` and `note` already are:

9. phase boundaries (`sdlc-loop` / `sdlc-goal` prose)
10. `gate{plan_review|alignment}` verdicts (`sdlc-plan-review`, `sdlc-align`)
11. `retro` grade (`sdlc-retro`)
12. `spend` per phase (extends the existing `spend` verb)

**Rejected:** a `Stop`-hook that reconstructs phases from the transcript. It would be a second, disagreeing
source of truth for the same fact, and the repo has been bitten three times by two halves of a feature built
separately and never checked against each other (`lane: auto`, the plan file, `gates.decision_gate`).

### A.6 Guard against the recurring failure

`tests/test_config_discoverability.py` already enforces "every `gates.*` key read anywhere must appear in the
scaffolded template". The emitter adds the same shape of guard: **every kind in `EVENT_KINDS` must have at
least one emitting call site under `skills/*/scripts/`, `hooks/`, or a `SKILL.md`, and every `--k` flag
accepted by `loop.py emit` must appear in the vocabulary table.** A vocabulary nothing writes is a metric that
will silently read zero forever — the fourth instance of the same bug class, pre-empted.

---

## 5. Subsystem B — the store

### B.1 DuckDB, single file

| Candidate | Verdict |
|---|---|
| **DuckDB** | **Chosen.** Embedded, so one command and no daemon — the whole point of local-first. Columnar, which is what `GROUP BY actor/goal/phase` over a window wants. Reads NDJSON and Parquet natively, so the "pipeline" is a `read_json_auto()` glob rather than an ETL service. Multi-repo rollup is `ATTACH` or a glob. |
| ClickHouse | Right answer *when hosted* — wrong for v1 because it is a server. The schema and SQL are kept close enough that this is an executor swap, not a rewrite. |
| Postgres / TimescaleDB | Buys transactional, contended writes. Ours are append-only NDJSON produced by one writer per actor. Pays for a server and gets nothing. |
| SQLite | Would work and is stdlib. But no columnar scan and no native JSON/Parquet ingest, so every rollup becomes hand-written row iteration. DuckDB is one dependency that deletes a lot of code. |

### B.2 Ingest — a command, not a service

`loopsmith-insight ingest [--repos <glob>]`, idempotent and incremental:

1. `git fetch` the ops branch worktree (already exists when the ledger is on).
2. `read_json_auto` over `ledger/entries/*.jsonl` and `ledger/events/*.jsonl` into raw tables — **and over
   `.sdlc/events/*.jsonl` when `telemetry.share` is off**, so a local-only repo still feeds its own dashboard.
3. Read committed artifacts: goal frontmatter, `*.slices.json`, `config.json`, `journey/*.md` timestamps.
4. Read local-only artifacts when present: `state/verify/*.json`, `state/work/*.json`, `STATE.md`.
5. Shell out for git facts (`git log` — reuse `velocity.py`'s measurement), and optionally `gh` for PR review
   timings and checks.
6. Optionally pull the Claude Code Analytics API for actor-day cost.
7. Upsert into the star schema.

**Watermark: reuse the ledger's own cursor.** `id` is `<actor>:<seq>`, monotonic per author, and `watch.sh`
already uses it as a resume cursor. Ingest stores `max(seq)` per `(project, actor, stream)`. No new cursor
mechanism, and re-running ingest is free.

### B.3 Schema — five tables

```sql
dim_project(project_id PK, repo, remote_url_sha256, north_star_present, config_json, first_seen, last_seen)
dim_actor  (actor_id PK, handle, areas[])
fact_goal  (project_id, goal_id PK, title, lane, source, done_when_present, plan_artifact_present,
            created_ts, claimed_ts, first_done_ts, terminal_ts, outcome, pr, issue,
            retro_grade, verify_state, phase_trace_completeness)
fact_event (project_id, goal_id, ts, actor_id, kind, phase, gate, verdict, cycle, ms,
            tokens_in, tokens_out, cost_cents, reason_class, ok, exit_code, reliability_class)
fact_handoff(project_id, from_actor, to_actor, area, issue, priority,
            opened_ts, ack_ts, ack_state, settled_ts)
```

Two columns carry the honesty contract into the store itself:

* `fact_event.reliability_class ∈ {1, 2}` — so no query can accidentally mix deterministic and best-effort data
  without saying so.
* `fact_goal.phase_trace_completeness ∈ [0,1]` — the per-goal coverage denominator, computed at ingest.

`fact_handoff` is its own table rather than a view over `fact_event` because the questions asked of it are
graph-shaped (who blocks whom, transitively) and it is small.

### B.3.1 The collector interface — ingest's primary Class-1 source

**Revised 2026-07-30 after rebasing onto 0.9.17.** This repo has converged on a repeated pattern that ingest
should consume as *one interface*, not as N bespoke readers:

| Collector | Emits |
|---|---|
| `skills/sdlc-align/scripts/alignment-collect.sh` | `{schema:"alignment-collect/v1", window, degraded[], commits[], dimensions{d1..d7}}` |
| `skills/sdlc-loop/scripts/pipeline.py card --json` | stage card with `PASS/WARN/FAIL/ABSENT` |
| `skills/sdlc-loop/scripts/discovery-scan.sh` | debt / test-gap candidates per file |

Every one of them is **read-only, deterministic (same state + window ⇒ byte-identical output), fail-open with
a machine-readable `degraded[]` rather than a crash, and secret-safe** (`alignment-collect` emits only
`{commit,file,line,pattern_id}` for a pattern hit, never the matched substring — its own docstring notes
stdout "is LLM-facing and may be committed").

Three consequences, all simplifications:

1. **Ingest has one adapter, keyed on `schema`.** New collectors are additive: drop a JSON pack in, ingest
   routes by its schema string. No per-collector code in the metric layer.
2. **`degraded[]` IS the ABSENT signal.** A pack reporting `degraded:["no_git"]` or `["no_test_command"]`
   means *not measured*, and the gap engine's Coverage class consumes those codes directly instead of the
   dashboard re-deriving absence. The vocabulary already exists; §7 adopts it.
3. **The collectors are the reason so much is derivable with no emitter.** They are already running the
   deterministic half of the measurement — LoopSmith just throws the output away after the skill reads it.
   Ingest's job for Class 1 is largely *to stop discarding it*.

**Ingest therefore runs the collectors on a schedule** (they are cheap, read-only, and deterministic) and
snapshots each pack with its `window`, rather than requiring instrumentation for facts git already holds.

### B.4 Metrics are SQL, not application code

One file per metric under `metrics/<id>.sql`, with a header comment carrying `name`, `question`, `personas`,
`reliability_class`, and `guardrail`. The dashboard **never computes a number** — it selects from a view.

Two reasons, both load-bearing:

1. **Credibility.** The product's entire value is that two people reading the same chart get the same number.
   One definition, one place.
2. **Testability.** A fixture event stream in, an asserted number out. A metric catalog nobody can test is a
   catalog of opinions. `metrics/<id>.sql` + `tests/fixtures/<id>.jsonl` + expected value.

---

## 6. Subsystem C.1 — the metric catalog

Columns: **⚙ derivability** — `NOW` derivable from data that already exists · `DET` needs the deterministic
emitter · `AGT` needs the agent emitter, renders coverage-qualified · `CONV` needs a new linking convention.

### Layer 0 — Delivery (all personas, differing altitude)

| # | Metric | Question | Formula / source | ⚙ |
|---|---|---|---|---|
| 1 | Throughput | Are we shipping? | `count(fact_goal.outcome='done')` per week | NOW |
| 2 | Cycle time p50/p85 | How long does a goal take? | `terminal_ts − claimed_ts`, **percentiles + scatterplot, never a mean** — the distribution is right-skewed | NOW |
| 3 | Lead time for change | First commit → merge | git / `gh` | NOW |
| 4 | Merge frequency | Deploy proxy | `git log --merges` — `velocity.py` already computes it | NOW |
| 5 | Change failure rate | Did shipping break things? | **`alignment-collect` d7 `repeated_revert_or_fixup_count`** is a real proxy available today; a `fixes:` convention upgrades it to exact | NOW (proxy) → CONV |
| 6 | MTTR proxy | Time to recover | `failed` → `done` of the fixing goal | CONV |
| 7 | Flow load (WIP) | How much is in flight? | replay `open_claims()` over time | NOW |
| 8 | Flow efficiency | **Where does the time go?** | `Σ phase.ms / (terminal_ts − claimed_ts)` | AGT |
| 9 | Flow distribution | New capability vs debt vs risk | share by `source` (`goal·discovery·radar·handoff`) × `lane` | NOW |
| 10 | Aging WIP | What is quietly rotting? | oldest open claim per actor | NOW |
| 11 | Throughput forecast | When will the backlog land? | Monte Carlo over the trailing throughput distribution → an **80% band** | NOW |

On #11: we ship the burndown *and* the Monte Carlo band on the same axes. The burndown is what was asked for
and what people read; the band is the honest statement of what is known. A single burndown line implies a
precision the data does not have.

### Layer 1 — Loop & agent economics (the wedge, part 1)

| # | Metric | Question | Formula / source | ⚙ |
|---|---|---|---|---|
| 12 | Autonomy rate | How often does the loop finish unaided? | `done` with no `park` and no human `ack` in span ÷ terminal goals | NOW |
| 13 | Interventions per goal | How much human attention per unit shipped? | parks + acks (+ review cycles when available), p50/p85 | NOW→DET |
| 14 | Park rate | How often does it stop? | parks ÷ terminal | NOW |
| 15 | **Park taxonomy** | *Why* does it stop? | share by `reason_class` | AGT |
| 16 | Review-cycle distribution | Is the cap masking a problem? | `max(gate.cycle)` per goal; mass at `max_review_cycles` is the alarm | DET |
| 17 | Cost per landed goal | Unit economics | `Σ cost_cents ÷ done`, by lane and model | AGT |
| 18 | Tokens per phase | Where does budget go? | `spend` grouped by phase | AGT |
| 19 | Budget-exhaustion rate | Are budgets mis-set? | runs stopping on budget vs empty backlog | DET |
| 20 | Rework ratio | How much building is re-building? | **`alignment-collect` d3 `churn_hotspots`** gives a file-level rework proxy now; exact implement-re-entry count needs the emitter | NOW (proxy) → AGT |
| 21 | Model-tier effectiveness | Is the expensive tier worth it? | outcome × cost by predicted tier | AGT |

#15 is the highest-value cheap metric in the catalog. A bare park rate says "the loop stopped 30% of the
time", which is unactionable. The taxonomy says *"18% `needs_decision`"* → goals are under-specified, fix the
intake; or *"14% `merge_conflict`"* → WIP is too high, lower `max_concurrent`. Same denominator, completely
different action.

#17 correction: per-goal cost is **not** derivable today. The Claude Code Analytics API is per-user-per-day and
`loop.py spend` is per-run. Actor-day cost is available now; goal-level attribution requires the emitter.

### Layer 2 — Gates & prevented rework (the wedge, part 2)

| # | Metric | Question | Formula / source | ⚙ |
|---|---|---|---|---|
| 22 | **Prevented rework** | What did the gates save? | `count(gate.verdict='block' and gate='plan_review')` × **your own measured** cost delta between goals that looped implement→review→implement and those that did not | AGT |
| 23 | Gate catch rate by gate | Where are defects actually caught? | blocks by `gate`; late-catch share is the leading indicator | AGT/DET |
| 24 | Gate coverage | Which gates actually ran? | per goal × applicable gate → `pass·warn·block·**absent**`. **`alignment-collect` d1 `plan_existed_pct` + d5 `commits_with_review_pct` already measure plan- and review-gate coverage against real commits today**; the emitter adds the rest and makes it per-goal rather than per-commit | NOW (partial) → AGT/DET |
| 25 | Escape rate | The gates' true score | defects found post-merge ÷ total found | CONV+ |
| 26 | Verify reliability | Is the proving command trustworthy? | **Current state is NOW; the trend is not**: `state/verify/<goal>.json` is overwritten on every run (verified — `verify_goal` writes latest-only), so history does not exist until the emitter records each run. Pass-rate and **flake** (same `command_sha256`, same commit, different `exit`) are DET | NOW (state) · DET (trend) |
| 27 | Decision-gate denials | Are the invariants earning their keep? | denials by decision id | DET |
| 28 | Alignment drift | Are we still building the right thing? | `/sdlc-align` verdicts over time | AGT |
| 29 | Retro grade mix | Intent vs shipped | `achieved / partial / diverged` trend | AGT |
| 30 | Debt inventory + trend | Is debt growing? | `discovery-scan` per file over time | NOW |

#22 is the ROI chart, and the multiplier must be **derived from the customer's own data**, never asserted from
an industry figure. "Your plan-review blocked 34 plans; goals that reach implement without a block cost a
median 1.0 units, goals that loop cost 2.7 — estimated avoided cost 58 units." Defensible because every input
is theirs.

#24 must render `absent` as its own colour, never folded into pass. This is a correctness requirement, not a
presentation preference.

#30 needs no emitter at all: `discovery-scan.sh` is deterministic and re-runnable, so ingest simply runs it and
snapshots. Trend for free.

### Layer 3 — Team & dependency (manager, cross-functional)

| # | Metric | Question | Formula / source | ⚙ |
|---|---|---|---|---|
| 31 | Handoff graph | Who blocks whom? | `fact_handoff`, by area | NOW |
| 32 | Handoff response time | How fast do we unblock each other? | `ack_ts − opened_ts` p50/p85, by area × priority | NOW |
| 33 | **Unanswered handoffs** | What has nobody even looked at? | `unanswered()` — already implemented | NOW |
| 34 | Deferred-handoff age | The silent killer | age of `ack.state='deferred'` — deliberately never settles | NOW |
| 35 | Lease contention | Is parallel work actually safe? | goals claimed by 2+ actors; expired leases | NOW |
| 36 | Parallelism yield | Is `parallel.enabled` paying for itself? | actual concurrency vs planned waves | DET |
| 37 | Ownership concentration | Bus factor | goals per area per actor vs CODEOWNERS | NOW |
| 38 | Cross-area coupling | Architecture or people? | share of goals needing a handoff, trend | NOW |

#33/#34 exist because `handoff_states()` already distinguishes "nobody replied" from "someone took it", and
`deferred` deliberately does not settle a handoff. That distinction is already correct in the code and the
dashboard must not flatten it — *"outstanding: 7"* is unactionable; *"3 nobody has looked at, 4 deferred >14d"*
is two different conversations.

### Layer 4 — Leadership

| # | Metric | Question | ⚙ |
|---|---|---|---|
| 39 | DX Core-4 rollup | Speed (#1 per engineer) · Quality (#5) · Impact (#9, % new capability) · **Effectiveness = declared gap** | mixed |
| 40 | Cost per project per week; $/landed-goal trend | Unit economics at portfolio altitude | NOW (actor-day) → AGT |
| 41 | Portfolio table | projects × throughput × park rate × gate coverage, one row each | NOW |
| 42 | Adoption & flag correlation | Which flags correlate with which outcomes? | NOW — `config.json` is committed |

**Effectiveness is an honest hole.** DX Core-4's Effectiveness dimension is the DXI, a 14-item Likert survey.
We have no survey. v1 shows a **labelled proxy** (flow efficiency + intervention rate) and never calls it a
DXI. Fabricating a survey score would poison the one thing this product sells, which is that the numbers are
real. A survey surface is a candidate for a later spec, not a shim.

#42 is also the internal payoff: it tells *you* which LoopSmith features correlate with good outcomes, and
therefore which to keep.

### Guardrails — binding, not advisory

* No metric renders at **individual grain** in the manager or leadership views, with two deliberate exceptions:
  **aging WIP (#10)** and **handoff response (#32)** — both are about unblocking a person, not ranking one.
* The IC view shows an individual **only their own** data.
* Every throughput metric is rendered adjacent to a quality counterweight (#1 next to #5, #12 next to #24).
  DX Core-4's own rule: a throughput metric shipped alone gets gamed.
* No metric is exported into a performance-review surface. This is a product constraint; it is what keeps the
  tool from being read as surveillance, which is the failure mode that kills adoption.

### v1 cut

Everything marked `NOW` — metrics **1,2,3,4,5,7,9,10,11,12,13,14,20,24,26,30,31,32,33,34,35,37,38,41,42**.
That is **25** metrics with **zero new instrumentation** — a real product on its own: burndown, forecast, WIP
and aging, the handoff graph, debt trend, change-failure, gate coverage, adoption.

Four of those (#5, #20, #24, and the Consistency gap class) only became `NOW` on the 0.9.17 rebase, because
`alignment-collect.sh` already measures them deterministically against real commits. They ship **labelled as
proxies** — `plan_existed_pct` is per-commit, not per-goal, and `repeated_revert_or_fixup_count` infers
failure from git shape rather than from a declared link. The emitter upgrades each from proxy to exact; it
does not *create* them. (#26 ships as the current-state tile only; its trend is tranche 2.)

Tranche 2 lands the emitter and adds the wedge (#15,16,17,22,23,27,29) plus exactness for the four proxies.

**#40 (cost per project/week) is deliberately held back** despite being partly `NOW`: its only v1 source is the
Claude Code Analytics API, which needs an org Admin key and is unavailable on Bedrock / Foundry / Vertex / AWS
(§11). A headline cost tile that is blank for a large fraction of adopters is worse than no tile; it lands with
the emitter, which works everywhere.

**Implementation slicing.** This spec is one design but roughly four plans: (i) store + ingest + the `NOW`
metrics, (ii) the dashboard shell over them, (iii) the emitter and its guard, (iv) the gap engine. (i) and (ii)
deliver value with no change to `sdlc-kit` at all — worth doing first for that reason alone.

### Cold start — stated plainly

`ledger.enabled` is **false** by default and `.sdlc/state/` is gitignored. So on day zero a fresh adopter has:
goal frontmatter, committed plans/slices, `config.json`, git, GitHub — **and every deterministic collector,
which needs nothing turned on** (§B.3.1). That yields metrics **3, 4, 5, 9, 20, 24, 30, 42** plus the
Consistency and Coverage gap classes. Materially better than the pre-rebase answer of five, and it means a
drop-in repo sees real gate-coverage and change-failure numbers before adopting anything. (#37 needs the
ledger's `area` field to attribute goals to actors — CODEOWNERS alone gives the roster, not the concentration.)

The dashboard's empty state must therefore be an **onboarding surface, not a zero**: "Throughput, cycle time
and WIP need the team ledger — turn it on with one config change and one `sync.py bootstrap`. Here is what you
*can* see today." A dashboard that shows `0` where it means `not measured` is the same lie as `absent` folded
into `pass`.

---

## 7. Subsystem C.2 — the gap engine

A gap is a **typed, evidenced rule**, never an LLM impression. Five classes, using
[`pipeline.py`](../../../skills/sdlc-loop/scripts/pipeline.py)'s existing `PASS/WARN/FAIL/ABSENT` vocabulary
and severity order rather than a parallel one:

| Class | Rule | Evidence rendered |
|---|---|---|
| **Coverage** | an applicable gate is `ABSENT` (never `PASS`); verify reports `NO-COMMAND`; a required review input was missing; **any collector pack carries a `degraded[]` code** (§B.3.1) | which goals, which gate, which input was absent, which degradation code |
| **Definition** | goal lacks `done_when`; no plan artifact under `.sdlc/plans/`; no `verify_command` and no `verify.command` | the goals, the missing field |
| **Threshold** | a metric crosses **its own trailing p85**, not a hardcoded constant | the series, the derived baseline, the breach |
| **Consistency** | two sources disagree: ledger `done` vs PR still open; **verify passed but no test file in the diff — `alignment-collect` d2 `tests_touched_with_source_pct` measures exactly this today**; slices declared vs files touched — **d1 `files_changed_outside_any_plan`** | both records, side by side |
| **Debt** | `discovery-scan` / radar inventory rising; `knowledge/gaps.md` queries unanswered | file, count, trend |

Three properties inherited deliberately from `pipeline.py`:

1. **No instrument ⇒ ABSENT, never PASS.** A gap engine that cannot tell "checked and fine" from "never
   checked" is worse than none.
2. **Derived baselines, not magic numbers — but a percentile crossing is not an alert.** A threshold gap
   fires against the project's own trailing history, never a hardcoded "cycle time > 5 days", which is wrong
   for most teams and destroys trust on first render. **CORRECTED 2026-08-01, measured in #119:** the original
   rule here was a single crossing of the trailing p85, which is self-defeating — ~15% of any series exceeds
   its own p85 *by construction*, and over 500 trials per shape a healthy stationary project fired a false
   WARN 91–99% of the time (78–93% even with a derived materiality margin; a ≥40-point minimum-history cutoff
   still left 76.2%). The rule is now **k consecutive breaches, k=3** — 0.15³ ≈ 0.34% on a stationary series.
   k is a RUN LENGTH, not a magnitude: it encodes no domain expectation, so the property this rule was
   protecting survives. Sensitivity was never the problem — a 4× sustained step-up still fires, starting at
   its first regressed point; a 10,000× single spike correctly does *not* fire on its own — no run-length
   filter with k≥2 ever can, on one point — which is exactly what "never on a single crossing" means.
3. **`--compare` semantics.** Gaps diff run-over-run into `regressed / improved / still-failing`, and
   **still-failing is the recurrence signal — systemic, not incidental; it goes to the backlog, not to a
   one-off fix.** That sentence is already in `pipeline.py`'s docstring; the gap engine adopts it.

### Presentation

Never a bare red number. Every gap is a card: **what · the evidence rows · the metric it moved · the one
action.** Where the action is a config change, the card deep-links into the configuration UI with the field
pre-selected. That link is the seam that makes analytics and configuration one product rather than two.

### The LLM's role

**No LLM in the finding path.** Rules find gaps; an LLM may write the *narrative* over already-computed
evidence, and may propose a backlog goal via the existing `pipeline propose` contract (`status: proposed`,
inert until a human promotes it). This mirrors the repo's own rule that a judgment without evidence is not a
verdict — and it means a gap can be unit-tested.

---

## 8. Testing

| Layer | Test |
|---|---|
| Emitter | round-trip per kind; unknown kind/verdict raises; **fail-open** — a read-only `events/` dir must not raise; `render()`/`TEAM.md` byte-identical with an events stream present; privacy caps enforced; vocabulary-vs-call-site guard (§A.6) |
| Ingest | fixture repo → asserted row counts; **idempotence** (ingest twice = same rows); watermark resume; missing `gh`/API degrades without failing |
| Metrics | one fixture event stream + expected value per `metrics/<id>.sql`; a `NOW` metric must not read any `reliability_class=2` row |
| Gaps | one fixture per class; **`absent` must never render as `pass`** (regression test, phrased as the invariant); baselines derived not constant |
| Cold start | a repo with no ledger and no events produces the onboarding state, not zeros |

---

## 9. What comes next (sketch — separate specs)

### D — Analytics dashboards

Four routes, one metric layer. Each answers one persona's decision, and each is a **different chart set**, not
a filter:

* **IC** — my queue, what is blocked on me, my parks by class, my gate verdicts, my cost. Own data only.
* **Manager** — burndown + Monte Carlo band, WIP and aging, handoff graph, park taxonomy, review-cycle
  distribution.
* **Leadership** — DX Core-4 four-tile rollup (Effectiveness labelled a proxy), prevented-rework, $/goal,
  portfolio table.
* **Cross-functional** — gate coverage matrix (with `absent` distinct), risk-review hit rate, alignment drift,
  debt and test-gap inventory.

Open question for that spec: static-generated (code-first, versioned, deploys as files — matches this repo's
character and makes charts reviewable in a PR) versus a served app (interactive drill-down, needed once
hosted). Recommendation to be argued there: static for v1, because local-first plus a single DuckDB file makes
a served app pure overhead.

### E — Configuration UI

The load-bearing constraint: **`.sdlc/config.json` is a committed file and it is authoritative** — its own
header says *"this file wins on conflict"*. So the UI must not become a second source of truth.

* Source of truth stays git. The UI produces a validated `config.json` and writes it locally (local mode) or
  opens a PR (hosted mode). Never a database LoopSmith reads.
* The schema for the form is generated from **`config.json.tmpl` and its `_`-prefixed sibling keys**, which are
  already long-form prose explaining every flag, its default, its failure mode, and its off-switch. That is a
  documentation asset the kit already maintains and a hand-built form would immediately duplicate and drift
  from.
* It must surface the traps `/sdlc-doctor` already detects (relative `verify.command` path in a worktree;
  unpinned board `number`; `work` off; unmapped custom fields) **at the field**, not in a separate report.
* Deep-link targets from gap cards (§7).

---

## 10. Open questions for the next spec

0. ~~License for `insight/`~~ **Decided 2026-07-30: per-folder source-available (BUSL-style).**
   `insight/LICENSE` carries its own license; the plugin stays MIT; the repo README and `insight/` headers
   mark the boundary clearly. First file into `insight/` is the LICENSE. (Exact BUSL parameters — change
   date, additional-use grant — are a one-time legal wording pass at that moment, not an engineering
   blocker.)
1. **Change-failure linkage (#5, #6, #25).** Needs a convention — a `fixes: <goal-id>` frontmatter field, or
   revert detection from git, or both. Cheap to add, and three metrics depend on it.
2. **Survey surface for DXI (#39).** Real gap. Build a survey, buy DXI, or ship the labelled proxy
   permanently.
3. **Hosted promotion path.** The event schema is designed for it; the auth, tenancy and data-processing story
   are not designed at all and should not be until someone is paying.
4. **Retention.** Events are unbounded. Local-first tolerates that for a long time; a policy is needed before
   hosted.

---

## 11. Not verified end-to-end

Per this project's standing rule against untested support claims, the following are **designed, not proven**,
and must not be documented as working until run in a real target environment:

* DuckDB `read_json_auto` over the ops-branch worktree on Windows paths.
* Claude Code Analytics API ingest — requires an Admin API key and an org account; unavailable on Bedrock,
  Foundry, Vertex, and Claude Platform on AWS, so it must be optional and degrade silently.
* The agent-emitted event path's real-world coverage rate. The whole coverage-denominator design exists
  *because* this number is unknown; the first dogfood run measures it.
* Marketplace subdirectory `source` scoping (§1.1 rule 3's mitigation if install weight bloats) — supported
  per the marketplace schema, never exercised by this repo.
