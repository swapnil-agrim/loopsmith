// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// The ONE place that decides what text a metric may show. Both Metric.tsx and MetricCell.tsx go
// through it, so a measured reading and an absent one can never disagree about their wording
// depending on which component rendered them.
import type { Metric } from "./api/metric";

export interface DescribedMetric {
  numeral: string | null;
  coverageText: string | null;
  reasonText: string | null;
  fixText: string | null;
  /** The same claim as `fixText`, short enough to survive board density. */
  fixShort: string | null;
  /** The metric's own one-line meaning, straight from its .sql header. Null when it has none. */
  question: string | null;
  /** Unbuilt metrics only: the specific next step that would fill this gap. */
  gapHint: string | null;
}

const FIX_TEXT: Record<"absent_no_data" | "absent_unbuilt", string> = {
  absent_no_data: "Time and usage will fix this -- no code change is needed.",
  absent_unbuilt: "Only a code change will fix this -- no amount of time or usage will.",
};

/** A two-word form of FIX_TEXT, for the 128px board cell where the full sentence cannot fit.
 *
 * It lives HERE, beside the sentence it abbreviates, rather than in MetricCell -- describeMetric is
 * the one place allowed to decide what a metric may say, and a component inventing its own shorter
 * wording is exactly how the hero readout and the board cell start disagreeing about the same
 * metric. Truncating the long sentence with CSS was the alternative and is worse: it rendered as
 * "Only a code chang..." on 36 of 42 cells, which is noise, not information.
 *
 * The distinction preserved is the one that matters: whether waiting fixes this, or only writing
 * code does. */
const FIX_SHORT: Record<"absent_no_data" | "absent_unbuilt", string> = {
  absent_no_data: "Needs data",
  absent_unbuilt: "Needs code",
};

/** Seconds as the largest unit that keeps the number small enough to read at a glance.
 *
 * The panel previously printed `String(value)` for everything, so cycle time read "4525.5" and
 * lead time "11734.5" -- raw seconds, which nobody converts in their head, sitting beside a
 * "0.9038" that is a ratio. Three different kinds of quantity, formatted identically, is how a
 * dense board stops being readable.
 *
 * Boundaries are chosen so the unit changes before the number gets long, not after: 5400s is 90
 * minutes, past which "1.5h" beats "90m". */
function formatSeconds(value: number): string {
  const s = Math.abs(value);
  if (s < 90) return `${value.toFixed(0)}s`;
  if (s < 5400) return `${(value / 60).toFixed(0)}m`;
  if (s < 172800) return `${(value / 3600).toFixed(1)}h`;
  return `${(value / 86400).toFixed(1)}d`;
}

/** A 0..1 fraction as a percentage.
 *
 * One decimal place, and NOT more: `0.9038` became `90.4%`, which is the same claim in the form
 * people actually compare. Deliberately never rounded to a whole number -- 0.4 of a percentage
 * point is real signal on a rate computed over 52 goals, and dropping it would quietly widen the
 * claim beyond what was measured. */
function formatRatio(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/** No unit was declared for this metric, so it is printed as-is rather than guessed at.
 *
 * Trailing-zero trimming only: `4525.50` -> `4525.5`. This is the honest fallback for a metric
 * that has a value but whose unit nobody has stated yet -- inventing a unit here would be the
 * same class of fabrication as inventing a coverage denominator. */
function formatBare(value: number): string {
  return Number.isInteger(value) ? String(value) : String(parseFloat(value.toFixed(4)));
}

export function formatNumeral(value: number, unit: string | null | undefined): string {
  switch (unit) {
    case "seconds":
      return formatSeconds(value);
    case "ratio":
      return formatRatio(value);
    case "count":
      return String(Math.round(value));
    default:
      return formatBare(value);
  }
}

export function describeMetric(metric: Metric): DescribedMetric {
  if (metric.state === "measured") {
    return {
      numeral: formatNumeral(metric.value, metric.unit),
      coverageText: `${metric.coverage.numerator}/${metric.coverage.denominator}`,
      reasonText: null,
      fixText: null,
      fixShort: null,
      question: metric.question ?? null,
      // No gap hint on a measured metric -- there is no gap. Deliberately not written as a
      // `state === "absent_unbuilt"` check here: in this arm `metric` is already narrowed to
      // MeasuredMetric, whose `state` is the literal "measured", so that comparison is a
      // TS2367 "types have no overlap" compile error.
      gapHint: null,
    };
  }
  return {
    numeral: null,
    coverageText: null,
    reasonText: metric.reason,
    fixText: FIX_TEXT[metric.state],
    fixShort: FIX_SHORT[metric.state],
    question: metric.question ?? null,
    gapHint: metric.state === "absent_unbuilt" ? (metric.gapHint ?? null) : null,
  };
}
