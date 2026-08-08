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
from insight.api import health as _health
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


def _review_cycle_cap_share(row):
    """metric_16: (project_id, cap, looped_goal_count, goals_at_cap_count, mass_at_cap_share, ...).
    How many looped goals hit the review-cycle cap. A one-row view -- the cap and the counts are
    project-level, not per goal -- so this stays on the single-row path."""
    looped, at_cap, share = row[2], row[3], row[4]
    if share is None or at_cap is None or looped is None:
        return None
    return share, at_cap, looped


# --------------------------------------------------------------------------- aggregates
# Extractors that need EVERY row, not the first one.
#
# WHY A SECOND REGISTRY. `VALUE_EXTRACTORS` receives one row, which is right for a view whose
# numbers are project-level constants. It is useless for a view with one row PER GOAL or PER GATE:
# metric_23 carries a catch count for each of four gates, and the honest project figure is the sum
# of the numerators over the sum of the denominators -- something no single row contains. Before
# this, those metrics were unwirable and read as "no extractor registered" forever, which was
# accurate and unhelpful.
#
# Each function takes the full row list and returns (value, numerator, denominator) or None. The
# same absence rule applies: if the rows cannot yield a defensible denominator, return None rather
# than inventing one.
def _gate_catch_rate(rows):
    """metric_23, one row per gate: (project_id, gate, late_catch, gate_event_count, catch_count,
    ...). The project-wide catch rate is total catches over total gate events -- NOT the mean of
    the per-gate rates, which would weight a gate that fired once the same as one that fired 75
    times."""
    caught = sum(r[4] for r in rows if r[4] is not None)
    events = sum(r[3] for r in rows if r[3] is not None)
    if not events:
        return None
    return caught / events, caught, events


def _lease_contention_rate(rows):
    """metric_35, one row per goal: (project_id, goal_id, contended, current_actor_id, claimed_ts).
    The share of goals whose lease was contended. A population of all-False is a REAL measurement
    of zero contention, not an absence -- which is why this counts rows rather than testing for
    truthiness anywhere."""
    known = [r for r in rows if r[2] is not None]
    if not known:
        return None
    contended = sum(1 for r in known if r[2])
    return contended / len(known), contended, len(known)


def _interventions_p50(rows):
    """metric_13, one row per goal: (goal_id, intervention_count, p50_interventions,
    p85_interventions). p50 is broadcast across every row; the denominator is the goal population
    the percentile was computed over, which is the row count itself."""
    scored = [r for r in rows if r[1] is not None]
    if not scored:
        return None
    p50 = scored[0][2]
    if p50 is None:
        return None
    return p50, len(scored), len(rows)


AGGREGATE_EXTRACTORS = {
    23: _gate_catch_rate,
    35: _lease_contention_rate,
    13: _interventions_p50,
}


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
    16: _review_cycle_cap_share,
    20: _rework_ratio,
}

# What each wired metric's value is counted in, so a client can render it for humans instead of
# printing raw seconds. Kept immediately beside VALUE_EXTRACTORS, and pinned to it by the test
# below's sibling in test_api_metrics_extractors.py, because the two are the same decision made
# twice: whoever teaches a metric to produce a value is the only person who knows what that value
# means. A registry declared anywhere else drifts the first time an extractor changes.
#
# "ratio" is a 0..1 fraction (autonomy rate, park rate, change failure rate, rework ratio);
# "seconds" is a duration; "count" is a bare tally. A metric absent from this map serves
# `unit: null` -- honest "nobody has said", never a guess.
VALUE_UNITS = {
    2: "seconds",   # cycle time p50
    3: "seconds",   # lead time for change p50
    5: "ratio",     # change failure rate
    12: "ratio",    # autonomy rate
    14: "ratio",    # park rate
    13: "count",    # interventions per goal, p50
    16: "ratio",    # share of looped goals at the review-cycle cap
    20: "ratio",    # rework ratio
    23: "ratio",    # gate catch rate
    35: "ratio",    # lease contention rate
}


def _fetch_rows(conn, sql):
    """Every row, or None on ANY failure -- same collapse-to-absence posture as `_fetch_row`
    below, for the aggregate extractors that need the whole result set."""
    if conn is None:
        return None
    try:
        return conn.execute(sql).fetchall()
    except Exception:
        return None


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


