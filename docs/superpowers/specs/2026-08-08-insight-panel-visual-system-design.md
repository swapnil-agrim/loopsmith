# LoopSmith Insight — panel visual system, health verdicts, and metric meaning

**Date:** 2026-08-08
**Status:** approved (design), pending implementation plan
**Supersedes nothing.** Extends `2026-08-04-loopsmith-insight-web-app-design.md` §3 (the absence
contract) and the delivery panel shipped in #312 / PR #526.

---

## 1. The problem

The delivery panel is correct and unreadable. Three defects, reported directly:

1. **Cluttered.** 42 equal-weight cards in one wall, no hierarchy, nothing to lead the eye.
2. **Missing chart vocabulary.** No doughnuts; only two chart types for 42 metrics.
3. **No verdict, no meaning, no guidance.** A card shows `22.6%` and nothing else. It never says
   what the metric means, whether 22.6% is good or bad, or what to do about the 36 blanks.

### 1.1 What the previous design got right, and must not lose

Absence is load-bearing here in a way it is not in other analytics products. `ABSENT != PASS` is the
product thesis: an unmeasured metric must never read as a healthy one. The current panel enforces
that through material — hatched surface, dashed/dotted edge, no numeral — and through a type-level
discriminated union that makes misuse a compile error. **Every change below is additive to that
discipline, never a relaxation of it.** A health verdict is a stronger claim than a value, so it
gets stricter rules, not looser ones.

### 1.2 The finding that reshaped this design

Two facts discovered during research, neither previously surfaced in the UI:

**(a) The "what does this mean" content already exists.** Every `insight/metrics/N.sql` carries a
header (`insight/metrics/header.py`, `REQUIRED_FIELDS`) with `name`, **`question`**, `personas`,
`reliability_class`, **`guardrail`**, and optionally `proxy` and `data_status`. The `question` field
is a one-line plain-English statement of what the metric answers — *"How long does a goal take?"*,
*"How much building is re-building?"*. None of it reaches the API today. This is the single
highest-value, lowest-risk change in the whole design: the copy is already written and reviewed.

**(b) Four of the six live readings are flagged unreliable by their own authors.**

| id | metric | flag | live status |
|---|---|---|---|
| 2 | Cycle time | `data_status: dark` | **stale** — needed `claimed_ts`/`terminal_ts`, now 52/54 populated |
| 14 | Park rate | `data_status: dark` | **stale** — needed `outcome` + `fact_event`, now 52 and 147 rows, `parked` present |
| 12 | Autonomy rate | `data_status: dark` | **STILL TRUE** — reads `kind IN ('parked','ack')`; `ack` never appears in `fact_event` |
| 3 | Lead time | clean | measured 4/118 — clean but very thin |
| 5 | Change failure rate | `proxy: true` | permanent by design |
| 20 | Rework ratio | `proxy: true` | permanent by design |

Metric 12's own guardrail names a **FALSE ZERO** trap and states *"a fixture-green test is not
evidence of a live dashboard number."* Painting a green edge on it would be the most damaging thing
this product could do. Health colour therefore cannot ship without resolving dark status per metric.

---

## 2. Decisions (locked with the author)

| # | Decision | Chosen |
|---|---|---|
| D1 | Basis for "healthy" | **Industry benchmarks where they legitimately apply, plus trailing baseline** |
| D2 | Dark metrics | **Never coloured.** Value shown, `DARK` tag, explicit no-verdict reason |
| D3 | Proxy metrics | **Coloured, with the `PROXY` tag always visible.** A proxy is a real measurement that approximates by design — permanent, not a defect |
| D4 | Dark-label handling | **Verify the 6 live metrics as part of this work**, clear where genuinely resolved, keep where a real gap remains |
| D5 | Layout | **Summary first, detail on demand.** Board collapses to per-band rows, expandable |
| D6 | Accent treatment | **Option A — left edge, 2px, vertical fade to transparent** |

---

## 3. The colour system

