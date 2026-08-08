// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// The 42-cell wall, collapsed to one row per band. Density becomes opt-in: each row says what is
// instrumented in that band at a glance, and expands to the cells on click. NOTHING is removed --
// the same 42 cells are one interaction away, which is the difference between decluttering and
// hiding.
//
// A SERVER Component, deliberately. <details>/<summary> toggles natively with no JavaScript and
// this holds no state, so "use client" would buy nothing and would push MetricCell plus 42 metric
// objects into the client bundle.
import type { Metric as MetricType } from "@/lib/api/metric";

import { MetricCell } from "./MetricCell";

export function BandBoard({
  metrics,
  bands,
}: {
  metrics: readonly MetricType[];
  bands: ReadonlyArray<{ name: string; ids: readonly number[] }>;
}) {
  const byId = new Map(metrics.map((m) => [m.id, m]));

  return (
    <div className="flex flex-col gap-2">
      {bands.map((band) => {
        // THROW, never drop. delivery/page.tsx's own findMetric treats a missing id as "a
        // transport bug, not a legitimate absence state", and silently shortening the board
        // would also push prove-delivery-cold-start-no-numerals.mjs's `numerals.length >= 46`
        // floor below threshold -- failing with a misleading message about empty slots rather
        // than the real cause.
        const present = band.ids.map((id) => {
          const m = byId.get(id);
          if (!m) {
            throw new Error(
              `BandBoard: catalog id ${id} is missing from the metrics payload -- ` +
              "collect_metrics() is contract-guaranteed to return all 42 entries, so this is a " +
              "transport bug, not an absence state.",
            );
          }
          return m;
        });
        const live = present.filter((m) => m.state === "measured").length;

        return (
          <details
            key={band.name}
            className="group rounded border border-panel-rule bg-panel-panel"
          >
            <summary className="flex cursor-pointer list-none items-center gap-3.5 px-4 py-3">
              <span
                className="min-w-[9.5rem] shrink-0 text-panel-bone"
                style={{ fontSize: "var(--panel-text-body)" }}
              >
                {band.name}
              </span>

              {/* One pip per metric: lit when it has a reading, void + hairline when it does not.
                  Same mint as every other "live" cue on the page, so the colour means one thing
                  everywhere. */}
              <span className="flex flex-1 flex-wrap gap-[3px]" aria-hidden="true">
                {present.map((m) => (
                  <span
                    key={m.id}
                    title={`${m.label} — ${m.state === "measured" ? "measured" : "not measured"}`}
                    className="h-[7px] w-[14px] rounded-[2px]"
                    style={
                      m.state === "measured"
                        ? {
                            background:
                              "linear-gradient(180deg,var(--panel-cyan),var(--panel-cyan-deep))",
                            boxShadow: "0 0 8px var(--panel-glow)",
                          }
                        : {
                            background: "var(--panel-void)",
                            boxShadow: "inset 0 0 0 1px var(--panel-void-edge)",
                          }
                    }
                  />
                ))}
              </span>

              <span
                className="panel-num shrink-0 text-panel-dim"
                style={{ fontSize: "var(--panel-text-caption)" }}
              >
                {live}/{band.ids.length}
              </span>
              <span
                aria-hidden="true"
                className="w-3 shrink-0 text-panel-faint transition-transform group-open:rotate-90"
                style={{ fontSize: "var(--panel-text-caption)" }}
              >
                ▸
              </span>
            </summary>

            <div className="flex flex-wrap gap-2 border-t border-panel-rule px-4 py-3.5">
              {present.map((m, i) => (
                <MetricCell key={m.id} metric={m} index={i} />
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}
