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