### 3.1 The move that makes colour possible

Today chroma carries *measured vs absent*. That is redundant: absence is already unmistakable from
material (hatch + dashed edge + no numeral + no hover response). **Chroma is therefore freed
entirely, and from now on means exactly one thing: a verdict.**

This is the central decision of the design. It is what stops "everything is green" — because most
cards have no defensible verdict and will correctly carry no colour at all.

### 3.2 Five states, mutually unmistakable

| State | Accent | Surface | Numeral | Tag |
|---|---|---|---|---|
| Healthy | left edge, `--ok`, fading down | raised | bone | `PROXY` if applicable |
| Watch | left edge, `--watch` | raised | bone | `PROXY` if applicable |
| Breach | left edge, `--breach` | raised | bone | `PROXY` if applicable |
| Measured, no verdict | **none** | raised | bone | `DARK`, or none |
| Absent | **none** | void + hatch, dashed/dotted edge | **absent entirely** | `NEEDS DATA` / `NEEDS CODE` |

Note the two tags behave differently, per D2/D3. `PROXY` is orthogonal to the verdict — it can
appear on any measured state, including a coloured one, because a proxy is a real measurement.
`DARK` is mutually exclusive with a verdict — its presence is *why* there is no accent.

New tokens (added to `insight/dash/colors.py`, regenerated into `tokens.generated.css` — never
hand-edited, `test_web_tokens_fresh.py` pins this):

```
--panel-ok:     #5ce0b0   (reuses the existing mint)
--panel-watch:  #e0b45c
--panel-breach: #e0715c
```

`watch` and `breach` are deliberately **desaturated** from alert-red/alert-amber. A board carrying
several of them must stay calm; saturated alerting reads as an outage and trains people to ignore it.

### 3.3 Accent geometry (D6)

```css
.card[data-verdict]::before {
  content:""; position:absolute; left:0; top:0; bottom:0; width:2px;
  background: linear-gradient(180deg, var(--accent), transparent 85%);
}
```

No fills, no dots, no coloured text beyond a single small verdict word. The accent is absent —
not grey, *absent* — when there is no verdict, so "no verdict" is visually distinct from every
verdict rather than being a fourth colour.

---

## 4. The health model

### 4.1 Two bases, and a hard rule about neither

A verdict may only be produced from a **declared** basis:

