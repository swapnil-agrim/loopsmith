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
def _cycle_time_p50(row):
    """metric_2: (goal_id, cycle_time_seconds, p50_seconds, p85_seconds,
    excluded_negative_duration_count, total_count). Value is the project's own p50; coverage is
    the goals that actually yielded a duration -- total MINUS the ones excluded for a negative
    duration. Reporting `total` as the numerator would claim coverage the metric does not have."""
    p50, excluded, total = row[2], row[4], row[5]
    if p50 is None or total is None:
        return None
    measured = total - (excluded or 0)
    return p50, measured, total


def _lead_time_p50(row):
    """metric_3: (p50_seconds, p85_seconds, measured_count, total_count). The coverage story this
    metric exists to tell: on this repo only a handful of merges carry a duration at all, so a p50
    over the non-null subset without its denominator would be a lie of omission."""
    p50, measured, total = row[0], row[2], row[3]
    if p50 is None or measured is None or total is None:
        return None
    return p50, measured, total


def _change_failure_rate(row):
    """metric_5: (collected_ts, window_commit_count, repeated_revert_or_fixup_count,
    change_failure_rate)."""
    commits, reverts, rate = row[1], row[2], row[3]
    if rate is None or reverts is None or commits is None:
        return None
    return rate, reverts, commits


def _rework_ratio(row):
    """metric_20: (collected_ts, window_commit_count, files_touched_more_than_once,
    total_files_touched, rework_ratio). Coverage is FILES, not commits -- the ratio's own
    denominator, not the window's."""
    repeated, total_files, ratio = row[2], row[3], row[4]
    if ratio is None or repeated is None or total_files is None:
        return None
    return ratio, repeated, total_files


# Which catalog ids know how to turn their view's row into (value, numerator, denominator).
#
# ONLY metrics whose OWN VIEW carries both a value and the counts behind it are registered. Spec
# section 3 says a `measured` metric carries coverage, so a metric whose view has no denominator
# cannot become `measured` without INVENTING one -- the exact ABSENT != PASS failure this product
# exists to prevent. Deliberately still absent, and why:
#
#   id 1  Throughput          (week, done_count)      -- a count, no denominator in view
#   id 7  Flow load (WIP)     (week_start, wip_count) -- same
#   id 9  Flow distribution   -- a share per (source, lane); no single scalar
#   id 11 Throughput forecast -- a p10..p90 RANGE, not a point value
#   ids 10, 26, 35, 41, 42    -- table-shaped: one row per goal/flag, not one measurement
#
# Those need a richer contract or their own presentation, not a fabricated denominator.
# insight/tests/test_api_metrics_extractors.py pins that they stay unwired.
#
# EVERY extractor below indexes POSITIONALLY, because `resolve_metric` hands it the tuple from
# `SELECT * FROM metric_N`. Each docstring states the column order it assumes; a view whose shape
# changes makes the extractor raise, which resolve_metric already degrades to absent_unbuilt
# rather than a crash.
VALUE_EXTRACTORS = {
    2: _cycle_time_p50,
    3: _lead_time_p50,
    5: _change_failure_rate,
    12: _numerator_denominator_rate,
    14: _numerator_denominator_rate,   # identical (numerator, denominator, rate) shape to 12
    20: _rework_ratio,
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
