# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The dashboard shell renderer (issue #124, E4.S1). Builds ONE self-contained HTML string from
an already-open DuckDB connection -- no `import duckdb` here, conn is passed in already open,
same convention as insight.gaps.report / insight.metrics.loader.

Output shape decision (see .sdlc/plans/124.md Decision 1, reproduced live against real headless
Chrome): all data is INLINED as JSON inside a <script type="application/json"> block, safely
escaped via json_script() below -- never a sibling data.json + fetch(), which fails outright
under file:// (CORS on the file: scheme, not a timing issue).

Generator decision (Decision 2): plain f-strings + two escaping primitives (html.escape for text,
json_script for the one inlined data block) -- no templating engine, no new dependency beyond the
one insight/ already declares (duckdb).

Data-status decision (Decision 3, REVISED after plan review -- .sdlc/plans/124.md sections K/L): a
metric's rendered status is derived from a LIVE, SHAPE-AWARE measured-count against its own view
(_measured() below), never a blanket `select count(*)` -- a bare-aggregate metric view with no
GROUP BY (e.g. 3/11/12/14) returns exactly ONE phantom row of NULLs/zeros over ZERO input rows,
so `count(*)` on it is always >= 1 regardless of whether anything was ever measured (live-proven,
section K -- this was a real bug in this story's own first draft, not a hypothetical). Nor is the
`-- data_status: dark` catalog label trusted alone (proven stale for 7 metrics today, section B) --
the label is shown alongside as context, never trusted as the answer. Cold-store detection is
likewise two independent signals, not one (section L): `ever_ingested` (a real `insight ingest` has
ever run against this store, signalled by a dim_project row) vs `has_data` (something measurable has
actually landed) -- conflating them tells a genuine onboarding-week user (ingested, nothing to
measure yet) to re-run a command they already ran.
"""
import datetime
import html
import json
import re
import urllib.parse

from insight.dash.colors import status_mark, texture_defs
from insight.dash.shell import base_style
from insight.gaps.report import build_report, json_default
from insight.metrics.loader import load_metrics

DEFAULT_OUT_DIR = "insight-dash"  # joined under .sdlc/ by the CLI layer, mirrors DEFAULT_DB_PATH

#: issue #134 (E5.S4), Decision 4: the config-UI deep-link contract, authored here because E8 has
#: no code yet. `CONFIG_UI_PATH` is a bare relative sibling filename, matching every existing
#: dash page's own cross-page convention (`ic.html`, `manager.html`, `leadership.html`,
#: `cross-functional.html` -- insight/__main__.py) -- `assert_self_contained`'s regex only
#: rejects an absolute `https?://` / protocol-relative `//` origin (or `@import`/`url()` pointing
#: at one), never a bare relative filename, so this link shape is untouched by that guard and a
#: static `file://` page can still form it.
CONFIG_UI_PATH = "config.html"


def config_deep_link(field):
    """The URL contract for "the card deep-links into the configuration UI with the field
    pre-selected" (spec line 566). Returns a RELATIVE url: `config.html?field=<field>`, `field`
    URL-encoded via stdlib `urllib.parse.quote` (its default-safe unreserved set already includes
    `.`, so a dotted path like `work.require_review` round-trips untouched; a value needing
    escaping, e.g. a literal space, is percent-encoded). `field` is a dotted path into a
    project's own `config_json` (e.g. `work.require_review`, `verify.command` -- see
    insight/gaps/coverage_review_missing.sql and coverage_verify_no_command.sql's own
    `-- config_field:` header lines). "Pre-selected" means: the config UI (E8, not built by this
    goal) opens with this field focused/scrolled-to -- E8.S5 is the real integration point; this
    function only authors and tests the URL shape it will consume.

    This docstring IS the contract's home -- .sdlc/plans/134.md Decision 4: this repo's own
    convention for a cross-epic contract is a docstring promoted to "durable, reusable" status in
    place (see assert_self_contained's own docstring, "promoted to a durable, reusable build-time
    guard for S2-S4"), not a new markdown file."""
    return f"{CONFIG_UI_PATH}?field={urllib.parse.quote(field)}"

#: Task 7 (issue #125, E4.S2, Decision 6): the badge/dot colours below used to be literal,
#: light-mode-only hex baked straight into this string, with no dark-mode variant at all. They now
#: reference insight.dash.colors' shared tokens via viz_css_vars() -- the SAME status vocabulary
#: every chart primitive in insight/dash/charts.py uses, so a viewer never sees two competing
#: colour systems for the same four PASS/WARN/FAIL/ABSENT states. `.viz-root` (the class the
#: <body> carries below) is the scope viz_css_vars()'s custom properties are declared under; the
#: `body` selector itself picks up `--dash-ink`/`--dash-surface` for the page's own text/background,
#: gaining dark-mode support the shell never had.
#:
#: issue #134 (E5.S4): `.badge*` (below, now removed) and `_ICON_CLASS` (below, now removed) were
#: only ever used by `_render_gaps_table`'s one-`<tr>`-per-finding badge -- confirmed by grep, no
#: other module references either. `_render_gap_findings` replaces that table with real four-part
#: cards (`.gap-card*`) for WARN/FAIL, using `_gap_status_svg`/`status_mark()` for the status
#: mark instead of a CSS badge class; PASS/ABSENT keep a compact table, styled by `.gap-quiet-table`.
_STYLE = f"""
{base_style()}
.dot-has {{ color: var(--dash-status-pass); }} .dot-empty {{ color: var(--dash-muted); }}
.gap-card {{ border: var(--dash-border-hairline) solid var(--dash-gridline); border-radius: var(--dash-radius-sm);
            padding: var(--dash-space-3) var(--dash-space-4); margin-bottom: var(--dash-space-3); }}
.gap-card-what {{ margin: 0 0 var(--dash-space-1); font-size: var(--dash-text-subhead); }}
.gap-card-evidence {{ margin: 0 0 var(--dash-space-1); padding-left: var(--dash-space-5); font-size: var(--dash-text-small); color: var(--dash-ink2); }}
.gap-card-metric {{ margin: 0 0 var(--dash-space-1); font-size: var(--dash-text-small); color: var(--dash-ink2); }}
.gap-card-action {{ margin: 0; }}
.gap-card-config-link {{ margin-left: var(--dash-space-2); }}
.gap-quiet-table td {{ font-size: var(--dash-text-small); }}
"""

#: The 8 collector-derived metrics available with zero setup on day zero -- no ledger, no
#: config change, nothing turned on (spec: docs/superpowers/specs/2026-07-30-loopsmith-insight-
#: data-platform-design.md lines 505-506, "Cold start -- stated plainly").
_COLD_START_METRICS = {"3", "4", "5", "9", "20", "24", "30", "42"}

#: The three characters that let embedded JSON interact with surrounding HTML/script lexing.
#: Escaping exactly these three (matches Django's json_script filter) is sufficient -- proven
#: live against a real </script> breakout payload, .sdlc/plans/124.md section F.
_JSON_SCRIPT_ESCAPES = (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"))


def json_script(data):
    """Safely serialize `data` for embedding inside a `<script type="application/json">` block."""
    s = json.dumps(data, default=json_default)
    for char, esc in _JSON_SCRIPT_ESCAPES:
        s = s.replace(char, esc)
    return s


_EXTERNAL_REF = re.compile(
    r'(?:src|href)\s*=\s*["\'](https?:)?//[^"\']*["\']'
    r'|@import\s+["\']?(?:https?:)?//'
    r'|url\(\s*["\']?(?:https?:)?//',
    re.IGNORECASE,
)


def assert_self_contained(html_text):
    """Raise AssertionError if `html_text` references an external origin (absolute http(s):// or
    protocol-relative //host in a src=/href=, or an @import / CSS url()). Called both by
    insight/tests/test_dash_render.py and by main()'s own `dash` branch, right after rendering
    and before writing to disk -- Task 3's "assert no external network fetches at render",
    the RENDERED-PAGE half (see .sdlc/plans/124.md Decision 4b).

    KNOWN BLIND SPOTS, confirmed live rather than left implied (.sdlc/plans/124.md section N) --
    this is a text-level heuristic, not an HTML/CSS parser: a `<meta http-equiv=refresh
    content="...url=https://...">`, `<form action="https://...">`, `<object data="https://...">`,
    `<video poster="https://...">`, a CSS `url (https://...)` with a space before the paren, a
    JS string built via concatenation (`fetch("htt"+"ps://...")`), or an UNQUOTED src=/href=
    attribute (`<script src=https://evil.js>`) all slip past this regex today. None of these
    shapes are reachable through THIS story's own template (render.py only ever emits quoted
    attributes via f-strings, and never a <meta>/<form>/<object>/<video> tag at all) -- named here
    because this function is promoted to a durable, reusable build-time guard for S2-S4, not kept
    private to this story's own known-safe output."""
    m = _EXTERNAL_REF.search(html_text)
    if m:
        raise AssertionError(f"rendered page references an external origin: {m.group(0)!r}")


class CoverageDenominatorMissing(RuntimeError):
    """Raised when a metric declares reliability_class 2 but its own view's result row does not
    carry all four coverage-denominator columns (insight.metrics.reliability's
    COVERAGE_DENOMINATOR_COLUMNS). Spec lines 125-127: 'A Class-2 metric with no coverage figure
    is a bug, not a number' -- caught loudly here, not degraded around, same posture as
    HeaderError/MetricLoadError. Raise, not a rendered ABSENT marker: this is a first-party
    authoring-contract violation, not a data-absence state -- see .sdlc/plans/129.md Decision D8
    for the full reasoning."""


#: Exact four names from insight.metrics.reliability.COVERAGE_DENOMINATOR_COLUMNS (issue #129).
_COVERAGE_COLUMNS = ("class1_count", "class2_count", "total_count", "coverage_pct")


def extract_coverage(metric_id, reliability_class, row):
    """Returns None if reliability_class != 2 or row is None (class-1, or a class-2 metric with
    nothing measured yet -- no value is being shown, so nothing needs qualifying). Otherwise
    returns the four coverage-denominator columns as a dict when all four are present as KEYS in
    `row` (presence, not truthiness -- coverage_pct may legitimately be NULL/None when
    total_count is 0, per reliability.py's own NULLIF guard; that is a real 'n/a', not a missing
    column). Raises CoverageDenominatorMissing when reliability_class == 2, a row exists, and any
    of the four columns is absent from row's keys (.sdlc/plans/129.md Decision D1/D8)."""
    if reliability_class != 2 or row is None:
        return None
    missing = [c for c in _COVERAGE_COLUMNS if c not in row]
    if missing:
        raise CoverageDenominatorMissing(
            f"metric {metric_id}: declares reliability_class 2 but its own view is missing "
            f"coverage-denominator column(s) {missing} -- spec lines 125-127: a class-2 metric "
            "with no coverage figure is a bug, not a number. Paste "
            "insight.metrics.reliability.COVERAGE_DENOMINATOR_COLUMNS into its SELECT list."
        )
    return {c: row[c] for c in _COVERAGE_COLUMNS}


def coverage_denominator_html(coverage):
    """Pure formatter, never raises. coverage=None -> "". Otherwise the one literal
    <span class="coverage-denom"> shape every embed site (this file's _render_metric_table,
    insight.dash.manager's WIP stat and park-rate stat) reuses unmodified -- one shape, three call
    sites, one regex (.sdlc/plans/129.md Decision D1). Leading space is deliberate: callers
    concatenate this directly onto the preceding value/markup with no extra separator."""
    if coverage is None:
        return ""
    pct = coverage["coverage_pct"]
    if pct is None:
        pct_text = "n/a"
    else:
        try:
            pct_text = f"{float(pct):.0%}"
        except (TypeError, ValueError):
            pct_text = html.escape(str(pct))
    # issue #129 review: escape all three, matching pct_text's own fallback above and every other
    # data-derived value in this file -- extract_coverage checks column PRESENCE only, not type,
    # so a .sql aliasing something non-numeric into these names must not inject unescaped content.
    return (
        ' <span class="coverage-denom">&mdash; coverage '
        f'{pct_text} ({html.escape(str(coverage["class1_count"]))} of '
        f'{html.escape(str(coverage["total_count"]))} rows class-1, '
        f'{html.escape(str(coverage["class2_count"]))} class-2)</span>'
    )


def _measured(cols, rows):
    """The live, shape-aware 'was anything real measured' signal for one metric view's own
    result (.sdlc/plans/124.md section K -- fixes a real bug this story's own first draft had:
    a blanket `count(*)` is always >= 1 for a bare-aggregate view with no GROUP BY, since SQL
    returns one row of NULLs/zeros over zero input rows, never zero rows). If the view has any
    column named `..._count` (19 of today's 25 do, verified live per-metric), the answer is the MAX
    value across all such columns, across all returned rows -- these columns are real 0 (not NULL)
    over an empty population, by SQL's own COUNT semantics, so this correctly reads 0 on a
    genuinely unmeasured view. If the view has no such column at all -- the exhaustive set is
    {10, 24, 26, 33, 34, 35}, six plain per-record views where a real zero-row result already means
    zero-population with no phantom-row problem to correct for -- the answer is simply the row
    count."""
    count_idx = [i for i, c in enumerate(cols) if c.endswith("_count")]
    if count_idx:
        return max((row[i] or 0) for row in rows for i in count_idx) if rows else 0
    return len(rows)


def _metric_rows(conn, metrics_dir=None):
    """Live per-metric rows: id, name, personas, reliability_class, measured (LIVE, shape-aware --
    see _measured()), has_data, labelled_dark (the catalog's own, possibly-stale claim), proxy."""
    registry = load_metrics(conn, metrics_dir=metrics_dir)  # MetricLoadError propagates -- fatal
    rows = []
    for metric_id in sorted(registry, key=int):
        meta = registry[metric_id]
        cur = conn.execute(f"select * from {meta['view_name']}")
        cols = [d[0] for d in cur.description]
        view_rows = cur.fetchall()
        measured = _measured(cols, view_rows)
        first_row = dict(zip(cols, view_rows[0])) if view_rows else None
        coverage = extract_coverage(metric_id, meta["reliability_class"], first_row)
        rows.append({
            "id": metric_id, "name": meta["name"], "personas": meta["personas"],
            "reliability_class": meta["reliability_class"], "measured": measured,
            "has_data": measured > 0,
            "labelled_dark": meta["extra"].get("data_status") == "dark",
            "proxy": meta["extra"].get("proxy") == "true",
            "coverage": coverage,
        })
    return rows


def _ever_ingested(conn):
    """True iff a real `insight ingest` has run against this store at least once -- signalled by
    a dim_project row, written unconditionally by write_project_snapshot on EVERY ingest run
    (adopted or skipped-with-a-reason alike; insight/ingest/artifact_reader.py:243-258), and
    NEVER written by `insight gaps`'s own open_store() call. Distinct from has_data (below) --
    see .sdlc/plans/124.md section L: a store can be ever_ingested=True and still have zero
    measurable data (a real onboarding-week adoption), which must NOT be told to re-run ingest."""
    return conn.execute("select count(*) from dim_project").fetchone()[0] > 0


def _ledger_has_rows(conn):
    """True iff insight_ledger has ever written a row into fact_event or fact_handoff -- the
    ONLY two tables insight.ingest.ledger_writer writes (verified by reading that module in
    full: _EVENT_INSERT_SQL writes fact_event, _apply_handoff/_apply_ack write fact_handoff;
    ingest_ledger_cursor and, since issue #380, ingest_ledger_cursor_legacy are resume/migration
    bookkeeping only, read by no metric view). Both are real
    base tables, not aggregate views, so a plain count(*) is exact -- no phantom-row
    correction needed (same posture as insight.dash.manager._reason_class_measured_count).
    Deliberately NOT derived from any metric's has_data -- see .sdlc/plans/128.md's own 'why the
    allowlist-complement condition is wrong' section: several non-cold-start metrics (26, 41)
    read non-ledger tables and would falsely flip a metric-derived signal on ordinary
    goal-file/collector ingest that never touched the ledger at all."""
    return (
        conn.execute("select count(*) from fact_event").fetchone()[0] > 0
        or conn.execute("select count(*) from fact_handoff").fetchone()[0] > 0
    )


def _render_onboarding(metric_rows):
    """The 'ingested, ledger off' onboarding surface -- #128's actual target state: this store
    HAS been ingested (dim_project exists), collectors have run, but fact_event/fact_handoff
    are both empty (see _ledger_has_rows). Lists each of the 8 cold-start metrics by its real
    has_data (from _metric_rows() -- no new SQL) so a metric that DOES already have data shows
    "has data", not "no data yet". Text is the spec's own wording at lines 511-513,
    HTML-equivalent markup for the source markdown's backticks/em dash/italics -- never
    paraphrased. `id="cold-start-metrics"` lets tests locate this table without confusing it
    with the catalog table below."""
    available = [m for m in metric_rows if m["id"] in _COLD_START_METRICS]
    rows = "".join(
        f"<tr><td>{html.escape(m['id'])}</td><td>{html.escape(m['name'])}</td>"
        f"<td>{'has data' if m['has_data'] else 'no data yet'}</td></tr>"
        for m in available
    )
    return (
        '<div class="banner"><strong>Ledger not enabled</strong> -- '
        "Throughput, cycle time and WIP need the team ledger &mdash; turn it on with one "
        "config change and one <code>sync.py bootstrap</code>. Here is what you "
        "<em>can</em> see today:"
        '<table id="cold-start-metrics"><thead><tr><th>id</th><th>name</th><th>status</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _render_metric_table(rows):
    out = []
    for m in rows:
        status_class = "dot-has" if m["has_data"] else "dot-empty"
        status_text = "has data" if m["has_data"] else "no data yet"
        stale = ""
        if m["labelled_dark"] and m["has_data"]:
            stale = ' <span title="catalog label says dark; live measured-count says otherwise">(label stale)</span>'
        out.append(
            f"<tr><td>{html.escape(m['id'])}</td><td>{html.escape(m['name'])}</td>"
            f"<td>{html.escape(', '.join(m['personas']))}</td><td>{m['reliability_class']}</td>"
            f"<td>{m['measured']}{coverage_denominator_html(m['coverage'])}</td>"
            f"<td class='{status_class}'>{status_text}{stale}</td></tr>"
        )
    return "".join(out)


def _gap_status_svg(status, id_prefix="dash"):
    """One <svg> per card/row: status_mark()'s colour + icon + label (three channels), plus
    texture_defs()'s hatch pattern for ABSENT -- mirrors insight.dash.cross_functional's own
    `_matrix_cell_svg` exactly (issue #134, E5.S4). Emits NO <defs> of its own: `render_dashboard`
    emits `texture_defs()` ONCE, immediately after `<body class="viz-root">` (issue #231's fix,
    applied here by direct precedent rather than re-derived) -- a per-element <defs> would put a
    whole page of gap cards on one duplicated id."""
    return (
        f'<svg width="90" height="16" viewBox="0 0 90 16" role="img" aria-label="{html.escape(status)}">'
        + status_mark(status, 6, 8, id_prefix=id_prefix)
        + "</svg>"
    )


def _render_gap_card(f):
    """One WARN/FAIL finding -> one four-part card: what (name) / evidence rows / metric this
    gap would move / one action, config-fixable ones deep-linking via config_deep_link (issue
    #134, E5.S4). Evidence rows are `html.escape(str(row))` -- the same `str(dict)` convention
    `render_report()` (insight/gaps/report.py, `f"    evidence: {row}"`) already uses; no cap on
    row count, mirroring `render_report`'s own unconditional loop.

    MARKUP SHAPE IS LOAD-BEARING -- see .sdlc/plans/134.md "Corrected anchors": every part gets a
    data-ONLY element, never a container that also holds fixed chrome (a status SVG's own
    `<text>`, or a literal label like "Metric this gap would move:"). The plan's own Task-4
    negative control caught a first draft where the anchor was the whole `<h3>`/`<p>` -- dropping
    `name` or `metric` still "passed" because those tags' own fixed text was mistaken for the
    part's data. `<span class="gap-card-name">` and the `<code>` inside `.gap-card-metric` exist
    for exactly that reason -- do not collapse them back into their parent tag."""
    config_link = ""
    if f["config_field"]:
        config_link = (
            f' <a class="gap-card-config-link" href="{html.escape(config_deep_link(f["config_field"]))}">'
            "Fix in config</a>"
        )
    evidence_rows = "".join(f"<li>{html.escape(str(row))}</li>" for row in f["evidence"])
    return (
        f'<div class="gap-card" data-rule-id="{html.escape(f["rule_id"])}">'
        f'<h3 class="gap-card-what">{_gap_status_svg(f["severity"])} '
        f'<span class="gap-card-name">{html.escape(f["name"])}</span></h3>'
        f'<ul class="gap-card-evidence">{evidence_rows}</ul>'
        f'<p class="gap-card-metric">Metric this gap would move: '
        f'<code>{html.escape(str(f["metric"]))}</code></p>'
        f'<p class="gap-card-action">{html.escape(f["action"])}{config_link}</p>'
        f"</div>"
    )


def _render_gap_findings(findings):
    """Splits `findings` by severity (issue #134, E5.S4): PASS/ABSENT never reach
    `_render_gap_card` (evidence is guaranteed empty by evaluate.py's own invariant, so a card is
    not even possible) -- they render as one compact `<tr>` each in a quiet table instead,
    mirroring `render_report()`'s own text-form PASS/ABSENT-vs-WARN/FAIL split. WARN/FAIL render
    as real cards, or a `<p class="gap-cards-empty">` fallback when there are none."""
    actionable = [f for f in findings if f["severity"] in ("WARN", "FAIL")]
    quiet = [f for f in findings if f["severity"] not in ("WARN", "FAIL")]

    if actionable:
        cards_html = "".join(_render_gap_card(f) for f in actionable)
    else:
        cards_html = '<p class="gap-cards-empty">No actionable gap findings.</p>'

    quiet_rows = "".join(
        f"<tr><td>{_gap_status_svg(f['severity'])}</td>"
        f"<td>{html.escape(f['name'])}</td><td>{html.escape(f['class'])}</td></tr>"
        for f in quiet
    )
    quiet_table = (
        '<table class="gap-quiet-table"><thead><tr><th>status</th><th>name</th><th>class</th>'
        f"</tr></thead><tbody>{quiet_rows}</tbody></table>"
    )
    return cards_html + quiet_table


def render_dashboard(conn, db_path_label, metrics_dir=None):
    """Render the S1 shell as one self-contained HTML string. Returns (html_text, summary) --
    summary is the small dict main()'s `dash` branch prints to stdout (never re-queries the store
    a second time for it). `metrics_dir` is test-only (mirrors load_metrics' own signature) -- the
    CLI never passes it, always the real shipped catalog."""
    metric_rows = _metric_rows(conn, metrics_dir=metrics_dir)
    gaps_report = build_report(conn)
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Two independent, data-derived signals -- NOT one blanket "cold" flag. See
    # .sdlc/plans/124.md sections K/L: has_data alone (via the shape-aware _measured(), section K)
    # cannot tell "never ingested" from "ingested, genuinely nothing measurable yet" -- that
    # second, real state (spec's own "cold start" onboarding week) needs a DIFFERENT message, or a
    # real ingested user is told to re-run a command they already ran.
    total_measured = sum(m["measured"] for m in metric_rows)
    has_data = total_measured > 0
    ever_ingested = _ever_ingested(conn)

    banner = ""
    if not ever_ingested:
        banner = (
            '<div class="banner"><strong>No data measured yet</strong> -- this store has never '
            "been ingested. Run <code>insight ingest</code>, then re-run <code>insight dash</code>.</div>"
        )
    elif not has_data:
        banner = (
            '<div class="banner"><strong>Ingested, nothing measurable yet</strong> -- this is '
            "expected right after a brand-new adoption (the spec's own \"cold start\" state). "
            "See the metric catalog below for what's cheap to get first.</div>"
        )
    elif not _ledger_has_rows(conn):
        # ever_ingested and has_data are both True here (some metric has data -- could be a
        # cold-start collector metric, or 26/41/etc. from ordinary goal-file ingest with no
        # ledger involved at all), but the ledger itself has never written a row. #128's target
        # state.
        banner = _render_onboarding(metric_rows)
    # else: the ledger has real rows too -- fully warm, no banner, matching today's behaviour.

    payload = {"generated_at": generated_at, "db_path": db_path_label,
               "ever_ingested": ever_ingested, "has_data": has_data,
               "metrics": metric_rows, "gaps": gaps_report}
    verdict = gaps_report["verdict"]

    html_text = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LoopSmith Insight -- Dashboard</title>
<style>{_STYLE}</style>
</head>
<body class="viz-root">
<!-- The ABSENT hatch pattern, defined ONCE for the whole document (issue #231 -- a per-element
     <defs> puts a whole page of gap-status marks on one duplicated id). Its id is derived from
     id_prefix ("dash", _gap_status_svg's own default), which also names the CSS vars
     status_mark() reads, so it cannot be varied per element -- defining it once here is the only
     way a page with several ABSENT/WARN/FAIL marks keeps a unique id. url(#...) resolves
     document-wide, so every mark below references this. Mirrors insight.dash.cross_functional's
     own render_cross_functional_view, by direct precedent. -->
<svg width="0" height="0" aria-hidden="true" focusable="false">{texture_defs()}</svg>
{banner}
<h1>LoopSmith Insight -- Dashboard (shell)</h1>
<p>Generated {html.escape(generated_at)} from <code>{html.escape(db_path_label)}</code>.
This is the E4.S1 shell: the real metric catalog and the real gap findings report, rendered as
plain tables. Chart primitives (S2) and persona-specific views (S3/S4) land on top of this later.</p>

<h2>Metric catalog ({len(metric_rows)})</h2>
<table>
<thead><tr><th>id</th><th>name</th><th>personas</th><th>reliability</th><th>measured</th><th>status</th></tr></thead>
<tbody>{_render_metric_table(metric_rows)}</tbody>
</table>

<h2>Gap findings -- verdict: {html.escape(verdict['overall'])}{' (errors present)' if verdict['errored'] else ''}</h2>
{_render_gap_findings(gaps_report['findings'])}

<script type="application/json" id="insight-dash-data">{json_script(payload)}</script>
<footer>Self-contained: no network fetch, no external script/style/font reference. Data is inlined above.</footer>
</body>
</html>"""

    summary = {"ever_ingested": ever_ingested, "has_data": has_data,
               "metric_count": len(metric_rows),
               "metrics_with_data": sum(1 for m in metric_rows if m["has_data"]),
               "gaps_verdict": verdict["overall"]}
    return html_text, summary
