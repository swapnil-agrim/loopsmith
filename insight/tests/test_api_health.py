# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.api.app (issue #299, E16.S1): the FastAPI skeleton and /health.

`pytest.importorskip("fastapi")` AND `pytest.importorskip("httpx")` at module top -- both,
not just fastapi: every test in this file uses fastapi.testclient.TestClient, which itself
needs httpx even though plain `import fastapi` does not (verified directly during planning,
see .sdlc/plans/299.md Decision 2). Follows test_store.py:14's precedent for duckdb -- local
verify (no `pip install` step) must degrade this file to SKIP rather than a COLLECTION ERROR
on a checkout without fastapi/httpx installed.
"""
import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from insight.api.app import create_app, get_connection  # noqa: E402
from insight.ingest.store import open_store  # noqa: E402


def test_health_returns_missing_when_store_file_does_not_exist(tmp_path):
    app = create_app(db_path=tmp_path / "nope.duckdb")
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"store": "missing"}


def test_health_returns_empty_when_store_exists_with_no_rows(tmp_path):
    db_path = tmp_path / "s.duckdb"
    open_store(db_path).close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"store": "empty"}


def test_health_returns_populated_when_store_has_data(tmp_path):
    db_path = tmp_path / "s.duckdb"
    conn = open_store(db_path)
    conn.execute("insert into dim_project (project_id) values ('p')")
    conn.close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"store": "populated"}


def test_write_through_the_api_connection_fails(tmp_path):
    """Clause 2's proof. Imports get_connection from insight.api.app specifically -- not
    open_store_read_only from insight.ingest.store directly -- so this is about the API's own
    wiring: it exercises the exact function object /health's route handler calls. Non-vacuous
    because (a) it performs a real INSERT against a real, populated-schema store, not a no-op;
    (b) it asserts the specific exception type (duckdb.InvalidInputException) rather than a bare
    Exception, so a code change that made the connection accidentally writable (no exception
    raised) fails this test rather than passing it vacuously; (c) it imports the opener through
    insight.api.app, the module /health actually lives in."""
    duckdb = pytest.importorskip("duckdb")
    db_path = tmp_path / "store.duckdb"
    open_store(db_path).close()
    conn = get_connection(db_path)
    try:
        with pytest.raises(duckdb.InvalidInputException):
            conn.execute("INSERT INTO dim_project (project_id) VALUES ('p2')")
    finally:
        conn.close()
