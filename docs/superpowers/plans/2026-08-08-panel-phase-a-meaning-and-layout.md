# Panel Phase A — Meaning and Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every card on the delivery panel say what it means and what would fill its gap, and replace the 42-card wall with a summary-first layout — without introducing any health verdict yet.

**Architecture:** The metric `.sql` headers already carry `question`, `guardrail`, `proxy` and `data_status`; they are parsed today only by `_reliability_class()` and by tests. Phase A plumbs that existing metadata through the Pydantic models into the generated TypeScript, then rebuilds the panel around it: accent-A geometry (with `health` always absent), a derived gap hint on absent cards, a doughnut primitive, and a collapsed per-band board. No verdict logic ships here, which is deliberate — it makes it impossible for this phase to render a wrong verdict.

**Tech Stack:** Python 3.10+/Pydantic v2, DuckDB, FastAPI (contract only), Next.js 16 App Router, Tailwind v4, hand-written SVG (no chart library).

## Global Constraints

- **`insight/` must stay independently extractable.** No import of, or path reference to, anything outside `insight/`. `insight/tests/test_standalone_extraction.py` enforces this and must stay green.
- **ABSENT != PASS.** An absent metric renders no numeral, ever. `prove-delivery-cold-start-no-numerals.mjs` enforces this.
- **A measured card carries NO `background-image`.** The hatch is exclusive to absence; this is the negative control in `prove-absence-primitives-render.mjs` that keeps "absent states are hatched" non-vacuous. The accent must be a `::before` bar, never a background.
- **No raw hex outside tokens.** New colours go in `insight/dash/colors.py` and are regenerated into `tokens.generated.css`; `test_web_tokens_fresh.py` and `test_globals_css_theme_matches_colors_py.py` pin this. The `@theme` block accepts ONLY lines matching `--color-panel-X: var(--panel-X);`.
- **Contract changes are additive.** New model fields are `Optional` with a `None` default. After any model change: `python3 -m insight.api.export_openapi` then `node scripts/generate-schema.mjs` (cwd `insight/web`), and commit both.
- **`data_status: dark` and `proxy: true` live in the header's `extra` dict**, not as top-level parsed fields.
- Verification commands, run from repo root unless stated:
  - `python3 -m pytest -q insight/tests/`
  - `cd insight/web && npx tsc --noEmit && npx next build && npm test`
  - Browser proofs need `INSIGHT_DEV_ROUTES=1` **and** no `insight/web/.env.local` present (a pinned `AUTH_URL` there breaks their random-port servers).

---

### Task 1: Expose the SQL header metadata on every metric

**Files:**
- Modify: `insight/api/metrics.py` (add `_header_fields()`, use it in `resolve_metric`)
- Modify: `insight/api/models.py` (`MetricBase` gains four optional fields)
- Test: `insight/tests/test_api_metric_metadata.py` (create)

**Interfaces:**
- Consumes: `insight.metrics.header.parse_header(text, source)` → dict with `name`, `question`, `personas` (list), `reliability_class` (int), `guardrail`, plus `extra` (dict).
- Produces: `MetricBase.question: Optional[str]`, `.guardrail: Optional[str]`, `.proxy: bool`, `.dataStatus: Optional[str]`. Task 3 regenerates TS from these; Task 4 renders `question`.

- [ ] **Step 1: Write the failing test**

