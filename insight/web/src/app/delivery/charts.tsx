// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// Hand-written SVG, no charting dependency. That is not asceticism: no chart library has a
// first-class concept of "this series was never measured", so every one of them renders an absent
// series as an empty axis -- a drawn zero, which is the single failure this product exists to
// prevent. Owning the marks means absence gets its own material (NoSensor below) instead of an
// empty grid that looks like a real measurement of nothing.
//
// WHAT CHANGED AND WHY. These charts previously received hardcoded empty arrays from page.tsx and
// therefore always drew NoSensor. For "goals landed" that was true. For cycle time it was NOT:
// metric_2 held 50 of 50 non-null durations while the panel said "cycle time not measured". A
// false absence is the same class of lie as a false reading, so the charts now take real series
// from insight.api.series (see its module docstring) and only claim absence when the series
// actually says so.
import type { Distribution, WeeklyThroughput } from "@/lib/delivery/pythonBridge";
import { hatchBackgroundImage } from "@/lib/absence-hatch";

const HATCH_BACKGROUND_IMAGE = hatchBackgroundImage("--panel-hatch");
const MONO = "var(--panel-font-mono, ui-monospace, monospace)";

/** The absence material, unchanged in contract from the pre-redesign version: hatched, achromatic,
 * carrying NO numeral of any kind. prove-delivery-cold-start-no-numerals.mjs asserts exactly that
 * for a cold start, and it is the reason a chart may never fall back to an empty axis. */
function NoSensor({ reason }: { reason: string }) {
  return (
    <div
      data-testid="chart-absent"
      className="flex h-40 flex-col items-center justify-center gap-1.5 rounded border border-dotted border-panel-void-edge bg-panel-void px-4 text-center text-panel-void-ink"
      style={{ backgroundImage: HATCH_BACKGROUND_IMAGE }}
    >
      <span className="panel-label" style={{ color: "var(--panel-void-ink)" }}>
        No sensor
      </span>
      <span style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.45 }}>{reason}</span>
    </div>
  );
}

function formatDuration(sec: number): string {
  if (sec < 90) return `${sec.toFixed(0)}s`;
  if (sec < 5400) return `${(sec / 60).toFixed(0)}m`;
  if (sec < 172800) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}

function formatValue(v: number, unit: string): string {
  return unit === "seconds" ? formatDuration(v) : v.toFixed(0);
}

/** Every observation, ranked ascending, drawn as a filled trace.
 *
 * A RANKED TRACE RATHER THAN EQUAL-WIDTH HISTOGRAM BINS, on purpose. This repo's own cycle times
 * run 1,887s to 63,693s -- a 34x spread with a long thin tail. Linear bins pile ~90% of the goals
 * into the first bar and say nothing, and the bin width is a free parameter that can be tuned until
 * the shape flatters. A ranked trace has no such parameter: it plots one mark per observation, and
 * the tail is visible as a tail.
 *
 * The Y AXIS is a separate question from the binning, and this component does switch it to log when
 * the spread demands it -- see the `useLog` comment in the body for the rule and for why the chart
 * says so out loud when it does. (An earlier version of this docstring lumped the two together and
 * rejected log scales outright; that conflated "don't tune bins to flatter the data" with "don't
 * use the axis that makes three decades legible", which are not the same claim.)
 *
 * p50/p85 are drawn as reference lines because a distribution without its quantiles invites the eye
 * to read the maximum as typical. */
