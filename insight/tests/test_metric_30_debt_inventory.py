# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #30, Debt inventory + trend (issue #111). VERIFIED live, both duckdb 1.4.5 and 1.5.5
(byte-identical): five discovery-scan/v1 packs -- t1 (3 candidates, first snapshot, ABSENT
trend), t2 (3 candidates, delta=0, PASS), t3 (adapter-degraded, ABSENT, candidate_count nulled),
t4 (20 candidates, delta=17 vs t2's 3 -- proves LAG(...IGNORE NULLS) correctly skips the
degraded t3 row rather than diffing against its NULL, FAIL), t5 (22 candidates, delta=2 vs t4's
20, WARN). LIVE TODAY, NOT DARK: discovery-scan/v1 already lands in fact_collector_pack via a
real ingest (#111 dossier, re-verified this session)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "30.jsonl"
MULTI_PROJECT_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "30_multi_project.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_30_computes_trend_status_skipping_degraded_snapshots(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["30"]["extra"] == {}
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_30 ORDER BY collected_ts"))
    shapes = [(r["candidate_count"], r["prior_count"], r["status"]) for r in rows]
    assert shapes == [
        (3, None, "ABSENT"),    # first-ever snapshot: no baseline to trend against
        (3, 3, "PASS"),          # flat
        (None, 3, "ABSENT"),     # adapter-degraded: count nulled, never trusted
        (20, 3, "FAIL"),         # jumps from the LAST GOOD snapshot (t2=3), not the degraded t3
        (22, 20, "WARN"),        # small growth off the real t4 baseline
    ]


def test_an_adapter_degraded_pack_is_absent_not_pass_despite_zero_candidates(conn):
    """The issue's own core requirement, for #30: a pack the adapter itself failed to produce
    (degraded_adapter non-empty) must render ABSENT, never PASS -- even though its own
    candidate_count is NULLed to a value that could otherwise be misread as "clean, zero debt"
    (a PASS-shaped absence of evidence, not evidence of a pass)."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT status FROM metric_30 WHERE collected_ts = '2026-07-30'")
    )
    assert rows == [{"status": "ABSENT"}]
    assert rows[0]["status"] != "PASS"


def test_a_second_projects_first_scan_does_not_trend_against_a_different_project(conn):
    """REGRESSION TEST for PR #186's blocking finding: the LAG in 30.sql's trend CTE had no
    PARTITION BY project_id (and neither packs/trend/the final SELECT projected project_id at
    all), so the view was unconditionally global across every project sharing the same store --
    reachable in practice via insight/__main__.py's --repos flag (#106), which ingests multiple
    project_ids into one store over one connection.

    Reproduced live pre-fix: projA's 2026-07-28 snapshot (20 candidates, its own first-ever scan,
    correctly ABSENT) leaked into projB's 2026-07-29 snapshot (1 candidate, ALSO its own
    first-ever scan) as a false prior_count=20 -- rendering PASS ("debt shrank from 20 to 1")
    for a project that has never been scanned before, the exact false-confident-PASS-instead-of
    -ABSENT outcome this metric's own guardrail (ABSENT PATH 2, FIRST-SNAPSHOT) exists to
    prevent, just triggered across a project boundary instead of within one project's own
    history.

    This fixture (30_multi_project.jsonl) plants projA's real history (one snapshot) alongside
    projB's own two-snapshot history in the SAME store/connection -- the exact shape --repos
    produces -- and asserts projB's first scan renders ABSENT with prior_count=NULL despite
    projA's non-NULL 20 being chronologically and lexically prior in an unpartitioned ORDER BY
    collected_ts. projB's SECOND scan then correctly trends against projB's OWN first scan (1),
    never projA's, proving the partition holds on both the ABSENT and the real-trend path."""
    load_fixture_jsonl(conn, MULTI_PROJECT_FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute(
            "SELECT project_id, collected_ts, candidate_count, prior_count, status "
            "FROM metric_30 ORDER BY project_id, collected_ts"
        )
    )
    by_project = {}
    for r in rows:
        by_project.setdefault(r["project_id"], []).append(r)

    assert [r["candidate_count"] for r in by_project["projA"]] == [20]
    assert [r["prior_count"] for r in by_project["projA"]] == [None]
    assert [r["status"] for r in by_project["projA"]] == ["ABSENT"]

    assert [r["candidate_count"] for r in by_project["projB"]] == [1, 3]
    assert [r["prior_count"] for r in by_project["projB"]] == [None, 1]
    assert [r["status"] for r in by_project["projB"]] == ["ABSENT", "WARN"]

    # THE core assertion: projB's first scan must never see projA's 20.
    projb_first = by_project["projB"][0]
    assert projb_first["prior_count"] != 20
    assert projb_first["status"] != "PASS"
