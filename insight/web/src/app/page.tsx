// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #302 [E17.S1]. Scaffold only -- no dashboards, no live data fetch (both out of scope per
// the issue). Typing `renderLabel` against the generated `Metric` union wires this page to the
// real API contract at compile time: an unrelated schema change (e.g. `label` renamed) now also
// breaks `npm run typecheck` here, not only in metric.consumer.ts's own proof script.
import type { Metric } from "@/lib/api/metric";

function renderLabel(metric: Metric): string {
  return metric.label;
}

// A real fetch from insight/api/ lands in a later story (E17.S3+); this placeholder only proves
// the union type-checks end to end from schema.d.ts through to a page.
const placeholder: Metric = {
  id: 0,
  label: "LoopSmith Insight",
  reason: "the dashboard has no live metrics yet -- scaffold only (E17.S1)",
  reliabilityClass: 0,
  state: "absent_unbuilt",
};

export default function Home() {
  // TEMPORARY (issue #302, demo 4 of 4): a deliberate BUILD-time failure, to watch the `web`
  // required check go red at `npm run build` specifically. `document` is legal browser-side JS and
  // `dom` is in tsconfig's `lib`, so tsc cannot catch it and ESLint has no rule for it -- it
  // survives typecheck, lint AND test, and dies only in next build's server-side prerender with
  // "ReferenceError: document is not defined". Reverted in the next commit.
  const title = document.title;
  return (
    <main data-demo={title}>
      <h1>{renderLabel(placeholder)}</h1>
    </main>
  );
}
