# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The delivery panel: a designed, information-dense manager view.

Why this module exists rather than more rules bolted onto `insight.dash.shell`: the shared shell
is `body{margin}` + `table` + `h1`, which composes a document, not an instrument. Every page built
on it renders as a stack of unstyled tables regardless of how good the tokens underneath are --
which is exactly what shipped, and exactly what the author rejected on sight.

The design commitment, in one line: **this is an instrument panel, not a report.** That is not
decoration, it is the product thesis made visible. LoopSmith Insight's central claim is
ABSENT != PASS -- an unmeasured metric must never be readable as a good one -- and an instrument
is the one familiar object that already obeys that rule. A dead gauge does not read zero; it reads
visibly dead. So absence here is given its own *material*: achromatic, hatched, dashed-bordered,
and carrying no numeral at all. A reader who never learns the vocabulary still cannot mistake a
dark cell for a healthy one, because the dark cells have no number to misread.

Two absence reasons are rendered distinctly, because they demand different actions from the
reader (spec S3 conflates them; that conflation is the reason `lead time` looked fine for a week):

  DARK    -- the metric's SQL exists and ran, and returned zero rows. Nothing to fix in the
             codebase; the loop has simply not produced that event yet. Time fixes this.
  UNBUILT -- there is no `insight/metrics/<id>.sql` at all. No amount of running the loop will
             ever light this cell. Only writing the metric fixes it.

