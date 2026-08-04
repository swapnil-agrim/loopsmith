# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The leadership persona view (issue #131, E5.S1): the DX Core-4 four-tile rollup -- Speed
(#1), Quality (#5), Impact (#9), Effectiveness (labelled proxy, never a DXI). See
.sdlc/plans/131.md for the full decision record; this module's own comments cite it by
Decision number rather than re-deriving the reasoning inline.

PRIVACY: this page has ZERO sanctioned individual-grain exceptions (Decision 5) -- stricter
than insight.dash.manager's one. Every fetcher below reads fact_goal or fact_collector_pack,
neither of which carries an actor column; nothing here imports insight.dash.actor or
insight.dash.ic, and render_leadership_view takes no actor parameter, same posture as
insight.dash.manager (Decision 5 of .sdlc/plans/127.md).
"""
import datetime
import html

from insight.dash.charts import _absent_line, render_stat_tile
from insight.dash.render import coverage_denominator_html, extract_coverage, json_script
from insight.dash.shell import base_style
from insight.metrics.loader import load_metrics

#: This page uses render_stat_tile exactly like manager.py/ic.py do -- same page-specific
#: `.stat-tile*` rule group precedent (charts.py's own comment on manager.py's _STYLE).
_STYLE = f"""
{base_style()}
.stat-tile {{ display: inline-block; padding: var(--dash-space-3) var(--dash-space-4);
             margin: 0 var(--dash-space-3) var(--dash-space-3) 0;
             border: var(--dash-border-hairline) solid var(--dash-gridline);
             border-radius: var(--dash-radius-sm); min-width: 10rem; }}
.stat-tile-label {{ font-size: var(--dash-text-small); color: var(--dash-ink2); }}
.stat-tile-value {{ font-size: var(--dash-text-display); font-family: var(--dash-font-mono);
                    font-variant-numeric: tabular-nums; }}
.tile-pair {{ display: flex; flex-wrap: wrap; }}
h3 {{ font-size: var(--dash-text-subhead); margin-top: var(--dash-space-5); }}
"""

#: Payload allowlists (issue #129: every widened `SELECT *` read needs its OWN allowlist).
_SPEED_PAYLOAD_KEYS = ("week", "done_count")
_QUALITY_PAYLOAD_KEYS = (
    "collected_ts", "window_commit_count", "repeated_revert_or_fixup_count",
    "change_failure_rate",
)
_IMPACT_PAYLOAD_KEYS = ("source", "lane", "goal_count", "share")
_PORTFOLIO_PAYLOAD_KEYS = ("project_id", "done_count", "park_rate", "gate_coverage_pct")


# --------------------------------------------------------------------------- page-specific fetchers

def _speed_row(conn):
    """Most recent week's row from metric_1 (Throughput) -- aggregate, team-wide, no
    per-engineer breakdown (Decision 2). Returns None if metric_1 has zero rows."""
    cur = conn.execute("SELECT * FROM metric_1 ORDER BY week DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _quality_row(conn):
    """Most recent alignment-collect window's row from metric_5 (Change failure rate, proxy).
    Returns None if metric_5 has zero rows."""
    cur = conn.execute("SELECT * FROM metric_5 ORDER BY collected_ts DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _impact_rows(conn):
    """Every (source, lane, goal_count, share) row from metric_9 -- NOT filtered to one row:
    see Decision 3, no single row means "% new capability"."""
    cur = conn.execute("SELECT * FROM metric_9 ORDER BY source, lane")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _impact_source_shares(rows):
    """Aggregate metric_9's (source, lane, goal_count, share) rows to source-only totals
    (Decision 3) -- the closest sanctioned lens metric_9's real schema supports. Returns
    [(source, goal_count, share)] sorted by share desc, then source asc; [] if rows is empty
    or every row's goal_count sums to 0."""
    totals = {}
    total_count = 0
    for r in rows:
        totals[r["source"]] = totals.get(r["source"], 0) + r["goal_count"]
        total_count += r["goal_count"]
    if not total_count:
        return []
    return sorted(
        ((src, cnt, cnt / total_count) for src, cnt in totals.items()),
        key=lambda t: (-t[2], str(t[0])),
    )


def _portfolio_rows(conn):
    """Every row of metric_41 (Portfolio table), one per known project (issue #132) -- the
    dim_project LEFT JOIN spine in 41.sql guarantees a row for every ingested project even
    when nothing was ever measured for it, with NULL (never 0) aggregates. Returns [] only
    when dim_project itself has zero rows (no repo ever ingested)."""
    cur = conn.execute("SELECT * FROM metric_41 ORDER BY project_id")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fmt_count_or_absent(value):
    """None (the LEFT JOIN spine's own "not measured" signal) -> "not measured"; any int -> its
    plain string. done_count has no reachable real-zero case (see .sdlc/plans/132.md Decision 3)
    but this helper is written generically so it also covers a future column with one."""
    return "not measured" if value is None else str(value)


def _fmt_fraction_pct_or_absent(value):
    """park_rate is a 0-1 fraction (14.sql's own scale, inherited by 41.sql). None -> "not
    measured"; 0.0 is a REAL measured zero (terminal goals exist, none ever parked) and renders
    "0%", never merged with the None case (.sdlc/plans/132.md Decision 3)."""
    return "not measured" if value is None else f"{value:.0%}"


def _fmt_pct_or_absent(value):
    """gate_coverage_pct is already a 0-100 percentage (24.sql's own scale, inherited by
    41.sql) -- do not confuse with park_rate's 0-1 scale above. None -> "not measured"."""
    return "not measured" if value is None else f"{value:.1f}%"


# --------------------------------------------------------------------------- bespoke section renderers

def _render_speed_quality(speed_row, quality_row, speed_coverage=None, quality_coverage=None,
                           id_prefix="dash"):
    """Section body for panel-speed-quality (Decision 6: the ONE section both tiles live in)."""
    parts = ['<div class="tile-pair" id="tile-pair-speed-quality">']
    if speed_row is None:
        parts.append(_absent_line(
            "no terminal goal has been measured yet (fact_goal.outcome='done' with a "
            "terminal_ts) -- metric #1 has zero rows.", id_prefix=id_prefix,
        ))
    else:
        parts.append(
            render_stat_tile(
                "Speed (goals shipped/week, team-wide, most recent week)",
                speed_row["done_count"], id_prefix=id_prefix,
            ) + coverage_denominator_html(speed_coverage)
        )
    if quality_row is None:
        parts.append(_absent_line(
            "no alignment-collect pack has ever been ingested -- metric #5 has zero rows.",
            id_prefix=id_prefix,
        ))
    else:
        rate = quality_row.get("change_failure_rate")
        value = f"{rate:.1%}" if rate is not None else "n/a (zero commits in window)"
        parts.append(
            render_stat_tile(
                "Quality (change-failure rate, proxy) -- counterweight to Speed",
                value, id_prefix=id_prefix,
            ) + coverage_denominator_html(quality_coverage)
        )
    parts.append("</div>")
    return "".join(parts)


def _impact_coverage(rows, reliability_class):
    """Coverage denominator for the Impact tile, aggregated across EVERY metric_9 row -- not
    extract_coverage() on a single arbitrary row (PR #223 review). _render_impact's own tile
    value sums goal_count across ALL (source, lane) rows (Decision 3); a coverage figure read
    from just impact_rows[0] would describe one row's population sitting next to a total spanning
    every row -- the exact denominator/value-population mismatch issue #129's coverage mechanism
    exists to prevent. Dormant today only because metric_9 declares reliability_class 1 (see
    extract_coverage below, which short-circuits to None before class1_count/etc are ever read) --
    this must not silently misbehave the day metric_9 is reclassified to 2.

    No other tile on this page needs this treatment: _speed_row/_quality_row each already read
    exactly ONE row (`LIMIT 1`), so extract_coverage's single-row contract already matches what's
    rendered beside it there.

    Delegates the class/None/raise decision to extract_coverage on the first row -- same
    contract, same CoverageDenominatorMissing wording (.sdlc/plans/129.md Decision D8); a class-2
    row missing the four coverage columns still raises, it is never swallowed here. Only once
    that confirms a real class-2 coverage figure applies does this re-sum class1_count/
    class2_count/total_count across every row and recompute coverage_pct from the summed totals,
    rather than carrying row[0]'s own value. coverage_pct is None when the summed total_count is
    0, matching reliability.py's own NULLIF guard -- a real "n/a", not a missing column."""
    first_row = rows[0] if rows else None
    first = extract_coverage("9", reliability_class, first_row)
    if first is None:
        return None
    class1_count = sum(r["class1_count"] for r in rows)
    class2_count = sum(r["class2_count"] for r in rows)
    total_count = sum(r["total_count"] for r in rows)
    coverage_pct = round(class1_count / total_count, 4) if total_count else None
    return {
        "class1_count": class1_count, "class2_count": class2_count,
        "total_count": total_count, "coverage_pct": coverage_pct,
    }


def _render_impact(rows, coverage=None, id_prefix="dash"):
    """Section body for panel-impact (Decision 6: tile + its own "no counterweight" statement
    share this ONE section). Impact itself never says "new capability" (Decision 3)."""
    parts = ['<div id="tile-impact">']
    shares = _impact_source_shares(rows)
    if not shares:
        parts.append(_absent_line(
            "no goal has been classified by source/lane yet -- metric #9 has zero rows.",
            id_prefix=id_prefix,
        ))
    else:
        total = sum(cnt for _src, cnt, _sh in shares)
        parts.append(
            render_stat_tile("Impact (goals classified, #9)", total, id_prefix=id_prefix)
            + coverage_denominator_html(coverage)
        )
        breakdown = "; ".join(
            f"{html.escape(src if src is not None else 'unspecified')} {sh:.0%}"
            for src, _cnt, sh in shares
        )
        parts.append(f"<p>By source: {breakdown}.</p>")
    parts.append("</div>")
    parts.append(_absent_line(
        "no counterweight defined in spec for Impact (#9) -- L474 pairs #1↔#5 and "
        "#12↔#24 only; rendered without one rather than inventing a pairing.",
        id_prefix=id_prefix,
    ))
    return "".join(parts)


def _render_effectiveness(id_prefix="dash"):
    """Section body for panel-effectiveness. Takes NO arguments, reads NO table (Decision 4):
    flow efficiency (#8) has no metrics/8.sql at all; intervention rate (#12/#13) both declare
    `-- data_status: dark`. STRUCTURAL SEPARATION (Decision 7): the tile's own markup lives in
    <div id="effectiveness-tile"> and NEVER contains the string "DXI"; the spec's own mandated
    disclaimer (spec L461-464, quoted verbatim), which legitimately contains "DXI" once, is a
    SIBLING <p>, outside that div."""
    tile = (
        '<div id="effectiveness-tile">'
        + _absent_line(
            "no instrument. Flow efficiency (#8) has no metrics/8.sql file at all; "
            "intervention rate (#12/#13) is labelled -- data_status: dark. This is a labelled "
            "proxy slot, not a fabricated survey score.",
            id_prefix=id_prefix,
        )
        + "</div>"
    )
    disclaimer = (
        '<p class="effectiveness-disclaimer">Effectiveness is an honest hole. '
        "DX Core-4&#39;s Effectiveness dimension is the DXI, a 14-item Likert survey. We have "
        "no survey. v1 shows a labelled proxy (flow efficiency + intervention rate) and never "
        "calls it a DXI. Fabricating a survey score would poison the one thing this product "
        "sells, which is that the numbers are real.</p>"
    )
    return tile + disclaimer


def _render_portfolio(rows, id_prefix="dash"):
    """Section body for panel-portfolio (issue #132): one row per project from metric_41, with a
    drill-through link on every row. THE HONESTY PROPERTY (.sdlc/plans/132.md Decision 2): every
    link points to the SAME team-wide manager.html -- per-project drill-through is structurally
    undeliverable today (1.sql/7.sql/14.sql carry no project_id), so the link text and an
    adjoining note say so plainly rather than implying a per-project scope the data can't back.
    NULL vs real zero (Decision 3): the three `_fmt_*_or_absent` helpers are the ONLY formatting
    used here -- never a raw f-string, never COALESCE, so a genuine None always renders "not
    measured" and a genuine 0.0 always renders as a real zero, never merged."""
    if not rows:
        return (
            f'<div id="tile-portfolio">'
            + _absent_line(
                "no project has been ingested yet -- dim_project has zero rows.",
                id_prefix=id_prefix,
            )
            + "</div>"
        )
    row_html = "".join(
        "<tr><td>{project}</td><td>{done}</td><td>{park}</td><td>{gate}</td>"
        '<td><a href="manager.html">Manager view (team-wide)</a></td></tr>'.format(
            project=html.escape(str(r["project_id"])),
            done=_fmt_count_or_absent(r["done_count"]),
            park=_fmt_fraction_pct_or_absent(r["park_rate"]),
            gate=_fmt_pct_or_absent(r["gate_coverage_pct"]),
        )
        for r in rows
    )
    return (
        '<div id="tile-portfolio">'
        '<p class="portfolio-note">Manager view is team-wide, not scoped to a single project -- '
        "every drill-through link below opens the SAME manager.html.</p>"
        "<table><thead><tr>"
        "<th>Project</th><th>Throughput (done)</th><th>Park rate</th><th>Gate coverage</th>"
        "<th>Drill-through</th>"
        f"</tr></thead><tbody>{row_html}</tbody></table>"
        "</div>"
    )


# --------------------------------------------------------------------------- page shell

def render_leadership_view(conn, now=None, metrics_dir=None):
    """Render the leadership persona's own page: the DX Core-4 rollup plus the cross-project
    Portfolio table, four sections (panel-speed-quality, panel-impact, panel-effectiveness,
    panel-portfolio, issue #132). Returns (html_text, summary). No
    `actor` parameter (Decision 5, mirrors manager.py's own Decision 5). `metrics_dir` is
    test-only, mirrors render_manager_view's own convention -- the CLI never passes it."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    generated_at = now.isoformat()

    registry = load_metrics(conn, metrics_dir=metrics_dir)

    speed_row = _speed_row(conn)
    quality_row = _quality_row(conn)
    impact_rows = _impact_rows(conn)
    portfolio_rows = _portfolio_rows(conn)

    speed_coverage = extract_coverage("1", registry["1"]["reliability_class"], speed_row)
    quality_coverage = extract_coverage("5", registry["5"]["reliability_class"], quality_row)
    # Aggregated across every impact_rows entry, not read from row[0] alone -- see
    # _impact_coverage's own docstring (PR #223 review) for why this tile needs that and the
    # other two on this page don't.
    impact_coverage = _impact_coverage(impact_rows, registry["9"]["reliability_class"])

    speed_payload = (
        {k: speed_row[k] for k in _SPEED_PAYLOAD_KEYS if k in speed_row}
        if speed_row is not None else None
    )
    quality_payload = (
        {k: quality_row[k] for k in _QUALITY_PAYLOAD_KEYS if k in quality_row}
        if quality_row is not None else None
    )
    impact_payload = [
        {k: r[k] for k in _IMPACT_PAYLOAD_KEYS if k in r} for r in impact_rows
    ]
    portfolio_payload = [
        {k: r[k] for k in _PORTFOLIO_PAYLOAD_KEYS if k in r} for r in portfolio_rows
    ]

    payload = {
        "generated_at": generated_at,
        "speed": speed_payload,
        "quality": quality_payload,
        "impact": impact_payload,
        "effectiveness_status": "absent",
        "portfolio": portfolio_payload,
    }

    html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LoopSmith Insight -- Leadership view</title>
<style>{_STYLE}</style>
</head>
<body class="viz-root">
<h1>LoopSmith Insight -- Leadership view</h1>
<p>Generated {html.escape(generated_at)}. Aggregate only: no metric on this page renders at
individual grain -- zero exceptions (stricter than the manager view).</p>

<section id="panel-speed-quality">
<h2>Speed &amp; Quality (DX Core-4 #1 / #5, throughput/quality counterweight pair)</h2>
{_render_speed_quality(speed_row, quality_row, speed_coverage, quality_coverage)}
</section>

<section id="panel-impact">
<h2>Impact (DX Core-4 #9)</h2>
{_render_impact(impact_rows, impact_coverage)}
</section>

<section id="panel-effectiveness">
<h2>Effectiveness (DX Core-4, labelled proxy)</h2>
{_render_effectiveness()}
</section>

<section id="panel-portfolio">
<h2>Portfolio (cross-project throughput, park rate, gate coverage, #41)</h2>
{_render_portfolio(portfolio_rows)}
</section>

<script type="application/json" id="insight-leadership-data">{json_script(payload)}</script>
<footer>Self-contained: no network fetch, no external script/style/font reference. Data is
inlined above. No individual-grain metric appears anywhere on this page.</footer>
</body>
</html>"""

    summary = {
        "speed_done_count": speed_row["done_count"] if speed_row else None,
        "quality_change_failure_rate": (quality_row or {}).get("change_failure_rate"),
        "impact_goal_count": sum(r["goal_count"] for r in impact_rows) if impact_rows else 0,
        "effectiveness_status": "absent",
        "portfolio_project_count": len(portfolio_rows),
    }
    return html_text, summary
