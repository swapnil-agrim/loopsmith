// SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
// issue #312 [E20.S1] retrospective gap closure. Ported verbatim from insight/dash/instrument.py's
// `.ro.absent`/`.cell.dark`/`.cell.unbuilt` rules (instrument.py:84-85, 130-135) -- see Metric.tsx's
// own header comment for the full "ported from the Python original" story, unrepeated here.
//
// The author's own decision on this issue (verbatim, from the issue thread): "Do not build a local
// hatched treatment inside the delivery panel -- one absence material across the product is the
// entire point; a reader must never have to learn a second absence vocabulary." Before this module
// existed, the exact same `repeating-linear-gradient(45deg, ... 0 3px, transparent 3px 7px)` string
// was independently typed in THREE places -- Metric.tsx, MetricCell.tsx, and delivery/charts.tsx's
// NoSensor -- a formula a future edit (say, widening the 3px/7px stripe) could drift out of sync in
// one copy and not the others. This is the ONE place it is typed now; every consumer calls this
// function instead of hand-typing the gradient.
//
// Takes the CSS custom-property NAME, not a raw color, because the existing callers legitimately
// differ on WHICH token they hatch with -- Metric.tsx's `.ro` analogue uses the softer
// `--panel-hatch-soft`, MetricCell.tsx's `.cell` analogue and the chart absence block both use
// `--panel-hatch` -- a difference ported verbatim from the Python original, not one this module
// should paper over.
export function hatchBackgroundImage(token: "--panel-hatch" | "--panel-hatch-soft"): string {
  return `repeating-linear-gradient(45deg, var(${token}) 0 3px, transparent 3px 7px)`;
}
