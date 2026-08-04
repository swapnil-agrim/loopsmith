# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The manager persona view (issue #127, E4.S4): burndown + MC band, WIP and aging, handoff
graph, park taxonomy, and review-cycle distribution -- team-wide, not scoped to one actor.

**THE GUARDRAIL, STATED HERE BECAUSE IT IS THE ACCEPTANCE CRITERION (spec Section 6):** no
metric renders at individual grain on this page, with exactly two sanctioned exceptions -- aging
WIP (metric_10, the `panel-wip-aging` `<section>` below) and hand-off response (metric_32, not
rendered on this page at all -- see .sdlc/plans/127.md Settled Question 2). Every other panel's
fetcher either reads a base table with an explicit non-actor projection (`_handoff_by_area_rows`
in `insight.dash.charts` -- `{area, handoff_count}`, no actor column present at all) or a
metric view that is already aggregated past individual grain (`metric_7`, `metric_14`). Nothing
in this module imports `insight.dash.ic` or `insight.dash.actor`, and `render_manager_view`
takes no `actor` parameter -- the manager view is team-wide by construction, never
actor-resolution-gated (Decision 5; unlike `ic.html`, there is no "unresolved actor" failure mode
here). `insight/tests/test_dash_manager_guardrail.py` is the proving test for this file's entire
reason to exist.

