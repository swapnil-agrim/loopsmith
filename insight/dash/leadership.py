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
.stat-tile {{ display: inline-block; padding: .75rem 1rem; margin: 0 .75rem .75rem 0;
             border: 1px solid var(--dash-gridline); border-radius: 6px; min-width: 10rem; }}
.stat-tile-label {{ font-size: 12px; color: var(--dash-ink2); }}
.stat-tile-value {{ font-size: 1.6rem; }}
.tile-pair {{ display: flex; flex-wrap: wrap; }}
h3 {{ font-size: 1rem; margin-top: 1.25rem; }}
"""

#: Payload allowlists (issue #129: every widened `SELECT *` read needs its OWN allowlist).
_SPEED_PAYLOAD_KEYS = ("week", "done_count")
_QUALITY_PAYLOAD_KEYS = (
    "collected_ts", "window_commit_count", "repeated_revert_or_fixup_count",
    "change_failure_rate",
)
_IMPACT_PAYLOAD_KEYS = ("source", "lane", "goal_count", "share")


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


# --------------------------------------------------------------------------- page shell

def render_leadership_view(conn, now=None, metrics_dir=None):
    """Render the leadership persona's own page: the DX Core-4 rollup, three sections
    (panel-speed-quality, panel-impact, panel-effectiveness). Returns (html_text, summary). No
    `actor` parameter (Decision 5, mirrors manager.py's own Decision 5). `metrics_dir` is
    test-only, mirrors render_manager_view's own convention -- the CLI never passes it."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    generated_at = now.isoformat()

    registry = load_metrics(conn, metrics_dir=metrics_dir)

    speed_row = _speed_row(conn)
    quality_row = _quality_row(conn)
    impact_rows = _impact_rows(conn)

    speed_coverage = extract_coverage("1", registry["1"]["reliability_class"], speed_row)
    quality_coverage = extract_coverage("5", registry["5"]["reliability_class"], quality_row)
    impact_first_row = impact_rows[0] if impact_rows else None
    impact_coverage = extract_coverage("9", registry["9"]["reliability_class"], impact_first_row)

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

    payload = {
        "generated_at": generated_at,
        "speed": speed_payload,
        "quality": quality_payload,
        "impact": impact_payload,
        "effectiveness_status": "absent",
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
    }
    return html_text, summary
