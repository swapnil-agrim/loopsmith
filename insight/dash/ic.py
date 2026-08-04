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

from insight.dash.charts import _absent_line, render_aging_wip, render_aging_wip_table, render_stat_tile
from insight.dash.colors import viz_css_vars
from insight.dash.instrument import page_close, page_open
from insight.dash.render import json_script

# issue #264: `viz_css_vars()`, not `base_style()` -- see manager.py's own comment on this same
# substitution for the full reasoning (instrument.page_open supplies the generic chrome now).
# issue #265 (D4) Design 7: same mechanical --dash- -> --panel- find/replace as manager.py's own
# .stat-tile* rule group -- see that module's comment for the full reasoning.
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
    rather than faked. It does NOT light up automatically the day a cost-emitting writer lands
    (issue #129 review, correcting this docstring's own prior claim): the spec classifies phase
    tokens/cost as Class-2, agent-emitted, so a real future cost-emitting writer tagging its rows
    correctly per spec is exactly what `reliability_class = 1` below excludes, forever, not just
    until one lands. Class-2 cost display is deliberately deferred to a later story pending the
    coverage-denominator treatment (see `extract_coverage`/`CoverageDenominatorMissing` in
    `insight.dash.render`) -- unlike `fact_goal.pr` (#120's precedent, a class-1 column), there is
    no "no second code change" story here.
    `reliability_class = 1` only, matching every other NOW-tier fetcher in this file
    (`_my_queue_rows`, `_park_count`, the `events` CTE) and spec line 563: a NOW metric must not
    read any reliability_class=2 row. Issue #129 D7: the spec's own Class-2 table names "phase
    tokens" as agent-emitted, so without this filter a future class-2 token-emitter would leak an
    unqualified, best-effort number into `_render_cost`'s "live" branch with no coverage figure
    and no error."""
    return conn.execute(
        "SELECT sum(tokens_in), sum(tokens_out), sum(cost_cents), count(*) FROM fact_event "
        "WHERE actor_id = ? AND reliability_class = 1 AND (tokens_in IS NOT NULL OR "
        "tokens_out IS NOT NULL OR cost_cents IS NOT NULL)", [actor],
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
    """issue #265 (D4) Design 6: routed through charts._absent_line's panel-material dispatch
    instead of hand-rolling its own `<svg>` + `status_mark` + `texture_defs` -- ic.py no longer
    imports either of those directly (test_ic_has_no_bespoke_absence_vocabulary_of_its_own).
    Plain text now, not a raw HTML fragment -- `_absent_line`'s panel branch (`not_measured_
    block`) `html.escape()`s `explain_text`, unlike the pre-#265 body, which spliced the
    <code>/&mdash;/&quot; markup in raw. Pinned substrings ("No hand-off has ever been recorded
    for this project yet", "Nothing blocked on you right now.") are unchanged."""
    if not rows:
        if not handoff_ever_ingested:
            return _absent_line(
                "No hand-off has ever been recorded for this project yet (fact_handoff has "
                'never been populated) -- different from "nothing blocked on you right now".',
                id_prefix="panel",
                provenance="no writer · fact_handoff (project has never ingested a hand-off)",
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
    """issue #265 (D4) Design 6: same panel-material routing as _render_blocked_on_me above,
    replacing the hand-rolled `<svg>` + `status_mark` + `texture_defs`. Pinned substring
    ("tokens_in/tokens_out/cost_cents have zero writers") is unchanged."""
    tin, tout, cost_cents, n = cost_row
    if not n:
        return _absent_line(
            "not yet instrumented (tokens_in/tokens_out/cost_cents have zero writers).",
            id_prefix="panel",
            provenance="no writer · fact_event.tokens_in/tokens_out/cost_cents (zero writers)",
        )
    return render_stat_tile(
        "My cost (tokens in/out, cents)",
        f"{tin or 0} / {tout or 0} / {cost_cents or 0}",
        id_prefix="panel",
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

    # The page's own <title> text is preserved exactly (issue #264 Step 8) -- only the head/nav
    # around it now come from the shared instrument.page_open()/page_close() shell.
    head = page_open("LoopSmith Insight -- IC view", current="ic", extra_css=_STYLE)

    html_text = f"""{head}
{banner}
<h1>LoopSmith Insight -- IC view for {html.escape(actor)}</h1>
<p>Generated {html.escape(generated_at)}. Own data only: every row on this page is scoped in SQL
to this one resolved actor, with the single exception of a hand-off counterparty's name (see
"Blocked on me").</p>

<h2>My queue ({len(my_queue_rows)})</h2>
{render_aging_wip(
    my_queue_rows, now=now, id_prefix="panel",
    provenance="no writer · fact_event (claimed/done/parked/failed, actor-scoped)",
)}
{render_aging_wip_table(my_queue_rows, now=now)}

<h2>Blocked on me ({len(blocked_rows)})</h2>
{_render_blocked_on_me(blocked_rows, handoff_ever_ingested)}

<h2>My parks</h2>
{render_stat_tile(
    "My parks (reason class not yet instrumented)", park_count, id_prefix="panel",
)}

<h2>My gate verdicts (given)</h2>
{render_stat_tile("PR verdicts given", len(verdict_rows), id_prefix="panel")}

<h2>My cost</h2>
{_render_cost(cost_row)}

<script type="application/json" id="insight-ic-data">{json_script(payload)}</script>
<footer>Self-contained: no network fetch, no external script/style/font reference. Data is inlined
above. This page authenticates the actor it was BUILT for, not the person viewing it -- see this
module's own docstring.</footer>
{page_close()}"""

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
