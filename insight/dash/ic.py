# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The IC (individual contributor) persona view (issue #126, E4.S3): "my queue", "blocked on
me", "my parks", "my gate verdicts", "my cost" -- one resolved actor's own data, and nothing else.

**THE PRIVACY BOUNDARY LIVES IN THE SQL, NOT HERE.** Every one of the five `_..._rows`/`_..._row`
functions below carries its own `WHERE actor_id = ?` / `WHERE to_actor = ?` / `WHERE actor = ?`
parameter-bound predicate, run directly against the fact tables -- never a Python-side filter over
an unfiltered fetch, and never a query that omits the predicate and relies on `render_ic_view` (or
anything downstream) to filter afterwards. `render_aging_wip`/`render_aging_wip_table`/
`render_stat_tile`/`status_mark` (all four reused UNMODIFIED from issue #125) are actor-agnostic by
design: they render whatever rows they are handed, with no awareness that "actor scoping" is even
a concept. `insight/tests/test_dash_ic_no_leak.py` is the proving test for this file's entire
reason to exist.

**THE ONE SANCTIONED EXCEPTION:** a hand-off counterparty's `from_actor` name may appear on a row
where `to_actor` is the resolved actor -- that row is, by construction, a two-party record the
resolved actor is themself a party to, and "what is blocked on me, and who do I need to unblock
it" is unanswerable without naming who it is from. Nothing else about that counterparty (their own
queue, their own parks, their own verdicts, any OTHER hand-off they are a party to) is ever
fetched. See `_blocked_on_me_rows` below.

**NAMED LIMITATION THIS MODULE DOES NOT SOLVE (issue #126 plan, Risks section): `ic.html`
authenticates the BUILDER, not the VIEWER.** `insight dash` resolves one actor (via
`insight.dash.actor.resolve_actor` -- a `--actor` flag, or the machine/process's own configured
`ledger.actor`) and writes one `ic.html` for them; it is not a multi-tenant server. Two distinct
failure shapes follow, and both are worth naming explicitly rather than leaving the first to imply
the whole risk: (1) MANY VIEWERS, ONE URL -- if `--out` is shared (a `--serve` session on a shared
network, a shared filesystem, the SDLC loop's own automation running under one fixed configured
actor), everyone who can reach it sees that ONE resolved actor's data, not their own. (2) THE
SHARPER, SINGLE-VIEWER CASE -- a stale, templated, or copy-pasted `config.json` can silently name a
DIFFERENT real teammate's identity in `ledger.actor`. A lone viewer, with nothing else wrong,
running `insight dash` against that config gets a page that renders cleanly, self-contained, and
correctly SQL-scoped -- to someone else's identity. Nothing in `resolve_actor` or in this module
can detect that mismatch; correctness here means "every row belongs to the resolved actor," never
"the resolved actor is who is actually reading the screen." Both shapes are within this story's
literal done_when ("another actor's individual data is unreachable FROM THIS VIEW" -- the view
itself never fetches another actor's rows) but neither is "each individual gets their own private
page," and it would be easy to misread the shipped feature as providing that.

Cold-start honesty (extends #124/#125's `ever_ingested`/`has_data` split, and this story's own
plan-review nit 1): a stale or typo'd `ledger.actor` that matches nothing anywhere in the store
would otherwise render a page indistinguishable from a genuinely idle, fully-caught-up actor --
every clause legitimately empty, with no signal that the identity itself might be wrong.
`_actor_ever_appeared` checks whether the resolved actor appears ANYWHERE this view reads from
(`fact_event.actor_id`, `fact_handoff.to_actor`/`from_actor`, `fact_pr_review.actor`) and, if not,
`render_ic_view` renders a distinct banner naming the suspect config key, instead of the ordinary
per-clause empty states. Separately, and unchanged from the plan's own Decision 8,
`_handoff_ever_ingested` distinguishes "no hand-off has ever been recorded for this project" from
"hand-offs exist, none outstanding for you right now" for the blocked-on-me clause specifically --
my parks and my gate verdicts given do NOT get this treatment, because both read real base-table
rows directly and both have confirmed-nonzero rows on the live store today, so a `0` for a specific
actor is a trustworthy, measured zero (Decision 8/9), not a masked absence.
"""
import datetime
import html

from insight.dash.charts import render_aging_wip, render_aging_wip_table, render_stat_tile
from insight.dash.colors import status_mark, texture_defs, viz_css_vars
from insight.dash.render import json_script

_STYLE = f"""
{viz_css_vars()}
body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 2rem; color: var(--dash-ink); background: var(--dash-surface); }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 4px 10px; border-bottom: 1px solid var(--dash-gridline);
          font-size: 13px; }}
