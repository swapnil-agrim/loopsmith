# LoopSmith Insight — dashboard design spec

**Date:** 2026-08-03
**Status:** design, awaiting review
**Scope:** subsystem **D** — the four persona dashboards. This is the spec the data-platform spec
(§9) said would be written and never was.

---

## 0. Why this document exists, and why it is late

The data-platform spec deferred this: *"Dashboards (D) and the config UI (E) are sketched in §9 and
get their own specs."* The sketch enumerated **which data each persona sees**. It contained no design
decision of any kind — a search of that spec returns zero hits for `typography`, `layout`,
`responsive`, `visual design`, `brand`.

Fifty-seven stories were then built from that sketch. E4 and E5 shipped `dashboard shell`,
`chart primitives`, and four `persona views`, all with rigorous `done_when` clauses — *own data only,
proven by a leak test with a negative control*; *a class-2 number without its coverage denominator is
a build failure*. Every one is mechanically checkable, and that rigour is why the review gates caught
real defects repeatedly.

**That is also why design was dropped.** The backlog only accepted work whose completion a test could
assert. "Looks like a product" is not test-shaped, so it never entered the process. The result is a
correct, well-tested, undesigned instrument: `-apple-system` text and unstyled tables.

So this spec's first obligation is not a colour palette. It is to make design acceptable to a process
that only accepts verifiable things — **without** pretending taste is a unit test.

### The rule this spec introduces

Every story below carries **two** acceptance clauses:

* **`done_when`** — mechanically checkable, exactly like every other story in this backlog.
* **`judged_when`** — a named human looks at it and says yes. Explicitly not automatable.

A story with only a `done_when` is how we got here. A story with only a `judged_when` cannot be run
by the loop unattended. Both, always.

---

## 1. What the product actually is

An engineering-analytics dashboard whose distinguishing claim is **honesty about what it does not
know**. Everything downstream follows from that.

Competitors render a confident zero. This renders *"not measured — `fact_event.reason_class` has zero
writers"*. That is the product, and the design must make it the most legible thing on the page rather
than the smallest grey text on it.

**Audiences, in the order they matter commercially:** leadership (buys it), manager (uses it daily),
IC (must not feel surveilled by it), cross-functional (audits with it).

---

## 2. The design problem unique to this product

Three states must be **instantly distinguishable at a glance**, without reading:

| state | meaning | today |
|---|---|---|
| **measured** | a real number from real rows | `8%` |
| **empty result** | measured correctly, nothing matched | `0` |
| **not measured** | no data path exists | `· ABSENT` grey text |

Conflating the last two is the failure the whole platform exists to prevent, and the current UI
conveys the difference in an eight-character grey string that a skimming reader will miss. **This is
the single highest-value design decision in the product** and it is currently unmade.

### The decision

**Absence gets its own visual class**, not a label:

* hatched fill and dashed border — texture reads as "different kind of thing" pre-attentively, where
  a colour swap reads as "same thing, different value"
* the words **"not measured"** at body weight, never a numeral, so it cannot be mistaken for a value
* a monospace provenance line naming the missing writer: `no writer · fact_event.reason_class`
* **empty result** stays a normal numeral with its denominator — `0 of 39` — visually a measurement,
  because it is one

`judged_when`: a reader shown the page for three seconds can say which tiles are measurements and
which are not, without reading any label.

---

## 3. Visual direction

**Instrument, not infographic.** This is read daily by people making staffing and scheduling
decisions; it is not a quarterly slide. Reference points are oscilloscopes and flight instruments —
dense, quiet, high-signal — not marketing dashboards.

Concretely:

* **Typography.** A distinctive humanist sans for prose and headings; a mono for every number,
  identifier, timestamp and provenance line. Numbers are `tabular-nums` throughout, so a value does
  not shift width as it updates. **Not** `-apple-system`, which is what shipped by default.
* **Colour.** Near-monochrome. Colour carries *state*, never decoration: one accent for measured, one
  warning for a fired gap, texture (not colour) for absence. Every state is also encoded in shape or
  text, so colour is never the only channel.
* **Density.** Compact by default. A manager scanning six panels should not scroll. Generous space
  *between* semantic groups, tight *within* them.
* **Chrome.** Hairline borders, no shadows, no gradients, no rounded-card drift. The page should look
  like it was made by the same people who wrote `ABSENT ≠ PASS`.