```python
# insight/tests/test_api_metric_metadata.py
"""Header metadata must reach the API. The `question` line is the card's meaning and already
exists, reviewed, in every .sql header -- it was simply never plumbed through."""
import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pydantic")

from insight.api.metrics import collect_metrics, resolve_metric  # noqa: E402

REAL = "insight/metrics"


def test_every_metric_with_sql_carries_its_question():
    metrics = collect_metrics(None, metrics_dir=REAL)
    with_sql = [m for m in metrics if "exists yet" not in getattr(m, "reason", "")]
    assert with_sql, "fixture problem: no metric has a .sql file"
    for m in with_sql:
        assert m.question, f"metric {m.id} ({m.label}) has a .sql but no question line"
        assert m.question.endswith("?"), f"metric {m.id}: question must read as a question"


def test_dark_and_proxy_flags_are_surfaced():
    dark = resolve_metric(None, 12, metrics_dir=REAL)     # data_status: dark
    proxy = resolve_metric(None, 20, metrics_dir=REAL)    # proxy: true
    clean = resolve_metric(None, 3, metrics_dir=REAL)     # neither
    assert dark.dataStatus == "dark"
    assert dark.proxy is False
    assert proxy.proxy is True
    assert clean.dataStatus is None and clean.proxy is False


def test_a_metric_with_no_sql_has_no_metadata_rather_than_invented_metadata():
    """id 6 has no 6.sql. It must report None, never a placeholder question."""
    m = resolve_metric(None, 6, metrics_dir=REAL)
    assert m.question is None and m.guardrail is None and m.proxy is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q insight/tests/test_api_metric_metadata.py`
Expected: FAIL — `AttributeError: 'AbsentUnbuiltMetric' object has no attribute 'question'`

- [ ] **Step 3: Add the model fields**

In `insight/api/models.py`, inside `class MetricBase`, after the existing fields:

```python
    # Straight from the metric's own `.sql` header (insight/metrics/header.py). These are the
    # card's MEANING -- `question` is a reviewed, one-line plain-English statement of what the
    # metric answers ("How long does a goal take?") that already existed in every header and was
    # parsed only by tests. Optional because a catalog id with no `.sql` has no header to read,
    # and inventing a question for it would be fabricating documentation.
    question: Optional[str] = None
    guardrail: Optional[str] = None
    # `proxy` is a bool, not Optional[bool]: "is this an approximation?" always has an answer,
    # and False is the honest default for a metric whose header does not claim otherwise.
    proxy: bool = False
    # `dark` today; left as a free string so a future header can add another status without a
    # model change. None means the header made no claim.
    dataStatus: Optional[str] = None
```

- [ ] **Step 4: Add the parser helper and wire it**

In `insight/api/metrics.py`, near `_reliability_class`:

```python
def _header_fields(mid, metrics_dir):
    """The metric's own header, or empty defaults when there is no `.sql` to read.

    Never raises: a malformed header degrades to "no metadata" exactly as a missing file does.
    A header problem must not be able to take down a metric that otherwise resolves fine --
    the metadata is context, and context failing closed to absent is the house rule."""
    sql_path = pathlib.Path(metrics_dir) / f"{mid}.sql"
    if not sql_path.exists():
        return {}
    try:
        parsed = parse_header(sql_path.read_text(encoding="utf-8"), source=str(sql_path))
    except Exception:
        return {}
    extra = parsed.get("extra") or {}
    return {
        "question": parsed.get("question") or None,
        "guardrail": parsed.get("guardrail") or None,
        # The header convention is the literal string "true" (issue #110); anything else,
        # including absence, is False.
        "proxy": str(extra.get("proxy", "")).strip().lower() == "true",
        "dataStatus": (extra.get("data_status") or "").strip() or None,
    }
```

Add `from insight.metrics.header import parse_header` to the imports at the top of the file.

Then in `resolve_metric`, immediately after `sql_path = metrics_dir / f"{mid}.sql"`:

```python
    header = _header_fields(mid, metrics_dir)
```

and spread `**header` into **every one of the five** `AbsentUnbuiltMetric(...)`, `AbsentNoDataMetric(...)` and `MeasuredMetric(...)` constructor calls in the function, e.g.:

```python
        return AbsentUnbuiltMetric(
            id=mid, label=label, reliabilityClass=reliability_class, **header,
            state="absent_unbuilt",
            reason=f"no {sql_path.name} exists yet -- only a code change can build this metric",
        )
```

> The no-`.sql` branch returns before `header` would be non-empty; `**{}` is a no-op there, so it is
> still correct to spread it for uniformity rather than special-casing one branch.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest -q insight/tests/test_api_metric_metadata.py`
Expected: PASS (3 passed)

- [ ] **Step 6: Run the full Python suite**

Run: `python3 -m pytest -q insight/tests/`
Expected: all pass. If `test_api_metrics_route.py` fails on an exact-payload comparison, update it to expect the new keys — do not remove the assertion.

- [ ] **Step 7: Commit**

```bash
git add insight/api/models.py insight/api/metrics.py insight/tests/test_api_metric_metadata.py
git commit -m "feat(insight): surface each metric's own question, guardrail, proxy and data_status

