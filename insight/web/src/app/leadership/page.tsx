// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #314 [E20.S3]. The leadership view: a curated subset of hero readouts drawn from the same
// catalog /delivery already serves (see .sdlc/plans/314.md Decision A for why no new CLI action
// or API surface was added, and for the id-13 exclusion). No BandBoard/charts here -- nine
// curated ids already fit as heroes; a second, denser rendering of the identical nine ids would
// duplicate content, not add information. Structurally identical to app/manager/page.tsx
// (.sdlc/plans/313.md's own template for this story).
export const dynamic = "force-dynamic";

import { Metric } from "@/components/Metric";
import { IntegrityStrip } from "@/components/IntegrityStrip";
// NOTE: this page is at /leadership but imports from `delivery/pythonBridge` -- that module's own
// contract (see its header) is already domain-neutral (every catalog metric, aggregate-only, no
// `--actor`), so it is reused as-is rather than renamed; see .sdlc/plans/313.md §1 for the full
// reasoning behind not renaming the file for a purely cosmetic gain.
import { fetchDeliveryMetrics } from "@/lib/delivery/pythonBridge";
import { findMetric } from "@/lib/api/findMetric";
import { LEADERSHIP_PRIMARY_READOUT_IDS } from "@/lib/leadership/curation";

export default async function LeadershipPage() {
  const metrics = await fetchDeliveryMetrics();
  const curated = LEADERSHIP_PRIMARY_READOUT_IDS.map((id) => findMetric(metrics, id));

  return (
    <main className="flex flex-col gap-10">
      <header className="panel-rise flex flex-col gap-1">
        <span className="panel-label panel-label-accent">Leadership</span>
        <h1 className="text-panel-bone" style={{ fontSize: "var(--panel-text-title)", letterSpacing: "-0.01em" }}>
          Speed, quality and portfolio health
        </h1>
      </header>

      {/* Scoped to the 9 curated metrics, not the full 42 -- reports THIS instrument's own
          coverage ("N of 9 metrics are absent, not zero"), not /delivery's full-catalog figure. */}
      <IntegrityStrip metrics={curated} />

      <section aria-label="Primary readouts" className="flex flex-col gap-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {curated.map((metric, i) => (
            <Metric key={metric.id} metric={metric} index={i} />
          ))}
        </div>
      </section>
    </main>
  );
}
