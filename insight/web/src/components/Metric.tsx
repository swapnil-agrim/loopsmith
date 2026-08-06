// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md Decision (a)/(f), Step 3.
//
// The primary metric readout (analogous to insight/dash/instrument.py's `.ro`). A thin JSX layer
// over describeMetric() (src/lib/metric-view.ts): no `state` narrowing or absence logic of its
// own -- that is proven once, browser-free, by prove-metric-view-behavior.mjs, and this
// component only maps the already-narrowed result onto markup.
//
// Two rendering details are load-bearing for Step 6's Playwright proof, not incidental:
//   1. The numeral lives in an element carrying `data-testid="metric-numeral"` UNCONDITIONALLY
//      across all three states -- empty for the two absent states, never omitted from the DOM.
//      A selector that matches nothing would make "no digit in it" pass vacuously; a selector
//      that always matches something is what makes that assertion mean anything.
//   2. Absent-state markup carries a STRUCTURAL (border-style), not opacity-only, distinction:
//      `border-dashed` for absent_no_data, `border-dotted` for absent_unbuilt (Decision (d)).
import type { Metric as MetricType } from "@/lib/api/metric";
import { describeMetric } from "@/lib/metric-view";

const BORDER_CLASS: Record<MetricType["state"], string> = {
  measured: "border-2 border-solid border-panel-rule-hard",
  absent_no_data: "border-2 border-dashed border-panel-void-edge",
  absent_unbuilt: "border-2 border-dotted border-panel-void-edge",
};

export function Metric({ metric }: { metric: MetricType }) {
  const d = describeMetric(metric);
  return (
    <div
      data-testid="metric-root"
      data-metric-state={metric.state}
      className={
        "inline-block rounded bg-panel-panel px-4 py-3 text-panel-bone " + BORDER_CLASS[metric.state]
      }
    >
      <div className="text-panel-dim" style={{ fontSize: "var(--panel-text-caption)" }}>
        {metric.label}
      </div>
      <div
        data-testid="metric-numeral"
        className="font-mono"
        style={{ fontSize: "var(--panel-text-display)" }}
      >
        {d.numeral ?? ""}
      </div>
      {d.coverageText !== null && (
        <div
          data-testid="metric-coverage"
          className="text-panel-dim"
          style={{ fontSize: "var(--panel-text-small)" }}
        >
          {d.coverageText}
        </div>
      )}
      {d.reasonText !== null && (
        <div
          data-testid="metric-reason"
          className="text-panel-void-ink"
          style={{ fontSize: "var(--panel-text-small)" }}
        >
          {d.reasonText}
        </div>
      )}
      {d.fixText !== null && (
        <div
          data-testid="metric-fix"
          className="text-panel-void-ink"
          style={{ fontSize: "var(--panel-text-caption)" }}
        >
          {d.fixText}
        </div>
      )}
    </div>
  );
}
