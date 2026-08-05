# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the GET /metrics route (issue #300 [E16.S2]): the end-to-end proof that the
Metric union survives serialization onto the wire, mirroring test_api_health.py's exact
pytest.importorskip pattern (TestClient needs httpx even though plain `import fastapi` does
not -- see that file's own docstring for the full reasoning, unchanged here).
"""
import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from insight.api.app import create_app  # noqa: E402
from insight.ingest.store import open_store  # noqa: E402
from insight.metrics.catalog import CATALOG  # noqa: E402

# The two "no numeral allowed" fields, by name -- everything else on an absent metric's dict must
# carry no int/float value at all (see test_cold_start_every_metric_is_absent_with_no_numeral_in_
# the_body below for why this is checked by key, not by scanning the response text).
_NUMERIC_FIELDS_ALLOWED_ON_ABSENT_METRICS = {"id", "reliabilityClass"}


def test_metrics_endpoint_returns_all_42_catalog_entries(tmp_path):
    db_path = tmp_path / "s.duckdb"
    open_store(db_path).close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert len(response.json()) == len(CATALOG) == 42


def test_cold_start_every_metric_is_absent_with_no_numeral_in_the_body(tmp_path):
    """THE mandatory fixture (criterion 3): fact tables present, empty -- `open_store` then
    close, no ingest, no dash render, matching test_api_health.py's own cold-start precedent."""
    db_path = tmp_path / "s.duckdb"
    open_store(db_path).close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 42

    for metric in body:
        assert metric["state"] in ("absent_no_data", "absent_unbuilt"), metric
        assert "value" not in metric, metric
        assert "coverage" not in metric, metric
        # Belt-and-suspenders: walk the dict's own key/value pairs (never the raw response
        # text -- a text scan can't tell "id": 12 from "value": 12 apart) and assert every
        # numeral-carrying key is one of the two structurally allowed ones.
        numeric_keys = {
            k for k, v in metric.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        assert numeric_keys <= _NUMERIC_FIELDS_ALLOWED_ON_ABSENT_METRICS, metric


def test_missing_store_file_also_returns_all_absent(tmp_path):
    """The sibling of /health's "missing" state: `conn is None` behaves identically to
    cold-start -- all absent, still 42 entries, still no numeral."""
    app = create_app(db_path=tmp_path / "nope.duckdb")
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 42
    assert all(m["state"] in ("absent_no_data", "absent_unbuilt") for m in body)
    assert all("value" not in m for m in body)


def test_populated_store_serialises_autonomy_rate_as_measured_via_the_real_endpoint(tmp_path):
    db_path = tmp_path / "s.duckdb"
    conn = open_store(db_path)
    conn.execute(
        "CREATE VIEW metric_12 AS "
        "SELECT 3 AS autonomous_done_count, 4 AS terminal_count, 0.75 AS autonomy_rate"
    )
    conn.close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    body = {m["id"]: m for m in response.json()}

    assert body[12]["state"] == "measured"
    assert body[12]["value"] == 0.75
    assert body[12]["coverage"] == {"numerator": 3, "denominator": 4}


def test_absent_no_data_and_absent_unbuilt_stay_distinct_in_the_response_body(tmp_path):
    db_path = tmp_path / "s.duckdb"
    open_store(db_path).close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    body = client.get("/metrics").json()

    states = {m["state"] for m in body}
    assert states == {"absent_no_data", "absent_unbuilt"}

    by_id = {m["id"]: m for m in body}
    assert by_id[12]["reason"] != by_id[6]["reason"]


def test_one_malformed_metric_view_does_not_erase_the_other_41(tmp_path):
    """A single .sql edited to the wrong column shape must degrade THAT metric to absence, never
    500 the whole response -- an endpoint that answers nothing when one metric is broken is worse
    than the fabricated `0` this story replaced: it erases 41 healthy readings too."""
    db_path = tmp_path / "s.duckdb"
    conn = open_store(db_path)
    conn.execute("CREATE VIEW metric_12 AS SELECT 3 AS n, 4 AS d")  # 2 columns, extractor wants 3
    conn.close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 42
    by_id = {m["id"]: m for m in body}
    assert by_id[12]["state"] == "absent_unbuilt"
    assert "value" not in by_id[12]


def test_reliability_class_field_is_camel_cased_on_the_wire(tmp_path):
    db_path = tmp_path / "s.duckdb"
    open_store(db_path).close()
    app = create_app(db_path=db_path)
    client = TestClient(app)
    body = client.get("/metrics").json()

    assert all("reliabilityClass" in m for m in body)
    assert not any("reliability_class" in m for m in body)
