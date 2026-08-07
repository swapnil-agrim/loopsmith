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
//
// issue #312 [E20.S1] Goal B, Task B1: the hatch fill, ported VERBATIM from
// insight/dash/instrument.py's `.ro.absent` rule (instrument.py:84-85) -- the author's own spec
// defect (decision (b) on the issue), not #304's: "hatched, achromatic" was written into every
// E20 story's done-when without the primitive ever painting with `--panel-hatch`/
// `--panel-hatch-soft` (both already defined, tokens.generated.css:9, unreferenced until now).
// The base fill (`bg-panel-panel`) and BORDER_CLASS are UNCHANGED: hatch is a second background
// LAYER (an inline `backgroundImage`, matching this file's own existing convention of mixing
// Tailwind classes with `style={{ fontSize: "var(--panel-text-*)" }}` for CSS-var-driven values),
// not a replacement for the dashed/dotted border distinction.
//
// #312 retrospective gap closure: the gradient formula itself is typed exactly once now, in
// `@/lib/absence-hatch`'s `hatchBackgroundImage()` -- MetricCell.tsx and delivery/charts.tsx's
// NoSensor both call the same function (with the token each already used) instead of each hand-
// typing their own copy of this string. See that module's header for the full story.
import type { Metric as MetricType } from "@/lib/api/metric";
import { hatchBackgroundImage } from "@/lib/absence-hatch";
import { describeMetric } from "@/lib/metric-view";

const BORDER_CLASS: Record<MetricType["state"], string> = {
  measured: "border-2 border-solid border-panel-rule-hard",
  absent_no_data: "border-2 border-dashed border-panel-void-edge",
  absent_unbuilt: "border-2 border-dotted border-panel-void-edge",
};

// `undefined` for `measured` -- React omits a `backgroundImage` style property entirely when its
// value is `undefined`, so `getComputedStyle(root).backgroundImage` reads "none" for a measured
// readout, the exact negative control prove-absence-primitives-render.mjs asserts (hatch is
// state-gated, not always-on).
const HATCH: Record<MetricType["state"], string | undefined> = {
  measured: undefined,
  absent_no_data: hatchBackgroundImage("--panel-hatch-soft"),
  absent_unbuilt: hatchBackgroundImage("--panel-hatch-soft"),
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
      style={{ backgroundImage: HATCH[metric.state] }}
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
