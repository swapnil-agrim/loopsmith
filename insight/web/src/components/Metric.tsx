// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// The hero readout: one large instrument, used for the handful of metrics a
// manager reads first. MetricCell.tsx is the same contract at board density.
//
// THE INVARIANT THIS COMPONENT EXISTS TO HOLD: a measured reading and an absent
// one must not be distinguishable only by reading the text. They differ in
// SURFACE (lifted gradient vs flat hatched void), in EDGE (solid vs dashed vs
// dotted), in CHROMA (bone numerals with a live glow vs achromatic graphite),
// and in BEHAVIOUR (an absent instrument does not respond to hover, because it
// has nothing to inspect). Any one of those alone would be a caption; together
// they are legible at a glance and across a room, which is the actual product
// claim. describeMetric() remains the single source of what text may appear --
// this file styles that decision and never re-derives it.
import type { Metric as MetricType } from "@/lib/api/metric";
import { hatchBackgroundImage } from "@/lib/absence-hatch";
import { describeMetric } from "@/lib/metric-view";

const EDGE: Record<MetricType["state"], string> = {
  measured: "panel-instrument",
  absent_no_data: "panel-void-surface",
  // Dotted, not dashed: `absent_unbuilt` is a different KIND of absence (no code
  // exists to measure it) from `absent_no_data` (code exists, nothing to read),
  // and the pre-redesign board already encoded that distinction in the border
  // style. Keeping it means the two absences stay tellable apart without prose.
  absent_unbuilt: "panel-void-surface [border-style:dotted] opacity-[.82]",
};

const HATCH: Record<MetricType["state"], string | undefined> = {
  measured: undefined,
  absent_no_data: hatchBackgroundImage("--panel-hatch-soft"),
  absent_unbuilt: hatchBackgroundImage("--panel-hatch-soft"),
};

/** Coverage rendered as a shape, not only as "47/52". A reading whose
 * denominator is small is a weaker claim than one whose denominator is the
 * whole population, and that difference should survive being skimmed. Returns
 * null when there is no coverage to draw -- never a zero-width bar, which would
 * read as "measured, and zero". */
function CoverageMeter({ metric }: { metric: MetricType }) {
  if (metric.state !== "measured") return null;
  const { numerator, denominator } = metric.coverage;
  if (!denominator || denominator <= 0) return null;
  const pct = Math.max(0, Math.min(1, numerator / denominator));
  return (
    <div
      className="panel-meter mt-2"
      role="img"
      aria-label={`coverage ${numerator} of ${denominator}`}
    >
      <span className="panel-meter-fill" style={{ width: `${pct * 100}%` }} />
    </div>
  );
}

export function Metric({ metric, index = 0 }: { metric: MetricType; index?: number }) {
  const d = describeMetric(metric);
  const isMeasured = metric.state === "measured";

  return (
    <div
      data-testid="metric-root"
      data-metric-state={metric.state}
      title={d.reasonText ?? undefined}
      className={`panel-accent panel-rise flex min-w-[190px] flex-1 flex-col px-4 py-3.5 ${EDGE[metric.state]}`}
      style={{
        backgroundImage: HATCH[metric.state],
        // Staggered reveal. Capped so a long row never leaves the last card
        // arriving noticeably after the reader has started reading the first.
        animationDelay: `${Math.min(index, 8) * 55}ms`,
      }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="panel-label truncate">{metric.label}</span>
        {/* The live pip. Present only on a measured reading, so "is there a
            reading here?" is answerable from a single 5px dot. */}
        <span className="flex shrink-0 items-baseline gap-1.5">
          {/* PROXY and DARK are different claims and are shown differently on purpose. A proxy is
              a real measurement that approximates by design -- permanent, and it may still carry
              a verdict. DARK means the underlying data may be wrong, and is mutually exclusive
              with a verdict: its presence is WHY there is no accent. */}
          {metric.proxy && <span data-testid="metric-proxy-tag" className="panel-tag">Proxy</span>}
          {metric.dataStatus === "dark" && (
            <span data-testid="metric-dark-tag" className="panel-tag">Dark</span>
          )}
          {isMeasured && (
            <span
              aria-hidden="true"
              className="h-[5px] w-[5px] rounded-full bg-panel-cyan"
              style={{ boxShadow: "0 0 8px var(--panel-cyan)" }}
            />
          )}
        </span>
      </div>

      {/* The metric's own meaning, from its .sql header. min-height keeps a row of cards on one
          baseline whether the question wraps to one line or two. */}
      {d.question && (
        <p
          data-testid="metric-question"
          className={isMeasured ? "mt-2 text-panel-dim" : "mt-2 text-panel-void-ink"}
          style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.45, minHeight: "2.9em" }}
        >
          {d.question}
        </p>
      )}

      <div
        data-testid="metric-numeral"
        className={`panel-num mt-1 ${isMeasured ? "panel-num-live" : "text-panel-void-ink"}`}
        style={{ fontSize: "var(--panel-text-display)" }}
      >
        {d.numeral ?? ""}
      </div>

      <CoverageMeter metric={metric} />

      {d.coverageText !== null && (
        <div
          data-testid="metric-coverage"
          className="panel-num mt-1.5 text-panel-dim"
          style={{ fontSize: "var(--panel-text-caption)" }}
        >
          {d.coverageText}
        </div>
      )}
      {/* The generic reason ("no value/coverage extractor registered yet") is suppressed when a
          SPECIFIC gap hint exists, because the hint says the same thing and then says what to do
          about it. Stacking both plus fixText put three near-identical sentences on every absent
          card -- the clutter this redesign exists to remove. The reason stays in the DOM as the
          card's title, so nothing is lost to a reader who wants it. */}
      {d.reasonText !== null && d.gapHint === null && (
        <div
          data-testid="metric-reason"
          className="mt-1.5 text-panel-void-ink"
          style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.45 }}
        >
          {d.reasonText}
        </div>
      )}
      {d.gapHint !== null && (
        <p
          data-testid="metric-gap-hint"
          className="mt-1.5 text-panel-dim"
          style={{ fontSize: "var(--panel-text-caption)", lineHeight: 1.45 }}
        >
          {d.gapHint}
        </p>
      )}
      {d.fixText !== null && (
        <div
          data-testid="metric-fix"
          className="mt-1 text-panel-faint"
          style={{ fontSize: "var(--panel-text-caption)", lineHeight: 1.4 }}
        >
          {d.fixText}
        </div>
      )}
    </div>
  );
}
