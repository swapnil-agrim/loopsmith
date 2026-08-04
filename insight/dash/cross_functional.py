# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The cross-functional persona view (issue #133, E5.S3): a gate-coverage matrix built entirely
on metric_24 (proxy, 2 of the 12 spec-vocabulary gates), plus two explicit not-measured panels
for risk-review hit rate and alignment drift. See .sdlc/plans/133.md for the full decision
record; this module's own comments cite it by Decision number rather than re-deriving the
reasoning inline.

HONESTY, BY CONSTRUCTION, NOT BY PROSE ALONE:
  - Rows are collector-pack WINDOWS, never goals (Decision 4) -- metric_24 carries no goal_id
    column at all, so there is no way to accidentally group by goal even if a future edit tried.
    The rendered column header says "Window (collected)", never "Goal".
  - Only the 2 gates with a real data source (plan_gate/review_gate) get columns; the other 10
    spec-vocabulary gates are named once in prose, never rendered as placeholder ABSENT columns
    (Decision 5 -- that would be a subtler version of the exact overclaim this page exists to
    prevent).
  - risk-review hit rate and alignment drift are no-argument, no-query functions, structurally
    incapable of producing a number -- same posture insight.dash.leadership._render_effectiveness
    takes for the Effectiveness tile.

PRIVACY: this page has ZERO sanctioned individual-grain exceptions (Decision 2) -- stricter than
insight.dash.manager's one, matching insight.dash.leadership. metric_24 carries no actor column
at all, and the two not-measured panels take no arguments and query nothing.
"""
import datetime
import html

from insight.dash.charts import _absent_line
from insight.dash.colors import status_mark, texture_defs, viz_css_vars
from insight.dash.instrument import page_close, page_open
from insight.dash.render import json_script
from insight.metrics.loader import load_metrics

# issue #264: `viz_css_vars()`, not `base_style()` -- see manager.py's own comment on this same
# substitution for the full reasoning (instrument.page_open supplies the generic chrome now).
_STYLE = f"""
{viz_css_vars()}
.matrix-scope-note, .matrix-absent-note {{ color: var(--dash-ink2); }}
"""

#: Payload allowlist (issue #129: every widened `SELECT *` read needs its OWN allowlist).
_MATRIX_PAYLOAD_KEYS = ("project_id", "collected_ts", "gate", "pct", "status")

_GATE_HEADERS = (
    ("plan_gate", "plan_gate (proxy: plan_review)"),
    ("review_gate", "review_gate (proxy: code_review)"),
)


# --------------------------------------------------------------------------- page-specific fetchers

def _gate_matrix_rows(conn):
    """Groups metric_24's flat (project_id, collected_ts, gate, pct, status) rows into one entry
    per (project_id, collected_ts) window with both gates nested under "gates" -- Decision 4:
    rows are windows, never goals, because metric_24 itself carries no goal_id column."""
    cur = conn.execute("SELECT * FROM metric_24 ORDER BY project_id, collected_ts, gate")
    cols = [d[0] for d in cur.description]
    flat = [dict(zip(cols, row)) for row in cur.fetchall()]
    grouped, order = {}, []
    for r in flat:
        key = (r["project_id"], r["collected_ts"])
        if key not in grouped:
            grouped[key] = {"project_id": r["project_id"], "collected_ts": r["collected_ts"], "gates": {}}
            order.append(key)
        grouped[key]["gates"][r["gate"]] = {"status": r["status"], "pct": r["pct"]}
    return [grouped[k] for k in order]


# --------------------------------------------------------------------------- cell composer + formatter

def _fmt_gate_pct(status, pct):
    """ABSENT never shows a percentage (Decision 3 / "What the HTML actually is"): metric_24's
    own pct column is a fail-open placeholder for the total-failure case (e.g. the no_git pack's
    plan_existed_pct is literally 0 in the raw JSON, hard-coded by the collector's own degrade
    path, not a measured zero) -- so ABSENT must never display a percentage at all, not even
    "0%"."""
    if status == "ABSENT":
        return "n/a"
    return f"{pct}%"


def _matrix_cell_svg(status, id_prefix="dash"):
    """One <svg> per table cell: status_mark()'s colour + icon + label (three channels), plus
    texture_defs()'s hatch pattern -- ONLY status_mark() itself layers the fourth, ABSENT-only
    texture channel on top (Decision 6). The same composition insight.dash.charts._absent_line
    already does for a <p>, generalized to all four statuses and inlined in a <td> instead.

    This emits NO <defs> of its own (issue #133 review). The pattern id is derived from id_prefix,
    which also names the CSS vars status_mark() reads, so it cannot be varied per element to keep
    the id unique -- render_cross_functional_view defines the pattern once for the document and
    url(#...) resolves to it from every mark here."""
    # No <defs> here: render_cross_functional_view emits texture_defs() once per document
    # (issue #133 review -- per-cell defs put a dozen elements on one id).
    return (
        f'<svg width="90" height="16" viewBox="0 0 90 16" role="img" aria-label="{html.escape(status)}">'
        + status_mark(status, 6, 8, id_prefix=id_prefix)
        + "</svg>"
    )


# --------------------------------------------------------------------------- bespoke section renderers

def _render_gate_matrix(rows, id_prefix="dash"):
    """Section body for panel-gate-matrix. rows == [] (metric_24 has zero rows because no
    alignment-collect/v1 pack was ever ingested) reuses insight.dash.charts._absent_line
    unmodified, same primitive leadership.py already uses for its own empty-state branches.

    issue #265 (D4) Design 5, independent plan-review Finding 1: the empty-rows branch is
    hardcoded to `id_prefix="panel"` -- NOT threaded through this function's own `id_prefix`
    parameter, which governs ONLY the verdict-badge cells further down (`_matrix_cell_svg`, a
    different code path, reached only when `rows` is non-empty). This function's one real call
    site (`render_cross_functional_view`) always wants panel material here regardless of what it
    passes for `id_prefix` -- a second parameter would add API surface with only one legal value
    ever passed (the exact "parameter that looks general but is special-cased" trap
    .sdlc/plans/263.md's own Decision 2 warns against). Naively passing `id_prefix="panel"` at
    the call site instead would ALSO flip every verdict badge to "panel", which
    `panel_css_vars()` does not carry the full PASS/WARN/FAIL/ABSENT vocabulary for -- the exact
    undefined-custom-property trap this module's own docstring warns about."""
    if not rows:
        return _absent_line(
            "no alignment-collect pack has ever been ingested -- metric #24 has zero rows.",
            id_prefix="panel",
            provenance="no writer · insight.ingest.collectors (alignment-collect never run)",
        )
    row_html = []
    for row in rows:
        window_iso = row["collected_ts"].isoformat() if hasattr(row["collected_ts"], "isoformat") \
            else str(row["collected_ts"])
        cells = []
        for gate, _label in _GATE_HEADERS:
            g = row["gates"].get(gate)
            status = g["status"] if g else "ABSENT"
            pct = g["pct"] if g else None
            cells.append(
                f'<td data-gate="{html.escape(gate)}" data-status="{html.escape(status)}">'
                f"{_matrix_cell_svg(status, id_prefix=id_prefix)}"
                f'<span class="cell-pct">{html.escape(_fmt_gate_pct(status, pct))}</span></td>'
            )
        row_html.append(
            f'<tr data-project="{html.escape(str(row["project_id"]))}" '
            f'data-window="{html.escape(window_iso)}">'
            f'<td>{html.escape(str(row["project_id"]))}</td>'
            f'<td>{html.escape(window_iso)}</td>'
            + "".join(cells) + "</tr>"
        )
    header_cells = "".join(f"<th>{html.escape(label)}</th>" for _gate, label in _GATE_HEADERS)
    # issue #264: the wrapper carries the horizontal-scroll guard, never the <table> itself --
    # `overflow-x:auto` on a `display:table` box behaves inconsistently across engines (plan
    # .sdlc/plans/264.md Sec 1.5). instrument.FRAME_CSS's `.table-scroll` rule supplies it.
    return (
        '<div class="table-scroll"><table><thead><tr>'
        f"<th>Project</th><th>Window (collected)</th>{header_cells}"
        f"</tr></thead><tbody>{''.join(row_html)}</tbody></table></div>"
    )


def _render_risk_review():
    """Section body for panel-risk-review. Takes NO arguments, reads NO table -- structurally
    incapable of producing a number, same posture insight.dash.leadership._render_effectiveness
    takes for its own Effectiveness tile: risk-detect.sh emits risk-detect/v1 output, but it is
    never ingested (insight.ingest.collectors.SOURCES lists only alignment-collect,
    discovery-scan, and pipeline-card), and there is no insight/metrics/*.sql view for this row."""
    return (
        '<div id="risk-review-tile">'
        + _absent_line(
            "no instrument. risk-detect.sh emits risk-detect/v1 output, but it is never "
            "ingested: insight.ingest.collectors.SOURCES lists only alignment-collect, "
            "discovery-scan, and pipeline-card. There is no insight/metrics/*.sql view for "
            "this row. Wiring risk-detect in is a follow-up story, not this one.",
            id_prefix="panel",
            provenance="no writer · insight.ingest.collectors (risk-detect/v1 never ingested)",
        )
        + "</div>"
    )


def _render_alignment_drift():
    """Section body for panel-alignment-drift. Takes NO arguments, reads NO table -- same
    structural posture as _render_risk_review above: alignment drift (#28) needs /sdlc-align
    verdicts written via the gate{alignment} emitter (spec A.5 site 10), which is not built --
    fact_event has zero writers for its gate/verdict/phase/cycle columns
    (insight/ingest/ledger_writer.py only ever populates project_id, goal_id, ts, actor_id, kind,
    reliability_class). There is no insight/metrics/*.sql view for this row either."""
    return (
        '<div id="alignment-drift-tile">'
        + _absent_line(
            "no instrument. Alignment drift (#28) needs /sdlc-align verdicts written via the "
            "gate{alignment} emitter (spec A.5 site 10), which is not built: fact_event has "
            "zero writers for its gate/verdict/phase/cycle columns "
            "(insight/ingest/ledger_writer.py only ever populates project_id, goal_id, ts, "
            "actor_id, kind, reliability_class). There is no insight/metrics/*.sql view for "
            "this row either.",
            id_prefix="panel",
            provenance=(
                "no writer · fact_event.gate/verdict/phase/cycle "
                "(ledger_writer.py never populates them)"
            ),
        )
        + "</div>"
    )


# --------------------------------------------------------------------------- page shell

def render_cross_functional_view(conn, now=None, metrics_dir=None):
    """Render the cross-functional persona's own page: the gate-coverage matrix (proxy, #24)
    plus the two not-measured panels for risk-review hit rate and alignment drift. Returns
    (html_text, summary). No `actor` parameter (Decision 2, mirrors leadership.py's own zero-
    exception posture). `metrics_dir` is test-only, mirrors render_leadership_view's own
    convention -- the CLI never passes it."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    generated_at = now.isoformat()

    load_metrics(conn, metrics_dir=metrics_dir)  # fail-fast-on-a-bad-catalog, same contract as
    # every other persona view -- registry itself is unused directly below.

    matrix_rows = _gate_matrix_rows(conn)

    flat_matrix_rows = [
        {"project_id": row["project_id"], "collected_ts": row["collected_ts"], "gate": gate,
         "pct": g["pct"], "status": g["status"]}
        for row in matrix_rows
        for gate, g in row["gates"].items()
    ]
    payload = {
        "generated_at": generated_at,
        "gate_matrix": [{k: r[k] for k in _MATRIX_PAYLOAD_KEYS} for r in flat_matrix_rows],
        "risk_review_status": "absent",
        "alignment_drift_status": "absent",
    }

    # The page's own <title> text is preserved exactly (issue #264 Step 9) -- only the head/nav
    # around it now come from the shared instrument.page_open()/page_close() shell.
    head = page_open(
        "LoopSmith Insight -- Cross-functional view", current="cross-functional", extra_css=_STYLE,
    )

    html_text = f"""{head}
<!-- The ABSENT hatch pattern, defined ONCE for the whole document (issue #133 review). Its id is
     derived from id_prefix, which also names the CSS vars status_mark() reads, so it cannot be
     varied per element -- defining it once is the only way a page with several ABSENT states
     keeps a unique id. url(#...) resolves document-wide, so every mark below references this. -->
<svg width="0" height="0" aria-hidden="true" focusable="false">{texture_defs()}</svg>
<h1>LoopSmith Insight -- Cross-functional view</h1>
<p>Generated {html.escape(generated_at)}. Aggregate only: no metric on this page renders at
individual grain -- zero exceptions, same posture as the leadership view.</p>

<section id="panel-gate-matrix">
<h2>Gate coverage matrix (proxy, #24)</h2>
<p class="matrix-scope-note">Rows are alignment-collect windows (one row per ingested collector
pack), not individual goals -- the per-goal gate emitter is not built (see this page's own "does
not deliver" note). Columns cover 2 of the 12 spec gate vocabulary (plan_gate, review_gate --
proxies for plan_review/code_review); the remaining 10 (post_review, merge, decision, alignment,
verify, risk_security, risk_contract, risk_migration, risk_release, risk_debug) have no data
source and are not rendered as columns, so this matrix never implies a coverage it cannot back.
Status uses pass/warn/fail/absent -- this repo's shipped vocabulary; the issue's own wording says
&quot;block&quot;.</p>
<p class="matrix-absent-note">ABSENT means the gate had no eligible activity in this window (no
commit touched a recognized source file for plan_gate; zero commits in the window for
review_gate) -- "did not apply here," not "applied and nothing was recorded". The data cannot
distinguish those two states; this matrix only claims the one it can.</p>
{_render_gate_matrix(matrix_rows)}
</section>

<section id="panel-risk-review">
<h2>Risk-review hit rate</h2>
{_render_risk_review()}
</section>

<section id="panel-alignment-drift">
<h2>Alignment drift</h2>
{_render_alignment_drift()}
</section>

<script type="application/json" id="insight-cross-functional-data">{json_script(payload)}</script>
<footer>Self-contained: no network fetch, no external script/style/font reference. Data is
inlined above. No individual-grain metric appears anywhere on this page.</footer>
{page_close()}"""

    summary = {
        "gate_matrix_window_count": len(matrix_rows),
        "risk_review_status": "absent",
        "alignment_drift_status": "absent",
    }
    return html_text, summary
