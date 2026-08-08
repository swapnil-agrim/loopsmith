// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// Board density: the same measured-vs-absent contract Metric.tsx holds, at the
// size where all 42 catalog entries fit on one screen. See Metric.tsx's header
// for the invariant; the only thing that changes here is scale, and what has to
// be dropped to survive it.
//
// WHAT GETS DROPPED, AND WHY IT IS SAFE: at this size the per-cell absence
// REASON does not fit, and the pre-redesign cell truncated it to
// "Only a code chang..." on 36 of 42 cells -- 36 identical, unreadable ellipses,
// which is visual noise pretending to be information. The reason now lives in
// the cell's `title`, so it is one hover away and still in the accessibility
// tree, while the cell itself carries the thing that IS readable at this size:
// state, told by surface and edge. Nothing about the absence is hidden -- the
// full prose is still on the hero readouts above and in the API response.
import type { Metric as MetricType } from "@/lib/api/metric";
import { hatchBackgroundImage } from "@/lib/absence-hatch";
import { describeMetric } from "@/lib/metric-view";

const EDGE: Record<MetricType["state"], string> = {
  measured: "panel-instrument",
  absent_no_data: "panel-void-surface",
  absent_unbuilt: "panel-void-surface [border-style:dotted] opacity-[.72]",
};

const HATCH: Record<MetricType["state"], string | undefined> = {
  measured: undefined,
  absent_no_data: hatchBackgroundImage("--panel-hatch"),
  absent_unbuilt: hatchBackgroundImage("--panel-hatch"),
};

export function MetricCell({ metric, index = 0 }: { metric: MetricType; index?: number }) {
  const d = describeMetric(metric);
  const isMeasured = metric.state === "measured";
  const { numerator, denominator } =
    metric.state === "measured" ? metric.coverage : { numerator: 0, denominator: 0 };
  const pct = denominator > 0 ? Math.max(0, Math.min(1, numerator / denominator)) : null;

  return (
    <div
      data-testid="metric-root"
      data-metric-state={metric.state}
      data-verdict={d.verdict ?? undefined}
      // The reason is the tooltip rather than truncated body text -- see the
      // header comment. Falls back to the label so a measured cell still names
      // itself on hover, where the label may be truncated by `truncate`.
      title={d.reasonText ?? metric.label}
      className={`panel-accent panel-rise flex h-[94px] w-[128px] flex-col justify-between px-2 py-1.5 ${EDGE[metric.state]}`}
      style={{
        backgroundImage: HATCH[metric.state],
        // Ripple across the band rather than all-at-once: makes the board read
        // as one instrument powering up. Capped so late cells are not left behind.
        animationDelay: `${Math.min(index, 14) * 22}ms`,
      }}
    >
      {/* Wraps to two lines rather than truncating. `truncate` turned 20 of the 42 labels into
          "MERGE FREQU..." / "FLOW LOAD (..." -- a board where half the instruments do not say what
          they measure. Two lines fit at this type size; the clamp keeps the cell height fixed so
          the grid stays aligned. */}
      <span
        className="panel-label w-full"
        style={{
          letterSpacing: "0.07em",
          lineHeight: 1.25,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {metric.label}
      </span>

      <span
        data-testid="metric-numeral"
        className={`panel-num ${isMeasured ? "panel-num-live" : "text-panel-void-ink"}`}
        style={{ fontSize: "var(--panel-text-subhead)" }}
      >
        {d.numeral ?? ""}
      </span>

      {/* A measured cell ends in its coverage: the counts in text AND the meter above them.
          The meter alone is not enough -- it shows the RATIO but not the size of the population
          behind it, and 1/2 and 500/1000 are very different claims that fill an identical bar.
          (Dropping the text here was caught by prove-absence-primitives-render.mjs, which
          requires both numerator and denominator to be readable on a measured cell.)

          An absent cell ends in the hatch it is already wearing -- deliberately no placeholder
          bar, because an empty meter is a drawn zero, the exact misreading this product exists
          to prevent. */}
      {d.coverageText !== null ? (
        <div className="flex w-full flex-col gap-1">
          {pct !== null && (
            <div className="panel-meter" role="img" aria-label={`coverage ${numerator} of ${denominator}`}>
              <span className="panel-meter-fill" style={{ width: `${pct * 100}%` }} />
            </div>
          )}
          <span
            data-testid="metric-coverage"
            className="panel-num text-panel-dim"
            style={{ fontSize: "var(--panel-text-micro)" }}
          >
            {d.coverageText}
          </span>
        </div>
      ) : (
        // The actionable half of an absence: whether waiting fixes this or only code does. Two
        // words, from describeMetric's own FIX_SHORT, with the full sentence on hover -- see that
        // constant's comment for why the short form is defined there and not invented here.
        <span
          data-testid="metric-fix"
          title={d.fixText ?? undefined}
          className="panel-label w-full truncate"
          style={{ color: "var(--panel-void-ink)", letterSpacing: "0.06em" }}
        >
          {d.fixShort}
        </span>
      )}
    </div>
  );
}
