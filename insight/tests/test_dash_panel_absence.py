# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The panel's load-bearing invariant: a readout with no measurement behind it must never render
a numeral -- above all, never `0`.

This file exists because the panel shipped with exactly that bug. On a populated store all four
primary readouts had data, so the absence branch rendered zero times and looked fine; against an
empty store `Goals landed` rendered a bare `0`. "0 goals landed" reads to a manager as *we shipped
nothing this week*, when the truth was *nothing has been ingested yet* -- opposite messages, and
precisely the ABSENT-as-PASS confusion this product exists to prevent. The root cause was in
`collect()`, not in the readout: `_scalar(..., 0)` defaulted a failed or empty count to zero,
destroying the difference between *counted zero* and *could not count*.

The cold-start store is therefore the fixture that matters. A test that only exercises the
populated path re-creates the exact blind spot that let the bug ship.
"""
import re

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.dash.panel import CATALOG, _readout, collect, render_panel  # noqa: E402

_FACTS = (
    "fact_goal(project_id VARCHAR, outcome VARCHAR)",
    "fact_event(ts TIMESTAMP, kind VARCHAR)",
    "fact_merge_lead_time(lead_time_seconds BIGINT)",
    "fact_pr_check(conclusion VARCHAR)",
    "fact_pr_review(pr INT)",
)


def _readout_values(html_text):
    """Every numeral-slot the page rendered, in order."""
    return re.findall(r'<div class="val">(.*?)</div>', html_text)


@pytest.fixture
def empty_store(tmp_path):
    """Schema present, zero rows, no metric_* views -- a freshly-initialised store before the
    first ingest. This is the state every new user sees first."""
    conn = duckdb.connect(str(tmp_path / "empty.duckdb"))
    for ddl in _FACTS:
        conn.execute(f"CREATE TABLE {ddl}")
    yield conn
    conn.close()


@pytest.fixture
def bare_store(tmp_path):
    """Not even the fact tables. Degenerate, but reachable: point `insight dash` at the wrong
    file and this is what it gets. It must render, not raise."""
    conn = duckdb.connect(str(tmp_path / "bare.duckdb"))
    yield conn
    conn.close()


def test_cold_start_renders_no_numerals(empty_store):
    """THE regression test. Every readout must refuse; a single numeral here is the shipped bug."""
    html_text = render_panel(empty_store, metrics_dir="insight/metrics")
    values = _readout_values(html_text)

    assert values, "no readouts rendered at all -- the selector or markup changed"
    assert all(v == "NO SENSOR" for v in values), (
        f"a readout rendered a numeral with nothing to measure: {values}. "
        "A count is only a measurement when there was something to count.")
    assert html_text.count('class="ro absent"') == len(values)


def test_cold_start_never_renders_a_bare_zero(empty_store):
    """Stated separately and narrowly, because `0` is the *specific* value that shipped and the
    only one that silently reads as a healthy measurement."""
    html_text = render_panel(empty_store, metrics_dir="insight/metrics")
    assert "0" not in _readout_values(html_text)


def test_collect_distinguishes_counted_zero_from_uncountable(empty_store, bare_store):
    """`collect()` reports what SQL actually knows; the *render* decides what it means.

    The distinction is easy to get backwards, so it is pinned explicitly. `count(*)` over a table
    that exists and holds no rows is a genuine measurement of zero -- the table WAS counted -- so
    `0` is the honest answer and forcing it to None would itself be a lie. Absence arises one level
    up, in two different places: the table is missing entirely (uncountable, None), or the count is
    real but zero, which leaves the landed-goals readout with no denominator. Only the second case
    is the renderer's judgement call, which is why `test_cold_start_renders_no_numerals` and not
    this test is what guards the shipped bug."""
    counted = collect(empty_store, metrics_dir="insight/metrics")
    assert counted["total_goals"] == 0, "an existing empty table WAS counted; 0 is the true answer"

    uncountable = collect(bare_store, metrics_dir="insight/metrics")
    assert uncountable["total_goals"] is None, "a missing table cannot be counted; None, not 0"

    # Metrics behind absent views stay None in both stores -- there is no view to return a zero.
    for d in (counted, uncountable):
        assert d["p50"] is None and d["p85"] is None
        assert d["autonomy"] is None and d["park"] is None


def test_a_row_with_a_null_value_is_absence_not_a_crash(tmp_path):
    """The third shape absence takes, and the one that actually crashed the CLI.

    `metric_12` on a never-ingested store returns ONE ROW whose rate is NULL -- not "no view", not
    "no rows". `if d["autonomy"]:` was true (a tuple of Nones is truthy), so a None reached
    `rate * 100` and `insight dash` died with a TypeError across 15 tests. The empty-store and
    bare-store fixtures both miss this: neither creates a view that returns a populated-looking
    row. A store shape that only appears mid-onboarding needs its own fixture or it is untested."""
    conn = duckdb.connect(str(tmp_path / "null_rate.duckdb"))
    for ddl in _FACTS:
        conn.execute(f"CREATE TABLE {ddl}")
    # One row, NULL rate -- exactly what the real metric SQL emits before any goal is ingested.
    conn.execute("CREATE VIEW metric_12 AS SELECT NULL::BIGINT n, NULL::BIGINT d, NULL::DOUBLE r")
    conn.execute("CREATE VIEW metric_14 AS SELECT NULL::BIGINT n, NULL::BIGINT d, NULL::DOUBLE r")

    d = collect(conn, metrics_dir="insight/metrics")
    assert d["autonomy"] is None, "a NULL rate must collapse to absence, not a tuple of Nones"
    assert d["park"] is None

    html_text = render_panel(conn, metrics_dir="insight/metrics")  # must not raise
    assert "NO SENSOR" in html_text
    conn.close()


def test_bare_store_renders_without_raising(bare_store):
    """A panel that crashes on a missing sensor cannot report on its own instrumentation."""
    html_text = render_panel(bare_store, metrics_dir="insight/metrics")
    assert all(v == "NO SENSOR" for v in _readout_values(html_text))
    assert f"of {len(CATALOG)} metrics dark" in html_text


def test_no_template_placeholder_leaks(empty_store):
    """`None` reaching the page as text means an f-slot escaped its guard. Matched in numeric
    contexts only -- the footer prose legitimately contains the word "None"."""
    html_text = render_panel(empty_store, metrics_dir="insight/metrics")
    leaks = re.findall(r"(?:>None<|None/|/None|None\s*(?:merges|goals|rows|events|%))", html_text)
    assert not leaks, f"template placeholder leaked into the page: {leaks}"


def test_absent_readout_carries_no_numeral_by_construction(empty_store):
    """Mutation-proof that the guard is load-bearing: `_readout` given both a value and an
    absent_reason must still refuse. Absence outranks a stale value -- if this ever inverts,
    every test above passes while the page lies."""
    absent = _readout("Goals landed", 999, coverage="999/999", absent_reason="nothing ingested")
    assert "999" not in absent
    assert "NO SENSOR" in absent and 'class="ro absent"' in absent


def test_populated_store_still_renders_real_numbers(empty_store):
    """The counterweight: a guard that refuses *everything* would pass every test above and be
    useless. Plant one real goal and assert a numeral comes back."""
    empty_store.execute("INSERT INTO fact_goal VALUES ('p', 'done'), ('p', NULL)")
    html_text = render_panel(empty_store, metrics_dir="insight/metrics")
    values = _readout_values(html_text)
    assert values[0] == "1", f"expected the landed count to render, got {values}"
    assert "1/2 goals reached terminal" in html_text
