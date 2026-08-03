# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.analytics_reader (issue #146, E7.S3). No pytest.importorskip("duckdb")
at module level: the classification/transport tests below (mirroring test_gh_reader.py's own
Task 4/5 posture) are pure Python, zero DuckDB. The write-layer/ingest_analytics_reader tests
further down DO need duckdb and are individually guarded, matching test_gh_reader.py's own
per-section importorskip placement.

EVERY test in this file that exercises the "API key absent" path calls
`monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)` explicitly before the call under
test -- never relying on the ambient test environment happening not to export a real key. See
.sdlc/plans/146.md Design decision 12 and test_gh_reader.py's own PATH-stripping test
(test_fetch_prs_absent_gh_via_stripped_path) for the rigour this mirrors: a developer machine
that genuinely has a real Admin API key exported must never have this suite silently take the
real-transport path instead of the intended degrade path.

NO TEST IN THIS FILE MAKES A REAL NETWORK CALL. Every transport is either the injected `transport=`
fake or absent entirely (the disabled/no-key short-circuits never construct one)."""
import json
import urllib.error

import pytest

from insight.ingest.analytics_reader import (  # noqa: E402
    _classify_analytics_response, _looks_non_anthropic_platform, fetch_usage,
)

_ENV_VAR = "ANTHROPIC_ADMIN_API_KEY"


# --------------------------------------------------------------------------- _classify_analytics_response

def test_classify_401_is_unauthenticated():
    assert _classify_analytics_response(401, "") == "analytics_unauthenticated"


def test_classify_403_is_forbidden():
    assert _classify_analytics_response(403, "") == "analytics_forbidden"


def test_classify_429_is_rate_limited():
    assert _classify_analytics_response(429, "") == "analytics_rate_limited"


def test_classify_non_anthropic_platform_signal_in_body():
    body = json.dumps({"type": "error", "error": {
        "type": "permission_error", "message": "This key is not supported on this platform.",
    }})
    assert _classify_analytics_response(400, body) == "analytics_non_anthropic_platform"


def test_classify_bedrock_mention_in_message_is_non_anthropic_platform():
    body = json.dumps({"error": {"type": "invalid_request_error",
                                  "message": "This organization is provisioned via Bedrock."}})
    assert _classify_analytics_response(400, body) == "analytics_non_anthropic_platform"


def test_classify_unrecognised_non_2xx_is_the_honest_catchall():
    assert _classify_analytics_response(500, "internal server error") == "analytics_http_error"


def test_classify_malformed_body_does_not_raise_and_falls_to_catchall():
    assert _classify_analytics_response(418, "{not json") == "analytics_http_error"


def test_classify_empty_body_does_not_raise():
    assert _classify_analytics_response(400, "") == "analytics_http_error"


def test_classify_matching_is_case_insensitive():
    body = json.dumps({"error": {"message": "Not Available For This Account Type"}})
    assert _classify_analytics_response(400, body) == "analytics_non_anthropic_platform"


def test_looks_non_anthropic_platform_direct_true():
    assert _looks_non_anthropic_platform('{"error": {"message": "routed via vertex"}}') is True


def test_looks_non_anthropic_platform_direct_false():
    assert _looks_non_anthropic_platform('{"error": {"message": "bad request"}}') is False


# --------------------------------------------------------------------------- fetch_usage: happy path

def test_fetch_usage_happy_path_returns_payload_and_no_code():
    def fake_transport(url, headers, timeout):
        assert headers["x-api-key"] == "sk-test-key"
        return 200, b'{"usage": [{"model": "sonnet", "cost_cents": 100}]}'

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert code is None
    assert payload == {"usage": [{"model": "sonnet", "cost_cents": 100}]}


def test_fetch_usage_passes_the_configured_days_window():
    seen = {}

    def fake_transport(url, headers, timeout):
        seen["url"] = url
        return 200, b"{}"

    fetch_usage("sk-test-key", 30, transport=fake_transport)
    assert "start_date=" in seen["url"]


# --------------------------------------------------------------------------- fetch_usage: transport failures

def test_fetch_usage_timeout_degrades_not_raises():
    def fake_transport(url, headers, timeout):
        raise TimeoutError("timed out")

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "adapter_timeout"


def test_fetch_usage_url_error_degrades_network_error():
    def fake_transport(url, headers, timeout):
        raise urllib.error.URLError("no route to host")

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "analytics_network_error"


def test_fetch_usage_http_error_classified_via_status(monkeypatch):
    def fake_transport(url, headers, timeout):
        raise urllib.error.HTTPError(url, 401, "unauthorized", {}, None)

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "analytics_unauthenticated"


def test_fetch_usage_non_2xx_returned_without_raising_is_still_classified():
    """The dual-path handling fetch_usage's own docstring names explicitly: a transport is free
    to represent a non-2xx outcome either by raising HTTPError OR by just returning the tuple --
    this pins the SECOND shape, since test_fetch_usage_http_error_classified_via_status above
    already pins the first."""
    def fake_transport(url, headers, timeout):
        return 403, b'{"error": {"message": "forbidden"}}'

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "analytics_forbidden"


def test_fetch_usage_http_exception_degrades_adapter_internal_error():
    """http.client.HTTPException (e.g. IncompleteRead) is not given its own code -- see the
    module docstring's own naming of this exact exception class and .sdlc/plans/146.md Design
    decision 9."""
    import http.client

    def fake_transport(url, headers, timeout):
        raise http.client.IncompleteRead(b"partial")

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "adapter_internal_error"


def test_fetch_usage_unforeseen_exception_degrades_adapter_internal_error():
    def fake_transport(url, headers, timeout):
        raise RuntimeError("something nobody predicted")

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "adapter_internal_error"


# --------------------------------------------------------------------------- fetch_usage: malformed payload

def test_fetch_usage_non_json_body_degrades_output_not_json():
    def fake_transport(url, headers, timeout):
        return 200, b"not json at all"

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "adapter_output_not_json"


def test_fetch_usage_non_object_json_degrades_output_not_json():
    def fake_transport(url, headers, timeout):
        return 200, b"[1, 2, 3]"

    payload, code = fetch_usage("sk-test-key", 14, transport=fake_transport)
    assert payload is None
    assert code == "adapter_output_not_json"


# --------------------------------------------------------------------------- fetch_usage: invalid window

def test_fetch_usage_absurdly_large_days_degrades_not_raises():
    """Mirrors test_fetch_prs_absurdly_large_days_degrades_not_raises (test_gh_reader.py) --
    same bug class (unguarded datetime arithmetic), same fix shape. No transport is ever called:
    the raise happens building the URL, before any callable (real or fake) would be invoked."""
    def fake_transport(url, headers, timeout):
        raise AssertionError("transport must never be called for an invalid days value")

    payload, code = fetch_usage("sk-test-key", 999999999999999999, transport=fake_transport)
    assert payload is None
    assert code == "analytics_invalid_window_days"


def test_fetch_usage_absurdly_negative_days_degrades_not_raises():
    def fake_transport(url, headers, timeout):
        raise AssertionError("transport must never be called for an invalid days value")

    payload, code = fetch_usage("sk-test-key", -999999999999999999, transport=fake_transport)
    assert payload is None
    assert code == "analytics_invalid_window_days"


def test_fetch_usage_zero_days_does_not_raise():
    def fake_transport(url, headers, timeout):
        return 200, b"{}"

    payload, code = fetch_usage("sk-test-key", 0, transport=fake_transport)
    assert code is None
    assert payload == {}


# --------------------------------------------------------------------------- ingest_analytics_reader

duckdb = pytest.importorskip("duckdb")  # noqa: E402 -- only from here down needs a real connection

from insight.ingest.analytics_reader import ingest_analytics_reader  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _explode_if_called(*args, **kwargs):
    raise AssertionError("transport must never be invoked on this path")


def test_ingest_analytics_reader_has_no_enabled_parameter(tmp_path, conn, monkeypatch):
    """FIXED post-#146 (blocking finding, reviewer-reproduced): an earlier revision of
    ingest_analytics_reader took an `enabled` flag and, when False, still wrote one
    claude-analytics/v1 pack row degraded with "analytics_disabled" on EVERY `insight ingest`
    run -- which permanently dragged every non-adopter's `insight gaps` verdict from PASS to
    WARN via insight/gaps/coverage_degraded_collector.sql (deliberately all-schema, no
    exclusion for this collector). The fix moved the `--claude-analytics` gate up to the CALLER
    (insight.__main__._ingest_one_repo, see its own docstring) instead of inside this function:
    a non-adopter's `insight ingest` run now never calls this function at all, so it never reads
    the env var, never touches the network, and writes NO claude-analytics/v1 row. This function
    itself therefore no longer has an `enabled` parameter -- pinned here structurally so a
    regression back to the old shape fails loudly. See insight/tests/test_cli.py's
    test_ingest_without_claude_analytics_flag_writes_no_pack_row for the caller-level proof, and
    insight/tests/test_gap_rule_coverage_degraded_collector.py's
    test_a_non_adopters_ingest_leaves_the_verdict_unaffected for the gap-rule-level proof."""
    import inspect
    assert "enabled" not in inspect.signature(ingest_analytics_reader).parameters


def test_ingest_no_key_present_degrades_no_key_and_never_calls_transport(tmp_path, conn, monkeypatch):
    """The load-bearing hermeticity test (.sdlc/plans/146.md Design decision 12): explicitly
    clears the env var rather than assuming it is already absent, mirroring
    test_fetch_prs_absent_gh_via_stripped_path's own rigour in test_gh_reader.py."""
    monkeypatch.delenv(_ENV_VAR, raising=False)
    result = ingest_analytics_reader(conn, tmp_path, transport=_explode_if_called)
    assert result["degraded"] == ["analytics_no_key"]
    pack = conn.execute(
        "select degraded_collector from fact_collector_pack where schema = 'claude-analytics/v1'"
    ).fetchone()
    assert pack == (["analytics_no_key"],)


