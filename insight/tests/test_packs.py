# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.packs (issue #100): the schema registry and persistence."""
import json
import pathlib
import stat

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.packs import (  # noqa: E402
    ADAPTER_UNKNOWN_SCHEMA, ingest_collectors, normalize, write_pack,
)
from insight.ingest.store import ensure_schema  # noqa: E402


def _write_script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


# --------------------------------------------------------------------------- normalize()

def test_normalize_alignment_collect_extracts_window_and_degraded():
    payload = {
        "schema": "alignment-collect/v1",
        "window": {"since_days": 3, "oldest": {"sha": "a", "date": "d1"},
                   "newest": {"sha": "b", "date": "d2"}, "commit_count": 5},
        "degraded": ["no_test_command"],
    }
    fields, extra = normalize("alignment-collect/v1", payload)
    assert fields == {
        "window_since_days": 3, "window_oldest_sha": "a", "window_oldest_date": "d1",
        "window_newest_sha": "b", "window_newest_date": "d2", "window_commit_count": 5,
        "degraded_collector": ["no_test_command"],
    }
    assert extra == []


def test_normalize_discovery_scan_is_all_null_window():
    fields, extra = normalize("discovery-scan/v1", {"schema": "discovery-scan/v1", "candidates": []})
    assert fields["window_since_days"] is None
    assert fields["degraded_collector"] == []
    assert extra == []


def test_normalize_none_payload_is_all_null_no_extra_code():
    """payload=None means collectors.run_source already contributed an adapter code — normalize
    must not ALSO add adapter_unknown_schema on top of it."""
    fields, extra = normalize("alignment-collect/v1", None)
    assert fields["degraded_collector"] == []
    assert extra == []


def test_normalize_unknown_schema_returns_unknown_code_and_null_window():
    fields, extra = normalize("totally-unexpected/v1", {"schema": "totally-unexpected/v1"})
    assert extra == [ADAPTER_UNKNOWN_SCHEMA]
    assert fields["window_since_days"] is None
    assert fields["degraded_collector"] == []


# --------------------------------------------------------------------------- write_pack()

def test_write_pack_persists_all_columns(conn):
    fields = {"window_since_days": 1, "window_oldest_sha": "a", "window_oldest_date": "d1",
              "window_newest_sha": "b", "window_newest_date": "d2", "window_commit_count": 2,
              "degraded_collector": ["no_git"]}
    write_pack(conn, "proj1", "alignment-collect/v1", fields, ["adapter_output_not_json"], '{"x":1}')
    row = conn.execute(
        "select project_id, schema, window_since_days, degraded_collector, degraded_adapter, "
        "raw_payload from fact_collector_pack"
    ).fetchone()
    assert row == ("proj1", "alignment-collect/v1", 1, ["no_git"], ["adapter_output_not_json"], '{"x":1}')


# --------------------------------------------------------------------------- ingest_collectors(): the done_when tests

def test_unknown_schema_is_recorded_and_skipped_not_raised(conn, tmp_path):
    """done_when: 'an unknown schema is recorded and skipped, never fatal.' A fake script stands
    in for alignment-collect and emits a schema nobody registered a normaliser for."""
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh",
                   "#!/bin/sh\necho '{\"schema\":\"bogus-thing/v1\",\"foo\":1}'\n")
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))  # doesn't raise
    bogus = next(r for r in results if r["name"] == "alignment-collect")
    assert bogus["schema"] == "bogus-thing/v1"
    assert bogus["degraded_adapter"] == [ADAPTER_UNKNOWN_SCHEMA]
    row = conn.execute(
        "select schema, degraded_adapter, raw_payload from fact_collector_pack "
        "where schema = 'bogus-thing/v1'"
    ).fetchone()
    assert row == ("bogus-thing/v1", [ADAPTER_UNKNOWN_SCHEMA], '{"schema": "bogus-thing/v1", "foo": 1}')
    # the OTHER two sources still ran and were recorded — one bad schema doesn't abort the loop
    assert len(results) == 3


