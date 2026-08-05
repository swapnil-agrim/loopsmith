# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The SQL semantic layer and the metric catalogue (issue #108, E2.S1).

metrics/<id>.sql, one file per metric, each with a `-- key: value` header carrying `name`,
`question`, `personas`, `reliability_class`, `guardrail` (insight/metrics/header.py -- see
.sdlc/plans/108.md Design decision A for the full grammar). insight/metrics/loader.py's
`load_metrics(conn, metrics_dir=None)` registers each conforming file as a real DuckDB view
named `metric_<id>`, and fails hard (MetricLoadError) if any file's header is missing a
required field -- deliberately, not a degrade: these are first-party, reviewed source files,
not runtime input (Design decision D). insight/metrics/testing.py's `load_fixture_jsonl` /
`rows_as_dicts` are the fixture-in/table-out harness every metric's own test (and every one of
#109-114's tests) is built on.

Only one metric ships in this story: #1 Throughput (insight/metrics/1.sql), the format's own
worked, end-to-end proof. The other 24 NOW metrics are #109-113's job; #114 owns semantic
reliability-class enforcement (checking what a metric's SQL actually reads against what it
declares) -- this module only checks that the five header fields are present and well-formed.
"""
from insight.metrics.catalog import CATALOG  # noqa: F401
from insight.metrics.header import HeaderError, parse_header  # noqa: F401
from insight.metrics.loader import DEFAULT_METRICS_DIR, MetricLoadError, load_metrics  # noqa: F401
