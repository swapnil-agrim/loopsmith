// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] Goal B, Task B3 -- the author's own decision, closing what plan §7 left
// open: the issue body names "the 42-metric instrumentation board, primary readouts, AND the flow
// charts" as in scope; its Out-of-scope section defers only chart INTERACTIVITY to E21. Ports
// insight/dash/panel.py's `_bars()` (panel.py:223-255) and `_strip()` (panel.py:258-320) as
// static, zero-dependency inline SVG (no pan/zoom/tooltip/hover state, matching the Python
// original's own hand-rolled-SVG-because-the-repo-is-zero-dep-by-policy framing,
// panel.py:224-225) -- translated from f-string markup into JSX, same math, same layout.
//
// WHY BOTH CHARTS RENDER ABSENT TODAY, AND WHY THAT IS CORRECT, NOT A BUG. This story's transport
// (Task B2) resolves through insight.api.metrics.collect_metrics() -- the SAME 42-metric union
// GET /metrics serves -- not panel.py's own collect(), which pulls a SEPARATE, richer query set
// (per-day merge counts for the bars chart, the full cycle-time spread for the strip chart) that
// the Metric union has no field for. Wiring a new data channel for exactly these two charts is out
// of this story's declared scope (.sdlc/plans/312.md §3a: the transport reuses the API's own
// already-tested resolver rather than inventing a second one; §8: registering new extractors for
// the other 41 metrics is explicitly out of scope, and the same "not this story's job" applies to
// data these charts alone would need). page.tsx therefore calls both components with empty data,
// and -- per the SAME absence rule as every other cell on this page -- they render the shared
// achromatic, hatched material and NO axis/numeral, honestly naming what would fix it, exactly
// like panel.py's own `_bars`/`_strip` already do for an empty `daily`/`spread`
// (panel.py:226-227, 271-272: `NO SENSOR &mdash; ...`). This is the SAME "correct, not broken"
// shape as 41 of the 42 board cells (insight/api/metrics.py:55-57: only metric id 12 has a
// registered VALUE_EXTRACTOR) -- extending this story to also wire per-day/spread extractors is
// explicitly out of scope (§8's own non-negotiable).
//
// The absence block below reuses the SAME hatch token (`--panel-hatch`) Task B1 just ported onto
// MetricCell.tsx -- one absence material across the product, not a third local treatment.
//
// #312 retrospective gap closure: this used to hand-type its own copy of the gradient formula --
// a THIRD independently-typed copy, after Metric.tsx's and MetricCell.tsx's, exactly the "second
// absence vocabulary" the issue's own decision comment forbids ("Do not build a local hatched
// treatment inside the delivery panel -- one absence material across the product is the entire
// point"). It now calls the same `hatchBackgroundImage()` those two call, with the same token this
// file already used -- see `@/lib/absence-hatch`'s header for the full story.
import { hatchBackgroundImage } from "@/lib/absence-hatch";

const HATCH_BACKGROUND_IMAGE = hatchBackgroundImage("--panel-hatch");

function NoSensor({ reason }: { reason: string }) {
  return (
    <div
      data-testid="chart-absent"
      className="flex h-32 items-center justify-center rounded border-2 border-dotted border-panel-void-edge bg-panel-void text-panel-void-ink"
      style={{ backgroundImage: HATCH_BACKGROUND_IMAGE, fontSize: "var(--panel-text-small)" }}
    >
      NO SENSOR &mdash; {reason}
    </div>
  );
}

const MONO = "var(--panel-font-mono, ui-monospace, monospace)";

/** Seconds as the coarsest unit that still reads precisely -- ported verbatim (math and
 * thresholds unchanged) from panel.py's own `_dur()`. */
function formatDuration(sec: number): string {
  if (sec < 90) return `${sec.toFixed(0)}s`;
  if (sec < 5400) return `${(sec / 60).toFixed(0)}m`;
  if (sec < 172800) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}

export interface DailyCount {
  readonly date: string; // "YYYY-MM-DD"
  readonly count: number;
}

/** Goals landed per day. Ported from panel.py's `_bars()` -- identical padding/gridline/bar math,
 * translated from f-string SVG text into JSX. Renders the shared absence block, never an empty
 * axis, when `daily` is empty (mirrors panel.py:226-227's own `if not daily: return "NO SENSOR"`). */