def test_collector_exit_nonzero_degrades_never_crashes_ingest(conn, tmp_path):
    """done_when: 'a collector that exits non-zero degrades, never crashes ingest.'"""
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh", "#!/bin/sh\nexit 9\n")
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))  # doesn't raise
    scan = next(r for r in results if r["name"] == "discovery-scan")
    assert scan["degraded_adapter"] == ["adapter_exit_nonzero"]
    row = conn.execute(
        "select degraded_adapter, raw_payload from fact_collector_pack where schema = 'discovery-scan/v1'"
    ).fetchone()
    assert row == (["adapter_exit_nonzero"], None)
    assert len(results) == 3


def test_no_collectors_root_degrades_all_three_never_raises(conn, tmp_path):
    results = ingest_collectors(conn, tmp_path, collectors_root=str(tmp_path / "does-not-exist"))
    assert len(results) == 3
    assert all(r["degraded_adapter"] == ["adapter_collector_not_found"] for r in results)
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3


def test_ingest_collectors_appends_not_upserts_on_repeated_runs(conn, tmp_path):
    root = tmp_path / "skills"  # nothing there; every source degrades, but still writes a row
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 6  # 3 sources x 2 runs, not deduplicated — this is a log, not a dimension


def test_object_schema_is_recorded_not_crashed(conn, tmp_path):
    # The schema string is used as a dict key downstream, so an object here used to raise
    # TypeError: unhashable type out of ingest entirely — killing the loop, so NO source got a
    # row. Strictly worse than the unknown-schema case the story asks us to survive.
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh",
                  '#!/bin/sh\necho \'{"schema":{"nested":true}}\'\n')
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    assert len(results) == 3  # every source still wrote a row
    scan = [r for r in results if r["schema"] == "discovery-scan/v1"][0]
    assert scan["degraded_adapter"] == ["adapter_schema_invalid"]
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3


def test_malformed_non_schema_field_is_recorded_not_crashed(conn, tmp_path):
    # A well-typed `schema` gets past every check in collectors.py, so a bad value in any OTHER
    # field only surfaces at the INSERT — window_since_days is INTEGER and DuckDB raises
    # ConversionException on a struct. 100% line coverage did not catch this; only a malformed
    # non-schema field does.
    root = tmp_path / "skills"
    _write_script(root / "sdlc-align" / "scripts" / "alignment-collect.sh",
                  '#!/bin/sh\necho \'{"schema":"alignment-collect/v1","window":'
                  '{"since_days":{"bad":1},"oldest":{},"newest":{},"commit_count":0},'
                  '"degraded":[]}\'\n')
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    assert len(results) == 3
    align = [r for r in results if r["schema"] == "alignment-collect/v1"][0]
    assert align["degraded_adapter"] == ["adapter_internal_error"]
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3  # the failed INSERT wrote nothing, so the fallback did not duplicate


def test_null_schema_is_invalid_not_missing(conn, tmp_path):
    root = tmp_path / "skills"
    _write_script(root / "sdlc-loop" / "scripts" / "discovery-scan.sh",
                  '#!/bin/sh\necho \'{"schema":null}\'\n')
    results = ingest_collectors(conn, tmp_path, collectors_root=str(root))
    scan = [r for r in results if r["schema"] == "discovery-scan/v1"][0]
    assert scan["degraded_adapter"] == ["adapter_schema_invalid"]


def test_an_internal_error_on_one_source_still_writes_every_row(conn, tmp_path, monkeypatch):
    # Pins the structural guarantee, not one known failure: whatever breaks inside the adapter
    # for one source, the other sources still land and ingest still returns.
    from insight.ingest import collectors as _collectors
    real = _collectors.run_source

    def boom(source, project_root, root):
        if source.name == "discovery-scan":
            raise RuntimeError("something nobody predicted")
        return real(source, project_root, root)

    monkeypatch.setattr(_collectors, "run_source", boom)
    results = ingest_collectors(conn, tmp_path, collectors_root=str(tmp_path / "nope"))
    assert len(results) == 3
    scan = [r for r in results if r["schema"] == "discovery-scan/v1"][0]
    assert scan["degraded_adapter"] == ["adapter_internal_error"]
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3


def test_project_id_is_deterministic_and_stable_across_calls(conn, tmp_path):
    root = tmp_path / "skills"
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    ingest_collectors(conn, tmp_path, collectors_root=str(root))
    ids = conn.execute("select distinct project_id from fact_collector_pack").fetchall()
    assert len(ids) == 1