def _header_fields(mid, metrics_dir):
    """The metric's own header metadata, or empty when there is no `.sql` to read.

    Reads the file a SECOND time (`_reliability_class` above already read it). That is a known,
    accepted cost, not an oversight: 42 small files, and `resolve_metric` is already file-bound.
    Merging the two reads would mean threading a parsed header through a function whose contract
    is "return an int, never raise", which is a worse trade than one extra stat+read.

    Never raises, for the same reason `_reliability_class` never raises: this is context, not a
    measurement. A malformed header degrades to "no metadata", exactly as a missing file does --
    anything that escapes here would 500 all 42 metrics over a stray comment line."""
    path = pathlib.Path(metrics_dir) / f"{mid}.sql"
    if not path.exists():
        return {}
    try:
        parsed = parse_header(path.read_text(encoding="utf-8"), source=str(path))
    except (HeaderError, OSError, UnicodeDecodeError):
        return {}
    extra = parsed.get("extra") or {}
    return {
        "question": parsed.get("question") or None,
        "guardrail": parsed.get("guardrail") or None,
        # The header convention is the literal string "true" (issue #110). Anything else --
        # including absence, "yes", or "1" -- is False, because a proxy claim should have to be
        # spelled the one documented way rather than guessed at.
        "proxy": str(extra.get("proxy", "")).strip().lower() == "true",
        "data_status": (extra.get("data_status") or "").strip() or None,
    }


def _gap_hint(conn, mid, sql_exists):
    """One sentence naming the next real step for an unbuilt metric.

    Three cases, each distinguished by evidence rather than guessed:
      * no `.sql`           -> the metric has not been written at all
      * `.sql` + rows       -> the data is already there; only an extractor is missing
      * `.sql` + empty view -> code cannot help; this one is waiting on data

    The row count is claimed ONLY when it can actually be counted. With no store, or a view that
    will not read, the hint falls back to the weaker but still-true statement -- asserting "0 rows
    are waiting" when we simply could not look is the same class of fabrication as reporting a
    value nobody measured."""
    if not sql_exists:
        return "No SQL written for this metric yet."
    if conn is None:
        return "No extractor registered yet for this metric."
    try:
        rows = conn.execute("SELECT count(*) FROM metric_%d" % mid).fetchone()[0]
    except Exception:
        return "No extractor registered yet for this metric."
    if rows:
        return (
            "No extractor registered. %d row%s already waiting in metric_%d -- wiring one "
            "would surface it." % (rows, "s are" if rows != 1 else " is", mid)
        )
    return "SQL exists but the view is empty -- this one needs data, not code."


# Which metrics can be compared against their OWN earlier window, and the SQL that splits the
# population in two at its median completion time.
#
# Only metrics whose underlying facts carry a time dimension can have a baseline at all -- most
# views are a single project-level row with no history in them, and inventing a "previous value"
# for those would be exactly the fabrication this module refuses everywhere else. Cycle time
# qualifies because fact_goal carries claimed_ts and terminal_ts per goal.
_BASELINE_SQL = {
    2: """
        WITH d AS (
            SELECT terminal_ts,
                   date_diff('second', claimed_ts, terminal_ts) AS secs
            FROM fact_goal
            WHERE terminal_ts IS NOT NULL AND claimed_ts IS NOT NULL
              AND terminal_ts >= claimed_ts
        ), m AS (SELECT median(epoch(terminal_ts)) AS mid FROM d)
        SELECT
            (SELECT median(secs) FROM d, m WHERE epoch(d.terminal_ts) <  m.mid),
            (SELECT count(*)     FROM d, m WHERE epoch(d.terminal_ts) <  m.mid),
            (SELECT count(*)     FROM d, m WHERE epoch(d.terminal_ts) >= m.mid)
    """,
    # NO BASELINE FOR METRIC 14 (park rate), deliberately. A first attempt split fact_goal on
    # `outcome = 'parked'` and always returned 0 -- because that outcome does not exist: the real
    # vocabulary is {done, failed, NULL}, and 14.sql derives its parked population differently.
    # A baseline computed over a DIFFERENT population than the metric it judges is worse than no
    # baseline: it would have rendered a confident verdict against a number that measures
    # something else. Restore this only alongside a query that matches 14.sql's own definition.
    # Gate catch rate: split the GATE EVENTS themselves, not goals -- the denominator this metric
    # is computed over is gate firings, and splitting a different population would compare the
    # current rate against a baseline drawn from something else.
    23: """
        WITH d AS (
            SELECT ts, CASE WHEN verdict IN ('block', 'warn') THEN 1.0 ELSE 0.0 END AS caught
            FROM fact_event
            WHERE kind = 'gate' AND verdict IS NOT NULL AND ts IS NOT NULL
        ), m AS (SELECT median(epoch(ts)) AS mid FROM d)
        SELECT
            (SELECT avg(caught) FROM d, m WHERE epoch(d.ts) <  m.mid),
            (SELECT count(*)    FROM d, m WHERE epoch(d.ts) <  m.mid),
            (SELECT count(*)    FROM d, m WHERE epoch(d.ts) >= m.mid)
    """,
}


