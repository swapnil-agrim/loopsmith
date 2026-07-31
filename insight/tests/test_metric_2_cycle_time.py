# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #2, Cycle time (issue #109). VERIFIED live against the real header/loader/testing
harness this session: 6 done goals (right-skewed: five close in 1-5h, one closes in 100h), one
parked goal and one claimed_ts-IS-NULL goal both correctly excluded.

POST-PR-REVIEW fold-in (round 2): g9 (claimed 1/5, terminal 1/1 -- terminal BEFORE claimed, i.e.
clock skew or a bad write) is added and must be EXCLUDED, not surfaced with a negative
cycle_time_seconds. VERIFIED live: before the `AND terminal_ts >= claimed_ts` guard, g9 appeared
with cycle_time_seconds=-345600, silently corrupting the percentiles. Decision (documented in
2.sql's own guardrail): reject the row outright rather than track it as a degraded/unmeasured
count the way metric_3 does for NULL lead_time_seconds -- a negative duration is never
legitimate (unlike an unmeasured NULL, which is a real, expected, common state for a real
collector), so it is a data-integrity bug elsewhere, not a fact about flow worth counting.

POST-PR-REVIEW fold-in (round 3): that exclusion was silent -- no count anywhere. Added
`excluded_negative_duration_count`, a broadcast constant (same window-function-style pattern as
p50_seconds/p85_seconds) so a systematic clock-skew bug that someday drops half a project's
goals leaves a visible signal instead of clean-looking percentiles with nothing wrong on the
surface.

POST-PR-REVIEW fold-in (round 4): that count itself went silent in the ONE scenario it exists
for -- with 100% of the done population negative-duration, the round-3 view's outer WHERE
dropped every row, so the broadcast count vanished along with the percentiles, making "all data
is corrupt" indistinguishable from "no data has arrived yet" (a genuinely empty table). VERIFIED
live, all three cases, before and after: empty table -- 0 rows both before and after (unchanged,
correct); some-good-some-excluded (existing fixture) -- unchanged; ALL-excluded (two synthetic
rows, both negative-duration, no good rows at all) -- 0 rows BEFORE this fix (indistinguishable
from empty), exactly 1 row (goal_id/percentiles NULL, excluded_negative_duration_count=2) AFTER.
Restructured as a `population` CTE (done-and-both-timestamps-present count, split good/excluded)
LEFT JOINed to the good rows, gated by `population.total_count > 0` -- this is what keeps the
genuinely-empty-table case at exactly 0 rows while giving the all-excluded case its one
surviving placeholder row."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "2.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_2_returns_six_scatter_rows_excluding_parked_and_null_claimed(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["2"]["extra"]["data_status"] == "dark"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_2 ORDER BY goal_id"))
    # g9 (terminal_ts BEFORE claimed_ts -- clock skew) must also be excluded, alongside the
    # pre-existing parked (g7) and null-claimed_ts (g8) exclusions.
    assert [r["goal_id"] for r in rows] == ["g1", "g2", "g3", "g4", "g5", "g6"]
    assert [r["cycle_time_seconds"] for r in rows] == [3600, 7200, 10800, 14400, 18000, 360000]


def test_metric_2_excludes_a_negative_duration_row_instead_of_corrupting_percentiles(conn):
    """The negative-cycle-time fold-in (post-review): g9 has terminal_ts (1/1) BEFORE
    claimed_ts (1/5) -- impossible for a real 'done' goal, a clock-skew/bad-write signal, not a
    legitimate value. Must never appear in the scatter, and the percentiles must be identical
    to what they'd be without it (proving it is excluded from the quantile_cont window too, not
    merely absent from a later SELECT)."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_2"))
    assert "g9" not in [r["goal_id"] for r in rows]
    percentiles = {(r["p50_seconds"], r["p85_seconds"]) for r in rows}
    assert percentiles == {(12600.0, 103500.0)}


def test_metric_2_surfaces_how_many_rows_the_negative_duration_guard_dropped(conn):
    """Round-3 fold-in: the negative-duration exclusion used to be silent -- a systematic
    clock-skew bug that dropped half a project's goals would render clean percentiles with zero
    signal anything was wrong. g9 is the fixture's one negative-duration row, so the broadcast
    excluded_negative_duration_count must read exactly 1 on every remaining row."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT DISTINCT excluded_negative_duration_count FROM metric_2"))
    assert rows == [{"excluded_negative_duration_count": 1}]


def test_metric_2_an_all_excluded_population_still_surfaces_the_count(conn):
    """Round-4 fold-in: a population where EVERY done goal is negative-duration must not vanish
    indistinguishably from an empty table. Two synthetic goals, both terminal_ts < claimed_ts,
    no good rows at all -- expect exactly one placeholder row with the real exclusion count and
    NULL everywhere else, not zero rows."""
    conn.execute(
        "INSERT INTO fact_goal (project_id, goal_id, outcome, claimed_ts, terminal_ts) VALUES "
        "('p1','gbad1','done','2026-01-05T00:00:00','2026-01-01T00:00:00'),"
        "('p1','gbad2','done','2026-01-06T00:00:00','2026-01-01T00:00:00')"
    )
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_2"))
    assert rows == [{
        "goal_id": None, "cycle_time_seconds": None,
        "p50_seconds": None, "p85_seconds": None,
        "excluded_negative_duration_count": 2,
    }]


def test_metric_2_a_genuinely_empty_table_still_returns_zero_rows(conn):
    """The other half of the round-4 fold-in's distinction: an empty fact_goal (nothing
    ingested yet) must stay at exactly zero rows -- not gain a spurious placeholder row of its
    own now that the all-excluded case gets one. This is what actually lets a consumer tell the
    two apart: 0 rows means no data; 1 row with excluded_negative_duration_count > 0 means bad
    data."""
    load_metrics(conn)  # no fixture loaded -- fact_goal is empty
    assert rows_as_dicts(conn.execute("SELECT * FROM metric_2")) == []


def test_metric_2_p85_differs_from_the_mean_on_a_known_right_skew(conn):
    """The issue's own done-when: a fixture with a known skew proves p85 != mean -- not the
    harness's own pre-existing synthetic self-test (#108's test_metrics_testing.py), which is
    explicitly NOT a substitute (see #109 research dossier §7)."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    percentiles = rows_as_dicts(conn.execute("SELECT DISTINCT p50_seconds, p85_seconds FROM metric_2"))
    assert percentiles == [{"p50_seconds": 12600.0, "p85_seconds": 103500.0}]
    mean_row = conn.execute(
        "SELECT avg(date_diff('second', claimed_ts, terminal_ts)) FROM fact_goal "
        "WHERE outcome = 'done' AND claimed_ts IS NOT NULL AND terminal_ts IS NOT NULL "
        "AND terminal_ts >= claimed_ts"  # match metric_2's own population (excludes g9, the
        # negative-duration/clock-skew row added post-review -- see this file's module
        # docstring): the mean must be computed over the SAME rows the view considers, or this
        # assertion would compare the view's p85 against a different population's mean.
    ).fetchone()
    assert mean_row[0] == 69000.0
    assert percentiles[0]["p85_seconds"] != mean_row[0]
    assert percentiles[0]["p50_seconds"] != mean_row[0]
