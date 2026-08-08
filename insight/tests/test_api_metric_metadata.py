# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Header metadata must reach the API.

Every `insight/metrics/N.sql` already carries a reviewed, one-line `question:` stating what the
metric answers, plus a `guardrail:` naming what it CANNOT tell you. Both were parsed only by
`_reliability_class` and by tests, so the delivery panel could render a number and never say what
it meant -- the "what does this card mean" gap, closed by plumbing rather than by writing new copy.
"""
import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pydantic")

from insight.api.metrics import collect_metrics, resolve_metric  # noqa: E402

REAL = "insight/metrics"


def test_every_metric_with_sql_carries_its_question():
    metrics = collect_metrics(None, metrics_dir=REAL)
    with_sql = [m for m in metrics if "exists yet" not in getattr(m, "reason", "")]
    assert with_sql, "fixture problem: no metric has a .sql file"
    for m in with_sql:
        assert m.question, f"metric {m.id} ({m.label}) has a .sql but no question line"
        # Phrased as a question, not a noun phrase. 29.sql read "Intent vs shipped" until this
        # test was written; the data was fixed, not the assertion, because the other 33 already
        # complied and a card that asks a question reads better than one that labels a topic.
        assert m.question.endswith("?"), (
            f"metric {m.id}'s question must read as a question, got {m.question!r}"
        )


def test_dark_and_proxy_flags_are_surfaced():
    dark = resolve_metric(None, 12, metrics_dir=REAL)     # data_status: dark
    proxy = resolve_metric(None, 20, metrics_dir=REAL)    # proxy: true
    clean = resolve_metric(None, 3, metrics_dir=REAL)     # neither
    assert dark.data_status == "dark"
    assert dark.proxy is False, "dark and proxy are different claims and must not be conflated"
    assert proxy.proxy is True
    assert clean.data_status is None and clean.proxy is False


def test_proxy_must_be_spelled_the_documented_way():
    """`proxy: true` is the convention (issue #110). A metric that does not say it is a proxy is
    not one -- absence must never read as True."""
    for mid in (2, 3, 12):
        assert resolve_metric(None, mid, metrics_dir=REAL).proxy is False


def test_a_metric_with_no_sql_has_no_metadata_rather_than_invented_metadata():
    """id 6 has no 6.sql. It must report None, never a placeholder question."""
    m = resolve_metric(None, 6, metrics_dir=REAL)
    assert m.question is None and m.guardrail is None and m.proxy is False
    assert m.data_status is None
