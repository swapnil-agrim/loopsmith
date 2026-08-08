# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The Metric discriminated union (issue #300 [E16.S2], spec §3): the ABSENT-is-never-PASS
doctrine given actual runtime teeth, one HTTP layer up from where insight/api/app.py's /health
route could only state it in a comment (issue #299).

Spec §3's union has exactly three shapes:

  measured           -- a real value AND a coverage numerator/denominator.
  absent_no_data      -- the metric's view exists (or ran) but produced nothing to measure.
  absent_unbuilt       -- there is no way to produce this metric yet (no SQL, or no API wiring).

The two absent shapes carry NO `value` field at all -- not a `value: None`, which would still
let a caller read `.value` and get something back. `extra="forbid"` on `MetricBase` (inherited
by every subclass) is what makes that a hard boundary: constructing an absent metric WITH a
`value` kwarg raises `ValidationError` (`extra_forbidden`) rather than silently accepting or
dropping it. That is the concrete mechanism behind criterion 2, not merely a convention.

`reliabilityClass` (camelCase on the wire, `reliability_class` in Python) is the one field named
in the wire contract from the start, mirroring `insight.metrics.header`'s own
`reliability_class` (1 or 2) -- see insight/api/metrics.py for how it is resolved per metric.
"""
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Coverage(BaseModel):
    """The numerator/denominator a `measured` metric's value was computed over -- e.g. "3/4
    landed without intervention" for autonomy rate. Never present on an absent metric: there is
    no coverage to report for a value that was never computed."""
    model_config = ConfigDict(extra="forbid")

    numerator: int
    denominator: int


class MetricBase(BaseModel):
    """Fields every metric shares, regardless of state. `extra="forbid"` here is inherited by
    every subclass below -- it is what turns "an absent metric carries a value" from a silently
    accepted (or silently dropped) extra kwarg into a real `ValidationError`."""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: int
    label: str
    reliability_class: int = Field(alias="reliabilityClass")

    # Straight from the metric's own `.sql` header (insight/metrics/header.py). These are the
    # card's MEANING -- `question` is a reviewed, one-line plain-English statement of what the
    # metric answers ("How long does a goal take?"), and `guardrail` records what the metric
    # CANNOT tell you. Both already existed in every header and were parsed only by tests, which
    # is why the panel could show a number but never say what it meant.
    #
    # Optional because a catalog id with no `.sql` has no header to read, and inventing a question
    # for it would be fabricating documentation -- the same rule the metrics themselves follow.
    question: Optional[str] = None
    guardrail: Optional[str] = None
    # A bool, not Optional[bool]: "is this an approximation?" always has an answer, and False is
    # the honest default for a header that makes no such claim.
    proxy: bool = False
    # "dark" today. Left a free string so a future header can add another status without a model
    # change; None means the header made no claim.
    data_status: Optional[str] = Field(default=None, alias="dataStatus")


class Health(BaseModel):
    """A verdict about a reading, which is a stronger claim than the reading itself.

    Only ever attached to a measured metric, and only when every gate in insight.api.health
    passes. Its ABSENCE is the default and means "no defensible verdict" -- never "fine"."""
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    verdict: Literal["healthy", "watch", "breach"]
    # Where the judgement came from, so a reader can weigh it. "baseline" is the metric's own
    # earlier window; "benchmark" is an external band and carries its citation in `source`.
    basis: Literal["baseline", "benchmark"]
    explanation: str
    source: Optional[str] = None


class MeasuredMetric(MetricBase):
    state: Literal["measured"]
    value: float
    coverage: Coverage
    # What `value` is COUNTED IN, so a client can render it for humans. Without this the delivery
    # panel printed cycle time as "4525.5" and lead time as "11734.5" -- raw seconds, which no
    # reader converts in their head, and which look like the same kind of quantity as the 0.9038
    # next to them when one is a duration and the other a ratio.
    #
    # ONLY on the measured arm, and deliberately so: a unit is a property of a reading, and an
    # absent metric has no reading to carry one. Putting it on MetricBase would hand every absent
    # metric a "seconds" that describes nothing.
    #
    # Optional with a None default so this is an ADDITIVE contract change: every existing consumer
    # of a measured metric keeps working, and a metric whose unit nobody has declared yet says so
    # by omission rather than by guessing "count" and being wrong.
    unit: Optional[Literal["seconds", "ratio", "count"]] = None
    # None means NO VERDICT, and that is the safe default rather than a neutral one: most metrics
    # legitimately have no defensible basis, insufficient coverage, or a dark data status, and the
    # panel renders no accent at all for them. A "neutral" verdict value would be a fourth colour
    # competing with the three that mean something.
    health: Optional[Health] = None


class AbsentNoDataMetric(MetricBase):
    """The metric's SQL exists (or ran) and returned nothing to measure -- a missing view, a
    view with zero rows, or a row whose value is NULL are all this same shape (see
    insight/api/metrics.py's `resolve_metric`, and `insight.dash.panel`'s `_metric_state`/
    `_scalar`, whose absence conventions this ports). Time, not a code change, fixes this."""
    state: Literal["absent_no_data"]
    reason: str


class AbsentUnbuiltMetric(MetricBase):
    """There is no way to produce this metric yet -- no `.sql` file, or (this story's own scope-
    down, Decision (b)) a `.sql` file with no registered value/coverage extractor. Only a code
    change fixes this, never the passage of time."""
    state: Literal["absent_unbuilt"]
    reason: str
    # What would actually fill this gap, DERIVED from the store rather than authored per metric.
    # "No extractor registered" tells a reader nothing they can act on; "2 rows are already
    # waiting in metric_4" tells them the data has arrived and only the wiring is missing. Because
    # it is derived, it cannot go stale as data lands.
    gap_hint: Optional[str] = Field(default=None, alias="gapHint")


# The discriminator ("state") lets Pydantic route a raw dict to the right concrete class without
# trying each one in turn -- and, combined with each subclass's Literal state value, is what
# makes test_metric_union_discriminates_on_state's two payloads resolve unambiguously.
Metric = Annotated[
    Union[MeasuredMetric, AbsentNoDataMetric, AbsentUnbuiltMetric],
    Field(discriminator="state"),
]

# Built once at import time and reused rather than constructed fresh per call -- TypeAdapter
# construction inspects the annotation and is not free; insight/api/metrics.py's
# collect_metrics() calls this once per catalog entry per request.
MetricAdapter = TypeAdapter(Metric)
