// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// One segment per catalog metric, lit when that metric has a reading and dark +
// hatched when it does not.
//
// WHY THIS EXISTS AT ALL. Every other dashboard answers "how are we doing?".
// The question this product is built around is the one before it: "how much of
// this instrument is actually connected?" On this repo's own store that answer
// is 6 of 42, and a panel that does not say so out loud invites the reader to
// treat 36 absences as 36 quiet successes. The strip makes the honest answer the
// first thing on the page and the hardest thing to miss -- it is the product
// thesis rendered as a shape instead of a caption.
//
// It is derived, never configured: the counts come from the same metric array
// the board below renders, so the strip cannot drift from what it summarises.
import type { Metric as MetricType } from "@/lib/api/metric";

import { Doughnut } from "./Doughnut";

export function IntegrityStrip({ metrics }: { metrics: readonly MetricType[] }) {
  const total = metrics.length;
  const measured = metrics.filter((m) => m.state === "measured").length;

  return (
    <section
      aria-label="Panel integrity"
      data-testid="integrity-strip"
      className="panel-rise flex flex-col gap-3"
    >
      <div className="flex items-center gap-3">
        <span className="panel-label panel-label-accent shrink-0">Panel integrity</span>
        <span className="h-px min-w-4 flex-1 bg-panel-rule" aria-hidden="true" />
      </div>

      {/* A doughnut, because coverage genuinely IS a part-to-whole -- 6 of 42 instrumented.
          Neutral tone on purpose: this is a statement of how much of the instrument is connected,
          not a verdict about whether that is good. */}
      <Doughnut
        numerator={measured}
        denominator={total}
        label="Panel integrity"
        tone="neutral"
        caption={
          <>
            <span className="text-panel-bone">{total - measured} of {total}</span> metrics are
            absent, not zero. A dark segment means the instrument is not connected &mdash; it is
            not a healthy reading.
          </>
        }
      />
    </section>
  );
}