* **Dark mode is mandatory**, not a toggle added later — this is read at 2am by someone checking a
  loop.

---

## 4. Stories

Nine stories. Each has both acceptance clauses.

### D1 — Design tokens and the absence primitive
**`done_when`** a single token module defines type scale, spacing, colour and the three data-states;
every existing view imports it; no view hardcodes a colour or font; a test asserts zero hex literals
outside the token file; `prefers-color-scheme` is honoured and a contrast check passes AA for every
token pair actually used.
**`judged_when`** the three states are distinguishable at a glance in both light and dark mode.

### D2 — The number component
**`done_when`** one component renders measured / empty-result / not-measured; it **refuses to render**
a class-2 number without its coverage denominator (extending #129's build failure into the component);
`tabular-nums` everywhere; a test plants each state and asserts the rendered class.
**`judged_when`** a wrong-looking number is obvious; a not-measured tile is never mistaken for zero.

### D3 — Page shell, navigation and responsive frame
**`done_when`** one shell wraps all four views; persona navigation is present on every page; the
layout reflows at 1440 / 1024 / 768 with no horizontal scroll; **the leadership view still cannot
reach individual-grain data** — proven by the existing leak test, which must survive the refactor.
**`judged_when`** moving between personas feels like one product rather than four pages.

### D4 — Chart restyle onto the token system
**`done_when`** the five existing primitives use only tokens; the forecast band is visually distinct
from measured history (texture, not opacity); every chart has an accessible name and a non-colour
channel; existing chart tests stay green.
**`judged_when`** a chart reads correctly at a glance and the inferred region is obviously inferred.

### D5 — Manager view, designed
**`done_when`** all six panels use D1/D2/D4; ABSENT panels use the absence primitive; the
individual-grain guardrail (aging WIP and hand-off response only) still holds under test.
**`judged_when`** a manager can answer "what is stuck and what is slowing down" in under ten seconds.

### D6 — Leadership view, designed
**`done_when`** the DX Core-4 tiles use the token system; Effectiveness renders as a declared gap and
**never** as a fabricated DXI; Impact renders unpaired per spec §6; portfolio drill-through works.
**`judged_when`** it survives being put on a screen in front of someone deciding whether to buy this.

### D7 — IC view, designed
**`done_when`** own-data-only holds under the existing leak test; no cross-actor row is reachable.
**`judged_when`** an IC reading their own page feels informed, not measured. **This is a veto: if it
reads as surveillance, the story fails regardless of `done_when`.**

### D8 — Cross-functional view and gap cards, designed
**`done_when`** the gate matrix shows pass/warn/block/absent as four visually distinct states; each
gap card carries what · evidence · metric moved · one action, and fails the build if any part is
missing (#134's check, restyled).
**`judged_when`** an auditor can tell "this gate passed" from "this gate never ran" without a legend.

### D9 — Empty and cold-start states, designed
**`done_when`** a store with no data renders the onboarding path, never zeros; every partially-populated
page degrades to the absence primitive rather than blank space.
**`judged_when`** a first-run user understands what to do next, and never believes the product is broken.

---

## 5. Explicitly out of scope

* **A design system for the config UI (E).** Related, separate spec.
* **Interactive drill-down, filtering, date-range pickers.** The static-generated decision from the
  data spec §9 stands; adding interactivity is a hosted-mode concern.
* **A logo, brand identity or marketing site.**
* **Charting libraries.** The five primitives are hand-rolled SVG and stay that way — the
  self-contained, zero-network requirement is load-bearing and a CDN dependency would break it.

---

## 6. Sequencing

D1 → D2 → D3 are foundational and strictly ordered. D4 depends on D1. D5–D9 depend on D1–D4 and are
mutually independent.

**Do these after E7 completes**, not before. The E7 metrics (#145–#149) fill panels that D5–D8 will
style; designing an empty panel and restyling it once it has data is two passes over the same file.

---

## 7. The honest caveat

`judged_when` cannot be run by an autonomous loop. These stories are **buildable** unattended, but
**not acceptable** unattended — a human has to look. That is a real constraint on the "run it
overnight" model, and pretending otherwise is what produced an undesigned product built entirely by
verifiable steps.

The loop can implement D1–D9 and prove every `done_when`. Someone still has to open the page.
