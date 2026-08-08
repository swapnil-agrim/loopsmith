# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""The events-stream payload must survive ingest.

WHAT WENT WRONG. `_write_event` inserted six columns -- project_id, goal_id, ts, actor_id, kind,
reliability_class -- into a twenty-column table, and its docstring justified that by saying every
other column "is spec vocabulary for a stream ledger.py does not populate yet".

That was true of `ledger/entries/`. It was NOT true of `ledger/events/`, which this repo's own loop
has been writing all along. Measured on the real ledger at the time this test was written: 387
event records carrying `verdict` x151, `phase` x125, `state` x125, `ms` x105, `ok` x105, `exit`
x105, `why` x69, `reason_class` x6 and `cycle` x11 -- every one of them dropped on the floor.

The visible symptom was six metrics reading as "no extractor / no data" while the answer they
needed was sitting in the ledger. A gate record even carried the full refusal text:

    {"kind":"gate","gate":"merge","goal":"312","verdict":"block",
     "why":"STALE HEAD - the PR is at 8a4eabc but this worktree is at 6b6636a..."}

so metric 23 (gate catch rate) and 27 (decision-gate denials) could have been real for weeks.

WHAT THIS TEST PINS, AND WHAT IT DELIBERATELY DOES NOT. Only the fields the ledger genuinely
carries are mapped. `tokens_in`, `tokens_out`, `cost_cents`, `model` and `grade` have NO source in
any ledger record today -- they stay NULL, and the last test here pins that, so a future change
cannot quietly start guessing them. `state` is carried by 125 phase records but has no column in
fact_event's spec-given schema; it is dropped on purpose rather than smuggled into a column that
means something else.
"""
import json
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.ledger_writer import ingest_ledger  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _write(project_root, actor, records, stream="events"):
    """Records live under <project_root>/.sdlc/ledger/<stream>/, because `ingest_ledger` takes the
    PROJECT ROOT and derives `.sdlc` itself. Writing to <project_root>/ledger/ instead silently
    ingests nothing -- the run reports `events: 0` and every assertion then fails on an empty
    table for the wrong reason, which is how this helper was first written."""
    path = pathlib.Path(project_root) / ".sdlc" / "ledger" / stream / f"{actor}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rows(conn):
    cur = conn.execute("SELECT * FROM fact_event")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def test_a_gate_record_keeps_its_verdict_and_why(tmp_path, conn):
    """The exact shape this repo's loop writes -- metric 23 and 27 both need `verdict`."""
    _write(tmp_path, "alice", [{
        "id": "alice:1", "ts": "2026-08-07T15:43:08Z", "actor": "alice", "goal": "312",
        "kind": "gate", "gate": "merge", "verdict": "block", "why": "STALE HEAD",
    }])
    ingest_ledger(conn, tmp_path)

    row = _rows(conn)[0]
    assert row["kind"] == "gate"
    assert row["gate"] == "merge", "the gate NAME is what metric 23 groups by"
    assert row["verdict"] == "block", "the verdict is the entire point of a gate record"
    assert row["why"] == "STALE HEAD"


def test_a_verify_record_keeps_its_timing_and_outcome(tmp_path, conn):
    """`ms`/`ok`/`exit` ride 105 verify records. Note the ledger spells it `exit`, the column is
    `exit_code` -- a rename, which is exactly the kind of mapping a test should pin."""
    _write(tmp_path, "alice", [{
        "id": "alice:1", "ts": "2026-08-07T10:00:00Z", "actor": "alice", "goal": "312",
        "kind": "verify", "ms": 4210, "ok": True, "exit": 0,
    }])
    ingest_ledger(conn, tmp_path)

    row = _rows(conn)[0]
    assert row["ms"] == 4210
    assert row["ok"] is True, "ok is a real boolean, not a truthy string"
    assert row["exit_code"] == 0, "ledger `exit` maps to column `exit_code`"


def test_a_failing_verify_records_a_false_not_a_null(tmp_path, conn):
    """The one that matters: `ok: false` must land as FALSE. If a falsy value were dropped to
    NULL, every failed verify would read as unmeasured and the pass rate would be a lie."""
    _write(tmp_path, "alice", [{
        "id": "alice:1", "ts": "2026-08-07T10:00:00Z", "actor": "alice", "goal": "312",
        "kind": "verify", "ms": 900, "ok": False, "exit": 1,
    }])
    ingest_ledger(conn, tmp_path)

    row = _rows(conn)[0]
    assert row["ok"] is False, "a failed verify must be FALSE, never NULL"
    assert row["exit_code"] == 1, "exit code 0 vs 1 must survive; 0 is falsy and must not vanish"


def test_a_park_record_keeps_its_reason_class(tmp_path, conn):
    """metric 15 (park taxonomy) groups entirely by reason_class."""
    _write(tmp_path, "alice", [{
        "id": "alice:1", "ts": "2026-08-07T10:00:00Z", "actor": "alice", "goal": "312",
        "kind": "park", "reason_class": "blocked_on_review",
    }])
    ingest_ledger(conn, tmp_path)
    assert _rows(conn)[0]["reason_class"] == "blocked_on_review"


def test_a_phase_record_keeps_its_phase_and_cycle(tmp_path, conn):
    _write(tmp_path, "alice", [{
        "id": "alice:1", "ts": "2026-08-07T10:00:00Z", "actor": "alice", "goal": "312",
        "kind": "phase", "phase": "implement", "cycle": 2,
    }])
    ingest_ledger(conn, tmp_path)
    row = _rows(conn)[0]
    assert row["phase"] == "implement"
    assert row["cycle"] == 2


def test_an_entries_record_with_no_payload_still_writes_nulls_not_crashes(tmp_path, conn):
    """The lifecycle stream carries none of these fields. It must keep working unchanged."""
    _write(tmp_path, "alice", [{
        "id": "alice:1", "ts": "2026-08-07T10:00:00Z", "actor": "alice", "goal": "312",
        "kind": "claimed",
    }], stream="entries")
    ingest_ledger(conn, tmp_path)

    row = _rows(conn)[0]
    assert row["kind"] == "claimed"
    assert row["reliability_class"] == 1, "entries/ is still class 1"
    for col in ("gate", "verdict", "phase", "ms", "ok", "reason_class", "why", "cycle"):
        assert row[col] is None, f"{col} must stay NULL when the record does not carry it"


def test_columns_with_no_ledger_source_are_never_invented(tmp_path, conn):
    """tokens/cost/model/grade have NO source in any ledger record today. A record that tries to
    supply them must still leave them NULL -- the mapping is an allow-list, not a blind copy, so
    a stray field in a hand-edited ledger line cannot inject a cost figure nobody measured."""
    _write(tmp_path, "alice", [{
        "id": "alice:1", "ts": "2026-08-07T10:00:00Z", "actor": "alice", "goal": "312",
        "kind": "gate", "verdict": "pass",
        "tokens_in": 999, "cost_cents": 12345, "model": "made-up", "grade": "A",
    }])
    ingest_ledger(conn, tmp_path)

    row = _rows(conn)[0]
    assert row["verdict"] == "pass", "the mapped field still lands"
    for col in ("tokens_in", "tokens_out", "cost_cents", "model", "grade"):
        assert row[col] is None, (
            f"{col} has no ledger source and must stay NULL -- ingesting it from an arbitrary "
            f"record field would fabricate a measurement"
        )
