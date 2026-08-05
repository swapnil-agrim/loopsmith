# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.api.models (issue #300 [E16.S2]): the Metric discriminated union that gives
spec §3's ABSENT-is-never-PASS doctrine runtime teeth in Pydantic.

`pytest.importorskip("pydantic")` only -- no fastapi/httpx needed, these are pure model tests.

The load-bearing property under test is not merely "an absent metric's `value` is `None`" (that
would still let a caller MISREAD absence as a zero-ish measurement) but that `value` DOES NOT
EXIST AS A FIELD AT ALL on an absent model -- `AbsentNoDataMetric`/`AbsentUnbuiltMetric` simply
have no such attribute, and constructing one WITH a `value` raises. That is what
`extra="forbid"` buys, and it is what criteria 1 and 2 actually require.
"""
import pytest

pydantic = pytest.importorskip("pydantic")

from pydantic import TypeAdapter, ValidationError  # noqa: E402

from insight.api.models import (  # noqa: E402
    AbsentNoDataMetric,
    AbsentUnbuiltMetric,
    Coverage,
    MeasuredMetric,
    Metric,
)


def test_measured_metric_carries_value_and_coverage():
    m = MeasuredMetric(
        id=12, label="Autonomy rate", reliabilityClass=2, state="measured",
        value=0.75, coverage=Coverage(numerator=3, denominator=4),
    )
    assert m.value == 0.75
    assert m.coverage.numerator == 3
    assert m.coverage.denominator == 4


def test_absent_no_data_metric_has_no_value_field():
    assert "value" not in AbsentNoDataMetric.model_fields


def test_absent_unbuilt_metric_has_no_value_field():
    assert "value" not in AbsentUnbuiltMetric.model_fields


def test_constructing_absent_no_data_metric_with_a_value_raises():
    # state= MUST be passed explicitly: plan-review proved empirically (pydantic 2.11.7) that
    # omitting it makes the call raise "missing" on its own, so a bare pytest.raises(ValidationError)
    # would stay green even if extra="forbid" were later weakened to extra="ignore" and value
    # were silently dropped -- exactly the regression this criterion exists to catch.
    with pytest.raises(ValidationError) as exc:
        AbsentNoDataMetric(
            id=1, label="x", reliabilityClass=1, state="absent_no_data", reason="r", value=5,
        )
    errors = exc.value.errors()
    assert "extra_forbidden" in {e["type"] for e in errors}
    offending = [e for e in errors if e["type"] == "extra_forbidden"]
    assert any(e["loc"] == ("value",) for e in offending)


def test_constructing_absent_unbuilt_metric_with_a_value_raises():
    with pytest.raises(ValidationError) as exc:
        AbsentUnbuiltMetric(
            id=1, label="x", reliabilityClass=1, state="absent_unbuilt", reason="r", value=5,
        )
    errors = exc.value.errors()
    assert "extra_forbidden" in {e["type"] for e in errors}
    offending = [e for e in errors if e["type"] == "extra_forbidden"]
    assert any(e["loc"] == ("value",) for e in offending)


def test_absent_metric_rejects_a_value_through_the_union_adapter_too():
    """Closes the second construction path a server could realistically use -- validating a raw
    dict through the union TypeAdapter, not just the concrete class directly."""
    with pytest.raises(ValidationError) as exc:
        TypeAdapter(Metric).validate_python({
            "id": 1, "label": "x", "reliabilityClass": 1,
            "state": "absent_no_data", "reason": "r", "value": 5,
        })
    assert "extra_forbidden" in {e["type"] for e in exc.value.errors()}


def test_metric_union_discriminates_on_state():
    measured = TypeAdapter(Metric).validate_python({
        "id": 12, "label": "Autonomy rate", "reliabilityClass": 2, "state": "measured",
        "value": 0.75, "coverage": {"numerator": 3, "denominator": 4},
    })
    absent = TypeAdapter(Metric).validate_python({
        "id": 1, "label": "x", "reliabilityClass": 1,
        "state": "absent_no_data", "reason": "r",
    })
    assert isinstance(measured, MeasuredMetric)
    assert isinstance(absent, AbsentNoDataMetric)
