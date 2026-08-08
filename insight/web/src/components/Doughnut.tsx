// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
//
// A doughnut is only honest for a PART-TO-WHOLE. Every rate metric here is one (47 of 52 goals),
// and so is panel coverage (6 of 42 instrumented) -- those get rings. A duration or an unbounded
// count is not, and keeps its distribution chart. That rule is the whole reason this component
// takes a numerator and a denominator rather than a percentage: a caller that only has a
// percentage does not have a whole, and should not be drawing a ring.
//
// The ring's colour is a VERDICT, never decoration. `tone` defaults to neutral, so a dark metric's
// ring stays grey no matter how large its number is.
export function Doughnut({
  numerator,
  denominator,
  label,
  caption,
  tone = "neutral",
}: {
  numerator: number;
  denominator: number;
  label: string;
  caption?: React.ReactNode;
  tone?: "neutral" | "ok" | "watch" | "breach";
}) {
  // Refuse, rather than clamp. A ring drawn from a whole we do not have is a fabricated
  // measurement, and silently clamping would hide the upstream bug that produced the bad pair.
  if (denominator <= 0 || numerator < 0 || numerator > denominator) return null;

  const pct = (numerator / denominator) * 100;
  const stroke = tone === "neutral" ? "var(--panel-void-ink)" : `var(--panel-${tone})`;

  return (
    <div className="flex items-center gap-4">
      <svg
        width="96"
        height="96"
        viewBox="0 0 42 42"
        role="img"
        aria-label={`${label}: ${numerator} of ${denominator}`}
        className="shrink-0"
      >
        {/* r = 15.9155 makes the circumference 100.00, so strokeDasharray reads directly as a
            percentage and needs no conversion constant that could drift from the geometry. */}
        <circle cx="21" cy="21" r="15.9155" fill="none" stroke="var(--panel-void)" strokeWidth="4" />
        <circle
          cx="21"
          cy="21"
          r="15.9155"
          fill="none"
          stroke={stroke}
          strokeWidth="4"
          strokeDasharray={`${pct} ${100 - pct}`}
          strokeDashoffset="25"
          // butt at zero: a round cap paints a visible dot even at 0%, which on a cold-start
          // panel reads as a sliver of measurement that was never taken.
          strokeLinecap={pct > 0 ? "round" : "butt"}
        />
        <text
          x="21"
          y="20.6"
          textAnchor="middle"
          fill="var(--panel-bone)"
          fontSize="6.6"
          fontFamily="var(--panel-font-mono)"
        >
          {pct.toFixed(1)}%
        </text>
        <text
          x="21"
          y="26"
          textAnchor="middle"
          fill="var(--panel-faint)"
          fontSize="3"
          fontFamily="var(--panel-font-mono)"
        >
          {numerator} / {denominator}
        </text>
      </svg>
      {caption && (
        <div
          className="text-panel-dim"
          style={{ fontSize: "var(--panel-text-small)", lineHeight: 1.55 }}
        >
          {caption}
        </div>
      )}
    </div>
  );
}