The .sql headers have carried a reviewed one-line `question:` for every metric since the
catalog was written, plus a `guardrail:` naming what the metric CANNOT tell you. Both were
parsed only by tests. The delivery panel could not say what a card meant because the API
never carried the sentence that says it."
```

---

### Task 2: Derive a gap hint for absent metrics

**Files:**
- Modify: `insight/api/models.py` (`AbsentUnbuiltMetric.gapHint`)
- Modify: `insight/api/metrics.py` (`_gap_hint()`)
- Test: `insight/tests/test_api_gap_hint.py` (create)

**Interfaces:**
- Consumes: Task 1's `_header_fields`; the live `conn`.
- Produces: `AbsentUnbuiltMetric.gapHint: Optional[str]`, rendered by Task 4.

- [ ] **Step 1: Write the failing test**

```python
# insight/tests/test_api_gap_hint.py
"""An absent card should say what would fill it. "Not implemented" is not actionable; "2 rows
are already waiting in metric_4" is. The three hints are derived from the store, never authored
per-metric, so they cannot go stale as data arrives."""
import pytest

duckdb = pytest.importorskip("duckdb")
pytest.importorskip("pydantic")

from insight.api.metrics import resolve_metric  # noqa: E402

REAL = "insight/metrics"


def test_no_sql_says_the_metric_is_unwritten():
    m = resolve_metric(None, 6, metrics_dir=REAL)
    assert "No SQL" in m.gapHint


