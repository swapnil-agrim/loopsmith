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
"hand-offs exist, none outstanding for you right now" for the blocked-on-me clause specifically.

issue #315 [E20.S4] D1 (BLOCKING plan-review round-2 correction): `_actor_ever_appeared` answers
"is this identity known to the store AT ALL," a `UNION ALL` across four structurally unrelated
sources -- it does NOT answer "has THIS SPECIFIC readout's own table ever received a row for
ANYONE." Those are not the same fact: `test_actor_ever_appeared_true_via_fact_pr_review_only`
proves an actor known only via `fact_pr_review` makes `_actor_ever_appeared` `True` while
`fact_event`/`fact_handoff` remain completely untouched. The web view (`/ic`, `page.tsx`) therefore
does NOT gate my-queue/my-parks/my-gate-verdicts' numerals on `actor_ever_appeared` alone --
`collect_ic_payload` below now also exposes `my_queue_ever_ingested`/`park_ever_ingested`/
`verdicts_ever_ingested` (each a per-table "has ANYONE's row for this exact predicate ever landed"
signal, mirroring `_handoff_ever_ingested`'s own shape), and the web page combines
`actor_ever_appeared AND <the readout's own ever_ingested flag>` before rendering a numeral instead
of a hatch. My parks and my gate verdicts DO now get an ever-ingested signal in the payload -- the
one thing that does NOT change is `render_ic_view`/this module's own HTML output below, which
continues to render `park_count`/`len(verdict_rows)` directly as it always has (a real, measured
count on a base-table scan, per Decision 8/9's original reasoning) and never consults the three new
keys -- see the cross-reference paragraph immediately below for why the two surfaces are allowed to
differ here.

issue #315 [E20.S4] D7 cross-reference (does NOT close #583): the IC *web* catalog cards
(`IC_PRIMARY_READOUT_IDS`, six catalog metrics rendered by `/ic`'s `<Metric>` grid) and this
module's own five actor-scoped panels (my queue, blocked on me, my parks, my gate verdicts, my
cost) are two different surfaces by design -- the former is the shared 42-metric catalog filtered
to IC-tagged ids, the latter is bespoke, actor-bound SQL with no catalog id at all -- and a reader
comparing "what does IC see" between `insight dash` (this module, Python-rendered) and `/ic` (the
web page) should expect the SAME five bespoke panels plus the web page's ADDITIONAL six catalog
cards this module has no equivalent for (the Python CLI path never composed from the shared
42-metric catalog). #583's actual ask -- a *decision* about whether the web view and the Python
view of the same persona should converge on one canonical list -- remains OPEN; this paragraph only
records a third instance of the same divergence so it does not go silently undocumented again.
"""
import datetime
import html

from insight.dash.charts import render_aging_wip, render_aging_wip_table, render_stat_tile
from insight.dash.colors import status_mark, texture_defs, viz_css_vars
from insight.dash.instrument import page_close, page_open
from insight.dash.render import json_script

# issue #264: `viz_css_vars()`, not `base_style()` -- see manager.py's own comment on this same
# substitution for the full reasoning (instrument.page_open supplies the generic chrome now).
_STYLE = f"""
{viz_css_vars()}
.stat-tile {{ display: inline-block; padding: var(--dash-space-3) var(--dash-space-4);
             margin: 0 var(--dash-space-3) var(--dash-space-3) 0;
             border: var(--dash-border-hairline) solid var(--dash-gridline);
             border-radius: var(--dash-radius-sm); min-width: 10rem; }}
.stat-tile-label {{ font-size: var(--dash-text-small); color: var(--dash-ink2); }}
.stat-tile-value {{ font-size: var(--dash-text-display); font-family: var(--dash-font-mono);
                    font-variant-numeric: tabular-nums; }}
.stat-tile-delta {{ font-size: var(--dash-text-small); color: var(--dash-ink2); }}
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


def _my_queue_kind_ever_ingested(conn, project_id=None):
    """issue #315 [E20.S4] D1: True iff at least one `fact_event` row exists (scoped to
    `project_id` when given) matching the SAME `kind IN ('claimed', 'done', 'parked', 'failed')
    AND reliability_class = 1` predicate `_MY_QUEUE_SQL` above already filters by -- unscoped to
    any actor. Mirrors `_handoff_ever_ingested`'s own shape exactly: an ever-any-row signal for
    THIS readout's own table, distinct from `_actor_ever_appeared` (which can be true via a
    completely different table, per this module's own docstring)."""
    predicate = "kind IN ('claimed', 'done', 'parked', 'failed') AND reliability_class = 1"
    if project_id is not None:
        return bool(conn.execute(
            f"SELECT EXISTS(SELECT 1 FROM fact_event WHERE {predicate} AND project_id = ?)",
            [project_id],
        ).fetchone()[0])
    return bool(
        conn.execute(f"SELECT EXISTS(SELECT 1 FROM fact_event WHERE {predicate})").fetchone()[0]
    )


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


def _park_ever_ingested(conn, project_id=None):
    """issue #315 [E20.S4] D1: True iff at least one `fact_event` row with `kind = 'parked' AND
    reliability_class = 1` exists (scoped to `project_id` when given) -- the same predicate
    `_park_count` above filters by, minus the `actor_id = ?` bound, unscoped to any actor. Mirrors
    `_handoff_ever_ingested`'s own shape."""
    predicate = "kind = 'parked' AND reliability_class = 1"
    if project_id is not None:
        return bool(conn.execute(
            f"SELECT EXISTS(SELECT 1 FROM fact_event WHERE {predicate} AND project_id = ?)",
            [project_id],
        ).fetchone()[0])
    return bool(
        conn.execute(f"SELECT EXISTS(SELECT 1 FROM fact_event WHERE {predicate})").fetchone()[0]
    )


# --------------------------------------------------------------------------- my gate verdicts (given)

def _verdicts_given_rows(conn, actor):
    """PR review verdicts this actor has personally GIVEN (they are the reviewer, `actor = ?`) --
    not verdicts received on their own PRs, which would need a `fact_goal.pr -> fact_pr_review`
    join no metric in this codebase performs today (accepted scope cut, see the plan's Risks)."""
    return _rows_as_dicts(conn.execute(
        "SELECT pr_number, verdict, event_ts FROM fact_pr_review WHERE actor = ? "
        "ORDER BY event_ts ASC", [actor],
    ))


def _verdicts_ever_ingested(conn, project_id=None):
    """issue #315 [E20.S4] D1: True iff at least one `fact_pr_review` row exists (scoped to
    `project_id` when given) -- unscoped to any actor. Mirrors `_handoff_ever_ingested`'s own
    shape."""
    if project_id is not None:
        return bool(conn.execute(
            "SELECT EXISTS(SELECT 1 FROM fact_pr_review WHERE project_id = ?)", [project_id],
        ).fetchone()[0])
    return bool(conn.execute("SELECT EXISTS(SELECT 1 FROM fact_pr_review)").fetchone()[0])


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
            '<text x="140" y="24" font-size="var(--dash-text-body)" fill="var(--dash-ink2)">not yet instrumented '
            "(tokens_in/tokens_out/cost_cents have zero writers)</text></svg>"
        )
    return render_stat_tile(
        "My cost (tokens in/out, cents)",
        f"{tin or 0} / {tout or 0} / {cost_cents or 0}",
    )


def collect_ic_payload(conn, actor, project_id=None, now=None):
    """The six actor-scoped queries + the payload dict literal, extracted out of `render_ic_view`
    (issue #310 [E19.S2] Task 1, pure refactor -- exactly the dict-construction that used to be
    inline here, moved verbatim). Returns the dict only -- no banner, no HTML/summary assembly;
    that stays `render_ic_view`'s job below. Called both by `render_ic_view` (unchanged HTML/CLI
    path) and by the new `insight web ic` CLI bridge (`insight/__main__.py`, Decision 1/4 of
    .sdlc/plans/310.md) -- this is the one function both callers share, so the migration to a real
    FastAPI endpoint (Decision 1's named follow-up) only needs a new thin route calling this same
    function, not a rewrite. Every fetch below is actor-scoped in SQL (module docstring's own
    privacy boundary) -- this function performs no filtering of its own, only assembly."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    generated_at = now.isoformat()

    actor_ever_appeared = _actor_ever_appeared(conn, actor)
    my_queue_rows = _my_queue_rows(conn, actor)
    my_queue_ever_ingested = _my_queue_kind_ever_ingested(conn, project_id)
    handoff_ever_ingested = _handoff_ever_ingested(conn, project_id)
    blocked_rows = _blocked_on_me_rows(conn, actor)
    park_count = _park_count(conn, actor)
    park_ever_ingested = _park_ever_ingested(conn, project_id)
    verdict_rows = _verdicts_given_rows(conn, actor)
    verdicts_ever_ingested = _verdicts_ever_ingested(conn, project_id)
    cost_row = _cost_row(conn, actor)

    return {
        "generated_at": generated_at,
        "actor": actor,
        "actor_ever_appeared": actor_ever_appeared,
        "my_queue": my_queue_rows,
        # issue #315 [E20.S4] D1: per-table "ever ingested for anyone" signals, one per bespoke
        # readout below (mirroring handoff_ever_ingested's own pre-existing shape) -- NOT consumed
        # by render_ic_view/this module's own HTML output (see the module docstring's D1/D7 notes),
        # only by the web page (page.tsx), which gates each readout's numeral on
        # `actor_ever_appeared AND <its own flag>` instead of `actor_ever_appeared` alone.
        "my_queue_ever_ingested": my_queue_ever_ingested,
        "blocked_on_me": blocked_rows,
        "handoff_ever_ingested": handoff_ever_ingested,
        "park_count": park_count,
        "park_ever_ingested": park_ever_ingested,
        "verdicts_given": verdict_rows,
        "verdicts_ever_ingested": verdicts_ever_ingested,
        "cost": {
            "tokens_in": cost_row[0], "tokens_out": cost_row[1],
            "cost_cents": cost_row[2], "n": cost_row[3],
        },
    }


def render_ic_view(conn, actor, project_id=None, now=None):
    """Render the IC persona's own page: my queue, blocked on me, my parks, my gate verdicts
    (given), my cost. Returns `(html_text, summary)`. Every fetch above is actor-scoped in SQL
    (Decision 4) -- this function only assembles already-filtered rows via #125's unmodified chart
    primitives; it performs no filtering of its own. See the module docstring for the privacy
    boundary and its one sanctioned exception, and for the cold-start banner's own reasoning."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    payload = collect_ic_payload(conn, actor, project_id=project_id, now=now)

    generated_at = payload["generated_at"]
    actor_ever_appeared = payload["actor_ever_appeared"]
    my_queue_rows = payload["my_queue"]
    handoff_ever_ingested = payload["handoff_ever_ingested"]
    blocked_rows = payload["blocked_on_me"]
    park_count = payload["park_count"]
    verdict_rows = payload["verdicts_given"]
    # Reconstructed from payload["cost"] rather than re-calling `_cost_row` a second time -- the
    # HTML renderer below (`_render_cost`) and the summary's `cost_absent` still want the raw
    # 4-tuple shape `_cost_row` returns, and `collect_ic_payload` already paid for that query once.
    cost = payload["cost"]
    cost_row = (cost["tokens_in"], cost["tokens_out"], cost["cost_cents"], cost["n"])

    banner = ""
    if not actor_ever_appeared:
        banner = (
            '<div class="banner"><strong>Actor '
            f'&quot;{html.escape(actor)}&quot; has never appeared in this project&#39;s ledger'
            "</strong> &mdash; check <code>ledger.actor</code> in <code>.sdlc/config.json</code> "
            "(or the <code>--actor</code> flag). Every clause below is empty because nothing was "
            "found for this identity, not necessarily because there is nothing to show.</div>"
        )

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