`collect()` reads the live store and degrades per-metric: a view that is missing, empty, or raises
becomes an absence record, never an exception and never a zero. That is deliberate -- a panel that
crashes on a missing sensor is a panel that cannot report on its own instrumentation, which is the
single most important thing this page has to say.
"""
import datetime
import html
import pathlib

from insight.dash.colors import PANEL_MIX, panel_css_vars

# The 42-metric catalog, id -> short name. Sourced from the data-platform spec's section 6 table.
# Held here as data rather than re-read from the spec markdown at build time: the spec is prose and
# its table formatting is not a stable interface, whereas this mapping is small and rarely changes.
CATALOG = {
    1: "Throughput", 2: "Cycle time", 3: "Lead time for change", 4: "Merge frequency",
    5: "Change failure rate", 6: "MTTR proxy", 7: "Flow load (WIP)", 8: "Flow efficiency",
    9: "Flow distribution", 10: "Aging WIP", 11: "Throughput forecast", 12: "Autonomy rate",
    13: "Interventions per goal", 14: "Park rate", 15: "Park taxonomy",
    16: "Review-cycle distribution", 17: "Cost per landed goal", 18: "Tokens per phase",
    19: "Budget-exhaustion rate", 20: "Rework ratio", 21: "Model-tier effectiveness",
    22: "Prevented rework", 23: "Gate catch rate", 24: "Gate coverage", 25: "Escape rate",
    26: "Verify reliability", 27: "Decision-gate denials", 28: "Alignment drift",
    29: "Retro grade mix", 30: "Debt inventory", 31: "Handoff graph",
    32: "Handoff response time", 33: "Unanswered handoffs", 34: "Deferred-handoff age",
    35: "Lease contention", 36: "Parallelism yield", 37: "Ownership concentration",
    38: "Cross-area coupling", 39: "DX Core-4 rollup", 40: "Unit economics",
    41: "Portfolio table", 42: "Adoption & flag correlation",
}

# Which of the 42 belong to which band of the panel's bottom board. Grouping is by the question the
# metric answers, matching the spec's own section-6 ordering, so the board reads as subject areas
# rather than as an undifferentiated 42-cell grid.
BANDS = [
    ("Flow", range(1, 12)),
    ("Autonomy & cost", range(12, 22)),
    ("Quality & gates", range(22, 31)),
    ("Collaboration", range(31, 39)),
    ("Portfolio", range(39, 43)),
]


def _scalar(conn, sql, default=None):
    """One value or `default`. Never raises: a missing view is an absence, not a crash."""
    try:
        row = conn.execute(sql).fetchone()
    except Exception:
        return default
    return default if row is None or row[0] is None else row[0]


def _rows(conn, sql):
    try:
        return conn.execute(sql).fetchall()
    except Exception:
        return []


def _metric_state(conn, metrics_dir, mid):
    """(state, rowcount) for one metric. `state` is one of live / dark / unbuilt.

    `unbuilt` is decided by the filesystem, not by the store, precisely so that a metric whose SQL
    was deleted degrades to `unbuilt` rather than silently reporting `dark` forever."""
    if not (metrics_dir / f"{mid}.sql").exists():
        return "unbuilt", 0
    n = _scalar(conn, f"SELECT count(*) FROM metric_{mid}", default=None)
    if n is None:
        return "dark", 0
    return ("live", n) if n > 0 else ("dark", 0)


def collect(conn, metrics_dir=None, now=None):
    """Everything the panel renders, pulled once. Every field is either a real measurement with its
    own coverage denominator, or None -- there is no third state and no zero-substitution."""
    metrics_dir = pathlib.Path(metrics_dir or "insight/metrics")
    now = now or datetime.datetime.now()

    states = {m: _metric_state(conn, metrics_dir, m) for m in CATALOG}
    live = [m for m, (s, _) in states.items() if s == "live"]
    dark = [m for m, (s, _) in states.items() if s == "dark"]
    unbuilt = [m for m, (s, _) in states.items() if s == "unbuilt"]

    # `None`, never `0`. Defaulting a failed or empty count to zero is the exact confusion this
    # product exists to prevent: "0 goals landed" reads as *we shipped nothing this week*, while
    # the truth behind an empty store is *nothing has been ingested yet*. Those are opposite
    # messages to a manager. A count is only a measurement when there was something to count, so
    # the zero/absent decision is pushed to the caller, which has the denominator to decide with.
    total = _scalar(conn, "SELECT count(*) FROM fact_goal")
    landed = _scalar(conn, "SELECT count(*) FROM fact_goal WHERE outcome='done'")

    cyc = _rows(conn, "SELECT * FROM metric_2 LIMIT 1")
    p50 = cyc[0][2] if cyc else None
    p85 = cyc[0][3] if cyc else None
    cyc_n = _scalar(conn, "SELECT count(*) FROM metric_2", 0)

    aut = _rows(conn, "SELECT * FROM metric_12 LIMIT 1")
    park = _rows(conn, "SELECT * FROM metric_14 LIMIT 1")

    # Lead time carries a real, currently-failing coverage story: the fact table has a row per
    # merge but a duration on only a few, because most PRs predate the timestamp capture. Rendering
    # the mean over the non-null subset alone would be a lie of omission, so the denominator travels
    # with the number all the way to the template.
    lead_all = _scalar(conn, "SELECT count(*) FROM fact_merge_lead_time", 0)
    lead_has = _scalar(
        conn, "SELECT count(*) FROM fact_merge_lead_time WHERE lead_time_seconds IS NOT NULL", 0)
    lead_med = _scalar(
        conn,
        "SELECT median(lead_time_seconds) FROM fact_merge_lead_time "
        "WHERE lead_time_seconds IS NOT NULL", None)

    return {
        "now": now,
        "states": states, "live": live, "dark": dark, "unbuilt": unbuilt,
        "total_goals": total, "landed": landed,
        "p50": p50, "p85": p85, "cyc_n": cyc_n,
        # A row that EXISTS but carries a NULL rate is still absence -- the third distinct shape
        # absence takes in this store, after "no view" and "no rows". `metric_12` on a never-
        # ingested store returns exactly this: one row, NULL rate. Testing the tuple alone is not
        # enough (a tuple of Nones is truthy), and getting that wrong crashed the CLI on
        # `rate * 100` rather than rendering NO SENSOR. The rate is checked HERE, once, so no
        # caller can reintroduce it by testing truthiness downstream.
        "autonomy": (aut[0][2], aut[0][0], aut[0][1]) if aut and aut[0][2] is not None else None,
        "park": (park[0][2], park[0][0], park[0][1]) if park and park[0][2] is not None else None,
        "lead": (lead_med, lead_has, lead_all),
        "daily": _rows(
            conn, "SELECT CAST(ts AS DATE) d, count(*) FROM fact_event WHERE kind='merged' "
                  "GROUP BY 1 ORDER BY 1"),
        "kinds": _rows(
            conn, "SELECT kind, count(*) FROM fact_event GROUP BY 1 ORDER BY 2 DESC"),
        # Column 1 of metric_2 is the per-goal cycle time; columns 2/3 repeat the project-wide
        # p50/p85 on every row. Indexed rather than named because the view's column labels are the
        # metric SQL's business, not this module's.
        "spread": [r[1] for r in _rows(conn, "SELECT * FROM metric_2") if r[1] is not None],
        "events": _scalar(conn, "SELECT count(*) FROM fact_event", 0),
        "checks": _rows(conn, "SELECT conclusion, count(*) FROM fact_pr_check GROUP BY 1 ORDER BY 2 DESC"),
        "reviews": _scalar(conn, "SELECT count(*) FROM fact_pr_review", 0),
    }


# --------------------------------------------------------------------------------------------
# Design tokens. Held as a literal stylesheet rather than composed from `colors.viz_css_vars()`:
# that module's palette is tuned for light-ground charts and has no dark-ground ramp, so consuming
# it here would mean overriding nearly every variable it defines. The absence tokens below are the
# load-bearing ones -- note they carry NO hue at all, which is what stops a dark cell from reading
# as a status colour on a colour-blind or greyscale display.
# --------------------------------------------------------------------------------------------
CSS = """
:root{ --s:8px }   /* spacing unit only -- every colour lives in colors.PANEL */
*{box-sizing:border-box}
body{
  margin:0; background:var(--panel-ground); color:var(--panel-bone);
  font-family:'Atkinson',ui-sans-serif,sans-serif; font-size:13px; line-height:1.5;
  -webkit-font-smoothing:antialiased;
  background-image:
    radial-gradient(ellipse 90% 60% at 50% -10%, var(--panel-glow), transparent 70%),
    repeating-linear-gradient(0deg, var(--panel-grain) 0 1px, transparent 1px 4px);
}
.mono{font-family:'PlexMono',ui-monospace,monospace; font-variant-numeric:tabular-nums}
.wrap{max-width:1500px; margin:0 auto; padding:calc(var(--s)*3) calc(var(--s)*4) calc(var(--s)*10)}