def test_sql_with_rows_waiting_says_how_many_and_where(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_7 AS SELECT * FROM (VALUES (1),(2)) AS t(wip_count)")
    m = resolve_metric(conn, 7, metrics_dir=REAL)
    conn.close()
    assert "2 rows" in m.gapHint and "metric_7" in m.gapHint


def test_sql_with_an_empty_view_says_it_needs_data_not_code(tmp_path):
    conn = duckdb.connect(str(tmp_path / "s.duckdb"))
    conn.execute("CREATE VIEW metric_7 AS SELECT 1 AS wip_count WHERE false")
    m = resolve_metric(conn, 7, metrics_dir=REAL)
    conn.close()
    assert "needs data" in m.gapHint.lower()
    assert "rows are already waiting" not in m.gapHint


def test_gap_hint_never_claims_rows_it_cannot_count(tmp_path):
    """No store at all: the hint must not assert anything about row counts."""
    m = resolve_metric(None, 7, metrics_dir=REAL)
    assert "rows are already waiting" not in (m.gapHint or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q insight/tests/test_api_gap_hint.py`
Expected: FAIL — `AttributeError: ... has no attribute 'gapHint'`

- [ ] **Step 3: Add the field**

In `insight/api/models.py`, inside `class AbsentUnbuiltMetric`:

```python
    # What would actually fill this gap, derived from the store rather than authored per metric.
    # "No extractor registered" tells a reader nothing they can act on; "2 rows are already
    # waiting in metric_4" tells them the data has already arrived and only wiring is missing.
    gapHint: Optional[str] = None
```

- [ ] **Step 4: Implement `_gap_hint`**

In `insight/api/metrics.py`:

```python
def _gap_hint(conn, mid, sql_exists):
    """One sentence naming the next real step for an unbuilt metric.

    Three cases, distinguished by evidence and never guessed:
      * no `.sql`            -> the metric has not been written at all
      * `.sql` + rows        -> the data is already there; only an extractor is missing
      * `.sql` + empty view  -> code cannot help; this one is waiting on data

    The row count is only claimed when it can actually be counted. With no store (`conn is
    None`) or an unreadable view, the hint falls back to the weaker, still-true statement --
    asserting "0 rows are waiting" when we simply could not look would be the same class of
    fabrication as reporting a value we did not measure."""
    if not sql_exists:
        return "No SQL written for this metric yet."
    if conn is None:
        return "No extractor registered yet for this metric."
    try:
        rows = conn.execute("SELECT count(*) FROM metric_%d" % mid).fetchone()[0]
    except Exception:
        return "No extractor registered yet for this metric."
    if rows:
        return (
            "No extractor registered. %d row%s already waiting in metric_%d -- "
            "wiring one would surface it." % (rows, "s are" if rows != 1 else " is", mid)
        )
    return "SQL exists but the view is empty -- this one needs data, not code."
```

Then pass it at both `AbsentUnbuiltMetric` construction sites:

```python
            gapHint=_gap_hint(conn, mid, sql_path.exists()),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest -q insight/tests/test_api_gap_hint.py`
Expected: PASS (4 passed)

- [ ] **Step 6: Mutation-check the fallback**

Temporarily change the `except Exception:` branch to `return "0 rows are already waiting"`. Run the suite — `test_gap_hint_never_claims_rows_it_cannot_count` must FAIL. Revert.

Run: `python3 -m pytest -q insight/tests/test_api_gap_hint.py`
Expected after revert: PASS

- [ ] **Step 7: Commit**

```bash
git add insight/api/models.py insight/api/metrics.py insight/tests/test_api_gap_hint.py
git commit -m "feat(insight): tell an absent card what would actually fill it

Derived from the store, not authored per metric, so the hint cannot go stale as data lands.
Distinguishes the three real cases: no SQL written, SQL with rows already waiting for an
extractor, and SQL whose view is empty and therefore needs data rather than code -- the last
being 14 of the 36 currently mislabelled 'needs code'."
```

---

### Task 3: Regenerate the OpenAPI contract and TypeScript types

**Files:**
- Modify: `insight/web/openapi.json` (generated)
- Modify: `insight/web/src/lib/api/schema.d.ts` (generated)

**Interfaces:**
- Consumes: Tasks 1–2 model fields.
- Produces: `components["schemas"]["MeasuredMetric"]["question"]` etc., available to Task 4.

- [ ] **Step 1: Regenerate both artefacts**

```bash
python3 -m insight.api.export_openapi
cd insight/web && node scripts/generate-schema.mjs
```

- [ ] **Step 2: Verify freshness passes**

Run: `cd insight/web && node scripts/check-schema-fresh.mjs`
Expected: prints `fresh`

- [ ] **Step 3: Confirm the new fields landed**

Run: `grep -n "question\|gapHint\|dataStatus" insight/web/src/lib/api/schema.d.ts | head`
Expected: each appears at least once.

- [ ] **Step 4: Typecheck**

Run: `cd insight/web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add insight/web/openapi.json insight/web/src/lib/api/schema.d.ts
git commit -m "chore(insight): regenerate OpenAPI and TS types for the metric metadata fields"
```

---

### Task 4: Card anatomy — question line, gap hint, accent-A geometry

**Files:**
- Modify: `insight/web/src/app/globals.css` (accent rules)
- Modify: `insight/web/src/lib/metric-view.ts` (expose `question`, `gapHint`)
- Modify: `insight/web/src/components/Metric.tsx`
- Test: extends `insight/web/scripts/prove-absence-primitives-render.mjs` expectations (Task 8)

**Interfaces:**
- Consumes: Task 3's generated types.
- Produces: `describeMetric()` returns `question: string | null` and `gapHint: string | null`; `.panel-accent` CSS contract `data-verdict="ok"|"watch"|"breach"`, absent attribute = no accent.

- [ ] **Step 1: Add the accent tokens to colors.py**

In `insight/dash/colors.py`, add to the `PANEL` dict:

```python
    "ok": "#5ce0b0",
    "watch": "#e0b45c",
    "breach": "#e0715c",
```

Then regenerate and mirror into the `@theme` block:

```bash
python3 -c "from insight.dash.colors import web_tokens_css; \
open('insight/web/src/app/tokens.generated.css','w').write(web_tokens_css())"
```

Add these three lines to `globals.css`'s `@theme` block (they match the required shape exactly):

```css
  --color-panel-ok: var(--panel-ok);
  --color-panel-watch: var(--panel-watch);
  --color-panel-breach: var(--panel-breach);
```

Run: `python3 -m pytest -q insight/tests/test_web_tokens_fresh.py insight/tests/test_globals_css_theme_matches_colors_py.py`
Expected: PASS

- [ ] **Step 2: Add the accent geometry to globals.css**

Append inside the existing `@layer components` block:

```css
  /* Health accent (spec D6, option A): a 2px left edge fading downward.
     Driven ONLY by [data-verdict]; an element without that attribute gets NO accent at all --
     not a grey one. "No verdict" must be visually distinct from every verdict, not a fourth
     colour. Phase A never sets the attribute; the rules ship now so Phase C is a data change
     rather than a styling change. */
  .panel-accent { position: relative; }
  .panel-accent[data-verdict]::before {
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
  }
  .panel-accent[data-verdict="ok"]::before {
    background: linear-gradient(180deg, var(--panel-ok), transparent 85%);
  }
  .panel-accent[data-verdict="watch"]::before {
    background: linear-gradient(180deg, var(--panel-watch), transparent 85%);
  }
  .panel-accent[data-verdict="breach"]::before {
    background: linear-gradient(180deg, var(--panel-breach), transparent 85%);
  }
```

> `::before` and not a background: `prove-absence-primitives-render.mjs` asserts a measured card's
> computed `background-image` is `none`, which is the negative control keeping "absence is hatched"
> meaningful. An accent painted as a background would silently defeat it.

- [ ] **Step 3: Write the failing render expectation**

Add to `insight/web/src/lib/metric-view.ts`'s `DescribedMetric` interface:

```ts
  /** The metric's own one-line meaning, from its .sql header. Null when it has no header. */
  question: string | null;
  /** For an unbuilt metric only: what would actually fill this gap. */
  gapHint: string | null;
```

and return them from both arms of `describeMetric`:

```ts
    question: metric.question ?? null,
    gapHint: metric.state === "absent_unbuilt" ? (metric.gapHint ?? null) : null,
```

- [ ] **Step 4: Render them in Metric.tsx**

Add `panel-accent` to the root `className` (no `data-verdict` yet), and insert after the label row:

```tsx
      {d.question && (
        <p
          data-testid="metric-question"
          className="mt-2 text-panel-dim"
          style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.45, minHeight: "2.6em" }}
        >
          {d.question}
        </p>
      )}
