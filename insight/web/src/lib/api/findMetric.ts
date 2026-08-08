// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #313 [E20.S2], .sdlc/plans/313.md Step 1. Moved verbatim out of
// src/app/delivery/page.tsx (issue #312 [E20.S1] Goal B, Task B3), where it was a module-private
// helper -- the manager page (src/app/manager/page.tsx) needs the identical lookup, and
// duplicating it is exactly the kind of copy this repo's owner blocks. Both pages now import this
// one definition.
import type { Metric } from "./metric";

export function findMetric(metrics: readonly Metric[], id: number): Metric {
  const metric = metrics.find((m) => m.id === id);
  if (!metric) {
    throw new Error(
      `insight web delivery did not return catalog id ${id} -- collect_metrics() is contract-` +
      "guaranteed to return all 42 catalog entries (test_api_metrics_route.py's own contract " +
      "test); a missing id here is a transport bug, not a legitimate absence state.",
    );
  }
  return metric;
}