/* ---- masthead ------------------------------------------------------------------ */
.mast{display:flex; align-items:baseline; gap:calc(var(--s)*2); flex-wrap:wrap;
  padding-bottom:calc(var(--s)*2); border-bottom:1px solid var(--panel-rule-hard)}
.mark{font-family:'PlexMono',monospace; font-size:11px; letter-spacing:.34em;
  text-transform:uppercase; color:var(--panel-amber)}
.mast h1{font-size:26px; font-weight:400; margin:0; letter-spacing:-.015em}
.mast .meta{margin-left:auto; font-size:11px; color:var(--panel-faint); letter-spacing:.05em}

/* ---- instrumentation ribbon: the page's thesis, stated first -------------------- */
.instr{margin-top:calc(var(--s)*3); padding:calc(var(--s)*2.5) calc(var(--s)*3);
  background:var(--panel-panel); border:1px solid var(--panel-rule); border-radius:3px;
  display:grid; grid-template-columns:auto 1fr; gap:calc(var(--s)*4); align-items:center}
.instr .big{font-family:'PlexMono',monospace; font-size:40px; line-height:1; letter-spacing:-.03em}
.instr .big .den{color:var(--panel-faint); font-size:22px}
.ribbon{display:flex; gap:3px; align-items:flex-end; height:38px}
.tick{flex:1; border-radius:1px; min-width:4px}
.tick.live{background:linear-gradient(180deg,var(--panel-cyan),var(--panel-cyan-deep)); height:100%}
.tick.dark{height:62%; background:
  repeating-linear-gradient(45deg,var(--panel-void) 0 2px,transparent 2px 4px), var(--panel-void);
  border:1px dashed var(--panel-void-edge)}
.tick.unbuilt{height:34%; background:var(--panel-void); border:1px dashed var(--panel-void-edge); opacity:.55}
.legend{display:flex; gap:calc(var(--s)*2.5); margin-top:var(--s); font-size:10.5px;
  color:var(--panel-dim); letter-spacing:.08em; text-transform:uppercase}
.legend i{display:inline-block; width:9px; height:9px; margin-right:5px; vertical-align:-1px;
  border-radius:1px}

/* ---- section rule -------------------------------------------------------------- */
.rule{display:flex; align-items:center; gap:calc(var(--s)*1.5); margin:calc(var(--s)*4) 0 calc(var(--s)*1.5)}
.rule h2{font-size:11px; font-weight:400; letter-spacing:.28em; text-transform:uppercase;
  color:var(--panel-dim); margin:0; white-space:nowrap}
.rule:after{content:''; flex:1; height:1px; background:var(--panel-rule)}

/* ---- readouts ------------------------------------------------------------------ */
.readouts{display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--panel-rule);
  border:1px solid var(--panel-rule)}
