// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #304 [E17.S3], .sdlc/plans/304.md Step 5.
//
// Unlinked dev route, PERMANENTLY -- issue #305 [E17.S4] decided this: the route is env-gated out
// of every production build (INSIGHT_DEV_ROUTES, see below), so a nav entry pointing at it would
// 404 for every real user. src/lib/nav.ts never lists it, in either linked or placeholder form,
// and scripts/prove-nav-items.mjs asserts that stays true. Also now carries one deliberately-wide
// fixture element (data-testid="dev-wide-fixture") for scripts/prove-shell-responsive-frame.mjs
// (CI-only) to exercise the shell's own-container-scrolls-not-the-page guarantee against.
//
// Exists solely so scripts/prove-absence-primitives-render.mjs (Step 6, CI-only) has something to
// visit: hardcoded fixture Metric literals, one per state, rendered through both <Metric> and
// <MetricCell>.
//
// GATED, not merely unlinked: insight/Dockerfile.web's runtime stage serves every route under
// app/ regardless of nav links ("unlinked" means "not in the nav," not "not built"). This is a
// plain env-var gate, deliberately not a NODE_ENV check -- `next build`/`next start`/the shipped
// image's `node server.js` all run in Next's production mode already, so NODE_ENV cannot
// distinguish CI's own proof build from the build that ships. INSIGHT_DEV_ROUTES is set to "1"
// only by .github/workflows/ci.yml's `web` job (job-level env); insight/Dockerfile.web's builder
// stage runs its own `npm run build` inside an isolated `docker build` context that never sees
// that env (GitHub Actions `env:` does not propagate into `docker build` without an explicit
// `--build-arg`, and none is added), so the shipped image bakes this route out unconditionally,
// with no Dockerfile change needed.
//
// No dynamic data -> Next statically prerenders this page once at `next build` time, so the env
// check below resolves once, at build time -- there is no runtime toggle.
import { notFound } from "next/navigation";

import type { Metric } from "@/lib/api/metric";
import { Metric as MetricReadout } from "@/components/Metric";
import { MetricCell } from "@/components/MetricCell";

const MEASURED: Metric = {
  id: 101,
  label: "Autonomy rate",
  reliabilityClass: 2,
  state: "measured",
  value: 0.82,
  coverage: { numerator: 41, denominator: 50 },
};

const ABSENT_NO_DATA: Metric = {
  id: 102,
  label: "Cycle time",
  reliabilityClass: 1,
  state: "absent_no_data",
  reason: "metric_102 has no value yet",
};

const ABSENT_UNBUILT: Metric = {
  id: 103,
  label: "Escape rate",
  reliabilityClass: 2,
  state: "absent_unbuilt",
  reason: "no escape_rate.sql exists yet -- only a code change can build this metric",
};

// Every case ID below is load-bearing for Step 6's Playwright script -- it scopes its locators
// as `[data-testid="case-<kind>-<id>"] [data-testid="metric-numeral"]` etc., so a typo'd ID here
// breaks that script's selectors, not silently passes them vacuously (the script asserts the
// scoped selector resolves to exactly one element before asserting anything about its content).
const CASES: ReadonlyArray<{ id: string; metric: Metric }> = [
  { id: "measured", metric: MEASURED },
  { id: "absent-no-data", metric: ABSENT_NO_DATA },
  { id: "absent-unbuilt", metric: ABSENT_UNBUILT },
];

export default function AbsenceStatesDevPage() {
  if (process.env.INSIGHT_DEV_ROUTES !== "1") {
    notFound();
  }

  return (
    <main style={{ display: "flex", flexDirection: "column", gap: "1.5rem", padding: "2rem" }}>
      <h1>Absence states (dev only)</h1>

      <section>
        <h2>Metric</h2>
        <div style={{ display: "flex", gap: "1rem" }}>
          {CASES.map(({ id, metric }) => (
            <div key={id} data-testid={`case-metric-${id}`}>
              <MetricReadout metric={metric} />
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>MetricCell</h2>
        <div style={{ display: "flex", gap: "1rem" }}>
          {CASES.map(({ id, metric }) => (
            <div key={id} data-testid={`case-cell-${id}`}>
              <MetricCell metric={metric} />
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Shell reflow fixture (E17.S4)</h2>
        {/* Deliberately wide -- exists so scripts/prove-shell-responsive-frame.mjs (CI-only) has
            something inside shell-content wide enough to overflow ITS container without
            overflowing the page. Not a real dashboard element; this page never ships (env gate
            above). */}
        <div data-testid="dev-wide-fixture" style={{ width: "2400px", height: "40px" }} />
      </section>
    </main>
  );
}
