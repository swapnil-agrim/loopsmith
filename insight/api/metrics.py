# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric resolution (issue #300 [E16.S2]): turns one catalog id, plus a store connection, into
one Pydantic `Metric` -- never a raw number, never an exception.

Ports `insight.dash.panel`'s already-proven absence conventions (`_metric_state`, `_scalar`) up
one layer onto the `Metric` union rather than inventing new ones: a view that is missing, empty,
or raises all collapse to the identical `absent_no_data` state, the same "degrade, never crash"
posture that module's own docstring states as deliberate. See that module for the fuller
rationale (its docstring's DARK/UNBUILT distinction is this file's absent_no_data/absent_unbuilt).

Scope, per .sdlc/plans/300.md Decision (b): of the 42 catalog ids, only id 12 (autonomy rate)
has a registered value/coverage extractor -- the single concrete proof criterion 1 requires. The
other 41 resolve to `absent_unbuilt`, honestly, rather than inventing value semantics for a
metric whose SQL shape was never verified against this contract. See that plan's Risks section
for what this means for the API's own "N live" count versus the dash panel's.
"""
import pathlib

from insight.api.models import (
    AbsentNoDataMetric,
    AbsentUnbuiltMetric,
    Coverage,
    MeasuredMetric,
)
from insight.metrics.catalog import CATALOG
from insight.metrics.header import HeaderError, parse_header

# CWD-relative, matching insight.dash.panel's own documented convention (and its own pre-existing
# fragility -- see .sdlc/plans/300.md Risks: starting the API server from a directory other than
# the repo root breaks metric resolution silently into "everything absent". Not introduced here.
DEFAULT_METRICS_DIR = "insight/metrics"


def _numerator_denominator_rate(row):
    """The (numerator, denominator, rate) extractor for metric_12 (and, identically, metric_14
    if a future story registers it -- see Decision (b)) -- ported from panel.py's own indexing
    convention (`aut[0][2], aut[0][0], aut[0][1]`). Returns None on a NULL rate: a row can exist
    while carrying no value yet, the third absence shape alongside "no view" and "zero rows"
    (test_dash_panel_absence.py's own `test_a_row_with_a_null_value_is_absence_not_a_crash`).

    A NULL numerator or denominator is the SAME absence, and returning None for it is not
    fussiness: spec 3 says a `measured` metric carries coverage, so a rate with no counts behind
    it cannot be measured without fabricating the coverage -- exactly the ABSENT!=PASS failure
    this story exists to prevent. Ingest filling in those counts is what fixes it, which is what
    makes this absent_no_data rather than absent_unbuilt."""
    numerator, denominator, rate = row[0], row[1], row[2]
    if rate is None or numerator is None or denominator is None:
        return None
    return rate, numerator, denominator


# Which catalog ids know how to turn their view's row into (value, numerator, denominator).
# Deliberately NOT populated for every built .sql file (33 more exist) -- see this module's
# docstring and .sdlc/plans/300.md Decision (b) for why that is a scope-down, not an oversight.
VALUE_EXTRACTORS = {
    12: _numerator_denominator_rate,
}


def _fetch_row(conn, sql):
    """One row, or None on ANY failure -- a missing store (`conn is None`), a missing view, or a
    raised exception all collapse to the same absence signal, never a crash. Ported from
    panel.py's `_scalar`/`_rows`, generalised to a full row rather than a scalar."""
    if conn is None:
        return None
    try:
        return conn.execute(sql).fetchone()
    except Exception:
        return None


def _reliability_class(mid, metrics_dir):
    """Reads the real header via insight.metrics.header.parse_header when the .sql exists;
    defaults to 1 when there is none, or its header fails to parse. This is classification
    metadata, not a measurement, so a wrong default doesn't trip the ABSENT!=PASS doctrine the
    way a fabricated `value` would -- but it IS a guess, flagged in .sdlc/plans/300.md Risks."""
    path = metrics_dir / f"{mid}.sql"
    if not path.exists():
        return 1
    try:
        header = parse_header(path.read_text(encoding="utf-8"), source=str(path))
    except (HeaderError, OSError, UnicodeDecodeError):
        # OSError/UnicodeDecodeError cover the read itself, not just the parse: `path.exists()`
        # above is a check-then-use race, and an unreadable or non-UTF-8 .sql must degrade to the
        # same default as an unparseable one. Anything that escapes here 500s all 42 metrics.
        return 1
    return header["reliability_class"]


def resolve_metric(conn, mid, metrics_dir=None):
    """One catalog id -> one `Metric`. Never raises: a missing store, a missing view, a view
    with no rows, or a row whose value is NULL are all absence, not an exception."""
    metrics_dir = pathlib.Path(metrics_dir or DEFAULT_METRICS_DIR)
    label = CATALOG[mid]
    reliability_class = _reliability_class(mid, metrics_dir)
    sql_path = metrics_dir / f"{mid}.sql"

    if not sql_path.exists():
        return AbsentUnbuiltMetric(
            id=mid, label=label, reliabilityClass=reliability_class,
            state="absent_unbuilt",
            reason=f"no {sql_path.name} exists yet -- only a code change can build this metric",
        )

    extractor = VALUE_EXTRACTORS.get(mid)
    if extractor is None:
        # The .sql exists and may well have real rows -- this is deliberately NOT
        # absent_no_data, because no amount of ingest will ever populate a `value` for it; only
        # registering an extractor will. See Decision (b): this is the "built but unmapped" case.
        return AbsentUnbuiltMetric(
            id=mid, label=label, reliabilityClass=reliability_class,
            state="absent_unbuilt",
            reason="no value/coverage extractor registered yet for this metric",
        )

    row = _fetch_row(conn, f"SELECT * FROM metric_{mid} LIMIT 1")
    try:
        extracted = extractor(row) if row is not None else None
    except Exception:
        # A row the extractor cannot read at all -- the view exists but has the wrong column
        # count or order, so indexing it raises. Deliberately absent_UNBUILT, not absent_no_data:
        # per spec 3.1 that split is "time/ingest fixes it" vs "only a code change fixes it", and
        # no amount of ingest will ever reshape a 2-column view into a 3-column one. Someone must
        # fix the .sql. Un-guarded, this took the WHOLE 42-metric response down with a 500 -- one
        # bad metric must never erase the other 41, which is the same "degrade, never crash"
        # posture _fetch_row already holds one line above.
        return AbsentUnbuiltMetric(
            id=mid, label=label, reliabilityClass=reliability_class,
            state="absent_unbuilt",
            reason=f"metric_{mid}'s rows do not match the shape its extractor expects",
        )
    if extracted is None:
        return AbsentNoDataMetric(
            id=mid, label=label, reliabilityClass=reliability_class,
            state="absent_no_data",
            reason=f"metric_{mid} has no value yet",
        )

    value, numerator, denominator = extracted
    return MeasuredMetric(
        id=mid, label=label, reliabilityClass=reliability_class,
        state="measured", value=value,
        coverage=Coverage(numerator=numerator, denominator=denominator),
    )


def collect_metrics(conn, metrics_dir=None):
    """Every catalog entry, resolved -- what GET /metrics serves, sorted by id."""
    return [resolve_metric(conn, mid, metrics_dir=metrics_dir) for mid in sorted(CATALOG)]
