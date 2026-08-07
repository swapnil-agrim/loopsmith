// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] Goal B, Task B3. Replaces Goal A's placeholder stub (PR #509 -- "the delivery
// panel board is not built yet"). Composed from the E17 shared primitives (<Metric>/<MetricCell>,
// src/components/Metric.tsx / MetricCell.tsx) and the existing shell (app/layout.tsx's <Shell>,
// the single root layout every route already goes through -- no second shell copy) ONLY. No raw
// `metric.value` interpolation anywhere in this file: every numeral this page can ever show is
// routed through one of the two primitives, which is what makes done-when 2's "class-2 without a
// coverage denominator cannot be displayed" guarantee (already structurally enforced by
// `Coverage` being a required, `extra="forbid"` field on `MeasuredMetric`, see .sdlc/plans/312.md
// §3c) actually reach this page rather than being a guarantee nothing here exercises.
//
// FOUR PRIMARY READOUTS -- catalog ids 1 (Throughput), 2 (Cycle time), 12 (Autonomy rate), 14
// (Park rate), insight/metrics/catalog.py:17-19 -- resolved through the SAME
// insight.api.metrics.collect_metrics() the 42-metric board below also uses (one fetch, one code
// path, matching the API's own already-contract-tested resolver rather than panel.py's separate
// HTML-oriented `collect()`/`_readout()` query set, .sdlc/plans/312.md §3a).
//
// THE 42-METRIC BOARD, GROUPED BY BAND -- BANDS ported verbatim from insight/dash/panel.py:55-61
// (Flow 1-11, Autonomy & cost 12-21, Quality & gates 22-30, Collaboration 31-38, Portfolio 39-42).
// insight.api.metrics.collect_metrics() always returns exactly len(CATALOG) == 42 entries
// (insight/tests/test_api_metrics_route.py:31), so every id referenced below is guaranteed
// present -- findMetric()'s throw is a genuine contract-violation guard, not a routine "maybe
// absent" branch (a MISSING catalog entry is a bug in the transport, not a legitimate absence
// state; a catalog entry whose STATE is absent_no_data/absent_unbuilt is the normal, expected
// case <MetricCell> already renders correctly).
//
// ~41 OF THE 42 CELLS RENDER ABSENT TODAY, AND THAT IS CORRECT, NOT A BUG (.sdlc/plans/312.md
// §8's own non-negotiable, restated in the issue's Out-of-scope section): only catalog id 12 has
// a registered VALUE_EXTRACTOR (insight/api/metrics.py:55-57). Registering extractors for the
// other 41 metrics is explicitly out of this story's scope. Do not read a mostly-hatched board on
// this page as broken.
//
// THE FLOW CHARTS -- author's own decision, closing what plan §7 left open: the issue names "the
// 42-metric instrumentation board, primary readouts, AND the flow charts" as in scope (only chart
// INTERACTIVITY defers to E21). See ./charts.tsx's own header comment for why both charts render
// the shared absence material today (no data channel for per-day/spread values exists in this
// story's transport) rather than a wired chart.
//
// FORCE-DYNAMIC, EXPLICITLY -- unlike /ic (issue #310), this page reads no session-derived value
// at all (there is no --actor flag on the delivery bridge, .sdlc/plans/312.md §3a point 3: the
// panel is aggregate-only). The reason this still must not be statically prerendered is different:
// the store's contents change between requests (ingest runs, a cold-start proof reseeds it), and
// a page with zero dynamic API calls would otherwise be eligible for Next's build-time static
// render -- which would bake ONE store snapshot into `.next` forever, exactly the kind of silent
// staleness scripts/prove-delivery-cold-start-no-numerals.mjs (Task B4) exists to catch by
// re-seeding and re-fetching against a REAL running server.
export const dynamic = "force-dynamic";

import type { Metric as MetricType } from "@/lib/api/metric";
import { Metric } from "@/components/Metric";
import { MetricCell } from "@/components/MetricCell";
import { fetchDeliveryMetrics } from "@/lib/delivery/pythonBridge";

import { BarsChart, StripChart } from "./charts";

const PRIMARY_READOUT_IDS: readonly number[] = [1, 2, 12, 14];

/** ids ported verbatim from insight/dash/panel.py:55-61's BANDS -- Python's `range(a, b)` is
 * exclusive of `b`; the arrays below spell out the identical inclusive-of-`b-1` id lists so a
 * reader never has to mentally re-derive a range boundary. */
const BANDS: ReadonlyArray<{ name: string; ids: readonly number[] }> = [
  { name: "Flow", ids: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] },
  { name: "Autonomy & cost", ids: [12, 13, 14, 15, 16, 17, 18, 19, 20, 21] },
  { name: "Quality & gates", ids: [22, 23, 24, 25, 26, 27, 28, 29, 30] },
  { name: "Collaboration", ids: [31, 32, 33, 34, 35, 36, 37, 38] },
  { name: "Portfolio", ids: [39, 40, 41, 42] },
];

function findMetric(metrics: readonly MetricType[], id: number): MetricType {
  const metric = metrics.find((m) => m.id === id);
  if (!metric) {
    throw new Error(
      `insight web delivery did not return catalog id ${id} -- collect_metrics() is contract-` +
      "guaranteed to return all 42 catalog entries (test_api_metrics_route.py's own contract " +
      "test); a missing id here is a transport bug, not a legitimate absence state.",
    );
  }
  return metric;
}

export default async function DeliveryPage() {
  const metrics = await fetchDeliveryMetrics();

  return (
    <main className="flex flex-col gap-8">
      <h1>Delivery</h1>

      <section aria-label="Primary readouts">
        <h2>Primary readouts</h2>
        <div className="flex flex-wrap gap-4">
          {PRIMARY_READOUT_IDS.map((id) => (
            <Metric key={id} metric={findMetric(metrics, id)} />
          ))}
        </div>
      </section>

      <section aria-label="Flow charts">
        <h2>Flow</h2>
        <div className="flex flex-wrap gap-6">
          <div className="min-w-[320px] flex-1">
            <h3 style={{ fontSize: "var(--panel-text-body)" }}>Goals landed per day</h3>
            {/* See ./charts.tsx's own header comment: this transport carries no per-day merge
                counts, so this always renders the shared absence material today -- correct, not
                broken, the same class of scope-down as the 41 unbuilt board cells below. */}
            <BarsChart daily={[]} />
          </div>
          <div className="min-w-[320px] flex-1">
            <h3 style={{ fontSize: "var(--panel-text-body)" }}>Cycle time distribution</h3>
            <StripChart spread={[]} p50={null} p85={null} />
          </div>
        </div>
      </section>

      <section aria-label="Instrumentation board">
        <h2>Instrumentation board &mdash; all 42 metrics</h2>
        <div className="flex flex-col gap-4">
          {BANDS.map((band) => (
            <div key={band.name}>
              <div className="text-panel-faint" style={{ fontSize: "var(--panel-text-caption)" }}>
                {band.name}
              </div>
              <div className="mt-1 flex flex-wrap gap-2">
                {band.ids.map((id) => (
                  <MetricCell key={id} metric={findMetric(metrics, id)} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
