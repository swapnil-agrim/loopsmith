# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Durability guard for the dark-metric label (issue #109, see .sdlc/plans/109.md Design
decision A): if a future edit silently drops the `-- data_status: dark` or `-- proxy: true`
line from one of these files, this test goes red instead of the label rotting into a lie.

RESIDUE #1, recorded here rather than hidden: metric 1 (Throughput) is REAL-DATA-DARK for the
identical reason as 2/7/10/11 -- fact_goal.outcome/claimed_ts/terminal_ts are 0/19 populated in
this repo's own real ingest today (#109 research dossier) -- but #109 does not touch 1.sql (out
of scope: #108 verified it spec-conformant, and this story's own brief says "Metric 1 is NOT
touched"). This test therefore asserts nothing about metric 1's header. A one-line addition to
1.sql's own guardrail/extra is a cheap fast-follow for whichever story next legitimately touches
that file -- not silently forgotten, named here.

RESIDUE #2, a DIFFERENT kind of caveat, kept separate rather than folded into "dark": metrics 7
and 10 additionally carry an unresolved SCHEMA assumption, not just a population fact -- both
read fact_event filtering kind IN (claimed,done,parked,failed), but spec section A.3's own
controlled vocabulary for fact_event.kind is {phase,gate,verify,slice,spend,retro,park,scan}, and
section A.1 says lifecycle "stays in entries and is never re-emitted" into events. This story's
own #180 (ledger persistence) must confirm or revise the table/vocabulary before either view is
dashboard-real -- see .sdlc/plans/109.md Design decision F for the full reasoning on why this is
deliberately NOT folded into the same data_status:dark flag (one is "known-empty today", a fact;
the other is "schema not yet confirmed by its own future story", an open question) and why no
mechanical test in this story can catch #180 picking a different table -- only #180's own author
reading this docstring, the guardrail text, or Decision F can."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402

DARK_METRIC_IDS = {
    "7", "10", "11", "12", "13", "17", "18", "19", "23", "26", "27", "29", "31",
    "32", "33", "34", "35", "37", "38",
}

# Verified 2026-08-08 and DELIBERATELY cleared -- see each file's own guardrail for the measured
# evidence. Asserted positively below rather than merely dropped from the set above: removing an id
# from a set proves nothing about what its header now says, and a future edit re-adding the label
# by accident would go unnoticed.
CLEARED_DARK_METRIC_IDS = {"2", "14"}
PROXY_METRIC_IDS = {"5", "20", "24", "41"}


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_every_dark_metric_declares_data_status_dark(conn):
    registry = load_metrics(conn)  # the real, shipped insight/metrics/ directory
    for metric_id in DARK_METRIC_IDS:
        assert registry[metric_id]["extra"].get("data_status") == "dark", (
            f"metric_{metric_id} reads a table this repo's own real ingest leaves empty "
            f"(#109 research dossier) but no longer declares '-- data_status: dark'"
        )


def test_verified_metrics_no_longer_declare_themselves_dark(conn):
    """The counterpart to the test above. #2's and #14's dark labels named specific conditions --
    unpopulated claimed_ts/terminal_ts, and unpopulated outcome/fact_event -- and both are now met
    on real ingested data. A label that outlives its own stated condition is worse than no label:
    it suppresses a legitimate reading forever, which is this product's failure mode inverted."""
    registry = load_metrics(conn)
    for metric_id in CLEARED_DARK_METRIC_IDS:
        assert registry[metric_id]["extra"].get("data_status") is None, (
            f"metric {metric_id}'s dark label was cleared on 2026-08-08 after its own stated "
            f"condition was verified met; re-adding it needs new evidence, recorded in the "
            f"guardrail the same way the clearing was"
        )


def test_autonomy_rate_stays_dark_because_its_condition_is_not_met(conn):
    """The one that must NOT be cleared, pinned separately so it cannot be swept along with its
    neighbours. 12.sql reads interventions from `kind IN ('parked','ack')`, and `ack` has never
    been emitted -- so autonomy rate is overstated. Tracked as issue #548."""
    registry = load_metrics(conn)
    assert registry["12"]["extra"].get("data_status") == "dark"


def test_the_change_failure_proxy_still_declares_itself_a_proxy(conn):
    registry = load_metrics(conn)
    for metric_id in PROXY_METRIC_IDS:
        assert registry[metric_id]["extra"].get("proxy") == "true"