**NAMED CONTRACT (plan-review nit, folded in explicitly rather than left implicit): the inlined
`<script type="application/json">` payload below sits OUTSIDE every `<section>` tag, exactly like
every other dash page's own data script.** That means anything it carries is readable via View
Source regardless of which panel a reader thinks they're looking at -- section-scoping cannot
protect it. THE INVARIANT: the manager payload carries no key derived from the raw
`_aging_wip_rows(conn)` rows (which carry `actor_id`) -- only COUNT-ONLY shapes are ever inlined
for that clause (`aging_wip_count`, an int). The rendered `panel-wip-aging` `<section>` itself
legitimately contains the real actor names (the one sanctioned exception, rendered by
`render_aging_wip`/`render_aging_wip_table`, reused unmodified from issue #125) -- that is fine
because it is inside its own sanctioned section; the JSON payload is not section-scoped, so it
gets the stricter, count-only treatment. `insight/tests/test_dash_manager_guardrail.py` asserts
this directly, separately from the section-stripping check.

Cold-start honesty (Decision 2 of .sdlc/plans/127.md): three of five panels render an honest
ABSENT shell on the real store today, and the THREE flavors of absence on this page are kept
textually distinct rather than collapsed into one generic dot -- "measured absence" (an
instrument exists, nothing has landed yet: burndown, handoff graph, park rate), "zero writers"
(a column exists in the schema but no code path anywhere ever populates it: the park-taxonomy
breakdown), and "no ingest path" (no route from the underlying data to the store at all, not
even a dark column: review-cycle distribution). See `_render_park_taxonomy` and
`_render_review_cycle_distribution` below for exactly which sentence belongs to which claim.
"""
import datetime
import html

from insight.dash.charts import (
    _absent_line,
    _aging_wip_rows,
    _burndown_weekly_remaining_rows,
    _handoff_by_area_rows,
    _mc_band,
    render_aging_wip,
    render_aging_wip_table,
    render_burndown_with_mc_band,
    render_handoff_graph_by_area,
    render_stat_tile,
)
from insight.dash.colors import viz_css_vars
from insight.dash.instrument import page_close, page_open
from insight.dash.render import coverage_denominator_html, extract_coverage, json_script
from insight.metrics.loader import load_metrics

#: This page uses render_stat_tile (WIP count, park rate) exactly like ic.py does, so it needs
#: the same `.stat-tile*` rule group ic.py's own _STYLE carries -- render.py does not use stat
#: tiles at all, so this is page-specific, not a sixth common group base_style() should absorb.
#: issue #264: `viz_css_vars()`, not `base_style()` -- this page no longer calls the old light
#: shell; the shared instrument frame (`instrument.page_open`) supplies the generic h1/h2/table/
#: th,td/.banner/code chrome instead (see instrument.FRAME_CSS's own generic-content block).
#: `_STYLE` must keep starting with `viz_css_vars()`'s own literal output --
#: test_dash_no_bare_spacing_or_font_size.py does `module._STYLE.replace(viz_css_vars(), "")`.
#: issue #265 (D4) Design 7: the .stat-tile* rule group reads --panel-* now (mechanical
#: find/replace of --dash- -> --panel-, no new rule, no visual redesign) -- this page's own
#: charts.py calls (below) all pass id_prefix="panel" now, so the CSS driving the same tiles must
#: read the same token family panel_css_vars() (already emitted by instrument.page_open()) names.
#: h3 is untouched -- Design 7's scope is the four .stat-tile* rules only.
_STYLE = f"""
{viz_css_vars()}
.stat-tile {{ display: inline-block; padding: var(--panel-space-3) var(--panel-space-4);
             margin: 0 var(--panel-space-3) var(--panel-space-3) 0;
             border: var(--panel-border-hairline) solid var(--panel-gridline);
             border-radius: var(--panel-radius-sm); min-width: 10rem; }}
.stat-tile-label {{ font-size: var(--panel-text-small); color: var(--panel-ink2); }}
.stat-tile-value {{ font-size: var(--panel-text-display); font-family: var(--panel-font-mono);
                    font-variant-numeric: tabular-nums; }}
.stat-tile-delta {{ font-size: var(--panel-text-small); color: var(--panel-ink2); }}
h3 {{ font-size: var(--dash-text-subhead); margin-top: var(--dash-space-5); }}
"""


#: The only keys of _wip_row's own result ever inlined into the manager payload (issue #129
#: review, fix 2) -- kept separate from whatever _wip_row's `SELECT *` happens to return, so what
#: gets embedded in the page stays a structural allowlist, not a byproduct of the query's shape.
_WIP_PAYLOAD_KEYS = ("week_start", "wip_count")

#: Same guarantee for _park_rate_row, which `SELECT *`s metric_14 for the same reason (issue #129
#: re-review). Both rows land in the JSON payload OUTSIDE every <section>, so both need the gate --
#: allowlisting only one of two identically-widened reads restores the invariant for neither.
_PARK_RATE_PAYLOAD_KEYS = ("parked_terminal_count", "terminal_count", "park_rate")


# --------------------------------------------------------------------------- page-specific fetchers

def _wip_row(conn):
    """The most recent week's row from `metric_7` (Flow load / WIP), an already area/actor-free
    aggregate view -- {week_start, wip_count}. Returns None if `metric_7` has zero rows (genuinely
    no claimed/done/parked/failed event has ever been measured -- distinct from a real week
    existing with `wip_count = 0`, which is a measured zero, not an absence)."""
    cur = conn.execute("SELECT * FROM metric_7 ORDER BY week_start DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _park_rate_row(conn):
    """`metric_14`'s own single row, read directly -- its `park_rate`/`parked_terminal_count`/
    `terminal_count` columns are NOT re-derived here (a bare `count(*)` over this aggregate view
    would be fooled by its own phantom NULL row over zero input rows; reading the columns it
    already computed avoids that trap entirely, same posture as `insight.dash.render._measured`).
    """
    cur = conn.execute("SELECT * FROM metric_14")
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _reason_class_measured_count(conn):
    """A genuine, filtered `count(*)` over `fact_event`, a base table -- NOT a bare `count(*)`
    over an aggregate view (Decision 9's own trap): a real filtered scan of real rows correctly
    returns 0 when nothing matches, no phantom-row correction needed. `reliability_class = 1`
    only, matching every other NOW-tier read in this codebase (a `reliability_class = 2` row must
    never be trusted -- spec line 563)."""
    return conn.execute(
        "SELECT count(*) FROM fact_event WHERE reason_class IS NOT NULL AND reliability_class = 1"
    ).fetchone()[0]


# --------------------------------------------------------------------------- bespoke ABSENT-text renderers

def _render_park_taxonomy(rate_row, reason_class_count, coverage=None, id_prefix="dash"):
    """TWO DISTINCT ABSENCE CLAIMS on one panel (Decision 2), never collapsed into one generic
    sentence: (a) park RATE -- `fact_goal.outcome` is unpopulated on the real store today, so
    `terminal_count = 0` is a genuine measured zero over an empty terminal population, not a
    broken instrument. (b) park TAXONOMY (the `reason_class` breakdown, spec #15's own question)
    -- `fact_event.reason_class` has zero writers anywhere in the ingest path, full stop; no
    store state could populate it today. That is categorically NOT "0 rows measured", it is "no
    code path can produce a value here" -- a different, stronger claim than (a)."""
    parts = ["<h3>Park rate</h3>"]
    if not rate_row or not rate_row.get("terminal_count"):
        parts.append(_absent_line(
            # issue #265 (D4) Step 7: plain text now, not a raw HTML fragment -- when id_prefix
            # is "panel", _absent_line delegates to colors.not_measured_block, which
            # html.escape()s explain_text (unlike the "dash" body, which splices it in raw). The
            # PRE-#265 text embedded literal <code>/&mdash; markup, which would have come out
            # double-escaped ("&amp;mdash;", a literal "<code>" tag as visible text) through that
            # path -- a real bug this rewrite avoids, not a wording preference. Every substring
            # test_dash_manager.py pins ("no terminal goals recorded yet") is a prefix of this
            # string, so it survives unchanged.
            "no terminal goals recorded yet (fact_goal.outcome is not populated on any row "
            "measured today) -- a measured zero over an empty terminal population, not a broken "
            "instrument.",
            id_prefix=id_prefix,
            provenance="no writer · fact_goal.outcome (not populated on any row measured today)",
        ))
    else:
        rate = rate_row.get("park_rate") or 0.0
        parts.append(render_stat_tile(
            "Park rate", f"{rate:.0%}",
            id_prefix=id_prefix,
        ))
        parts.append(coverage_denominator_html(coverage))
        parts.append(
            f'<p>{rate_row["parked_terminal_count"]} of {rate_row["terminal_count"]} terminal '
            "goal(s) parked at least once.</p>"
        )
    parts.append("<h3>Park taxonomy (why it stopped)</h3>")
    if not reason_class_count:
        parts.append(_absent_line(
            # Same plain-text rationale as the park-rate branch above.
            "no instrument. fact_event.reason_class has zero writers anywhere in the ingest "
            "path today -- this is not a measured zero, it is the complete absence of a data "
            "path.",
            id_prefix=id_prefix,
            provenance="no writer · fact_event.reason_class (zero writers in the ingest path)",
        ))
    else:
        parts.append(
            f"<p>{reason_class_count} <code>fact_event</code> row(s) carry a "
            "<code>reason_class</code> value.</p>"
        )
    return "".join(parts)


def _render_review_cycle_distribution(id_prefix="dash"):
    """The STRONGEST ABSENT flavor on this page (Decision 2): unlike park taxonomy (a schema
    column with zero writers) or burndown (a view that exists and returns empty), `review_cycles`
    lives ONLY in a per-goal, transient JSON record (`.sdlc/state/work/<goal>.json`, written by
    `work.py`'s own `_record`/`_save`) that no `insight ingest` reader touches at all -- zero
    references to it anywhere under `insight/`. There is no `metrics/16.sql`, no dark column, no
    empty view: no route from this data to the store whatsoever. Takes no arguments -- there is
    no live branch this function can ever render without new ingest work outside this story's
    scope (see .sdlc/plans/127.md Risk 4)."""
    return _absent_line(
        # issue #265 (D4) Step 7: plain text now -- see _render_park_taxonomy's own comment
        # above for why the pre-#265 <code>/&mdash; markup could not survive a panel-material
        # dispatch (it would double-escape). "no ingest path exists for this data at all" and
        # "review_cycles" both remain intact substrings for test_dash_manager.py's pins.
        "no ingest path exists for this data at all. review_cycles lives only in per-goal "
        "state JSON (.sdlc/state/work/<goal>.json, written by work.py's own _record/_save), "
        "never ingested into the store -- not a dark metric, not an empty view, a complete "
        "absence of any route from this data to the store.",
        id_prefix=id_prefix,
        provenance="no writer · insight ingest (review_cycles is never read from .sdlc state)",
    )


# --------------------------------------------------------------------------- page shell

def render_manager_view(conn, now=None, metrics_dir=None):
    """Render the manager persona's own page: burndown + MC band, WIP and aging, handoff graph
    (area grain), park taxonomy, review-cycle distribution. Returns (html_text, summary). No
    `actor` parameter -- see this module's own docstring, Decision 5. Every panel is wrapped in
    its own `<section id="panel-...">` -- the structural boundary
    `insight/tests/test_dash_manager_guardrail.py` strips the one sanctioned panel by, before
    checking the remainder for any individual-grain leak. `metrics_dir` is test-only (mirrors
    render_dashboard's own established convention, issue #129 D5) -- the CLI never passes it,
    always the real shipped catalog."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    generated_at = now.isoformat()

    # CREATE OR REPLACE VIEW metric_<id> for every metric this page reads; also read for
    # reliability_class so metric_7/metric_14's coverage denominators (issue #129) can be
    # threaded in below.
    registry = load_metrics(conn, metrics_dir=metrics_dir)

    weekly_remaining = _burndown_weekly_remaining_rows(conn)
    p10_total, p90_total = _mc_band(conn)

    wip_row = _wip_row(conn)
    aging_rows = _aging_wip_rows(conn)

    handoff_rows = _handoff_by_area_rows(conn)

    park_rate_row = _park_rate_row(conn)
    reason_class_count = _reason_class_measured_count(conn)

    if wip_row is None:
        wip_stat_html = _absent_line(
            "no claimed/done/parked/failed event has ever been measured.",
            id_prefix="panel", provenance="no writer · fact_event (claimed/done/parked/failed)",
        )
    else:
        wip_coverage = extract_coverage("7", registry["7"]["reliability_class"], wip_row)
        wip_stat_html = (
            render_stat_tile("WIP (open claims)", wip_row["wip_count"], id_prefix="panel")
            + coverage_denominator_html(wip_coverage)
        )

    park_coverage = extract_coverage("14", registry["14"]["reliability_class"], park_rate_row)

    # issue #129 review: _wip_row reads `SELECT *` (needed so extract_coverage above can see a
    # future coverage-denominator column without a second code change), but what gets INLINED
    # into the payload below must stay structural, not test-dependent -- this module's own
    # docstring's "only COUNT-ONLY shapes are ever inlined" invariant. Naming metric_7's coverage
    # columns explicitly doesn't work: it's class-1 today and its view carries none of them, so
    # naming them here would KeyError. An explicit allowlist of the known-safe keys survives a
    # future column addition to metric_7's view without widening what this page ever inlines.
    wip_payload = (
        {k: wip_row[k] for k in _WIP_PAYLOAD_KEYS if k in wip_row} if wip_row is not None else None
    )
    park_rate_payload = (
        {k: park_rate_row[k] for k in _PARK_RATE_PAYLOAD_KEYS if k in park_rate_row}
        if park_rate_row is not None else None
    )

    payload = {
        "generated_at": generated_at,
        "burndown": {
            "weeks": len(weekly_remaining), "p10_total": p10_total, "p90_total": p90_total,
        },
        "wip": wip_payload,
        # COUNT ONLY -- never the raw _aging_wip_rows() rows themselves (they carry actor_id).
        # See this module's own docstring for why this is a named, tested invariant.
        "aging_wip_count": len(aging_rows),
        "handoff_by_area": handoff_rows,
        "park_rate": park_rate_payload,
        "reason_class_measured_count": reason_class_count,
    }

    # The page's own <title> text is preserved exactly (issue #264 Step 6) -- only the head/nav
    # around it now come from the shared instrument.page_open()/page_close() shell.
    head = page_open("LoopSmith Insight -- Manager view", current="manager", extra_css=_STYLE)

    html_text = f"""{head}
<h1>LoopSmith Insight -- Manager view</h1>
<p>Generated {html.escape(generated_at)}. Team-wide: no metric on this page renders at
individual grain except aging WIP (below) and hand-off response (not rendered on this page).</p>

<section id="panel-burndown">
<h2>Burndown + Monte Carlo band</h2>
{render_burndown_with_mc_band(
    weekly_remaining, p10_total=p10_total, p90_total=p90_total, id_prefix="panel",
    provenance="no writer · fact_goal.terminal_ts (not populated on any row measured today)",
)}
</section>

<section id="panel-wip-aging">
<h2>WIP and aging</h2>
{wip_stat_html}
{render_aging_wip(
    aging_rows, now=now, id_prefix="panel",
    provenance="no writer · fact_event (claimed/done/parked/failed, metric_10)",
)}
{render_aging_wip_table(aging_rows, now=now)}
</section>

<section id="panel-handoff-graph">
<h2>Handoff graph (by area)</h2>
{render_handoff_graph_by_area(
    handoff_rows, id_prefix="panel",
    provenance="no writer · fact_handoff (no rows with a linked issue)",
)}
</section>

<section id="panel-park-taxonomy">
<h2>Park taxonomy</h2>
{_render_park_taxonomy(park_rate_row, reason_class_count, park_coverage, id_prefix="panel")}
</section>

<section id="panel-review-cycles">
<h2>Review-cycle distribution</h2>
{_render_review_cycle_distribution(id_prefix="panel")}
</section>

<script type="application/json" id="insight-manager-data">{json_script(payload)}</script>
<footer>Self-contained: no network fetch, no external script/style/font reference. Data is
inlined above. No individual-grain metric appears on this page except aging WIP (panel-wip-aging)
and hand-off response (not rendered here) -- see this module's own docstring.</footer>
{page_close()}"""

    summary = {
        "burndown_weeks": len(weekly_remaining),
        "wip_count": wip_row["wip_count"] if wip_row else None,
        "aging_wip_count": len(aging_rows),
        "handoff_areas": len(handoff_rows),
        "park_terminal_count": (park_rate_row or {}).get("terminal_count") or 0,
        "reason_class_measured_count": reason_class_count,
    }
    return html_text, summary
