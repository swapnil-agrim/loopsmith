// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #315 [E20.S4], .sdlc/plans/315.md Decision D2/Task 3. The shared absence-governed
// scalar-readout primitive for /ic's five bespoke, actor-scoped counts (my queue, blocked on me,
// my parks, my gate verdicts given, my cost) -- none of which have a catalog id, so none of them
// can go through <Metric> itself (that component reads a catalog `MetricType`, which these are
// not). This is the SAME absence contract (surface, edge, chroma, hatch), reused rather than
// reinvented, at hero density, with `state` computed by the caller (page.tsx) per D1's per-table
// gating rule, not derived here.
//
// REUSES Metric.tsx's OWN testid convention (D2), not a new one: the numeral element carries the
// identical `data-testid="metric-numeral"` scripts/lib/cold-start-proof.mjs's NUMERAL_SLOT_RE is
// already keyed on, so the shared digit-scan harness needs zero changes to cover these five tiles.
// `data-readout-id` is a SECOND, distinguishing attribute added purely so a specific proof can
// assert an exact value for one particular tile without disturbing the generic scan every other
// proof already relies on -- see this file's own header note in .sdlc/plans/315.md Decision D2 for
// the rejected alternative (a second testid) and why it was rejected.
import type { Metric as MetricType } from "@/lib/api/metric";
import { FIX_TEXT } from "@/lib/metric-view";
import { EDGE, HATCH } from "./Metric";

// The SAME state union the catalog metrics use, aliased rather than re-declared: these tiles carry
// no catalog id, but they render the identical absence contract, and a separately-typed copy is
// exactly what lets the two drift. EDGE/HATCH come from Metric.tsx for the same reason -- a review
// of #315 caught them hand-copied here and byte-identical, which holds only until someone edits one.
export type ActorReadoutState = MetricType["state"];

export function ActorReadout({
  label,
  state,
  numeralText,
  reasonText,
  readoutId,
  index = 0,
}: {
  label: string;
  state: ActorReadoutState;
  numeralText: string | null;
  reasonText: string | null;
  readoutId: string;
  index?: number;
}) {
  const isMeasured = state === "measured";
  const fixText = state === "measured" ? null : FIX_TEXT[state];

  return (
    <div
      data-testid="metric-root"
      data-metric-state={state}
      title={reasonText ?? undefined}
      className={`panel-accent panel-rise flex min-w-[190px] flex-1 flex-col px-4 py-3.5 ${EDGE[state]}`}
      style={{
        backgroundImage: HATCH[state],
        animationDelay: `${Math.min(index, 8) * 55}ms`,
      }}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="panel-label truncate">{label}</span>
        {isMeasured && (
          <span
            aria-hidden="true"
            className="h-[5px] w-[5px] shrink-0 rounded-full bg-panel-cyan"
            style={{ boxShadow: "0 0 8px var(--panel-cyan)" }}
          />
        )}
      </div>

      <div
        data-testid="metric-numeral"
        data-readout-id={readoutId}
        className={`panel-num mt-1 ${isMeasured ? "panel-num-live" : "text-panel-void-ink"}`}
        style={{ fontSize: "var(--panel-text-display)" }}
      >
        {numeralText ?? ""}
      </div>

      {!isMeasured && reasonText !== null && (
        <div
          data-testid="metric-reason"
          className="mt-1.5 text-panel-void-ink"
          style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.45 }}
        >
          {reasonText}
        </div>
      )}
      {!isMeasured && fixText !== null && (
        <div
          data-testid="metric-fix"
          className="mt-1 text-panel-faint"
          style={{ fontSize: "var(--panel-text-caption)", lineHeight: 1.4 }}
        >
          {fixText}
        </div>
      )}
    </div>
  );
}