def _baseline(conn, mid):
    """The metric's earlier window, or None when it has no derivable history.

    Splitting at the MEDIAN completion time rather than a fixed calendar window is deliberate: a
    fixed window ("last 7 days") produces an empty half the moment the loop pauses for a week, and
    an empty half is not a baseline -- it is a comparison against nothing that would still render a
    confident verdict. A median split always yields two non-empty halves when there is any data at
    all, and health.py's own per-window minimum rejects the case where those halves are too small
    to mean anything."""
    sql = _BASELINE_SQL.get(mid)
    if sql is None or conn is None:
        return None
    row = _fetch_row(conn, sql)
    if not row or row[0] is None:
        return None
    return {"earlier": float(row[0]), "earlier_n": int(row[1]), "recent_n": int(row[2])}


def resolve_metric(conn, mid, metrics_dir=None):
    """One catalog id -> one `Metric`. Never raises: a missing store, a missing view, a view
    with no rows, or a row whose value is NULL are all absence, not an exception."""
    metrics_dir = pathlib.Path(metrics_dir or DEFAULT_METRICS_DIR)
    label = CATALOG[mid]
    reliability_class = _reliability_class(mid, metrics_dir)
    sql_path = metrics_dir / f"{mid}.sql"
    header = _header_fields(mid, metrics_dir)

    if not sql_path.exists():
        return AbsentUnbuiltMetric(
            id=mid, label=label, reliabilityClass=reliability_class, **header,
            state="absent_unbuilt",
            gapHint=_gap_hint(conn, mid, sql_path.exists()),
            reason=f"no {sql_path.name} exists yet -- only a code change can build this metric",
        )

    extractor = VALUE_EXTRACTORS.get(mid)
    aggregate = AGGREGATE_EXTRACTORS.get(mid)
    if extractor is None and aggregate is None:
        # The .sql exists and may well have real rows -- this is deliberately NOT
        # absent_no_data, because no amount of ingest will ever populate a `value` for it; only
        # registering an extractor will. See Decision (b): this is the "built but unmapped" case.
        return AbsentUnbuiltMetric(
            id=mid, label=label, reliabilityClass=reliability_class, **header,
            state="absent_unbuilt",
            gapHint=_gap_hint(conn, mid, sql_path.exists()),
            reason="no value/coverage extractor registered yet for this metric",
        )

    # An aggregate reads every row; a single-value extractor reads the first. A metric is never in
    # both registries -- the test below pins that, because two extractors for one id would make
    # which number wins depend on dict ordering.
    if aggregate is not None:
        rows = _fetch_rows(conn, f"SELECT * FROM metric_{mid}")
        row = rows if rows else None
    else:
        row = _fetch_row(conn, f"SELECT * FROM metric_{mid} LIMIT 1")
    try:
        extracted = (aggregate(row) if aggregate is not None else extractor(row)) if row is not None else None
    except Exception:
        # A row the extractor cannot read at all -- the view exists but has the wrong column
        # count or order, so indexing it raises. Deliberately absent_UNBUILT, not absent_no_data:
        # per spec 3.1 that split is "time/ingest fixes it" vs "only a code change fixes it", and
        # no amount of ingest will ever reshape a 2-column view into a 3-column one. Someone must
        # fix the .sql. Un-guarded, this took the WHOLE 42-metric response down with a 500 -- one
        # bad metric must never erase the other 41, which is the same "degrade, never crash"
        # posture _fetch_row already holds one line above.
        return AbsentUnbuiltMetric(
            id=mid, label=label, reliabilityClass=reliability_class, **header,
            state="absent_unbuilt",
            gapHint=_gap_hint(conn, mid, sql_path.exists()),
            reason=f"metric_{mid}'s rows do not match the shape its extractor expects",
        )
    if extracted is None:
        return AbsentNoDataMetric(
            id=mid, label=label, reliabilityClass=reliability_class, **header,
            state="absent_no_data",
            reason=f"metric_{mid} has no value yet",
        )

    value, numerator, denominator = extracted
    return MeasuredMetric(
        id=mid, label=label, reliabilityClass=reliability_class, **header,
        state="measured", value=value,
        coverage=Coverage(numerator=numerator, denominator=denominator),
        # .get, not [], on purpose: a metric can be wired for a value before anyone has declared
        # what that value is counted in, and `unit: null` is the honest way to say so. Raising
        # here would make an undeclared unit break a reading that is otherwise perfectly good.
        unit=VALUE_UNITS.get(mid),
        # None whenever any gate fails -- see insight.api.health. A dark metric never reaches a
        # verdict, which is why the header's data_status is passed in rather than checked here.
        health=_health.evaluate(
            mid, value, numerator, denominator,
            data_status=header.get("data_status"),
            baseline=_baseline(conn, mid),
            unit=VALUE_UNITS.get(mid),
        ),
    )


def collect_metrics(conn, metrics_dir=None):
    """Every catalog entry, resolved -- what GET /metrics serves, sorted by id."""
    return [resolve_metric(conn, mid, metrics_dir=metrics_dir) for mid in sorted(CATALOG)]