def test_ingest_blank_key_degrades_no_key(tmp_path, conn, monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "   ")
    result = ingest_analytics_reader(conn, tmp_path, transport=_explode_if_called)
    assert result["degraded"] == ["analytics_no_key"]


def test_ingest_with_key_and_network_error_transport_degrades(tmp_path, conn, monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "sk-a-real-looking-key")

    def fake_transport(url, headers, timeout):
        raise urllib.error.URLError("no route")

    result = ingest_analytics_reader(conn, tmp_path, transport=fake_transport)
    assert result["degraded"] == ["analytics_network_error"]
    pack = conn.execute(
        "select degraded_collector from fact_collector_pack where schema = 'claude-analytics/v1'"
    ).fetchone()
    assert pack == (["analytics_network_error"],)


def test_ingest_happy_path_writes_undegraded_pack_row(tmp_path, conn, monkeypatch):
    monkeypatch.setenv(_ENV_VAR, "sk-a-real-looking-key")

    def fake_transport(url, headers, timeout):
        return 200, b'{"usage": []}'

    result = ingest_analytics_reader(conn, tmp_path, transport=fake_transport)
    assert result["degraded"] == []
    pack = conn.execute(
        "select degraded_collector from fact_collector_pack where schema = 'claude-analytics/v1'"
    ).fetchone()
    assert pack == ([],)


