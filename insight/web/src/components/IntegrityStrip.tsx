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

export function IntegrityStrip({ metrics }: { metrics: readonly MetricType[] }) {
  const total = metrics.length;
  const measured = metrics.filter((m) => m.state === "measured").length;
  const pct = total > 0 ? Math.round((measured / total) * 100) : 0;

  return (
    <section
      aria-label="Panel integrity"
      data-testid="integrity-strip"
      className="panel-rise flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="panel-label panel-label-accent">Panel integrity</span>
        <span className="panel-num panel-num-live" style={{ fontSize: "var(--panel-text-head)" }}>
          {measured}
          <span className="text-panel-faint">/</span>
          {total}
        </span>
        <span className="text-panel-dim" style={{ fontSize: "var(--panel-text-small)" }}>
          metrics instrumented ({pct}%)
        </span>
        {/* Said in words as well as shape. The strip is the fast read; this is
            the one that survives being described to somebody over a call. */}
        <span
          className="ml-auto text-panel-void-ink"
          style={{ fontSize: "var(--panel-text-caption)" }}
        >
          {total - measured} not measured &mdash; absent, not zero
        </span>
      </div>

      <div className="flex gap-[3px]" role="img"
           aria-label={`${measured} of ${total} metrics have a reading; ${total - measured} are absent`}>
        {metrics.map((m, i) => {
          const live = m.state === "measured";
          return (
            <span
              key={m.id}
              title={`${m.label} — ${live ? "measured" : "not measured"}`}
              className="h-6 min-w-0 flex-1 rounded-[2px]"
              style={{
                // Lit segments carry the same mint the coverage meters use, so
                // "live" means one colour everywhere on the page. Dark segments
                // get the void surface and a hairline, never a mid-grey fill
                // that could read as a dimmer reading.
                background: live
                  ? "linear-gradient(180deg, var(--panel-cyan), var(--panel-cyan-deep))"
                  : "var(--panel-void)",
                boxShadow: live ? "0 0 10px var(--panel-glow)" : "inset 0 0 0 1px var(--panel-void-edge)",
                animation: `panel-rise 520ms cubic-bezier(0.16,1,0.3,1) both`,
                animationDelay: `${Math.min(i, 42) * 14}ms`,
              }}
            />
          );
        })}
      </div>
    </section>
  );
}
