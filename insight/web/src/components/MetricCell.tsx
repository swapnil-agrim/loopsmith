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
import { hatchBackgroundImage } from "@/lib/absence-hatch";
import { describeMetric } from "@/lib/metric-view";

const BORDER_CLASS: Record<MetricType["state"], string> = {
  measured: "border-2 border-solid border-panel-rule-hard",
  absent_no_data: "border-2 border-dashed border-panel-void-edge",
  absent_unbuilt: "border-2 border-dotted border-panel-void-edge",
};

// issue #312 [E20.S1] Goal B, Task B1: ported VERBATIM from instrument.py's `.cell.dark`/
// `.cell.unbuilt` rules (instrument.py:130-135) -- see Metric.tsx's own header comment for the
// full "why this extends the shared primitive" story, unrepeated here. Unlike Metric.tsx's `.ro`
// analogue, the FILL itself changes for absent states (`--panel-raised` -> `--panel-void`), not
// just an added hatch layer, and text colour switches to `--panel-void-ink` (matching
// `.cell.dark .nm`/`.cell.dark .n`) -- `absent_unbuilt` additionally dims to `opacity:.72`
// (`.cell.unbuilt`). BORDER_CLASS above is unchanged.
//
// #312 retrospective gap closure: the gradient formula is typed exactly once, in
// `@/lib/absence-hatch`'s `hatchBackgroundImage()` -- see Metric.tsx's header for the full story.
const SURFACE_CLASS: Record<MetricType["state"], string> = {
  measured: "bg-panel-raised text-panel-bone",
  absent_no_data: "bg-panel-void text-panel-void-ink",
  absent_unbuilt: "bg-panel-void text-panel-void-ink opacity-[.72]",
};
const HATCH: Record<MetricType["state"], string | undefined> = {
  measured: undefined,
  absent_no_data: hatchBackgroundImage("--panel-hatch"),
  absent_unbuilt: hatchBackgroundImage("--panel-hatch"),
};

export function MetricCell({ metric }: { metric: MetricType }) {
  const d = describeMetric(metric);
  return (
    <div
      data-testid="metric-root"
      data-metric-state={metric.state}
      className={
        "inline-flex w-28 flex-col items-start gap-0.5 rounded px-2 py-1.5 " +
        SURFACE_CLASS[metric.state] + " " + BORDER_CLASS[metric.state]
      }
      style={{ backgroundImage: HATCH[metric.state] }}
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
