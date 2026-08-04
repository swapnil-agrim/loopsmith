# LoopSmith Insight — the deployed web application

**Date:** 2026-08-04
**Status:** approved (author, in conversation)
**Supersedes:** the persona-page work in `insight/dash/` (issues #265, #277, #279–#281, #283, all closed not-planned)

---

## 1. Why this spec exists, and what it corrects

Insight was built as a **static HTML generator**: `insight dash` renders self-contained files that
open over `file://`. Every design constraint followed from that — zero dependencies, no network
request, everything inlined.

That constraint was inherited from the wrong place. The **plugin** must install into arbitrary
user repositories, where a dependency tree is real friction and zero-dep is a genuine feature. The
**dashboard** is a different product with different economics: it is installed once, on one server,
by someone who expects to run an installer. Carrying the plugin's constraint across that boundary
produced a Python program rendering HTML strings, which is not the right tool for an enterprise web
interface and was never going to feel like a product.

The two are separate deliverables and this spec treats them that way.

### 1.1 The mistake this spec must not repeat

The previous dashboard design spec failed in a specific, instructive way. Its acceptance clauses
were **plumbing** — "zero hex literals", "AA contrast", "no horizontal scroll". All were satisfiable
by building a token module and embedding two fonts, which is exactly what the loop did. Every clause
passed and the pages remained undesigned, because tokens are not a design system; composition is.

Worse, the tokens shipped **broken and unnoticed**: `panel.py` requested `font-family:'Atkinson'`
while the `@font-face` rule declared `"Atkinson Hyperlegible"`, so the embedded typefaces never
applied. The check that should have caught it (`document.fonts.size == 2`) counted *declared* faces,
not *applied* ones — a test that could not fail.

**Every story in §8 therefore states its acceptance in terms of observable behaviour or
composition, never in terms of a token, a lint rule, or a file existing.** Where a clause could be
satisfied by something inert, it is rewritten.

---

## 2. The three tiers

| tier | responsibility | technology | licence |
|---|---|---|---|
| **Engine** | LoopSmith running on N machines: runs goals, writes the append-only ledger and telemetry stream | Python plugin, **zero-dep** | MIT |
| **Analytics core** | ingest → DuckDB; the 34 metric SQL files; the gap engine; reliability classes | Python + DuckDB | BUSL-1.1 |
| **Web app** | login, roles, dashboards, charts | **Next.js + TypeScript** | BUSL-1.1 |

The analytics core is not rewritten. It carries 1,125 passing tests, and the metric definitions are
SQL files rather than code precisely so the execution engine can be swapped later without touching
them. The web tier consumes it over HTTP and **never reimplements any part of it**.

---

## 2.1 Independence: Insight must be extractable to its own repository

**Requirement (author, 2026-08-04):** LoopSmith Insight and LoopSmith Core are separate entities.
Insight will be lifted into its own repository later, so nothing may exist that would prevent
`git mv insight/ ../loopsmith-insight/` from producing a working, fully-tested project.

### 2.1.1 What is already clean

Verified, not assumed:

* **Neither imports the other.** `tests/test_import_boundary.py` enforces both directions by
  parsing every file with `ast` — not grep, so an alias or a docstring mention cannot fool it. Its
  own docstring states the rule this requirement depends on: *"THE ALLOWED COUPLING IS FILE FORMATS
  ONLY."*
* `insight/` imports nothing outside the standard library except `duckdb`.
* `insight/pyproject.toml` already declares a **separate distribution**, `loopsmith-insight`. There
  is no root `pyproject.toml` — the plugin is not a Python distribution at all.

The Python code boundary is therefore already correct and does not need work.

### 2.1.2 What would actually break, and why imports are the wrong thing to look at

Two guarantees are enforced today **only because both projects sit in one checkout**. Neither is an
import, so no import checker will ever flag them, and both fail *silently* on extraction:

1. **`tests/test_git_reader_velocity_parity.py`** proves `insight.ingest.git_reader.measure_window()`
   returns the same measurement as the plugin's `skills/sdlc-velocity/scripts/velocity.py`. Two
   independent implementations, held in agreement by a test that needs both trees present. After
   extraction the test cannot run, and the two drift apart with green suites on both sides.
2. **`tests/test_vocabulary_coverage.py`** ties the plugin's `ledger.EVENT_KINDS` to its emitting
   call sites — and `insight/ingest` reads exactly those kinds. Rename a kind in the engine and
   Insight goes quietly blind to it; nothing on either side fails.

Three further items are scaffolding rather than semantics: one `ci.yml` holding both jobs, the root
`LICENSE`/`README` carve-out, and `.sdlc/config.json`'s two-suite verify command.

### 2.1.3 The mechanism: a versioned data contract plus a standalone proof

The engine→product coupling is *supposed* to exist — a JSONL file format is the product's input.
The defect is that it is currently enforced by **co-location** instead of by a contract. So:

* The formats Insight consumes (ledger entries, the telemetry event stream, goal frontmatter,
  `config.json`, `state/*`) are **documented and versioned**, with **golden fixture files committed
  inside `insight/`**.
* The cross-repo tests above are replaced by **contract tests that run independently on each side
  against those same fixtures**. The engine asserts it still *writes* the contract; Insight asserts
  it still *reads* it. Neither needs the other's source, so both survive extraction, and a
  breaking engine change fails the engine's own suite.
* An unknown field must remain ignorable by an older reader — `ledger.py`'s existing contract —
  so the format can evolve without lockstep releases.

And the proof that makes this real rather than aspirational: **a test that copies `insight/` alone
into a temporary directory, with no plugin present, and runs its entire suite there.** If that
passes, extractability is demonstrated on every CI run instead of being discovered the day someone
tries it (E15.S3).

### 2.1.4 Binding on all new work in this spec

Everything the web application adds — `insight/api/`, `insight/web/`, the Compose file, the
Dockerfiles, the seed data — **lives under `insight/`**. Nothing lands at the repository root.
`insight/web/` gets a BUSL marker convention for `.ts`/`.tsx`, and the import-boundary checker is
extended to both new directories so the AST guard keeps covering them.

---

## 3. The central design decision: absence enforced by the type system

Insight's one defensible claim is **ABSENT ≠ PASS** — an unmeasured metric must never be readable
as a healthy one.

Today that claim rests on developer discipline, and the record shows discipline is not enough. The
panel shipped a literal `0` for "goals landed" against an empty store — *we shipped nothing* where
the truth was *nothing has been ingested*. It survived review because every test used a populated
store, so the absence branch never executed. Absence then turned out to have **three** distinct
shapes in the store, each independently mishandled: a missing view, a view returning zero rows, and
a row whose value is NULL.

The web tier moves the guarantee from discipline into the compiler. The API never returns a bare
number:

```ts
type Coverage = { numerator: number; denominator: number }

type Metric = {
  id: number
  label: string
  reliabilityClass: 1 | 2
} & (
  | { state: "measured";       value: number; coverage: Coverage }
  | { state: "absent_no_data"; value?: never; reason: string }
  | { state: "absent_unbuilt"; value?: never; reason: string }
)
```

Because this is a discriminated union with `value?: never` on the absent arms, a component that
reaches for `metric.value` without first narrowing on `state` **does not compile**. Rendering a
zero where nothing was measured stops being a bug that review might catch and becomes a build
failure.

Two consequences are binding on every story below:

1. **The frontend computes no metric.** Ever. Percentages, ratios, rollups and thresholds are
   computed in SQL or in the API and arrive as values with their coverage attached. Two
   implementations of this doctrine would diverge, and the divergence is the single bug that would
   discredit the product.
2. **The TypeScript types are generated from the API's OpenAPI schema, never hand-written**, so the
   contract cannot drift silently (§8, E16.S3).

### 3.1 The two absence reasons stay distinct

`absent_no_data` (the metric's SQL ran and returned nothing — time and usage fix this) and
`absent_unbuilt` (no SQL file exists — only writing the metric fixes this) demand different actions
from a reader and are rendered differently. Collapsing them is how `lead time` looked fine for a
week.

---

## 4. Stack, with reasons

**Next.js (App Router) + TypeScript.** Server Components matter here beyond ergonomics: data
fetching and filtering happen on the server, so per-user data never has to reach the browser to be
hidden (§5.2).

**Auth.js (NextAuth v5)** with a Credentials provider. Chosen over a hand-rolled session layer
because swapping to **OIDC/SAML is a provider change, not a rewrite**. Enterprise SSO is the
obvious next request and this keeps the door open.

**visx** for charts — not Recharts, Chart.js, or Nivo. This is deliberate and is the one place a
popular default is wrong: **no charting library has a concept of "absent."** Given a gap in a
series, every one of them draws either a break in the line or nothing, which is visually
indistinguishable from zero — the exact confusion this product exists to prevent. visx exposes D3
primitives as React components, so the achromatic hatched absence material is rendered by us, on
purpose, as a first-class mark.

**Tailwind + CSS custom properties** seeded from the existing `colors.PANEL` / `PANEL_ALPHA`
tokens. The instrument design language transfers; nothing is redesigned from zero.

**FastAPI**, deliberately thin — a transport over the analytics core, with Pydantic models giving
the §3 contract runtime teeth on the Python side.

**Docker Compose**: `web` + `api` + a volume holding the DuckDB file. One `docker compose up`.

### 4.1 Rejected: Next.js reading DuckDB directly

Tempting (one runtime) and rejected. The metric layer is 34 SQL files plus the gap engine plus
reliability-class logic, all covered by tests. Re-homing it into TypeScript means either duplicating
it — fatal, per §3 — or a large, risky port of the one part of this product that already works.

---

## 5. Security model

Scope for this release: **single host, localhost, plain HTTP.** That is honest for a machine-local
deployment and stated plainly rather than dressed up. LAN or public exposure requires HTTPS and a
hardening pass, filed as its own epic and **not** smuggled in here (§9).

### 5.1 Authentication

Demo-grade in *surface*, correct in *construction*: accounts seeded from a config file; no signup,
no password reset, no admin UI. Passwords hashed with a memory-hard KDF (argon2id, or scrypt via
stdlib), verified in constant time. Sessions are server-side; cookies are `HttpOnly`, `SameSite=Lax`,
and `Secure` when served over TLS. Failed attempts are throttled per account.

### 5.1.1 Where the session lives, and how the API is protected

Stated explicitly because "Auth.js *and* FastAPI" is otherwise ambiguous, and two plausible answers
would be implemented inconsistently across stories.

**The web tier owns authentication entirely.** Auth.js issues and validates the session; the
browser talks only to Next.js. **The API is never published to the host** — in Compose it binds to
the internal network only, with no port mapping, so it is unreachable from the browser and from
other machines. Next.js Server Components and route handlers call it server-to-server.

This means the API does not authenticate end users, and that is a deliberate, bounded decision
resting on the API being unreachable except from the web container. Two obligations follow, and
neither is optional:

1. **The API is never given a published port** in any Compose file. A test asserts the rendered
   Compose config exposes no port for the `api` service — this is the whole basis of the trust
   boundary, so it is verified, not assumed.
2. **Authorization is still enforced in the web tier on every request** (§5.2). "The API is
   internal" justifies skipping *end-user authentication* at the API; it justifies nothing about
   *authorization*, which remains per-request and per-role.

When the non-goals in §9 are taken up — LAN exposure, multi-machine collection — this boundary is
the first thing that must change, because both make the API reachable. That is called out here so
the decision is revisited deliberately rather than inherited by accident.

### 5.2 Authorization — the invariant that constrains the architecture

`insight/tests/test_dash_ic_no_leak.py` asserts on the **whole rendered HTML string**, not the
parsed payload, and says why: the data is inlined in a `<script type="application/json">` block that
is readable via View Source whether or not any JS parses it. That instinct is correct and must
survive the port.

Under multi-user login it hardens into three rules, each with adversarial tests:

1. **The actor is resolved from the session and nowhere else.** An IC requesting
   `/ic?actor=someone-else` receives *their own* data; the parameter is never consulted. Without
   this, adding login makes the product less safe than the static build it replaced.
2. **The role × route matrix is asserted exhaustively** — every role against every route — so a
   newly added route cannot default to reachable.
3. **Leadership still cannot reach individual-grain data**, enforced where it already is (in the
   query/renderer), not newly in the router. A shared shell must never become a shared data path.

Filtering happens server-side, before HTML exists. "Send everything, hide it in the client" is
prohibited, and rule 1's tests are written to catch it.

---

## 6. What survives, what is retired

**Survives unchanged:** the engine; ingest; all 34 metric SQL files; the gap engine; reliability
classes; the fixtures and the 1,125 tests.

**Survives, repurposed:** `colors.PANEL` and `PANEL_ALPHA` become the web app's CSS custom
properties. `insight/dash/panel.py` stays as the **offline artifact** — `insight dash` still emits a
self-contained file needing no server, which is genuinely useful for sharing a snapshot and for CI.

**Retired:** the four persona page modules' *presentation* role (`manager.py`, `leadership.py`,
`ic.py`, `cross_functional.py`). Their **queries and guardrails move to the API**, where the tests
that prove the leak invariants move with them. The HTML rendering is dropped.

---

## 7. Repository layout

```
insight/                    <- the entire extraction unit; `git mv insight/ ../` must just work
  api/                      FastAPI service (BUSL) — thin transport over the analytics core
  web/                      Next.js application (BUSL)
  dash/                     retained: panel.py (offline artifact) + colors.py (token source)
  metrics/                  unchanged — 34 .sql files
  gaps/        ingest/       unchanged
  contract/                 versioned format docs + golden fixtures (E15.S4)
  docker-compose.yml        NOT at the repository root — see §2.1.4
  Dockerfile.web  Dockerfile.api
  pyproject.toml            already a separate distribution: loopsmith-insight
```

Nothing this spec adds may land at the repository root. Anything that does is, by definition, a
thing that will not travel when `insight/` is extracted.

The BUSL header marker and the plugin/insight import-boundary tests extend to both new
directories; `insight/web/` needs a marker convention for `.ts`/`.tsx` (E15.S2).

---

## 8. Epics and stories

Ordered so each depends only on what precedes it. The loop claims goals in **issue-number order**,
so issues are created in exactly this sequence.

### E15 — The gate and extractability, first (4 stories) — BLOCKING

Nothing else may land before this. The verify gate is currently `pytest tests/ && pytest
insight/tests/`, and CI gates `main` on four Python jobs. A Next.js app merged today would pass all
four while being entirely untested — the same shape as the stale-head defect that previously shipped
bugs to protected `main` past four green checks. §2.1's independence requirement also has to bind
*before* new directories exist, not be retrofitted across 20 stories of web code.

- **S1** — Extend the verify gate and CI with a web job: type-check (`tsc --noEmit`), lint, unit
  tests, and a production build. *Done when* a deliberately broken `.tsx` — a type error and a
  failing test, committed on a scratch branch — **fails** the gate. A gate that has never been seen
  to fail is not a gate.
- **S2** — Repo layout under `insight/` only (§2.1.4), BUSL markers for `.ts`/`.tsx`, and the AST
  import-boundary checker extended to `insight/api/` and `insight/web/`. *Done when* a test proves a
  plugin file importing from `insight/` fails **and** that the new directories are actually walked
  by the checker — a guard that skips the new tree is worse than none, because it reads as covered.
- **S3** — **The standalone-extraction proof** (§2.1.3). A test copies `insight/` alone into a
  temporary directory, with no plugin tree present, and runs its full suite there. *Done when* that
  test passes in CI **and** is shown to fail when `insight/` is made to depend on something outside
  itself. This is what converts "extractable" from a claim into a fact checked on every run.
- **S4** — **Freeze the engine↔product data contract** (§2.1.3). Document and version the ledger,
  telemetry-event, goal-frontmatter and `config.json` formats Insight reads; commit golden fixtures
  under `insight/`; replace `test_git_reader_velocity_parity.py` and the Insight-facing half of
  `test_vocabulary_coverage.py` with contract tests that run **independently on each side** against
  those fixtures. *Done when* renaming an event kind in the engine fails the **engine's own** suite,
  with no reference to `insight/` anywhere in the failing test.

### E16 — The API contract (3 stories)

- **S1** — FastAPI skeleton, `/health`, read-only DuckDB access reusing `insight.ingest.store`.
- **S2** — **The metric contract.** Pydantic models encoding §3's union. *Done when* constructing an
  absent metric carrying a `value` raises, and a cold-start store yields every metric in an absent
  state with **no numeral anywhere in the response body**.
- **S3** — OpenAPI → generated TypeScript types, wired into the build. *Done when* changing a field
  name in a Pydantic model and regenerating **breaks the frontend type-check**.

### E17 — Web foundation (4 stories)

- **S1** — Next.js + TypeScript + Tailwind scaffold, containerised.
- **S2** — Design tokens ported from `colors.PANEL`; Atkinson Hyperlegible + IBM Plex Mono
  self-hosted. *Done when* a test asserts the computed font-family of rendered text **resolves to
  the embedded face** — not merely that a `@font-face` rule exists. This clause exists because that
  exact bug shipped.
- **S3** — Absence primitives as React components. *Done when* a component reaching `metric.value`
  without narrowing on `state` fails `tsc`, proven by a compile-failure fixture.
- **S4** — App shell: masthead, role-aware nav, responsive frame at 1440/1024/768.

### E18 — Authentication (3 stories)

- **S1** — Auth.js Credentials provider, argon2id/scrypt password store, `insight users add` CLI.
- **S2** — Login page on the instrument language; middleware protecting **every** route by default.
  *Done when* an unauthenticated request to each route redirects to login — asserted route by route,
  not by sampling.
- **S3** — Logout, session expiry, per-account failed-attempt throttling.

### E19 — Authorization (3 stories)

- **S1** — Role → route matrix, enforced server-side; 403 page.
- **S2** — **Session-bound actor** (§5.2 rule 1). *Done when* an authenticated IC requesting another
  actor's data receives their own, and a test proves the other actor's identifiers appear **nowhere
  in the response body**.
- **S3** — Exhaustive role × route matrix tests; nav renders only reachable views.

### E20 — Dashboards (5 stories)

Delivery panel · Manager · Leadership · IC · Cross-functional. Each *done when* its page composes
from E17's primitives, every numeral carries its coverage denominator, and **a cold-start test
proves no readout renders a numeral against an empty store.** That last clause is mandatory on every
one: the `0`-goals-landed bug shipped precisely because every test used a populated store.

Leadership additionally keeps its zero-individual-grain guarantee; IC keeps its no-cross-actor-leak
guarantee, asserted on the whole response.

### E21 — Interactive charts (2 stories)

- **S1** — visx primitives carrying the absence material: a gap in a series renders as visibly
  absent, never as a break that could read as zero. *Done when* a series containing an absent point
  is distinguishable from one containing a zero, in greyscale.
- **S2** — Tooltips, crosshair, drill-down, filtering.

### E22 — Ship (2 stories)

- **S1** — Docker Compose (`web` + `api` + volume), seed/demo data path. *Done when* a test asserts
  the rendered Compose config publishes **no port for the `api` service** (§5.1.1 obligation 1) —
  the entire trust boundary rests on that, so it is verified rather than assumed.
- **S2** — Error and empty states; run documentation.

**26 stories.** At this repository's measured median of 1.26 h/goal, roughly 33 hours of loop time.

---

## 9. Non-goals

Explicitly out of scope, to be filed separately rather than absorbed:

- **HTTPS, LAN and public exposure hardening.** This release is localhost/plain-HTTP by decision.
- **Multi-machine collection.** The engine runs on N machines; ingest today reads local files and
  git. Centralised collection is a real epic and is sequenced after this one.
- **SSO/OIDC.** The Auth.js choice keeps this cheap later; it is not built now.
- **Multi-tenancy**, and the DuckDB → ClickHouse/Postgres swap it would motivate.
- **Signup, password reset, admin user management.**

---

## 10. Risks

| risk | mitigation |
|---|---|
| The loop merges broken TypeScript past green Python checks | E15.S1 is blocking and must demonstrate a *failing* gate before anything else lands |
| Metric logic drifts into the frontend | §3 rule 1; types generated from OpenAPI (E16.S3); no arithmetic in components |
| A leak invariant is lost in the port | The existing leak tests move to the API *with* the queries; E19.S2 asserts on whole response bodies |
| Acceptance clauses satisfiable by inert artifacts | Every clause in §8 is phrased as observable behaviour; several explicitly require seeing the check fail |
| Two frontends rot | `insight/dash/` is deliberately reduced to `panel.py` + `colors.py`; the persona renderers are retired, not maintained in parallel |
| Extraction breaks silently — the coupling is semantic, not an import, so no import checker sees it | E15.S3's standalone-suite proof runs every CI run; E15.S4 converts the two co-location-only guarantees into contract tests that run independently on each side |
| New web work quietly lands at the repo root and cannot travel | §2.1.4 is binding; E15.S2 lands before any web code exists, so the rule constrains all 20+ later stories rather than being retrofitted |