export function BarsChart({ daily }: { daily: readonly DailyCount[] }) {
  if (daily.length === 0) {
    return <NoSensor reason="no merge events ingested" />;
  }
  const w = 620;
  const h = 150;
  const padL = 34;
  const padB = 26;
  const peak = Math.max(...daily.map((d) => d.count)) || 1;
  const bw = (w - padL) / daily.length;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label="Goals landed per day">
      {[0, 1, 2, 3, 4].map((i) => {
        const y = h - padB - ((h - padB) * i) / 4;
        const v = (peak * i) / 4;
        return (
          <g key={i}>
            <line x1={padL} y1={y} x2={w} y2={y} stroke="var(--panel-grid)" strokeWidth={1} />
            <text x={0} y={y - 3} fill="var(--panel-faint)" fontSize={9} fontFamily={MONO}>
              {v.toFixed(0)}
            </text>
          </g>
        );
      })}
      {daily.map((d, i) => {
        const bh = (h - padB) * (d.count / peak);
        const x = padL + i * bw + bw * 0.18;
        const bwid = bw * 0.64;
        const y = h - padB - bh;
        return (
          <g key={d.date}>
            <rect x={x} y={y} width={bwid} height={bh} fill="var(--panel-amber)" rx={1} />
            <text
              x={x + bwid / 2} y={y - 5} fill="var(--panel-bone)" fontSize={10.5}
              textAnchor="middle" fontFamily={MONO}
            >
              {d.count}
            </text>
            <text
              x={x + bwid / 2} y={h - 8} fill="var(--panel-faint)" fontSize={9}
              textAnchor="middle" fontFamily={MONO}
            >
              {d.date.slice(5)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

const DURATION_TICKS = [60, 300, 900, 1800, 3600, 7200, 21600, 86400, 172800];

/** Cycle-time distribution as a log-scale strip plot with p50/p85 marked -- ported from
 * panel.py's `_strip()`, including its log10-axis reasoning (a linear axis compresses the bulk of
 * the points into the leftmost fifth once one long-tail outlier exists, panel.py:263-266) and its
 * label-collision handling when p50/p85 render close together. Renders the shared absence block,
 * never an empty axis, when `spread` carries no positive value (mirrors panel.py:271-272's own
 * `if not vals: return "NO SENSOR"`). */
export function StripChart({
  spread, p50, p85,
}: {
  spread: readonly number[];
  p50: number | null;
  p85: number | null;
}) {
  const vals = spread.filter((v) => v > 0).sort((a, b) => a - b);
  if (vals.length === 0) {
    return <NoSensor reason="cycle time not measured" />;
  }
  const w = 620;
  const h = 132;
  const padL = 10;
  const iw = w - padL * 2;
  const base = 74;
  const lo = vals[0];
  const hi = vals[vals.length - 1];
  const llo = Math.log10(lo);
  const lhi = Math.log10(hi);
  const span = lhi - llo || 1;
  const x = (v: number) => padL + iw * ((Math.log10(Math.max(v, 1)) - llo) / span);

  const marks: { v: number; label: string; color: string }[] = [];
  if (p50 !== null) marks.push({ v: p50, label: "p50", color: "var(--panel-amber)" });
  if (p85 !== null) marks.push({ v: p85, label: "p85", color: "var(--panel-red)" });
  const close = marks.length === 2 && Math.abs(x(marks[0].v) - x(marks[1].v)) < 76;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img"
      aria-label="Cycle time distribution, log scale"
    >
      {DURATION_TICKS.filter((t) => lo <= t && t <= hi).map((tick) => (
        <g key={tick}>
          <line x1={x(tick)} y1={34} x2={x(tick)} y2={base + 8} stroke="rgba(233,227,214,.08)" />
          <text
            x={x(tick)} y={h - 16} fill="var(--panel-faint)" fontSize={9}
            textAnchor="middle" fontFamily={MONO}
          >
            {formatDuration(tick)}
          </text>
        </g>
      ))}
      <line x1={padL} y1={base} x2={w - padL} y2={base} stroke="rgba(233,227,214,.14)" />
      {vals.map((v, i) => (
        <line
          key={i} x1={x(v)} y1={base - 16} x2={x(v)} y2={base + 8}
          stroke="var(--panel-cyan)" strokeWidth={1.5} opacity={0.5}
        />
      ))}
      {marks.map((m, i) => {
        const anchor = close ? (i === 0 ? "end" : "start") : "middle";
        const dx = close ? (i === 0 ? -4 : 4) : 0;
        return (
          <g key={m.label}>
            <line x1={x(m.v)} y1={26} x2={x(m.v)} y2={base + 8} stroke={m.color} strokeWidth={1.5} />
            <text
              x={x(m.v) + dx} y={20} fill={m.color} fontSize={10}
              textAnchor={anchor} fontFamily={MONO}
            >
              {m.label} {formatDuration(m.v)}
            </text>
          </g>
        );
      })}
      <text x={padL} y={h - 3} fill="var(--panel-faint)" fontSize={9} fontFamily={MONO}>
        fastest {formatDuration(lo)}
      </text>
      <text x={w - padL} y={h - 3} fill="var(--panel-faint)" fontSize={9} textAnchor="end" fontFamily={MONO}>
        log scale &middot; slowest {formatDuration(hi)}
      </text>
    </svg>
  );
}