export function TraceChart({
  series,
  label,
  gradientId,
}: {
  series: Distribution;
  label: string;
  gradientId: string;
}) {
  if (series.state === "absent") {
    return <NoSensor reason={series.reason} />;
  }

  const { values, unit, p50, p85, min, max, measured, total } = series;
  const w = 620;
  const h = 160;
  const padL = 46;
  const padB = 20;
  const padT = 10;
  const plotW = w - padL - 8;
  const plotH = h - padB - padT;

  // A LOG AXIS WHEN THE DATA DEMANDS ONE, chosen from the data rather than hardcoded.
  //
  // This repo's own cycle times span 1,887s to 63,693s -- 34x. On a linear axis 90% of the goals
  // are pinned to the bottom pixel and the chart reads as "flat, then one spike", which is a
  // worse description of the distribution than no chart at all. A log axis spreads those decades
  // out and shows the actual shape.
  //
  // Applied only when it is BOTH valid and needed: every value strictly positive (log of zero is
  // undefined, and the interventions series legitimately contains zeros) and a spread of at least
  // 20x (below that a linear axis is honest and easier to read). The axis is labelled as log
  // on-chart, because an unlabelled log scale flatters a bad tail into looking controlled.
  const useLog = min > 0 && max / min >= 20;
  const peak = max > 0 ? max : 1;
  const logMin = Math.log10(min > 0 ? min : 1);
  const logMax = Math.log10(peak);
  const logSpan = logMax - logMin || 1;

  const x = (i: number) =>
    padL + (values.length === 1 ? plotW / 2 : (i / (values.length - 1)) * plotW);
  const y = (v: number) =>
    useLog
      ? padT + plotH - ((Math.log10(Math.max(v, min)) - logMin) / logSpan) * plotH
      : padT + plotH - (v / peak) * plotH;
  /** Tick VALUE at each of the 5 gridlines, in data space -- linear or log as chosen above. */
  const tickValue = (i: number) =>
    useLog ? Math.pow(10, logMin + (logSpan * i) / 4) : (peak * i) / 4;

  const line = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(values.length - 1).toFixed(1)},${padT + plotH} L${x(0).toFixed(1)},${padT + plotH} Z`;

  const refs: Array<{ v: number; name: string }> = [];
  if (p85 > 0) refs.push({ v: p85, name: "p85" });
  if (p50 > 0) refs.push({ v: p50, name: "p50" });

  return (
    <div className="flex flex-col gap-1.5">
      <svg
        viewBox={`0 0 ${w} ${h}`}
        width="100%"
        height={h}
        role="img"
        aria-label={`${label}: ${measured} observations, p50 ${formatValue(p50, unit)}, p85 ${formatValue(p85, unit)}`}
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--panel-amber)" stopOpacity="0.42" />
            <stop offset="100%" stopColor="var(--panel-amber)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Graticule. Four lines, labelled in the series' own unit. */}
        {[0, 1, 2, 3, 4].map((i) => {
          const gy = padT + plotH - (plotH * i) / 4;
          return (
            <g key={i}>
              <line x1={padL} y1={gy} x2={w - 8} y2={gy} stroke="var(--panel-grid)" strokeWidth={1} />
              <text x={padL - 6} y={gy + 3} fill="var(--panel-faint)" fontSize={9} fontFamily={MONO} textAnchor="end">
                {formatValue(tickValue(i), unit)}
              </text>
            </g>
          );
        })}

        <path d={area} fill={`url(#${gradientId})`} />
        <path d={line} fill="none" stroke="var(--panel-amber)" strokeWidth={1.75} strokeLinejoin="round" />

        {refs.map((r) => (
          <g key={r.name}>
            <line
              x1={padL} y1={y(r.v)} x2={w - 8} y2={y(r.v)}
              stroke="var(--panel-cyan)" strokeWidth={1} strokeDasharray="4 3" opacity={0.75}
            />
            <text
              x={w - 10} y={y(r.v) - 4} fill="var(--panel-cyan)"
              fontSize={9.5} fontFamily={MONO} textAnchor="end"
            >
              {r.name} {formatValue(r.v, unit)}
            </text>
          </g>
        ))}
      </svg>

      {/* Coverage stated under every chart, same contract as a scalar readout: the reader should
          never have to guess how much of the population the trace is drawn from. */}
      <div className="flex items-baseline justify-between">
        <span className="panel-num text-panel-faint" style={{ fontSize: "var(--panel-text-micro)" }}>
          ranked by value, {measured} observation{measured === 1 ? "" : "s"}
          {/* Never silent about the axis: a log scale that is not announced makes a long tail
              look like a gentle slope. */}
          {useLog ? " · log scale" : ""}
        </span>
        <span className="panel-num text-panel-dim" style={{ fontSize: "var(--panel-text-micro)" }}>
          {measured}/{total} measured
        </span>
      </div>
    </div>
  );
}

/** Weekly landed goals. Deliberately drawn even when it is only two weeks deep -- with the point
 * count stated, so nobody reads two bars as a trend. Hiding a short real series would be the same
 * dishonesty as padding it. */