.banner {{ background: var(--dash-status-warn); border: 1px solid var(--dash-baseline);
          padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1.5rem; color: var(--dash-on-status); }}
.stat-tile {{ display: inline-block; padding: .75rem 1rem; margin: 0 .75rem .75rem 0;
             border: 1px solid var(--dash-gridline); border-radius: 6px; min-width: 10rem; }}
.stat-tile-label {{ font-size: 12px; color: var(--dash-ink2); }}
.stat-tile-value {{ font-size: 1.6rem; }}
.stat-tile-delta {{ font-size: 12px; color: var(--dash-ink2); }}
footer {{ margin-top: 2rem; font-size: 12px; color: var(--dash-muted); }}
"""


def _rows_as_dicts(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# --------------------------------------------------------------------------- my queue

#: An UN-REDUCED, actor-scoped variant of metric_10.sql's own last-event-wins CTE
#: (insight/metrics/10.sql), reused byte-for-byte above the final per-actor ROW_NUMBER()
#: reduction step, which metric_10 applies and this view deliberately does NOT: "my queue" needs
#: EVERY open claim an actor holds, not only their oldest one (metric_10 collapses to one row per
#: actor, the exact reduction test_my_queue_returns_every_open_claim_for_the_actor_not_just_the_
#: oldest regression-pins). Not a bare count(*) on an aggregate view (Decision 9) -- a direct
#: filtered scan of fact_event, a real base table.
_MY_QUEUE_SQL = """
WITH events AS (
    SELECT project_id, goal_id, actor_id, ts,
           CASE WHEN kind = 'claimed' THEN 1 ELSE 0 END AS is_claim
    FROM fact_event
    WHERE kind IN ('claimed', 'done', 'parked', 'failed')
      AND reliability_class = 1
),
latest_event AS (
    SELECT project_id, goal_id, actor_id, ts, is_claim,
           ROW_NUMBER() OVER (
               PARTITION BY project_id, goal_id ORDER BY ts DESC, actor_id DESC, is_claim ASC
           ) AS rn
    FROM events
)
SELECT actor_id, goal_id, ts AS claimed_ts
FROM latest_event
WHERE rn = 1 AND is_claim = 1 AND actor_id = ?
ORDER BY ts ASC
"""


def _my_queue_rows(conn, actor):
    """Every open claim the resolved actor currently holds. `actor_id = ?` is bound directly into
    the SQL text above -- the privacy boundary for this clause. Shape: [{actor_id, goal_id,
    claimed_ts}], the exact shape `render_aging_wip`/`render_aging_wip_table` (#125, unmodified)
    already expect."""
    return _rows_as_dicts(conn.execute(_MY_QUEUE_SQL, [actor]))


# --------------------------------------------------------------------------- blocked on me

_BLOCKED_ON_ME_SQL = """
SELECT from_actor, to_actor, area, issue, priority, opened_ts
FROM fact_handoff
WHERE to_actor = ? AND settled_ts IS NULL
ORDER BY opened_ts ASC
"""


def _blocked_on_me_rows(conn, actor):
    """Open hand-offs addressed TO the resolved actor -- `to_actor = ?`, never the reciprocal
    (`from_actor = ?` would be hand-offs THIS actor sent, a different clause entirely, never
    shown here). `settled_ts IS NULL` mirrors `skills/sdlc-loop/scripts/ledger.py`'s own
    `outstanding()` semantics (an entry is outstanding until a terminal ack closes it). Each
    returned row carries `from_actor` -- the one sanctioned counterparty-name exception, see the
    module docstring -- and nothing else about that counterparty."""
    return _rows_as_dicts(conn.execute(_BLOCKED_ON_ME_SQL, [actor]))


def _handoff_ever_ingested(conn, project_id=None):
    """True iff at least one fact_handoff row exists (scoped to `project_id` when given) --
    mirrors `insight.dash.render._ever_ingested`'s own shape: an ever-any-row signal, distinct
    from "zero rows outstanding for this actor right now" (Decision 8). Distinguishes a project
    where hand-offs have simply never been recorded from one where they have, and none happen to
    be blocking this actor at the moment -- the two must never share the same copy."""
    if project_id is not None:
        return bool(conn.execute(
            "SELECT EXISTS(SELECT 1 FROM fact_handoff WHERE project_id = ?)", [project_id],
        ).fetchone()[0])
    return bool(conn.execute("SELECT EXISTS(SELECT 1 FROM fact_handoff)").fetchone()[0])


# --------------------------------------------------------------------------- my parks

def _park_count(conn, actor):
    """A real, direct `count(*)` on `fact_event`, a base table -- NOT the bare-aggregate-view
    phantom-row trap (Decision 9): this is a filtered scan of real rows, so zero matches genuinely
    returns 0, not a one-row-of-NULLs artifact. `reason_class` has zero writers today
    (`ledger_writer.py`'s own six-column `_EVENT_INSERT_SQL`), so this clause honestly reports the
    COUNT only, never an invented "by class" breakdown."""
    return conn.execute(
        "SELECT count(*) FROM fact_event WHERE kind = 'parked' AND reliability_class = 1 "
        "AND actor_id = ?", [actor],
    ).fetchone()[0]


# --------------------------------------------------------------------------- my gate verdicts (given)

def _verdicts_given_rows(conn, actor):
    """PR review verdicts this actor has personally GIVEN (they are the reviewer, `actor = ?`) --
    not verdicts received on their own PRs, which would need a `fact_goal.pr -> fact_pr_review`
    join no metric in this codebase performs today (accepted scope cut, see the plan's Risks)."""
    return _rows_as_dicts(conn.execute(
        "SELECT pr_number, verdict, event_ts FROM fact_pr_review WHERE actor = ? "
        "ORDER BY event_ts ASC", [actor],
    ))


# --------------------------------------------------------------------------- my cost

def _cost_row(conn, actor):
    """Returns (tokens_in_sum, tokens_out_sum, cost_cents_sum, n) for the resolved actor. `n` is a
    real `count(*)` over rows where at least one of the three cost columns is non-NULL -- today
    that is always zero (`_write_event`'s own six-column insert never populates any of the
    three), so this always returns `(None, None, None, 0)` on every real store, honestly ABSENT
    rather than faked, but wired to light up automatically the day a cost-emitting writer lands,
    with no second code change (mirrors #120's own precedent for `fact_goal.pr`)."""
    return conn.execute(
        "SELECT sum(tokens_in), sum(tokens_out), sum(cost_cents), count(*) FROM fact_event "
        "WHERE actor_id = ? AND (tokens_in IS NOT NULL OR tokens_out IS NOT NULL "
        "OR cost_cents IS NOT NULL)", [actor],
    ).fetchone()


# --------------------------------------------------------------------------- cold-start (plan-review nit 1)

def _actor_ever_appeared(conn, actor):
    """True iff the resolved actor appears ANYWHERE this view reads from -- `fact_event.actor_id`,
    `fact_handoff.to_actor`/`from_actor`, or `fact_pr_review.actor`. A stale or typo'd
    `ledger.actor` that matches nothing renders a fully-populated-looking "all clear" page,
    byte-identical to a genuinely idle actor, unless this is checked separately (plan-review nit
    1). Each predicate below is bound, exactly like every other query in this file -- this check
    reads only whether the actor exists, never any other actor's rows."""
    return bool(conn.execute(
        "SELECT EXISTS("
        "SELECT 1 FROM fact_event WHERE actor_id = ? "
        "UNION ALL SELECT 1 FROM fact_handoff WHERE to_actor = ? OR from_actor = ? "
        "UNION ALL SELECT 1 FROM fact_pr_review WHERE actor = ?"
        ")",
        [actor, actor, actor, actor],
    ).fetchone()[0])


# --------------------------------------------------------------------------- rendering helpers

def _render_blocked_on_me(rows, handoff_ever_ingested):
    if not rows:
        if not handoff_ever_ingested:
            return (
                '<p><svg width="16" height="16" viewBox="0 0 16 16" role="img" '
                'aria-label="ABSENT">' + texture_defs() + status_mark("ABSENT", 6, 8) + '</svg> '
                "No hand-off has ever been recorded for this project yet "
                "(<code>fact_handoff</code> has never been populated) &mdash; different from "
                "&quot;nothing blocked on you right now&quot;.</p>"
            )
        return "<p>Nothing blocked on you right now.</p>"
    body = "".join(
        f'<tr><td>{html.escape(str(r["from_actor"] or ""))}</td>'
        f'<td>{html.escape(str(r["area"] or ""))}</td>'
        f'<td>{html.escape(str(r["issue"]) if r["issue"] is not None else "")}</td>'
        f'<td>{html.escape(str(r["priority"] or ""))}</td></tr>'
        for r in rows
    )
    return (
        "<table><thead><tr><th>from</th><th>area</th><th>issue</th><th>priority</th></tr>"
        f"</thead><tbody>{body}</tbody></table>"
    )


def _render_cost(cost_row):
    tin, tout, cost_cents, n = cost_row
    if not n:
        return (
            '<svg width="480" height="40" viewBox="0 0 480 40" role="img" '
            'aria-label="My cost: not yet instrumented">'
            + texture_defs() + status_mark("ABSENT", 20, 20) +
            '<text x="140" y="24" font-size="13" fill="var(--dash-ink2)">not yet instrumented '
            "(tokens_in/tokens_out/cost_cents have zero writers)</text></svg>"
        )
    return render_stat_tile(
        "My cost (tokens in/out, cents)",
        f"{tin or 0} / {tout or 0} / {cost_cents or 0}",
    )


def render_ic_view(conn, actor, project_id=None, now=None):
    """Render the IC persona's own page: my queue, blocked on me, my parks, my gate verdicts
    (given), my cost. Returns `(html_text, summary)`. Every fetch above is actor-scoped in SQL
    (Decision 4) -- this function only assembles already-filtered rows via #125's unmodified chart
    primitives; it performs no filtering of its own. See the module docstring for the privacy
    boundary and its one sanctioned exception, and for the cold-start banner's own reasoning."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    generated_at = now.isoformat()

    actor_ever_appeared = _actor_ever_appeared(conn, actor)
    my_queue_rows = _my_queue_rows(conn, actor)
    handoff_ever_ingested = _handoff_ever_ingested(conn, project_id)
    blocked_rows = _blocked_on_me_rows(conn, actor)
    park_count = _park_count(conn, actor)
    verdict_rows = _verdicts_given_rows(conn, actor)
    cost_row = _cost_row(conn, actor)

    banner = ""
    if not actor_ever_appeared:
        banner = (
            '<div class="banner"><strong>Actor '
            f'&quot;{html.escape(actor)}&quot; has never appeared in this project&#39;s ledger'
            "</strong> &mdash; check <code>ledger.actor</code> in <code>.sdlc/config.json</code> "
            "(or the <code>--actor</code> flag). Every clause below is empty because nothing was "
            "found for this identity, not necessarily because there is nothing to show.</div>"
        )

    payload = {
        "generated_at": generated_at,
        "actor": actor,
        "actor_ever_appeared": actor_ever_appeared,
        "my_queue": my_queue_rows,
        "blocked_on_me": blocked_rows,
        "handoff_ever_ingested": handoff_ever_ingested,
        "park_count": park_count,
        "verdicts_given": verdict_rows,
        "cost": {
            "tokens_in": cost_row[0], "tokens_out": cost_row[1],
            "cost_cents": cost_row[2], "n": cost_row[3],
        },
    }

    html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LoopSmith Insight -- IC view</title>
<style>{_STYLE}</style>
</head>
<body class="viz-root">
{banner}
<h1>LoopSmith Insight -- IC view for {html.escape(actor)}</h1>
<p>Generated {html.escape(generated_at)}. Own data only: every row on this page is scoped in SQL
to this one resolved actor, with the single exception of a hand-off counterparty's name (see
"Blocked on me").</p>

<h2>My queue ({len(my_queue_rows)})</h2>
{render_aging_wip(my_queue_rows, now=now)}
{render_aging_wip_table(my_queue_rows, now=now)}

<h2>Blocked on me ({len(blocked_rows)})</h2>
{_render_blocked_on_me(blocked_rows, handoff_ever_ingested)}

<h2>My parks</h2>
{render_stat_tile("My parks (reason class not yet instrumented)", park_count)}

<h2>My gate verdicts (given)</h2>
{render_stat_tile("PR verdicts given", len(verdict_rows))}

<h2>My cost</h2>
{_render_cost(cost_row)}

<script type="application/json" id="insight-ic-data">{json_script(payload)}</script>
<footer>Self-contained: no network fetch, no external script/style/font reference. Data is inlined
above. This page authenticates the actor it was BUILT for, not the person viewing it -- see this
module's own docstring.</footer>
</body>
</html>"""

    summary = {
        "actor": actor,
        "actor_ever_appeared": actor_ever_appeared,
        "my_queue_count": len(my_queue_rows),
        "blocked_on_me_count": len(blocked_rows),
        "handoff_ever_ingested": handoff_ever_ingested,
        "park_count": park_count,
        "verdicts_given_count": len(verdict_rows),
        "cost_absent": not cost_row[3],
    }
    return html_text, summary
