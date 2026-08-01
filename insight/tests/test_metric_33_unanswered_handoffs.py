# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Metric #33, Unanswered handoffs (issue #112, E2.S5, Task 1). Mirrors `ledger.py`'s own
`unanswered()`: `settled_ts IS NULL AND ack_ts IS NULL AND ack_state IS NULL` against
`fact_handoff`'s already-merged columns -- see .sdlc/plans/112.md Design decision B for why this
reads the merged columns directly instead of replaying raw ledger entries in SQL, and for the
disclosed, non-exact divergence between `ledger_writer.py`'s `settled_ts` (last-write-wins) and
`ledger.py`'s own `outstanding()` (a one-way settlement ratchet).

Fixture (33.jsonl), five fact_handoff rows under project_id='p1', all in area='backend':
  - issue=101: genuinely unanswered -- no ack at all.
  - issue=102: deferred, never resolved -- has an ack, so excluded from "unanswered."
  - issue=103: resolved and settled.
  - issue=104: accepted, outstanding but not unanswered and not deferred (extra coverage,
    proves this view does not accidentally count every non-settled row as "unanswered").
  - issue=105: THE DISCLOSED DIVERGENCE FIXTURE (Decision B, post-review). Represents a raw
    ledger history of an earlier `resolved` ack followed by a LATER `deferred` re-ack on the
    same issue. `fact_handoff` cannot carry that history -- only the latest ack survives -- so
    this row's stored shape (ack_ts set, ack_state='deferred', settled_ts=NULL) is structurally
    IDENTICAL to issue=102's row, even though its real history differs. `ledger.py`'s own
    `outstanding()` would treat this issue as permanently settled forever (once any ack reached
    'resolved'); `ledger_writer.py` instead overwrote `settled_ts` back to NULL on the later,
    de-escalating ack. That divergence is Task 2 (metric 34)'s concern -- this file only proves
    metric 33 itself is UNAFFECTED by it, since "has any ack at all" is insensitive to which
    ack state a hand-off's history passed through."""
import pathlib
import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.store import ensure_schema  # noqa: E402
from insight.metrics.loader import load_metrics  # noqa: E402
from insight.metrics.testing import load_fixture_jsonl, rows_as_dicts  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "33.jsonl"


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def test_metric_33_returns_only_the_genuinely_unanswered_row(conn):
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT issue FROM metric_33 ORDER BY issue"))
    # Not 102 (deferred, has an ack), not 103 (resolved, settled), not 104 (accepted, has an
    # ack), not 105 (has an ack, even though its history is the Decision B divergence case).
    assert [r["issue"] for r in rows] == [101]


def test_metric_33_is_unaffected_by_the_resolved_then_redeferred_divergence(conn):
    """Unlike metric 34 (Task 2), metric 33's own definition ("no ack at all") is insensitive
    to WHICH ack state a hand-off's history passed through -- any ack at all excludes a row
    from "unanswered," so the Decision B divergence (which is about how settled_ts behaves
    across a HISTORY of acks) has no observable effect on this particular metric. Issue 105
    must be absent from metric_33's output, exactly like issue 102."""
    load_fixture_jsonl(conn, FIXTURE)
    load_metrics(conn)
    rows = rows_as_dicts(conn.execute("SELECT issue FROM metric_33"))
    issues = {r["issue"] for r in rows}
    assert 105 not in issues
    assert 102 not in issues


def test_metric_33_declares_itself_dark(conn):
    load_fixture_jsonl(conn, FIXTURE)
    registry = load_metrics(conn)
    assert registry["33"]["extra"]["data_status"] == "dark"