```

and replace the existing `fixText` block with the gap hint when present:

```tsx
      {d.gapHint && (
        <p
          data-testid="metric-gap-hint"
          className="mt-1.5 text-panel-faint"
          style={{ fontSize: "var(--panel-text-caption)", lineHeight: 1.45 }}
        >
          {d.gapHint}
        </p>
      )}
```

Keep the existing `metric-fix` element — `prove-absence-primitives-render.mjs` requires exactly one, and removing it fails that proof.

- [ ] **Step 5: Build and eyeball**

```bash
cd insight/web && npx tsc --noEmit && npx next build
```
Expected: clean build.

- [ ] **Step 6: Commit**

```bash
git add insight/dash/colors.py insight/web/src/app/tokens.generated.css \
        insight/web/src/app/globals.css insight/web/src/lib/metric-view.ts \
        insight/web/src/components/Metric.tsx
git commit -m "feat(insight): every card states its own question, and gains accent-A geometry

The accent is driven solely by [data-verdict] and Phase A never sets it, so no verdict can be
rendered before the health model exists. Painted as ::before, never a background, so the
measured-carries-no-background-image negative control keeps its teeth."
```

---

### Task 5: Doughnut primitive

**Files:**
- Create: `insight/web/src/components/Doughnut.tsx`
- Test: `insight/web/scripts/prove-doughnut-is-part-of-whole.mjs` (create)

**Interfaces:**
- Produces: `<Doughnut numerator={n} denominator={d} label={string} caption={string} tone="neutral"|"ok"|"watch"|"breach" />`. Task 6 and Task 7 consume it.

- [ ] **Step 1: Write the failing proof**

```js
// insight/web/scripts/prove-doughnut-is-part-of-whole.mjs
// A doughnut asserts "these parts make a whole". Rendering one for a denominator of zero, or a
// numerator above the denominator, would draw a ring that means nothing -- so the component must
// refuse rather than clamp silently.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const SRC = readFileSync(path.join(WEB, "src", "components", "Doughnut.tsx"), "utf-8");

