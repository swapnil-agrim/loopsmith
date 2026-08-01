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

from insight.gaps.report import build_report, json_default
from insight.metrics.loader import load_metrics

DEFAULT_OUT_DIR = "insight-dash"  # joined under .sdlc/ by the CLI layer, mirrors DEFAULT_DB_PATH

_STYLE = """
body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 2rem; color: #1b1f23; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 4px 10px; border-bottom: 1px solid #e1e4e8; font-size: 13px; }
.dot-has { color: #1a7f37; } .dot-empty { color: #6e7781; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 11px; }
.badge-warn { background: #fff3cd; color: #7a5b00; }
.badge-fail { background: #ffe0e0; color: #8a1c1c; }
.badge-pass { background: #e6f4ea; color: #1a7f37; }
.badge-absent { background: #eee; color: #555; }
.banner { background: #fff3cd; border: 1px solid #f0d98a; padding: .75rem 1rem;
          border-radius: 6px; margin-bottom: 1.5rem; }
footer { margin-top: 2rem; font-size: 12px; color: #6e7781; }
"""

_ICON_CLASS = {"PASS": "badge-pass", "WARN": "badge-warn", "FAIL": "badge-fail", "ABSENT": "badge-absent"}

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
        rows.append({
            "id": metric_id, "name": meta["name"], "personas": meta["personas"],
            "reliability_class": meta["reliability_class"], "measured": measured,
            "has_data": measured > 0,
            "labelled_dark": meta["extra"].get("data_status") == "dark",
            "proxy": meta["extra"].get("proxy") == "true",
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
            f"<td>{m['measured']}</td>"
            f"<td class='{status_class}'>{status_text}{stale}</td></tr>"
        )
    return "".join(out)


def _render_gaps_table(findings):
    out = []
    for f in findings:
        cls = _ICON_CLASS[f["severity"]]
        out.append(
            f"<tr><td>{html.escape(f['rule_id'])}</td><td>{html.escape(f['class'])}</td>"
            f"<td><span class='badge {cls}'>{f['severity']}</span></td>"
            f"<td>{html.escape(str(f['metric']))}</td></tr>"
        )
    return "".join(out)


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
<body>
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
<table>
<thead><tr><th>rule</th><th>class</th><th>severity</th><th>metric</th></tr></thead>
<tbody>{_render_gaps_table(gaps_report['findings'])}</tbody>
</table>

<script type="application/json" id="insight-dash-data">{json_script(payload)}</script>
<footer>Self-contained: no network fetch, no external script/style/font reference. Data is inlined above.</footer>
</body>
</html>"""

    summary = {"ever_ingested": ever_ingested, "has_data": has_data,
               "metric_count": len(metric_rows),
               "metrics_with_data": sum(1 for m in metric_rows if m["has_data"]),
               "gaps_verdict": verdict["overall"]}
    return html_text, summary