- **`benchmark`** — a published external band, applicable only to metrics that genuinely map to one.
  In this catalog that is DORA's *lead time for change* (#3) and *change failure rate* (#5).
  (Deployment frequency ≈ #4 merge frequency and MTTR ≈ #6 are unwired/unbuilt today.)
- **`baseline`** — the metric's own trailing history: the current window compared against its own
  prior window. Precedent already exists in `insight/gaps/baseline.py`, which uses a trailing p85
  rather than an absolute magnitude, for exactly this reason.

**If neither basis is declared for a metric, it gets no verdict.** Not a neutral verdict — no
verdict, no accent, and a stated reason. This is the same discipline as absence, applied one level
up, and it is what prevents the design from degenerating into decorative green.

> **Benchmark values are configuration, not code, and each carries its source.** The band table
> lives in `insight/api/health_bands.py` as data, and every entry must carry a `source` string
> naming the report and edition it came from. DORA's published bands have changed between report
> editions; the initial values must be transcribed from a specific cited edition and checked by a
> human, not filled in from recollection. An entry without a `source` is a bug and is rejected by a
> test.

### 4.2 Coverage gates the verdict

A verdict on a thin sample is a confident-sounding guess. Lead time is currently **4/118 = 3.4%
measured**; asserting "elite" from four observations would be exactly the failure this product
exists to prevent.

Rule: a verdict requires **coverage ≥ 20% of the population AND ≥ 10 observations**. Below that the
metric renders measured, uncoloured, with the reason *"coverage too thin for a verdict (4/118)"*.

These two numbers are the one genuinely arbitrary choice in this design. They are declared as named
constants with this justification attached, and they are deliberately conservative: the cost of
withholding a verdict is mild, the cost of a wrong verdict is the product's credibility.

### 4.3 Dark and proxy (D2, D3)

- `data_status: dark` → **no verdict, ever, while the flag stands.** Reason text comes from the
  guardrail, condensed.
- `proxy: true` → verdict permitted, `PROXY` tag always rendered alongside.

### 4.4 Direction of "good" must be declared

`22.6%` rework is bad-when-high; `90.4%` autonomy is good-when-high. Nothing in the catalog states
direction. Each metric with a basis declares `direction: "lower_is_better" | "higher_is_better"`.
A metric with a basis but no direction is a configuration error and fails a test — it must never
silently guess, because guessing wrong inverts the verdict.

### 4.5 Where it runs

Server-side, in Python, beside metric resolution — the baseline needs the store. `MeasuredMetric`
gains:

```python
health: Optional[Health] = None      # None means "no verdict", never "healthy"
# Health = { verdict: "healthy"|"watch"|"breach",
#            basis: "benchmark"|"baseline",
#            explanation: str,        # "better than your trailing p85 (2.3h)"
#            source: str }            # cited, for benchmark
```

Optional with a `None` default, so this is an additive contract change exactly like `unit` was.
The **absence of a verdict is the default**, which is the fail-safe direction.

---

## 5. Card anatomy

Every card, measured or not, carries in reading order:

1. **Metric name** (`--panel-text-micro`, tracked, dim)
2. **Verdict word** or **`DARK`/`PROXY`/`NEEDS CODE` tag**, right-aligned
3. **`question:` line** — the meaning, verbatim from the SQL header. *This is the "tell me what
   this card means" requirement, and it costs no new copywriting.*
4. **The numeral** — unit-formatted (`75m`, `90.4%`), or absent entirely
5. **Coverage meter + counts**
6. **One line of context:** the verdict's explanation, the no-verdict reason, or — for absent
   metrics — **what would fill the gap**

Item 6 is the "what can be done" requirement, and it is derived, not written by hand:

- `absent_unbuilt`, view has rows → *"No extractor registered. 2 rows are already waiting in
  `metric_4`; wiring one would surface it."*
- `absent_unbuilt`, view empty → *"SQL exists but the view is empty — needs data before it can be
  wired."*
- `absent_unbuilt`, no `.sql` → *"No SQL written for this metric yet."*
- `absent_no_data` → *"Time and usage will fix this."*

The full `guardrail` text — including the *"CANNOT TELL YOU…"* limitations — is available on the
card's detail expansion and in `title`, never truncated inline.

---

## 6. Chart vocabulary

| Mark | Used for | Rule |
|---|---|---|
| **Doughnut** | Rate metrics and panel coverage | Only where the data is genuinely part-to-whole (47 of 52 goals; 6 of 42 metrics). Never for a duration or an unbounded count. |
| **Ranked trace** | Continuous distributions | Log axis when spread ≥ 20× and all values > 0; labelled on-chart when log. |
| **Histogram** | Discrete low-cardinality counts | Bins are the distinct observed values — no tunable bin width. Falls back to the trace when that assumption breaks. |
| **Bars** | Short time series | Point count stated so two bars are never read as a trend. |
| **Hatched "No sensor"** | Absent series | Carries no numeral. Never an empty axis. |

The doughnut is the requested addition and it is honest here specifically because a rate *is* a
part-to-whole. Ring stroke uses the verdict colour when one exists, neutral otherwise.

---

## 7. Layout (D5)

```
masthead ─────────────────────────────────────────────
PANEL INTEGRITY   doughnut 6/42 + one sentence
─── 01 Primary readouts ───   3–6 large cards, verdict-accented
─── 02 Distributions ─────    2–3 charts
─── 03 Instrumentation ───    5 collapsed band rows, expand on click
```

The integrity doughnut leads because a reader who sees six numbers first assumes the other
thirty-six are fine; a reader who sees "6 of 42 instrumented" first cannot.

The board collapses from 42 cards to 5 rows, each with band name, coverage pips, `n/total`, and a
disclosure chevron. Expansion is per band and client-side. **Nothing is removed** — density becomes
opt-in rather than mandatory.

---

## 8. Dark-label verification (D4)

Not a UI task; a data-integrity task, done per metric with evidence recorded in the commit:

| id | expected outcome | evidence required |
|---|---|---|
| 2 | clear `dark` | `claimed_ts`/`terminal_ts` populated for the measured population |
| 14 | clear `dark` | `outcome` populated **and** `fact_event.kind='parked'` present |
| 12 | **keep `dark`** | `ack` still absent from `fact_event` → interventions undercounted |
| 3, 5, 20 | unchanged | 3 already clean; 5 and 20 are permanent proxies |

A label is cleared **only** when the guardrail's own stated condition is met, and the guardrail text
is updated in the same commit to record what was verified and when. Metric 12 additionally gets a
follow-up issue for the missing `ack` emission — the metric is wrong until that lands, and that is
worth tracking separately rather than hiding.

---

## 9. Data contract changes

All additive; existing consumers unaffected.

1. `MetricBase` gains `question`, `guardrail`, `proxy`, `dataStatus`, `personas` — parsed from the
   SQL header via the existing `insight/metrics/header.py`, which is currently used only by tests.
2. `MeasuredMetric` gains `health` (§4.5).
3. `AbsentUnbuiltMetric` gains `gapHint` (§5 item 6).
4. OpenAPI re-exported and `schema.d.ts` regenerated (`check-schema-fresh.mjs` enforces).

---

## 10. Invariants to test

The following must be machine-checked, because each is a way this design could silently become
dishonest:

1. A `dark` metric never carries a `health` verdict. (Mutation: force one; test fails.)
2. A metric below the coverage gate never carries a verdict.
3. A metric with a basis but no declared `direction` fails a test rather than defaulting.
4. Every benchmark band entry has a non-empty `source`.
5. `health = None` renders **no accent**, never a neutral-coloured one.
6. An absent card renders no numeral and no accent — the existing cold-start proof, extended.
7. A measured card still carries **no `background-image`** — the existing negative control that
   keeps "absence is hatched" non-vacuous. The accent is a `::before` bar, not a background.
8. Every one of the 42 metrics resolves a non-empty `question` string.

---

## 10.1 Suggested phasing

This is one coherent design but more than one sitting of work. The implementation plan should split
it so something visible lands early and the riskiest judgement lands last:

- **Phase A — meaning and layout (no verdicts).** Expose the header metadata, put the `question`
  line and `gapHint` on every card, adopt accent-A geometry with `health` always `None`, collapse
  the board, add the doughnuts. Ships the decluttering and the "what does this mean" fix with zero
  risk of a wrong verdict, because no verdict is possible yet.
- **Phase B — dark-label verification (§8).** Data-integrity only, no UI change.
- **Phase C — health verdicts.** Bands, baseline, coverage gate, direction, and the accent going
  live. Depends on B, because a verdict on a dark metric is the failure mode this design exists to
  avoid.

## 11. Out of scope

- Wiring extractors for the 36 unbuilt metrics (separate, larger work).
- Emitting `ack` events so autonomy rate becomes trustworthy (own issue, §8).
- Per-role dashboards beyond the existing route policy.
- Alerting, thresholds-as-notifications, or anything that leaves the page.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Benchmark bands transcribed wrong from memory | `source` required per entry; values checked by a human against the cited edition before merge |
| Baseline needs history the store may not hold | Baseline only for metrics with a real time dimension; everything else honestly reports "no basis" |
| The 20%/10 coverage gate is arbitrary | Declared as named constants with rationale; conservative by choice |
| Colour creeps back into meaning "measured" | Invariant test 5; the accent is driven solely by `health` |
