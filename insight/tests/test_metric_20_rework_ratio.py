# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #20, Rework ratio (issue #110). VERIFIED live against a real alignment-collect/v1
raw_payload shape, both duckdb 1.4.5 and 1.5.5 (byte-identical): json_extract + json_array_length
+ from_json/unnest over d3.churn_hotspots, all independently re-verified on 1.5.5 (the research
dossier only checked 1.4.5 -- see this plan's Verification method section). SECOND fixture row
(post-review, blocking finding): a pack whose churn_hotspots is [] -- alignment-collect.sh's own
ORDINARY code path for a window with zero non-merge commits, not the no_git fail-open path --
must surface as a real row (total_files_touched=0, rework_ratio=NULL), never vanish."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "20.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_20_computes_rework_ratio_from_churn_hotspots(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["20"]["extra"]["proxy"] == "true"
    rows = rows_as_dicts(conn.execute("SELECT * FROM metric_20 ORDER BY collected_ts"))
    assert len(rows) == 2

    empty_window, populated_window = rows[0], rows[1]
    # the EMPTY-ARRAY pack: a real, measured row, not a vanished one (the blocking fix)
    assert empty_window["window_commit_count"] == 0
    assert empty_window["files_touched_more_than_once"] == 0
    assert empty_window["total_files_touched"] == 0
    assert empty_window["rework_ratio"] is None

    assert populated_window["window_commit_count"] == 8
    assert populated_window["files_touched_more_than_once"] == 2
    assert populated_window["total_files_touched"] == 5
    assert populated_window["rework_ratio"] == 0.4


def test_metric_20_window_commit_count_of_1_with_a_changes_5_hotspot_is_not_floored_to_zero(conn):
    """Post-review fix: the guardrail's earlier draft claimed a window with <=1 commit
    "forces rework_ratio=0.0 by construction" -- FALSE, demonstrated here. This view derives
    rework_ratio entirely from churn_hotspots (via json_extract/from_json/unnest) and never
    reads window_commit_count in that computation, so an internally-inconsistent collector
    payload (window_commit_count=1, one hotspot claiming changes=5 -- a shape a real collector
    should never emit, since one commit cannot touch a file more than once, but nothing here
    checks that invariant) is rendered faithfully: rework_ratio=1.0, not 0.0. Any floor at
    window_commit_count<=1 is a property of the upstream collector keeping its own numbers
    consistent, not a guarantee this SQL provides -- see 20.sql's own reworded guardrail."""
    conn.execute(
        "INSERT INTO fact_collector_pack "
        "(project_id, schema, collected_ts, window_commit_count, raw_payload) VALUES ("
        "'p1', 'alignment-collect/v1', '2026-08-01T00:00:00', 1, "
        "'{\"dimensions\": {\"d3\": {\"churn_hotspots\": "
        "[{\"file\": \"a.py\", \"changes\": 5}]}}}'"
        ")"
    )
    load_metrics(conn)
    rows = rows_as_dicts(
        conn.execute("SELECT * FROM metric_20 WHERE window_commit_count = 1")
    )
    assert len(rows) == 1
    assert rows[0]["total_files_touched"] == 1
    assert rows[0]["files_touched_more_than_once"] == 1
    assert rows[0]["rework_ratio"] == 1.0  # NOT 0.0 -- the "floor" does not hold in this SQL