def test_ingest_survives_an_unforeseen_explosion_in_fetch_usage(tmp_path, conn, monkeypatch):
    """Defense in depth, mirroring test_ingest_gh_reader_survives_an_unforeseen_explosion_in_fetch_prs
    (test_gh_reader.py) -- simulated by monkeypatching fetch_usage itself to raise, which no
    input-validation guard inside it could anticipate."""
    monkeypatch.setenv(_ENV_VAR, "sk-a-real-looking-key")
    import insight.ingest.analytics_reader as analytics_reader_module

    def _boom(*args, **kwargs):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(analytics_reader_module, "fetch_usage", _boom)
    result = ingest_analytics_reader(conn, tmp_path, transport=None)
    assert result["degraded"] == ["adapter_internal_error"]
    pack = conn.execute(
        "select degraded_collector from fact_collector_pack where schema = 'claude-analytics/v1'"
    ).fetchone()
    assert pack == (["adapter_internal_error"],)


def test_ingest_appends_not_upserts_the_pack_row_across_runs(tmp_path, conn, monkeypatch):
    monkeypatch.delenv(_ENV_VAR, raising=False)
    ingest_analytics_reader(conn, tmp_path)
    ingest_analytics_reader(conn, tmp_path)
    count = conn.execute(
        "select count(*) from fact_collector_pack where schema = 'claude-analytics/v1'"
    ).fetchone()[0]
    assert count == 2  # a log, not a dimension -- matches gh_reader.py's own identical guarantee
