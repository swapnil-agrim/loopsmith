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

from insight.dash.colors import PANEL_MIX
from insight.dash.instrument import (
    alert,
    board,
    card,
    footer,
    masthead,
    page_close,
    page_open,
    readout as _readout,
    section_rule,
)
# CATALOG moved to insight.metrics.catalog (issue #300 [E16.S2], Decision (a)): insight.api's
# /metrics route needs the same id -> label mapping, and insight/metrics/ is the shared home
# for catalog data, not insight.dash. Re-imported here (not re-defined) so every existing
# `from insight.dash.panel import CATALOG` keeps resolving to the exact same object.
from insight.metrics.catalog import CATALOG

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
# Panel's own instrument furniture -- everything else moved to instrument.FRAME_CSS byte-for-byte
# (issue #264, plan .sdlc/plans/264.md Sec 1.2). This block still uses panel's local `--s:8px`
# unit (declared in FRAME_CSS) and its own literal px sizes deliberately -- see FRAME_CSS's own
# module docstring in instrument.py for why extending test_dash_no_bare_spacing_or_font_size.py to
# this text is explicitly out of scope for this goal.
CSS = """
/* ---- instrumentation ribbon: the page's thesis, stated first -------------------- */
.instr{margin-top:calc(var(--s)*3); padding:calc(var(--s)*2.5) calc(var(--s)*3);
  background:var(--panel-panel); border:1px solid var(--panel-rule); border-radius:3px;
  display:grid; grid-template-columns:auto 1fr; gap:calc(var(--s)*4); align-items:center}
.instr .big{font-family:var(--panel-font-mono); font-size:40px; line-height:1; letter-spacing:-.03em}
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

/* ---- event mix: one stacked proportion bar, not a seven-row list ---------------- */
.stack{display:flex; height:22px; border-radius:2px; overflow:hidden; margin-bottom:calc(var(--s)*1.5)}
.stack span{display:block}
.mixkey{display:grid; grid-template-columns:1fr auto; gap:2px 10px; font-size:11px}
.mixkey .k{color:var(--panel-bone)} .mixkey .k i{display:inline-block; width:8px; height:8px;
  border-radius:1px; margin-right:6px}
.mixkey .v{font-family:var(--panel-font-mono); font-size:10.5px; color:var(--panel-dim)}

@media (max-width:1080px){ .instr{grid-template-columns:1fr} }
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


def _board_bands(d):
    """Bands/cells for `instrument.board()`, escaped at this call site per its no-new-escaping
    contract (issue #264) -- panel.py keeps owning the 42-metric catalog and band membership
    (`CATALOG`, `BANDS`); `board()` only lays out the markup. The page's closing argument: a
    reader who scrolls to the board sees the entire instrumentation surface at once, and the
    hatched cells outnumber the lit ones -- which is the true state of the product and should not
    require reading a table to discover."""
    bands = []
    for name, ids in BANDS:
        cells = []
        for m in ids:
            if m not in CATALOG:
                continue
            state, n = d["states"][m]
            note = {"live": f"{n} row{'s' if n != 1 else ''}",
                    "dark": "NO DATA", "unbuilt": "UNBUILT"}[state]
            cells.append((m, _e(CATALOG[m]), state, _e(note)))
        bands.append((_e(name), cells))
    return bands


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
        alert(f'Lead time is {has}/{all_} covered',
              f'median {_e(_dur(med))} over {has} merges &mdash; the other '
              f'{(all_ or 0) - (has or 0)} carry no duration. Do not read this as a trend.',
              "crit")
        if med is not None else
        alert('Lead time &mdash; NO SENSOR', f'0/{all_ or 0} merges carry a duration', "void"))
    alerts.append(
        alert(f'{n_dark + n_unbuilt} of {total_m} metrics dark',
              f'{n_dark} awaiting data &middot; {n_unbuilt} never built. None of these is a zero.',
              "void"))
    checks = dict(d["checks"])
    if checks:
        tot = sum(checks.values())
        fails = tot - checks.get("SUCCESS", 0)
        alerts.append(
            alert(f'CI {checks.get("SUCCESS", 0)}/{tot} green',
                  f'{fails} non-success across ingested check runs'))

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
    # Built outside the f-string below: pre-3.12 f-strings cannot contain a backslash (here, the
    # footer body's own embedded "\n" line-continuation) inside a replacement field.
    footer_html = footer(
        "Self-contained page &mdash; fonts embedded, no network request, no third-party asset.\n"
        "  Hatched cells are <strong>absent, not zero</strong>: a metric with no data and a "
        "metric reading\n  zero are different facts and are never drawn the same way."
    )
    # The page's own <title> text is preserved exactly (issue #264 Step 5) -- only the head/nav
    # around it now come from the shared instrument.page_open()/page_close() shell.
    head = page_open("LoopSmith Insight — Delivery", current="panel", extra_css=CSS)

    return f"""{head}
{masthead("LoopSmith Insight", "Delivery",
          f'{_e(db_label)} &middot; built {_e(built)} &middot; {d["events"] or 0} events')}

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

{section_rule("Primary readouts")}
<div class="readouts">{"".join(ro)}</div>

{section_rule("Flow")}
<div class="cols">
  <div class="colmain">
    {card("Goals landed per day",
          "fact_event &middot; kind=merged &middot; class 1, deterministic",
          _bars(d["daily"]))}
    {card("Cycle time distribution",
          str(d["cyc_n"]) + " goals &middot; markers are this project's own trailing percentiles",
          _strip(d["spread"], d["p50"], d["p85"]))}
  </div>
  <div class="colrail">
    {card("Attention", "ordered by what should change next", "".join(alerts))}
    {card("Event mix",
          f'{d["events"] or 0} ledger events &middot; {d["reviews"] or 0} reviews',
          kinds)}
  </div>
</div>

{section_rule(f"Instrumentation board &mdash; all {total_m} metrics")}
{board(_board_bands(d))}

{footer_html}

{page_close()}"""
