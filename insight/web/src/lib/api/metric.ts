// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #301 [E16.S3], .sdlc/plans/301.md Decision (d).
//
// HAND-WRITTEN, not generated -- exempt from schema.d.ts's regenerate-and-diff check
// (scripts/check-schema-fresh.mjs only ever regenerates and diffs schema.d.ts by its own fixed
// path; it never touches this file).
//
// openapi-typescript@7.13.0 renders the /metrics response's `oneOf`/`discriminator` schema as
// three inline object types under `components["schemas"]` (verified live -- see schema.d.ts's
// own `metrics_metrics_get` operation), but emits no top-level `Metric` export of its own. This
// file supplies that missing name, so every consumer imports one `Metric` type rather than
// re-deriving the same three-way union at every call site.
import type { components } from "./schema.d.ts";

export type Metric =
  | components["schemas"]["MeasuredMetric"]
  | components["schemas"]["AbsentNoDataMetric"]
  | components["schemas"]["AbsentUnbuiltMetric"];