assert.ok(/denominator\s*<=\s*0/.test(SRC),
  "Doughnut must refuse a non-positive denominator rather than dividing by it");
assert.ok(/return null/.test(SRC),
  "the refusal must render nothing, not a zero-length ring that reads as a real measurement");
assert.ok(!/background-image|backgroundImage/.test(SRC),
  "the doughnut must not introduce a background-image (absence owns that channel)");
console.log("OK: Doughnut refuses a whole it cannot divide");
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd insight/web && node scripts/prove-doughnut-is-part-of-whole.mjs`
Expected: FAIL — `ENOENT ... Doughnut.tsx`

- [ ] **Step 3: Implement the component**

```tsx
// insight/web/src/components/Doughnut.tsx
// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// A doughnut is only honest for a part-to-whole. Every rate metric here IS one (47 of 52 goals),
// and so is panel coverage (6 of 42 instrumented). A duration or an unbounded count is NOT, and
// keeps its distribution chart -- see the chart vocabulary table in the Phase A spec.
//
// The ring's colour is a VERDICT, never a decoration: `tone` defaults to neutral and Phase A never
// passes anything else, so a dark metric's ring stays grey even when its number is large.
const CIRC = 100; // using r such that the circumference is 100 makes dasharray read as a percent

export function Doughnut({
  numerator, denominator, label, caption, tone = "neutral",
}: {
  numerator: number;
  denominator: number;
  label: string;
  caption?: string;
  tone?: "neutral" | "ok" | "watch" | "breach";
}) {
  // Refuse rather than clamp: a ring drawn from a whole we do not have is a fabricated
  // measurement, and silently clamping would hide the upstream bug that produced it.
  if (denominator <= 0 || numerator < 0 || numerator > denominator) return null;

  const pct = (numerator / denominator) * 100;
  const stroke =
    tone === "neutral" ? "var(--panel-void-ink)" : `var(--panel-${tone})`;

  return (
    <div className="flex items-center gap-4">
      <svg width="96" height="96" viewBox="0 0 42 42" role="img"
           aria-label={`${label}: ${numerator} of ${denominator}`} className="shrink-0">
        <circle cx="21" cy="21" r="15.9155" fill="none" stroke="var(--panel-void)" strokeWidth="4" />
        <circle cx="21" cy="21" r="15.9155" fill="none" stroke={stroke} strokeWidth="4"
                strokeDasharray={`${pct} ${CIRC - pct}`} strokeDashoffset="25" strokeLinecap="round" />
        <text x="21" y="20.6" textAnchor="middle" fill="var(--panel-bone)"
              fontSize="6.6" fontFamily="var(--panel-font-mono)">
          {pct.toFixed(1)}%
        </text>
        <text x="21" y="26" textAnchor="middle" fill="var(--panel-faint)"
              fontSize="3" fontFamily="var(--panel-font-mono)">
          {numerator} / {denominator}
        </text>
      </svg>
      {caption && (
        <p className="text-panel-dim" style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.5 }}>
          {caption}
        </p>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the proof and register it**

Run: `cd insight/web && node scripts/prove-doughnut-is-part-of-whole.mjs`
Expected: `OK: Doughnut refuses a whole it cannot divide`

Append ` && node scripts/prove-doughnut-is-part-of-whole.mjs` to the `test` script in `insight/web/package.json`.

- [ ] **Step 5: Commit**

```bash
git add insight/web/src/components/Doughnut.tsx \
        insight/web/scripts/prove-doughnut-is-part-of-whole.mjs insight/web/package.json
git commit -m "feat(insight): add a doughnut primitive that refuses a whole it cannot divide"
```

---

### Task 6: Collapsed band board

**Files:**
- Create: `insight/web/src/components/BandBoard.tsx`
- Modify: `insight/web/src/components/MetricCell.tsx` (accept `panel-accent`)

**Interfaces:**
- Consumes: `Metric[]`, the `BANDS` list currently inline in `delivery/page.tsx`.
- Produces: `<BandBoard metrics={Metric[]} bands={ReadonlyArray<{name, ids}>} />`. Task 7 consumes it.

- [ ] **Step 1: Implement the component**

```tsx
// insight/web/src/components/BandBoard.tsx
// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// The 42-cell wall, collapsed to one row per band. Density becomes opt-in: each row states what
// is instrumented in that band, and expands to the cells on click. NOTHING is removed -- the same
// 42 cells are one interaction away, which is the difference between decluttering and hiding.
//
// A Client Component only because <details> open state is interactive. It receives an
// already-resolved metric list and derives everything from it, so it cannot disagree with the
// integrity strip above it about what is measured.
"use client";

import type { Metric as MetricType } from "@/lib/api/metric";
import { MetricCell } from "./MetricCell";

export function BandBoard({
  metrics, bands,
}: {
  metrics: readonly MetricType[];
  bands: ReadonlyArray<{ name: string; ids: readonly number[] }>;
}) {
  const byId = new Map(metrics.map((m) => [m.id, m]));

  return (
    <div className="flex flex-col gap-2">
      {bands.map((band) => {
        const present = band.ids.map((id) => byId.get(id)).filter(Boolean) as MetricType[];
        const live = present.filter((m) => m.state === "measured").length;
        return (
          <details key={band.name} className="group rounded border border-panel-rule bg-panel-panel">
            <summary className="flex cursor-pointer list-none items-center gap-3.5 px-4 py-3">
              <span className="min-w-[9rem] shrink-0" style={{ fontSize: "var(--panel-text-body)" }}>
                {band.name}
              </span>
              <span className="flex flex-1 flex-wrap gap-[3px]" aria-hidden="true">
                {present.map((m) => (
                  <span
                    key={m.id}
                    title={`${m.label} — ${m.state === "measured" ? "measured" : "not measured"}`}
                    className="h-[7px] w-[14px] rounded-[2px]"
                    style={
                      m.state === "measured"
                        ? { background: "linear-gradient(180deg,var(--panel-cyan),var(--panel-cyan-deep))",
                            boxShadow: "0 0 8px var(--panel-glow)" }
                        : { background: "var(--panel-void)", boxShadow: "inset 0 0 0 1px var(--panel-void-edge)" }
                    }
                  />
                ))}
              </span>
              <span className="panel-num shrink-0 text-panel-dim"
                    style={{ fontSize: "var(--panel-text-caption)" }}>
                {live}/{band.ids.length}
              </span>
              <span aria-hidden="true" className="w-3 text-panel-faint"
                    style={{ fontSize: "var(--panel-text-caption)" }}>▸</span>
            </summary>
            <div className="flex flex-wrap gap-2 border-t border-panel-rule px-4 py-3.5">
              {present.map((m, i) => (
                <MetricCell key={m.id} metric={m} index={i} />
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Add `panel-accent` to MetricCell's root className**

In `insight/web/src/components/MetricCell.tsx`, change the root `className` template to include `panel-accent ` before `panel-rise`. Do not add a `data-verdict` attribute — Phase A renders no verdicts.

- [ ] **Step 3: Build**

Run: `cd insight/web && npx tsc --noEmit && npx next build`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add insight/web/src/components/BandBoard.tsx insight/web/src/components/MetricCell.tsx
git commit -m "feat(insight): collapse the 42-cell board to one expandable row per band"
```

---

### Task 7: Recompose the delivery page

**Files:**
- Modify: `insight/web/src/app/delivery/page.tsx`
- Modify: `insight/web/src/components/IntegrityStrip.tsx` (use the doughnut)

**Interfaces:**
- Consumes: `Doughnut` (Task 5), `BandBoard` (Task 6).

- [ ] **Step 1: Replace the integrity strip's segment row with the doughnut**

In `IntegrityStrip.tsx`, replace the 42-segment `<div className="flex gap-[3px]">…</div>` with:

```tsx
      <Doughnut
        numerator={measured}
        denominator={total}
        label="Panel integrity"
        caption={`${total - measured} of ${total} metrics are absent, not zero. A dark segment means the instrument is not connected — it is not a healthy reading.`}
      />
```

and import it: `import { Doughnut } from "./Doughnut";`

- [ ] **Step 2: Swap the board for BandBoard in page.tsx**

Replace the entire `<section aria-label="Instrumentation board">` body's inner `{BANDS.map(...)}` block with:

```tsx
        <BandBoard metrics={metrics} bands={BANDS} />
```

and import `BandBoard`. Delete the now-unused inline band rendering and the `live` computation.

- [ ] **Step 3: Build and verify the page renders**

```bash
cd insight/web && npx next build
```

Then, from repo root with the dashboard running:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -b "$JAR" http://localhost:3000/delivery
```
Expected: `200`

- [ ] **Step 4: Commit**

```bash
git add insight/web/src/app/delivery/page.tsx insight/web/src/components/IntegrityStrip.tsx
git commit -m "feat(insight): summary-first delivery panel — integrity doughnut, collapsed board"
```

---

### Task 8: Full gate

**Files:** none modified — this task is verification only.

- [ ] **Step 1: Python suite**

Run: `python3 -m pytest -q insight/tests/`
Expected: all pass, 0 failures.

- [ ] **Step 2: Web unit proofs**

Run: `cd insight/web && npm test`
Expected: every proof prints OK, exit 0.

- [ ] **Step 3: Browser proofs under CI conditions**

```bash
cd insight/web && mv .env.local /tmp/env.local.bak
export INSIGHT_DEV_ROUTES=1
for p in prove:fonts prove:absence-states prove:shell-responsive prove:role-forbidden \
         prove:ic-bridge prove:ic-no-leak prove:delivery-bridge prove:delivery-cold-start; do
  npm run $p >/tmp/$p.log 2>&1 && echo "PASS $p" || echo "FAIL $p"
done
mv /tmp/env.local.bak .env.local
```
Expected: eight `PASS` lines.

- [ ] **Step 4: Confirm the cold-start invariant still holds**

Run: `cd insight/web && INSIGHT_DEV_ROUTES=1 npm run prove:delivery-cold-start`
Expected: `cold-start /delivery renders no numerals anywhere`. The doughnut must NOT render on a cold start — `denominator` is 42 but `measured` is 0, which is a legitimate ring of zero; confirm the proof's numeral scan still passes, and if it fails, gate the doughnut on `measured > 0` rather than weakening the proof.

- [ ] **Step 5: Lint and typecheck**

Run: `cd insight/web && npx tsc --noEmit && npx eslint .`
Expected: 0 errors (one pre-existing warning in `prove-actor-is-session-bound.mjs` is acceptable).

- [ ] **Step 6: Commit any fixes, then open the PR**

```bash
git push -u origin sdlc/panel-phase-a
gh pr create --base main --title "feat(insight): panel Phase A — meaning, layout and doughnuts" --body-file /tmp/pr-body.md
```

---

## Self-Review

**Spec coverage.** §5 card anatomy → Tasks 1, 2, 4. §6 doughnuts → Task 5. §7 layout → Tasks 6, 7. §9 contract → Tasks 1–3. §10 invariants 6, 7, 8 → Task 8 and Task 1's test. Invariants 1–5 concern verdicts and belong to Phase C, correctly absent here. §3 accent geometry → Task 4 Step 2, shipped inert.

**Deliberately deferred to Phase C:** the `health` field, band values, baseline, coverage gate, `direction`. Phase A sets no `data-verdict` anywhere, so the accent CSS ships dormant — this is why Phase A cannot render a wrong verdict.

**Type consistency.** `question`/`guardrail`/`proxy`/`dataStatus` on `MetricBase` (Task 1) are read as `metric.question` in Task 4. `gapHint` is on `AbsentUnbuiltMetric` only (Task 2) and Task 4 narrows on `state === "absent_unbuilt"` before reading it. `Doughnut`'s props in Task 5 match both call sites in Task 7.

**Known risk carried into Task 8 Step 4:** the integrity doughnut renders `0/42` on a cold start, and `0` is a numeral. The step names the fix (gate on `measured > 0`) rather than allowing the proof to be weakened.
