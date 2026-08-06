// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md Decision (a) / Step 1.
//
// Plain, browser-free presentation logic: no React import. <Metric>/<MetricCell> (JSX, Step 3)
// call describeMetric() and map its output onto markup -- they add no `state` narrowing or
// absence logic of their own. Keeping this logic here, not inline in JSX, is what lets it be
// proven with plain node:assert in prove-metric-view-behavior.mjs, in the always-on gate, rather
// than pushed entirely into the CI-only Playwright proof (Decision (a)'s own reasoning).
import type { Metric } from "./api/metric";

/** What a component needs to render one Metric, with all `state` narrowing already done. */
export interface DescribedMetric {
  /** The formatted numeral, or `null` for either absent state -- never a fabricated number. */
  numeral: string | null;
  /** "numerator/denominator", present only for `measured` -- `null` otherwise. */
  coverageText: string | null;
  /** The backend's own `reason` string, present only for the two absent states. */
  reasonText: string | null;
  /** Static per-state copy naming what would fix the absence (Decision (c)) -- never parsed
   * from `reasonText`, so this holds regardless of backend wording. `null` for `measured`. */
  fixText: string | null;
}

// One fixed sentence per absent `state`, matching spec §3.1's own framing: "time and usage fix
// this" (absent_no_data) vs. "only a code change fixes this" (absent_unbuilt). Rendered
// ALONGSIDE the backend `reason`, never instead of it.
const FIX_TEXT: Record<"absent_no_data" | "absent_unbuilt", string> = {
  absent_no_data: "Time and usage will fix this -- no code change is needed.",
  absent_unbuilt: "Only a code change will fix this -- no amount of time or usage will.",
};

function formatNumeral(value: number): string {
  return String(value);
}

export function describeMetric(metric: Metric): DescribedMetric {
  if (metric.state === "measured") {
    return {
      numeral: formatNumeral(metric.value),
      coverageText: `${metric.coverage.numerator}/${metric.coverage.denominator}`,
      reasonText: null,
      fixText: null,
    };
  }
  return {
    numeral: null,
    coverageText: null,
    reasonText: metric.reason,
    fixText: FIX_TEXT[metric.state],
  };
}