export function WeeklyBars({ series }: { series: WeeklyThroughput }) {
  if (series.state === "absent") {
    return <NoSensor reason={series.reason} />;
  }
  const points = series.points;
  const w = 620;
  const h = 160;
  const padL = 46;
  const padB = 26;
  const padT = 10;
  const plotH = h - padB - padT;
  const peak = Math.max(...points.map((p) => p.count)) || 1;
  const slot = (w - padL - 8) / points.length;
  // Bars stay readable whether there are 2 of them or 30: capped so a two-point series does not
  // render as two enormous slabs.
  const barW = Math.min(slot * 0.55, 72);

  return (
    <div className="flex flex-col gap-1.5">
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img"
           aria-label={`Goals landed per week across ${points.length} weeks`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="weekly-bar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--panel-cyan)" stopOpacity="0.95" />
            <stop offset="100%" stopColor="var(--panel-cyan-deep)" stopOpacity="0.55" />
          </linearGradient>
        </defs>

        {[0, 1, 2, 3, 4].map((i) => {
          const gy = padT + plotH - (plotH * i) / 4;
          return (
            <g key={i}>
              <line x1={padL} y1={gy} x2={w - 8} y2={gy} stroke="var(--panel-grid)" strokeWidth={1} />
              <text x={padL - 6} y={gy + 3} fill="var(--panel-faint)" fontSize={9} fontFamily={MONO} textAnchor="end">
                {((peak * i) / 4).toFixed(0)}
              </text>
            </g>
          );
        })}

        {points.map((p, i) => {
          const bh = plotH * (p.count / peak);
          const cx = padL + slot * i + slot / 2;
          const bx = cx - barW / 2;
          const by = padT + plotH - bh;
          return (
            <g key={p.week}>
              <rect x={bx} y={by} width={barW} height={bh} fill="url(#weekly-bar)" rx={2} />
              <text x={cx} y={by - 5} fill="var(--panel-bone)" fontSize={11} fontFamily={MONO} textAnchor="middle">
                {p.count}
              </text>
              <text x={cx} y={h - 8} fill="var(--panel-faint)" fontSize={9} fontFamily={MONO} textAnchor="middle">
                {p.week}
              </text>
            </g>
          );
        })}
      </svg>

      <span className="panel-num text-panel-faint" style={{ fontSize: "var(--panel-text-micro)" }}>
        {points.length} week{points.length === 1 ? "" : "s"} of history
      </span>
    </div>
  );
}

/** How many observations sat at each distinct value.
 *
 * WHY A HISTOGRAM AND NOT A RANKED TRACE, for this series specifically. Interventions per goal on
 * this repo's own store are small integers, and 45 of 52 goals sat at zero. Ranked, that is a flat
 * line along the axis with a single spike at the right edge -- technically the truth, and useless.
 * Binned by value it says the thing a reader actually wants: most goals needed no intervention at
 * all, and the handful that did are countable.
 *
 * Bins are the DISTINCT OBSERVED VALUES, not equal-width buckets, which is only legitimate because
 * this series is discrete and low-cardinality -- there is no binning parameter to tune and so no
 * opportunity to flatter the shape by choosing one. Falls back to the ranked trace when that
 * assumption does not hold, rather than silently drawing a misleading chart. */
export function HistogramChart({
  series,
  label,
  gradientId,
}: {
  series: Distribution;
  label: string;
  gradientId: string;
}) {
  if (series.state === "absent") {
    return <NoSensor reason={series.reason} />;
  }

  const distinct = Array.from(new Set(series.values)).sort((a, b) => a - b);
  const allIntegers = series.values.every((v) => Number.isInteger(v));
  if (!allIntegers || distinct.length > 16) {
    // Not the discrete, low-cardinality shape this chart assumes -- draw the ranked trace instead
    // of forcing a binning choice this component has no basis to make.
    return <TraceChart series={series} label={label} gradientId={gradientId} />;
  }

  const counts = distinct.map((v) => ({
    value: v,
    n: series.values.filter((x) => x === v).length,
  }));
  const peak = Math.max(...counts.map((c) => c.n)) || 1;

  const w = 620;
  const h = 160;
  const padL = 46;
  const padB = 26;
  const padT = 10;
  const plotH = h - padB - padT;
  const slot = (w - padL - 8) / counts.length;
  const barW = Math.min(slot * 0.62, 56);

  return (
    <div className="flex flex-col gap-1.5">
      <svg
        viewBox={`0 0 ${w} ${h}`}
        width="100%"
        height={h}
        role="img"
        aria-label={`${label}: ${counts.map((c) => `${c.n} at ${c.value}`).join(", ")}`}
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--panel-amber)" stopOpacity="0.9" />
            <stop offset="100%" stopColor="var(--panel-amber-deep)" stopOpacity="0.45" />
          </linearGradient>
        </defs>

        {[0, 1, 2, 3, 4].map((i) => {
          const gy = padT + plotH - (plotH * i) / 4;
          return (
            <g key={i}>
              <line x1={padL} y1={gy} x2={w - 8} y2={gy} stroke="var(--panel-grid)" strokeWidth={1} />
              <text x={padL - 6} y={gy + 3} fill="var(--panel-faint)" fontSize={9} fontFamily={MONO} textAnchor="end">
                {((peak * i) / 4).toFixed(0)}
              </text>
            </g>
          );
        })}

        {counts.map((c, i) => {
          const bh = plotH * (c.n / peak);
          const cx = padL + slot * i + slot / 2;
          const by = padT + plotH - bh;
          return (
            <g key={c.value}>
              <rect x={cx - barW / 2} y={by} width={barW} height={bh} fill={`url(#${gradientId})`} rx={2} />
              <text x={cx} y={by - 5} fill="var(--panel-bone)" fontSize={10} fontFamily={MONO} textAnchor="middle">
                {c.n}
              </text>
              <text x={cx} y={h - 8} fill="var(--panel-faint)" fontSize={9} fontFamily={MONO} textAnchor="middle">
                {c.value}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="flex items-baseline justify-between">
        <span className="panel-num text-panel-faint" style={{ fontSize: "var(--panel-text-micro)" }}>
          goals per value &middot; p50 {series.p50} &middot; max {series.max}
        </span>
        <span className="panel-num text-panel-dim" style={{ fontSize: "var(--panel-text-micro)" }}>
          {series.measured}/{series.total} measured
        </span>
      </div>
    </div>
  );
}
