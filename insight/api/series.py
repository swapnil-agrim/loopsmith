# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Chart-shaped payloads: the row-level series behind a metric, not its scalar.

WHY THIS MODULE EXISTS SEPARATELY FROM metrics.py. `collect_metrics()` answers "what is the
reading?" -- one value plus its coverage. A chart needs the distribution behind that reading, which
is a different shape (N rows, not 1) and would not fit the Metric union without deforming it. So
this is an ADDITIVE second payload on its own CLI action rather than a change to `web delivery`'s
response shape, which is contract-tested as a bare JSON array (insight/tests/test_cli_web_delivery
.py) and has a TypeScript consumer generated from it.

THE ABSENCE RULE APPLIES HERE TOO, AND IS EASIER TO GET WRONG. A chart's natural "nothing" value is
an empty array, and an empty array drawn on an axis looks exactly like a real measurement of zero --
a flat line, an empty histogram, a chart that says "we looked and there was nothing" when the truth
is "we never looked". Every series below is therefore a discriminated shape: either
`{"state": "measured", ...}` with real rows, or `{"state": "absent", "reason": ...}` with NO data
key at all. A caller cannot render an absent series as an empty one by accident, because there is
nothing to render.

That distinction is not hypothetical. Before this module existed, the delivery panel hardcoded
`<StripChart spread={[]} />` and rendered "NO SENSOR - cycle time not measured" while metric_2 held
50 of 50 non-null cycle times. The panel was claiming an absence that was not real -- the same
failure as reporting a value that is not real, pointed the other way.
"""

_MISSING_VIEW = "metric view %s does not exist in this store -- run `insight ingest`"
_NO_ROWS = "metric view %s exists but returned no rows"
_ALL_NULL = "metric view %s returned rows, but every %s value was NULL"


def _absent(reason):
    return {"state": "absent", "reason": reason}


def _view_exists(conn, view):
    """True when `view` can actually be selected from. Checked by querying rather than by reading
    a catalog table, because a view can exist as an object and still fail to resolve (a dropped
    underlying table), and for our purposes that is indistinguishable from missing."""
    try:
        conn.execute("SELECT 1 FROM %s LIMIT 1" % view).fetchall()
        return True
    except Exception:
        return False


def _numeric_column(conn, view, column):
    """Every non-NULL value of `column`, ascending. Returns None (never []) when the view is
    missing, empty, or entirely NULL -- the three shapes of absence, kept distinct in the reason
    text so an operator can tell "never ingested" from "ingested, nothing to measure"."""
    if not _view_exists(conn, view):
        return None, _MISSING_VIEW % view
    total = conn.execute("SELECT count(*) FROM %s" % view).fetchone()[0]
    if not total:
        return None, _NO_ROWS % view
    rows = conn.execute(
        "SELECT %s FROM %s WHERE %s IS NOT NULL ORDER BY 1" % (column, view, column)
    ).fetchall()
    if not rows:
        return None, _ALL_NULL % (view, column)
    return [r[0] for r in rows], None


def _quantile(sorted_values, q):
    """Nearest-rank quantile over an already-sorted list. Deliberately not interpolated: these are
    observed durations, and reporting a p50 that no goal actually took invents a data point in a
    product whose entire claim is that it does not."""
    if not sorted_values:
        return None
    idx = int(round(q * (len(sorted_values) - 1)))
    return sorted_values[max(0, min(idx, len(sorted_values) - 1))]


def _distribution(conn, view, column, unit):
    values, reason = _numeric_column(conn, view, column)
    if values is None:
        return _absent(reason)
    numeric = [float(v) for v in values]
    # `total` is the population the distribution is drawn FROM, so a reader can see that 50
    # measured values came out of 52 goals -- coverage, same contract as a scalar metric's.
    total = conn.execute("SELECT count(*) FROM %s" % view).fetchone()[0]
    return {
        "state": "measured",
        "unit": unit,
        "values": numeric,
        "p50": _quantile(numeric, 0.50),
        "p85": _quantile(numeric, 0.85),
        "min": numeric[0],
        "max": numeric[-1],
        "measured": len(numeric),
        "total": total,
    }


def _weekly_throughput(conn):
    """metric_1 is (week, done_count) -- a genuine time series, but on this repo's own store only
    two weeks deep. Returned as-is with its own length: a two-point series is honest and worth
    drawing, and the caller is told how many points it got rather than being handed something that
    looks like a trend."""
    view = "metric_1"
    if not _view_exists(conn, view):
        return _absent(_MISSING_VIEW % view)
    rows = conn.execute(
        "SELECT week, done_count FROM %s WHERE week IS NOT NULL AND done_count IS NOT NULL "
        "ORDER BY week" % view
    ).fetchall()
    if not rows:
        return _absent(_NO_ROWS % view)
    return {
        "state": "measured",
        "points": [{"week": str(w), "count": int(c)} for w, c in rows],
    }


def collect_series(conn):
    """The whole chart payload. A None connection (no store) degrades every series to an honest
    absence rather than raising -- same posture as collect_metrics(None), and the reason a missing
    store is a successful response for aggregate delivery data."""
    if conn is None:
        reason = "no store: run `insight ingest` to populate one"
        return {
            "cycleTime": _absent(reason),
            "interventions": _absent(reason),
            "weeklyThroughput": _absent(reason),
        }
    return {
        # metric_2 (id 2, Cycle time): one row per goal, so cycle_time_seconds IS the distribution
        # behind the scalar p50 the readout shows.
        "cycleTime": _distribution(conn, "metric_2", "cycle_time_seconds", "seconds"),
        # metric_13 (id 13, Interventions per goal): same shape, count rather than duration.
        "interventions": _distribution(conn, "metric_13", "intervention_count", "count"),
        "weeklyThroughput": _weekly_throughput(conn),
    }
