# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #24, Gate coverage (issue #111). VERIFIED live against the real header/loader/testing
harness this session, on duckdb 1.4.5 AND 1.5.5 (byte-identical): five alignment-collect/v1
packs -- no_git (total failure, both real denominators zero, verified against
skills/sdlc-align/scripts/alignment-collect.sh directly), real/high (100/90), real/mixed (60/40),
no_test_command-only (real denominators, 100/100 -- the exact case a first-draft, array-wide
ABSENT gate got wrong, per an independent review; this repo's own real collector run degrades
with no_test_command on every run today and STILL measures real d1/d5 values, reproduced live in
this plan's Verification method section), no_recognized_source (plan_gate's denominator is
empty, review_gate's is not -- a partially-degraded pack, one gate ABSENT and the other real in
the SAME row)."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "24.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_24_computes_gate_status_per_pack(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["24"]["extra"]["proxy"] == "true"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_24 ORDER BY collected_ts, gate"))
    statuses = [(r["gate"], r["pct"], r["status"]) for r in rows]
    assert statuses == [
        ("plan_gate", 0, "ABSENT"), ("review_gate", 0, "ABSENT"),        # no_git: both denominators empty
        ("plan_gate", 100, "PASS"), ("review_gate", 90, "PASS"),         # real, high
        ("plan_gate", 60, "WARN"), ("review_gate", 40, "FAIL"),          # real, mixed
        ("plan_gate", 100, "PASS"), ("review_gate", 100, "PASS"),        # no_test_command-only: real denominators, real high pct
        ("plan_gate", 0, "ABSENT"), ("review_gate", 80, "PASS"),         # no_recognized_source: partially degraded
    ]


def test_a_degraded_pack_is_absent_not_pass_despite_zero_percent(conn):
    """The issue's own core requirement: a fixture carrying an absent/unmeasured gate must
    render ABSENT, never PASS -- and, the sharper version specific to #24, never FAIL either,
    even though the collector's no_git fail-open path hard-codes both percentage fields to the
    exact same 0 a real, measured, zero-scoring pack would carry (Design decision A). This is
    the TOTAL-FAILURE case -- both real denominators (commits_with_source, window_commit_count)
    are genuinely zero, not just the pct fields."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT gate, status FROM metric_24 WHERE collected_ts = '2026-07-29'")
    )
    assert rows == [
        {"gate": "plan_gate", "status": "ABSENT"},
        {"gate": "review_gate", "status": "ABSENT"},
    ]
    assert all(r["status"] not in ("PASS", "FAIL") for r in rows)


def test_a_non_denominator_affecting_degrade_code_does_not_force_absent(conn):
    """THE REGRESSION TEST FOR THE BLOCKING REVIEW FINDING: a pack degraded ONLY by
    no_test_command (a d2-only code, unrelated to d1/d5) must render its REAL, measured status
    for both gates, not ABSENT -- because its real denominators (commits_with_source,
    window_commit_count) are both nonzero. A gate that keys ABSENT on "the pack's flat
    degraded[] array is non-empty" (the first draft's design) fails this test: it would render
    ABSENT/ABSENT here despite genuinely-measured 100%/100% coverage -- perfect data reported as
    unmeasured. This is the exact live behavior this repo's own real collector run produces
    today (see this plan's Verification method section)."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT gate, status FROM metric_24 WHERE collected_ts = '2026-08-01'")
    )
    assert rows == [
        {"gate": "plan_gate", "status": "PASS"},
        {"gate": "review_gate", "status": "PASS"},
    ]
    assert all(r["status"] != "ABSENT" for r in rows)