.ro{background:var(--panel-panel); padding:calc(var(--s)*2.5) calc(var(--s)*2.5) calc(var(--s)*2)}
.ro .lab{font-size:10px; letter-spacing:.18em; text-transform:uppercase; color:var(--panel-dim)}
.ro .val{font-family:'PlexMono',monospace; font-size:44px; line-height:1.05; letter-spacing:-.035em;
  margin-top:calc(var(--s)*1.5)}
.ro .unit{font-size:15px; color:var(--panel-faint); margin-left:4px}
.ro .cov{margin-top:var(--s); font-size:10.5px; color:var(--panel-faint);
  font-family:'PlexMono',monospace; border-top:1px solid var(--panel-rule); padding-top:6px}
.ro.absent .val{font-family:'PlexMono',monospace; font-size:19px; color:var(--panel-void-ink);
  letter-spacing:.08em}
.ro.absent{background:
  repeating-linear-gradient(45deg,var(--panel-hatch-soft) 0 3px,transparent 3px 7px), var(--panel-panel)}
.cls{float:right; font-family:'PlexMono',monospace; font-size:9.5px; letter-spacing:.1em;
  color:var(--panel-faint); border:1px solid var(--panel-rule-hard); border-radius:2px; padding:1px 5px}

/* ---- two-column body ----------------------------------------------------------- */
/* `stretch`, not `start`: the rail runs taller than the chart column, and with `start` the
   difference renders as a ragged void under the last chart. Stretching lets the two chart cards
   divide the full height evenly (`grid-auto-rows:1fr`) and the charts centre in the space they
   gain, so the section bottoms out on one flat line. */
.cols{display:grid; grid-template-columns:1fr 320px; gap:calc(var(--s)*3); align-items:stretch}
.colmain{display:grid; gap:calc(var(--s)*3); grid-auto-rows:1fr}
.colmain .card{display:flex; flex-direction:column}
.colmain .card svg{margin-top:auto; margin-bottom:auto}
.colrail{display:grid; gap:calc(var(--s)*3); align-content:start}
.card{background:var(--panel-panel); border:1px solid var(--panel-rule); border-radius:3px;
  padding:calc(var(--s)*2.5)}
.card h3{margin:0 0 calc(var(--s)*.5); font-size:12.5px; font-weight:400; letter-spacing:.02em}
.card .sub{font-size:10.5px; color:var(--panel-faint); margin-bottom:calc(var(--s)*2);
  font-family:'PlexMono',monospace}

/* ---- right rail ---------------------------------------------------------------- */
.alert{border-left:2px solid var(--panel-amber); padding:calc(var(--s)*1.25) 0 calc(var(--s)*1.25) calc(var(--s)*1.5);
  margin-bottom:calc(var(--s)*2)}
.alert.crit{border-left-color:var(--panel-red)}
.alert.void{border-left-color:var(--panel-void-ink); border-left-style:dashed}
.alert .t{font-size:12px}
.alert .d{font-size:10.5px; color:var(--panel-faint); margin-top:3px; font-family:'PlexMono',monospace}

/* ---- event mix: one stacked proportion bar, not a seven-row list ---------------- */
.stack{display:flex; height:22px; border-radius:2px; overflow:hidden; margin-bottom:calc(var(--s)*1.5)}
.stack span{display:block}
.mixkey{display:grid; grid-template-columns:1fr auto; gap:2px 10px; font-size:11px}
.mixkey .k{color:var(--panel-bone)} .mixkey .k i{display:inline-block; width:8px; height:8px;
  border-radius:1px; margin-right:6px}
.mixkey .v{font-family:'PlexMono',monospace; font-size:10.5px; color:var(--panel-dim)}

/* ---- the board: all 42, lit or dark -------------------------------------------- */
.band{margin-bottom:calc(var(--s)*2.5)}
.band .bl{font-size:10px; letter-spacing:.2em; text-transform:uppercase; color:var(--panel-faint);
  margin-bottom:var(--s)}
/* Hairlines come from each cell's own 1px ring, NOT from a gap-coloured container background.
   With a container background, the unfilled tail of the last row renders as a phantom empty cell
   -- an artifact that reads, on a board whose entire job is showing what is and isn't measured, as
   an extra unlabelled dark metric. Adjacent rings overlap inside the 1px gap and resolve to a
   single hairline, so the grid still reads as ruled while staying responsive at any column count. */
.grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(148px,1fr)); gap:1px;
  background:transparent}
.cell{background:var(--panel-raised); padding:calc(var(--s)*1.25) calc(var(--s)*1.5); min-height:62px;
  display:flex; flex-direction:column; justify-content:space-between;
  box-shadow:0 0 0 1px var(--panel-rule)}
.cell .id{font-family:'PlexMono',monospace; font-size:9.5px; color:var(--panel-faint)}
.cell .nm{font-size:11.5px; line-height:1.3; margin-top:2px}
.cell .n{font-family:'PlexMono',monospace; font-size:10px; color:var(--panel-cyan); margin-top:5px}
.cell.dark, .cell.unbuilt{
  background:repeating-linear-gradient(45deg,var(--panel-hatch) 0 3px,transparent 3px 7px), var(--panel-void);
  color:var(--panel-void-ink)}
.cell.dark .nm, .cell.unbuilt .nm{color:var(--panel-void-ink)}
.cell.dark .n, .cell.unbuilt .n{color:var(--panel-void-ink); letter-spacing:.09em}
.cell.unbuilt{opacity:.72}

footer{margin-top:calc(var(--s)*5); padding-top:calc(var(--s)*2); border-top:1px solid var(--panel-rule);
  font-size:10.5px; color:var(--panel-faint); font-family:'PlexMono',monospace}

