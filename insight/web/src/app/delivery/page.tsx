// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] Goal B. The delivery panel: hero readouts, the charts behind them, and the
// full 42-metric instrumentation board.
//
// READING ORDER IS THE DESIGN. The page opens with the integrity strip -- how much of this
// instrument is actually connected -- before a single value. That order is deliberate and is the
// product's thesis as a layout: a reader who sees six numbers first will assume the other
// thirty-six are fine, and a reader who sees "6/42 instrumented" first cannot.
export const dynamic = "force-dynamic";

import type { Metric as MetricType } from "@/lib/api/metric";
import { Metric } from "@/components/Metric";
import { MetricCell } from "@/components/MetricCell";
import { IntegrityStrip } from "@/components/IntegrityStrip";
import { BandBoard } from "@/components/BandBoard";
import { fetchDeliveryMetrics, fetchDeliverySeries } from "@/lib/delivery/pythonBridge";
import { HistogramChart, TraceChart, WeeklyBars } from "./charts";

const PRIMARY_READOUT_IDS: readonly number[] = [1, 2, 3, 12, 14, 20];

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

function SectionHeading({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="panel-label panel-label-accent shrink-0">{eyebrow}</span>
      <h2 className="shrink-0 text-panel-bone" style={{ fontSize: "var(--panel-text-subhead)" }}>
        {title}
      </h2>
      {/* The rule carries the eye across the full width and gives each band a horizon line --
          the thing that makes a dense board read as sections rather than as one wall of cells. */}
      <span className="h-px min-w-4 flex-1 bg-panel-rule" aria-hidden="true" />
    </div>
  );
}

export default async function DeliveryPage() {
  // Concurrent, not sequential: two independent child processes, so awaiting them in series would
  // add the slower one's latency to the faster one's for no reason.
  const [metrics, series] = await Promise.all([fetchDeliveryMetrics(), fetchDeliverySeries()]);

  return (
    <main className="flex flex-col gap-10">
      <header className="panel-rise flex flex-col gap-1">
        <span className="panel-label panel-label-accent">Delivery</span>
        <h1 className="text-panel-bone" style={{ fontSize: "var(--panel-text-title)", letterSpacing: "-0.01em" }}>
          Flow, autonomy and cost
        </h1>
      </header>

      <IntegrityStrip metrics={metrics} />

      <section aria-label="Primary readouts" className="flex flex-col gap-3">
        <SectionHeading eyebrow="01" title="Primary readouts" />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {PRIMARY_READOUT_IDS.map((id, i) => (
            <Metric key={id} metric={findMetric(metrics, id)} index={i} />
          ))}
        </div>
      </section>

      <section aria-label="Flow charts" className="flex flex-col gap-3">
        <SectionHeading eyebrow="02" title="Distributions" />
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <figure className="panel-rise panel-instrument m-0 flex flex-col gap-3 p-4">
            <figcaption className="flex items-baseline justify-between gap-2">
              <span className="text-panel-bone" style={{ fontSize: "var(--panel-text-body)" }}>
                Cycle time per goal
              </span>
              <span className="panel-label">ranked</span>
            </figcaption>
            <TraceChart series={series.cycleTime} label="Cycle time per goal" gradientId="trace-cycle" />
          </figure>

          <figure className="panel-rise panel-instrument m-0 flex flex-col gap-3 p-4">
            <figcaption className="flex items-baseline justify-between gap-2">
              <span className="text-panel-bone" style={{ fontSize: "var(--panel-text-body)" }}>
                Interventions per goal
              </span>
              <span className="panel-label">by count</span>
            </figcaption>
            {/* A histogram, not a ranked trace: interventions are small integers and most goals
                sit at zero, which ranked is a flat line with one spike. See HistogramChart's own
                docstring -- it falls back to the trace if that shape assumption ever stops
                holding, rather than drawing something misleading. */}
            <HistogramChart series={series.interventions} label="Interventions per goal" gradientId="hist-interventions" />
          </figure>

          <figure className="panel-rise panel-instrument m-0 flex flex-col gap-3 p-4 xl:col-span-2">
            <figcaption className="flex items-baseline justify-between gap-2">
              <span className="text-panel-bone" style={{ fontSize: "var(--panel-text-body)" }}>
                Goals landed per week
              </span>
              <span className="panel-label">throughput</span>
            </figcaption>
            <WeeklyBars series={series.weeklyThroughput} />
          </figure>
        </div>
      </section>

      <section aria-label="Instrumentation board" className="flex flex-col gap-5">
        <SectionHeading eyebrow="03" title="Instrumentation board — all 42 metrics" />
        {/* Collapsed to one row per band -- the same 42 cells, one interaction away. See
            BandBoard for why nothing is dropped rather than filtered. */}
        <BandBoard metrics={metrics} bands={BANDS} />
      </section>
    </main>
  );
}
