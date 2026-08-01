# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #34, Deferred-handoff age (issue #112, E2.S5, Task 2). "The silent killer": age of
ack.state='deferred' -- deliberately never settles. Reads fact_handoff's own already-merged
ack_state/settled_ts/ack_ts columns (Decision B/H) rather than replaying raw ledger entries;
returns the raw ack_ts (when the deferral decision was made), NOT a computed age -- a static
.sql view has no runtime "now", matching 10.sql's own claimed_ts precedent (Decision H).

Fixture (34.jsonl) is the SAME five-row shape as 33.jsonl (Decision B: reuse, do not duplicate
the arithmetic reasoning across two hand-authored fixtures for what is structurally the same
scenario) -- issues 101 (unanswered) / 102 (deferred, never resolved) / 103 (resolved) / 104
(accepted) / 105 (the disclosed divergence: raw history resolved-then-later-deferred, stored
identically to 102 since fact_handoff only ever carries the latest ack).

THIS IS THE BLOCKING-FINDING PINNING FILE (Decision B, post-review): metric_34 CANNOT
distinguish "genuinely never-resolved deferred" (issue 102) from "once resolved, later
re-acked as deferred" (issue 105) -- both look identical in fact_handoff's own merged columns.
ledger.py's own outstanding() would treat issue 105 as PERMANENTLY settled (a one-way ratchet:
once any ack for an issue reaches resolved/declined, it is settled forever) and would never
surface it as an outstanding deferred hand-off at all. This view, built on ledger_writer.py's
last-write-wins settled_ts, surfaces issue 105 exactly like issue 102 -- a named, accepted,
disclosed gap, not a bug this story fixes (no edits to insight/ingest/* per Decision K)."""
import datetime
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "34.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_34_returns_only_the_deferred_row(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT issue FROM metric_34 ORDER BY issue"))
    # Not 101 (never acked at all -- metric 33's row, not this one), not 103 (resolved), not
    # 104 (accepted). 105 is included deliberately -- see the next test.
    assert [r["issue"] for r in rows] == [102, 105]


def test_metric_34_cannot_distinguish_a_never_resolved_deferred_from_a_resolved_then_redeferred_one(conn):
    """The pinning test for the BLOCKING finding. Issue 105 (raw history: resolved, then later
    deferred) must appear in metric_34's output with the SAME shape as issue 102 (raw history:
    deferred, never resolved) -- same ack_state='deferred', same settled_ts IS NULL.
    ledger.py's own outstanding() would never show issue 105 here at all (permanently settled
    once resolved), but this view cannot tell the two histories apart because fact_handoff only
    ever stores the latest ack. This test passes BECAUSE the gap exists, not despite it."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    # metric_34's own SELECT list (Decision B/H) does not project ack_state/settled_ts at all
    # -- they are filter-only predicates -- which is itself part of what makes the two
    # histories indistinguishable from this view's own output. Confirmed here two ways:
    # (1) fact_handoff's OWN stored columns for issues 102 and 105 are structurally identical
    # (the real divergence in their raw ledger history has already been collapsed away by
    # ledger_writer.py's last-write-wins merge, before this view ever runs);
    # (2) metric_34 itself surfaces both issues with no column that could tell them apart.
    stored = {
        r["issue"]: r
        for r in rows_as_dicts(
            conn.execute(
                "SELECT issue, ack_state, settled_ts FROM fact_handoff WHERE issue IN (102, 105)"
            )
        )
    }
    assert stored[102]["ack_state"] == stored[105]["ack_state"] == "deferred"
    assert stored[102]["settled_ts"] is None
    assert stored[105]["settled_ts"] is None

    rows = rows_as_dicts(conn.execute("SELECT issue FROM metric_34 ORDER BY issue"))
    assert [r["issue"] for r in rows] == [102, 105]


def test_metric_34_exposes_the_raw_ack_ts_not_a_computed_age(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    cursor = conn.execute("SELECT * FROM metric_34 ORDER BY issue")
    columns = [d[0] for d in cursor.description]
    assert not any(c in ("age", "age_seconds", "age_days") for c in columns)
    rows = rows_as_dicts(conn.execute("SELECT issue, ack_ts FROM metric_34 ORDER BY issue"))
    assert rows == [
        {"issue": 102, "ack_ts": datetime.datetime(2026, 1, 2, 3, 0, 0)},
        {"issue": 105, "ack_ts": datetime.datetime(2026, 1, 5, 4, 0, 0)},
    ]


def test_metric_34_excludes_the_all_null_phantom_row_from_an_orphaned_ack(conn):
    """BLOCKING-finding regression (post-review, issue #112 PR #190) -- worse than metric 33's
    version of the same gap. The fixture's 6th and 7th rows are a goal-only hand-off (issue=None,
    never acked in its own row) plus its orphaned ack (issue=None, ack_state='deferred',
    settled_ts NULL, every non-ack column NULL). Before the issue IS NOT NULL fix, the orphaned
    ack row alone satisfied metric_34's own WHERE (ack_state = 'deferred' AND settled_ts IS
    NULL) and surfaced as an all-NULL phantom (area/from_actor/to_actor/opened_ts all NULL, only
    ack_ts populated) -- while the REAL deferred hand-off it belongs to never appears at all
    (its own ack_state stays NULL forever, since the merge that would have set it had no issue
    number to land on). issue IS NOT NULL removes the phantom; it does not and cannot recover
    the real row -- that residue is named, accepted, and out of scope here (Decision K)."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT issue, area FROM metric_34"))
    assert all(r["issue"] is not None for r in rows)
    assert all(r["area"] is not None for r in rows)
    assert [r["issue"] for r in rows] == [102, 105]


def test_metric_34_declares_itself_dark(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["34"]["extra"]["data_status"] == "dark"