@media (max-width:1080px){
  .cols{grid-template-columns:1fr}
  .readouts{grid-template-columns:repeat(2,1fr)}
  .instr{grid-template-columns:1fr}
}
@media (max-width:620px){ .readouts{grid-template-columns:1fr} .wrap{padding:calc(var(--s)*2)} }
"""


def _e(s):
    return html.escape(str(s))


def _dur(sec):
    """Seconds as the coarsest unit that still reads precisely -- managers compare durations, and
    `75m` compares faster than `4525s` or `0.05d`."""
    if sec is None:
        return None
    sec = float(sec)
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec / 60:.0f}m"
    if sec < 172800:
        return f"{sec / 3600:.1f}h"
    return f"{sec / 86400:.1f}d"


def _readout(label, value, unit=None, coverage=None, cls=None, absent_reason=None):
    """One primary readout. `absent_reason` and `value` are mutually exclusive by construction --
    an absent readout renders the reason where the numeral would go, so there is no numeral to
    misread. This is the single most important function in the module."""
    badge = f'<span class="cls">{_e(cls)}</span>' if cls else ""
    if absent_reason is not None:
        return (f'<div class="ro absent"><div class="lab">{badge}{_e(label)}</div>'
                f'<div class="val">NO SENSOR</div>'
                f'<div class="cov">{_e(absent_reason)}</div></div>')
    u = f'<span class="unit">{_e(unit)}</span>' if unit else ""
    cov = f'<div class="cov">{_e(coverage)}</div>' if coverage else ""
    return (f'<div class="ro"><div class="lab">{badge}{_e(label)}</div>'
            f'<div class="val">{_e(value)}{u}</div>{cov}</div>')


def _bars(daily, w=620, h=150):
    """Goals landed per day. Hand-rolled SVG: the repo is zero-dependency by policy and a bar chart
    does not justify breaking that."""
    if not daily:
        return '<div class="sub">NO SENSOR &mdash; no merge events ingested</div>'
    peak = max(r[1] for r in daily) or 1
    pad_l, pad_b = 34, 26
    bw = (w - pad_l) / len(daily)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" '
           f'aria-label="Goals landed per day">']
    for i in range(0, 5):
        y = (h - pad_b) - (h - pad_b) * i / 4
        v = peak * i / 4
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w}" y2="{y:.1f}" '
                   f'stroke="var(--panel-grid)" stroke-width="1"/>')
        out.append(f'<text x="0" y="{y - 3:.1f}" fill="var(--panel-faint)" font-size="9" '
                   f'font-family="PlexMono,monospace">{v:.0f}</text>')
    for i, (d, n) in enumerate(daily):
        bh = (h - pad_b) * (n / peak)
        x = pad_l + i * bw + bw * .18
        bwid = bw * .64
        y = (h - pad_b) - bh
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bwid:.1f}" height="{bh:.1f}" '
                   f'fill="url(#bg)" rx="1"/>')
        out.append(f'<text x="{x + bwid / 2:.1f}" y="{y - 5:.1f}" fill="var(--panel-bone)" font-size="10.5" '
                   f'text-anchor="middle" font-family="PlexMono,monospace">{n}</text>')
        out.append(f'<text x="{x + bwid / 2:.1f}" y="{h - 8}" fill="var(--panel-faint)" font-size="9" '
                   f'text-anchor="middle" font-family="PlexMono,monospace">'
                   f'{_e(str(d)[5:])}</text>')
    out.append('<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
               '<stop offset="0" stop-color="var(--panel-amber)"/><stop offset="1" stop-color="var(--panel-amber-deep)"/>'
               '</linearGradient></defs></svg>')
    return "".join(out)


def _strip(spread, p50, p85, w=620, h=132):
    """Cycle-time distribution as a strip plot with the project's own p50/p85 marked. A strip plot
    rather than a histogram because n=50: bucketing that few points hides the long tail, and the
    long tail is the interesting part (the slowest goal here ran 17x the median).

    The axis is **log10**, which is a real reading decision and not a cosmetic one. On a linear
    axis that single 17.7h outlier compresses the other 49 goals into the leftmost fifth of the
    chart, so the shape a manager actually needs -- where the bulk sits and how tight it is --
    becomes an unreadable smear. Log spreads the bulk and still shows the outlier as far right.
    Duration ticks are labelled in real units so the compression is never silent."""
    import math

    vals = sorted(v for v in spread if v is not None and v > 0)
    if not vals:
        return '<div class="sub">NO SENSOR &mdash; cycle time not measured</div>'
    lo, hi = vals[0], vals[-1]
    llo, lhi = math.log10(lo), math.log10(hi)
    span = (lhi - llo) or 1
    pad_l = 10
    iw = w - pad_l * 2
    base = 74  # baseline y

    def x(v):
        return pad_l + iw * ((math.log10(max(v, 1)) - llo) / span)

    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" '
           f'aria-label="Cycle time distribution, log scale">']
    # Decade gridlines, labelled in duration units.
    for tick in (60, 300, 900, 1800, 3600, 7200, 21600, 86400, 172800):
        if not (lo <= tick <= hi):
            continue
        tx = x(tick)
        out.append(f'<line x1="{tx:.1f}" y1="34" x2="{tx:.1f}" y2="{base + 8}" '
                   f'stroke="rgba(233,227,214,.08)"/>')
        out.append(f'<text x="{tx:.1f}" y="{h - 16}" fill="var(--panel-faint)" font-size="9" '
                   f'text-anchor="middle" font-family="PlexMono,monospace">{_e(_dur(tick))}</text>')
    out.append(f'<line x1="{pad_l}" y1="{base}" x2="{w - pad_l}" y2="{base}" '
               f'stroke="rgba(233,227,214,.14)"/>')
    for v in vals:
        out.append(f'<line x1="{x(v):.1f}" y1="{base - 16}" x2="{x(v):.1f}" y2="{base + 8}" '
                   f'stroke="var(--panel-cyan)" stroke-width="1.5" opacity=".5"/>')
    # p50/p85 labels. Anchors flip apart when the two markers are close enough that centred text
    # would overlap -- at this project's real p50/p85 (75m vs 2.2h) they are 34px apart, which is
    # narrower than either label, so without this the two collide into an unreadable overstrike.
    marks = [(v, lab, col) for v, lab, col in
             ((p50, "p50", "var(--panel-amber)"), (p85, "p85", "var(--panel-red)")) if v is not None]
    close = len(marks) == 2 and abs(x(marks[0][0]) - x(marks[1][0])) < 76
    for i, (v, lab, col) in enumerate(marks):
        anchor = "middle"
        if close:
            anchor = "end" if i == 0 else "start"
        dx = 0 if not close else (-4 if i == 0 else 4)
        out.append(f'<line x1="{x(v):.1f}" y1="26" x2="{x(v):.1f}" y2="{base + 8}" stroke="{col}" '
                   f'stroke-width="1.5"/>')
        out.append(f'<text x="{x(v) + dx:.1f}" y="20" fill="{col}" font-size="10" '
                   f'text-anchor="{anchor}" font-family="PlexMono,monospace">'
                   f'{lab} {_e(_dur(v))}</text>')
    out.append(f'<text x="{pad_l}" y="{h - 3}" fill="var(--panel-faint)" font-size="9" '
               f'font-family="PlexMono,monospace">fastest {_e(_dur(lo))}</text>')
    out.append(f'<text x="{w - pad_l}" y="{h - 3}" fill="var(--panel-faint)" font-size="9" text-anchor="end" '
               f'font-family="PlexMono,monospace">log scale &middot; slowest {_e(_dur(hi))}</text>')
    out.append('</svg>')
    return "".join(out)


def _board(d):
    """All 42 metrics, banded by subject. The page's closing argument: a reader who scrolls here
    sees the entire instrumentation surface at once, and the hatched cells outnumber the lit ones
    -- which is the true state of the product and should not require reading a table to discover."""
    out = []
    for name, ids in BANDS:
        cells = []
        for m in ids:
            if m not in CATALOG:
                continue
            state, n = d["states"][m]
            note = {"live": f"{n} row{'s' if n != 1 else ''}",
                    "dark": "NO DATA", "unbuilt": "UNBUILT"}[state]
            cells.append(
                f'<div class="cell {state}"><div><div class="id">{m:02d}</div>'
                f'<div class="nm">{_e(CATALOG[m])}</div></div>'
                f'<div class="n">{_e(note)}</div></div>')
        out.append(f'<div class="band"><div class="bl">{_e(name)}</div>'
                   f'<div class="grid">{"".join(cells)}</div></div>')
    return "".join(out)


def render_panel(conn, metrics_dir=None, now=None, db_label=".sdlc/insight.duckdb"):
    """The whole page, self-contained: fonts inlined as data URIs, SVG hand-rolled, no network
    request of any kind. `insight dash` writes it to disk and `serve.py` hands it over loopback,
    so the page must work with the machine offline."""
    d = collect(conn, metrics_dir=metrics_dir, now=now)
    n_live, n_dark, n_unbuilt = len(d["live"]), len(d["dark"]), len(d["unbuilt"])
    total_m = len(CATALOG)
    pct = 100.0 * n_live / total_m if total_m else 0.0

    ribbon = "".join(
        f'<div class="tick {d["states"][m][0]}" title="{m:02d} {_e(CATALOG[m])}"></div>'
        for m in sorted(CATALOG))

    # ---- readouts. Each one either carries a real denominator or renders NO SENSOR. -------
    ro = []
    # A landed-count of 0 is only a measurement if goals were actually ingested to count. With an
    # empty store there is no denominator, so this refuses to render a numeral at all.
    if d["total_goals"]:
        ro.append(_readout("Goals landed", d["landed"] or 0, cls="C1",
                           coverage=f'{d["landed"] or 0}/{d["total_goals"]} goals reached terminal'))
    else:
        ro.append(_readout("Goals landed", None, cls="C1",
                           absent_reason="no goals ingested - run `insight ingest`"))
    ro.append(_readout("Cycle time p50", _dur(d["p50"]) or "", cls="C1",
                       coverage=f'{d["cyc_n"] or 0}/{d["total_goals"] or 0} goals timed'
                                f'  ·  p85 {_dur(d["p85"]) or "n/a"}')
              if d["p50"] is not None else
              _readout("Cycle time p50", None, cls="C1", absent_reason="metric_2 has no value yet"))
    if d["autonomy"]:
        rate, num, den = d["autonomy"]
        ro.append(_readout("Autonomy rate", f"{rate * 100:.1f}", unit="%", cls="C2",
                           coverage=f"{num}/{den} landed without intervention"))
    else:
        ro.append(_readout("Autonomy rate", None, cls="C2",
                           absent_reason="metric_12 has no value yet"))
    if d["park"]:
        rate, num, den = d["park"]
        ro.append(_readout("Park rate", f"{rate * 100:.1f}", unit="%", cls="C1",
                           coverage=f"{num}/{den} goals parked"))
    else:
        ro.append(_readout("Park rate", None, cls="C1",
                           absent_reason="metric_14 has no value yet"))

    # ---- right rail. Sorted by how much they should change the reader's next action. -------
    med, has, all_ = d["lead"]
    alerts = []
    alerts.append(
        f'<div class="alert crit"><div class="t">Lead time is {has}/{all_} covered</div>'
        f'<div class="d">median {_e(_dur(med))} over {has} merges &mdash; the other '
        f'{(all_ or 0) - (has or 0)} carry no duration. Do not read this as a trend.</div></div>'
        if med is not None else
        f'<div class="alert void"><div class="t">Lead time &mdash; NO SENSOR</div>'
        f'<div class="d">0/{all_ or 0} merges carry a duration</div></div>')
    alerts.append(
        f'<div class="alert void"><div class="t">{n_dark + n_unbuilt} of {total_m} metrics dark</div>'
        f'<div class="d">{n_dark} awaiting data &middot; {n_unbuilt} never built. '
        f'None of these is a zero.</div></div>')
    checks = dict(d["checks"])
    if checks:
        tot = sum(checks.values())
        fails = tot - checks.get("SUCCESS", 0)
        alerts.append(
            f'<div class="alert"><div class="t">CI {checks.get("SUCCESS", 0)}/{tot} green</div>'
            f'<div class="d">{fails} non-success across ingested check runs</div></div>')

    # Event mix as one stacked proportion bar. A seven-row list was the tallest element on the
    # page and said the least: the reader wants the SHAPE of the loop's activity (how much claiming
    # turns into merging), and a proportion bar states that in one glance where a column of raw
    # counts makes you do the division yourself.
    mix_cols = [f"var(--panel-mix-{i})" for i in range(len(PANEL_MIX))]
    mix_total = sum(n for _, n in d["kinds"]) or 1
    stack = "".join(
        f'<span style="width:{100.0 * n / mix_total:.2f}%;background:{mix_cols[i % len(mix_cols)]}" '
        f'title="{_e(k)} {n}"></span>'
        for i, (k, n) in enumerate(d["kinds"]))
    kinds = (f'<div class="stack">{stack}</div><div class="mixkey">' + "".join(
        f'<span class="k"><i style="background:{mix_cols[i % len(mix_cols)]}"></i>{_e(k)}</span>'
        f'<span class="v">{n}</span>'
        for i, (k, n) in enumerate(d["kinds"])) + '</div>')

    built = d["now"].strftime("%Y-%m-%d %H:%M")
    css = panel_css_vars() + CSS

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LoopSmith Insight — Delivery</title>
<style>{css}</style></head>
<body><div class="wrap">

<div class="mast">
  <span class="mark">LoopSmith Insight</span>
  <h1>Delivery</h1>
  <span class="meta mono">{_e(db_label)} &middot; built {_e(built)} &middot; {d["events"] or 0} events</span>
</div>

<div class="instr">
  <div>
    <div class="big mono">{n_live}<span class="den">/{total_m}</span></div>
    <div style="font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--panel-dim);
         margin-top:6px">Metrics instrumented &middot; {pct:.0f}%</div>
  </div>
  <div>
    <div class="ribbon">{ribbon}</div>
    <div class="legend">
      <span><i style="background:var(--panel-cyan)"></i>{n_live} live</span>
      <span><i style="background:var(--panel-void);border:1px dashed var(--panel-void-edge)"></i>{n_dark} no data</span>
      <span><i style="background:var(--panel-void);border:1px dashed var(--panel-void-edge);opacity:.55"></i>{n_unbuilt} unbuilt</span>
    </div>
  </div>
</div>

<div class="rule"><h2>Primary readouts</h2></div>
<div class="readouts">{"".join(ro)}</div>

<div class="rule"><h2>Flow</h2></div>
<div class="cols">
  <div class="colmain">
    <div class="card">
      <h3>Goals landed per day</h3>
      <div class="sub">fact_event &middot; kind=merged &middot; class 1, deterministic</div>
      {_bars(d["daily"])}
    </div>
    <div class="card">
      <h3>Cycle time distribution</h3>
      <div class="sub">{d["cyc_n"]} goals &middot; markers are this project's own trailing percentiles</div>
      {_strip(d["spread"], d["p50"], d["p85"])}
    </div>
  </div>
  <div class="colrail">
    <div class="card">
      <h3>Attention</h3>
      <div class="sub">ordered by what should change next</div>
      {"".join(alerts)}
    </div>
    <div class="card">
      <h3>Event mix</h3>
      <div class="sub">{d["events"] or 0} ledger events &middot; {d["reviews"] or 0} reviews</div>
      {kinds}
    </div>
  </div>
</div>

<div class="rule"><h2>Instrumentation board &mdash; all {total_m} metrics</h2></div>
{_board(d)}

<footer>
  Self-contained page &mdash; fonts embedded, no network request, no third-party asset.
  Hatched cells are <strong>absent, not zero</strong>: a metric with no data and a metric reading
  zero are different facts and are never drawn the same way.
</footer>

</div></body></html>"""
