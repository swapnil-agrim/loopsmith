// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #301 [E16.S3], .sdlc/plans/301.md Decision (e) and Files.
//
// Two small, permanent proof functions -- not product code (E17.S3 is where a real component
// consumes `Metric`; out of scope here). Both compile as part of every `npm run typecheck`, and
// both are reused UNMODIFIED by scripts/prove-metric-contract-safety.mjs's `npm run test`
// scenarios, which is the point: these functions never change, only the schema underneath them
// does.
import type { Metric } from "./metric.ts";

/**
 * Criterion 3's positive case: `metric.state` must be narrowed to `"measured"` before `.value`
 * is reachable at all -- `AbsentNoDataMetric`/`AbsentUnbuiltMetric` carry no `value` field
 * (insight/api/models.py's `extra="forbid"` is the runtime side of this; this is the
 * compile-time side). Returns `null` for either absent state instead of throwing, matching this
 * repo's ABSENT-is-never-PASS doctrine one layer up: a caller that forgets to check for `null`
 * still cannot accidentally read a fabricated number.
 */
export function measuredValueOrNull(metric: Metric): number | null {
  if (metric.state === "measured") {
    return metric.value;
  }
  return null;
}

/**
 * Reads `label`, a field every `Metric` variant shares, so no `state` narrowing is needed here.
 * Reused UNMODIFIED by prove-metric-contract-safety.mjs's criterion-2 scenario: that script
 * mutates the wire property `label` -> `title` in a copy of openapi.json, regenerates
 * schema.d.ts from the mutation, and asserts this exact function -- untouched -- now fails
 * `tsc --noEmit` with `TS2339` (property `label` no longer exists on the mutated schema). That
 * is the mechanical proof for done-when 2: renaming a Pydantic field and regenerating breaks the
 * frontend type-check.
 */
export function metricLabel(metric: Metric): string {
  return metric.label;
}
