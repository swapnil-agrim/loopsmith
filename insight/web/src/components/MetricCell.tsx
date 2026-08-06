// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md Decision (f), Step 3.
//
// The compact board-cell equivalent the issue calls "chart-cell equivalent" (Decision (f): no
// such term exists anywhere in this repo or the spec, and charts are E21/out of scope here --
// this is a compact cell analogous to insight/dash/instrument.py's `.cell` (id, name, numeral or
// absence hatch), not an SVG/chart mark). Shares describeMetric() with <Metric> (src/components/
// Metric.tsx) so the two surfaces cannot drift on which state gets which treatment -- see that
// file's header comment for why the numeral testid and border-style rules are load-bearing, not
// incidental; the same two rules apply here.
import type { Metric as MetricType } from "@/lib/api/metric";
import { describeMetric } from "@/lib/metric-view";

const BORDER_CLASS: Record<MetricType["state"], string> = {
  measured: "border-2 border-solid border-panel-rule-hard",
  absent_no_data: "border-2 border-dashed border-panel-void-edge",
  absent_unbuilt: "border-2 border-dotted border-panel-void-edge",
};

export function MetricCell({ metric }: { metric: MetricType }) {
  const d = describeMetric(metric);
  return (
    <div
      data-testid="metric-root"
      data-metric-state={metric.state}
      className={
        "inline-flex w-28 flex-col items-start gap-0.5 rounded bg-panel-raised px-2 py-1.5 text-panel-bone " +
        BORDER_CLASS[metric.state]
      }
    >
      <span
        className="w-full truncate text-panel-dim"
        style={{ fontSize: "var(--panel-text-micro)" }}
      >
        {metric.label}
      </span>
      <span
        data-testid="metric-numeral"
        className="font-mono"
        style={{ fontSize: "var(--panel-text-subhead)" }}
      >
        {d.numeral ?? ""}
      </span>
      {d.coverageText !== null && (
        <span
          data-testid="metric-coverage"
          className="text-panel-dim"
          style={{ fontSize: "var(--panel-text-micro)" }}
        >
          {d.coverageText}
        </span>
      )}
      {d.fixText !== null && (
        <span
          data-testid="metric-fix"
          className="w-full truncate text-panel-void-ink"
          style={{ fontSize: "var(--panel-text-micro)" }}
        >
          {d.fixText}
        </span>
      )}
    </div>
  );
}
