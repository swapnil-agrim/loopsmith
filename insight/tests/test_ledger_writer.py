# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.ledger_writer (issue #105, E1.S7): the ledger persistence path --
fact_event/fact_handoff writes, reliability_class tagging, and the incremental resume cursor.

Module-level importorskip("duckdb") like test_git_reader.py: this file is a
read+write module's own test file, not a pure-reader one.

FIXTURES ARE WRITTEN AS REAL FILES under <tmp_path>/.sdlc/ledger/entries/<actor>.jsonl (and
.../ledger/events/<actor>.jsonl for the reliability-class tests), not as pre-built Python dicts
handed straight to internal functions -- this exercises the SAME path a real `insight ingest`
run takes (ledger_reader.read_all_with_reliability's own file globbing/parsing), not a shortcut
around it.
"""
import json
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest import ledger_writer  # noqa: E402
from insight.ingest.ledger_writer import ingest_ledger  # noqa: E402
from insight.ingest.packs import project_id_for  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _entry_path(sdlc_dir, actor, stream="entries"):
    return pathlib.Path(sdlc_dir) / "ledger" / stream / f"{actor}.jsonl"


def _write_records(sdlc_dir, actor, records, stream="entries"):
    """Append `records` (dicts) as JSONL lines to <sdlc_dir>/ledger/<stream>/<actor>.jsonl --
    the real on-disk shape ledger_reader.py globs, not a shortcut around it."""
    path = _entry_path(sdlc_dir, actor, stream=stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rec(actor, seq, ts, kind, goal="g1", **fields):
    """One ledger record dict, id shaped exactly like ledger.append()'s own `<actor>:<seq>`."""
    out = {"id": f"{actor}:{seq}", "ts": ts, "actor": actor, "kind": kind, "goal": goal}
    out.update(fields)
    return out


def _rows(conn, table, order_by=None):
    sql = f"SELECT * FROM {table}" + (f" ORDER BY {order_by}" if order_by else "")
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# --------------------------------------------------------------------------- fact_event mapping


def test_lifecycle_kind_writes_to_fact_event_with_verbatim_kind_and_class_1(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "claimed", goal="g7")])
    result = ingest_ledger(conn, tmp_path)
    assert result == {"events": 1, "handoffs": 0, "skipped": 0}
    rows = _rows(conn, "fact_event")
    assert len(rows) == 1
    row = rows[0]
    assert row["goal_id"] == "g7"
    assert row["actor_id"] == "alice"
    assert row["kind"] == "claimed"  # verbatim, matching metrics/7.sql's and 10.sql's own
                                      # `WHERE kind IN ('claimed', ...)` -- settles the
                                      # ASSUMPTION those two views' guardrails flagged
    assert row["reliability_class"] == 1
    assert row["project_id"] == project_id_for(tmp_path)
    # Every column this story's real ledger.py data has no value for stays NULL, not guessed.
    for col in ("phase", "gate", "verdict", "cycle", "ms", "tokens_in", "tokens_out",
                "cost_cents", "reason_class", "ok", "exit_code"):
        assert row[col] is None, col


def test_every_non_handoff_kind_lands_in_fact_event(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    kinds = ["claimed", "done", "parked", "failed", "release", "note", "merged"]
    records = [_rec("alice", i + 1, f"2026-01-0{i + 1}T00:00:00Z", k, goal=f"g{i}")
               for i, k in enumerate(kinds)]
    _write_records(sdlc, "alice", records)
    result = ingest_ledger(conn, tmp_path)
    assert result["events"] == len(kinds)
    assert result["handoffs"] == 0
    got_kinds = sorted(r["kind"] for r in _rows(conn, "fact_event"))
    assert got_kinds == sorted(kinds)


def test_a_malformed_ts_degrades_to_null_not_a_crash(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "not-a-timestamp", "claimed")])
    result = ingest_ledger(conn, tmp_path)
    assert result == {"events": 1, "handoffs": 0, "skipped": 0}
    assert _rows(conn, "fact_event")[0]["ts"] is None


# --------------------------------------------------------------------------- fact_handoff mapping


def test_handoff_with_no_ack_yet_opens_a_row_with_null_ack_fields(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:00:00Z", "handoff", goal="g1",
             to="bob", area="backend", issue=42, priority="high", why="please review"),
    ])
    result = ingest_ledger(conn, tmp_path)
    assert result == {"events": 0, "handoffs": 1, "skipped": 0}
    rows = _rows(conn, "fact_handoff")
    assert len(rows) == 1
    row = rows[0]
    assert (row["from_actor"], row["to_actor"], row["area"], row["issue"], row["priority"]) == (
        "alice", "bob", "backend", 42, "high")
    assert row["ack_ts"] is None and row["ack_state"] is None and row["settled_ts"] is None


def test_ack_resolved_settles_an_open_handoff(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:00:00Z", "handoff", to="bob", issue=42),
    ])
    _write_records(sdlc, "bob", [
        _rec("bob", 1, "2026-01-02T00:00:00Z", "ack", issue=42, state="resolved"),
    ])
    ingest_ledger(conn, tmp_path)
    rows = _rows(conn, "fact_handoff")
    assert len(rows) == 1  # merged into the SAME row, not a second one
    row = rows[0]
    assert row["ack_state"] == "resolved"
    assert row["ack_ts"] is not None
    assert row["settled_ts"] == row["ack_ts"]  # settled exactly when the ack IS a settling one


def test_ack_deferred_does_not_settle_the_handoff(tmp_path, conn):
    """Mirrors ledger.py's own outstanding(): 'deferred' deliberately stays outstanding -- a
    promise to look later is not a resolution. settled_ts must stay NULL."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "handoff", issue=7)])
    _write_records(sdlc, "bob", [_rec("bob", 1, "2026-01-02T00:00:00Z", "ack", issue=7, state="deferred")])
    ingest_ledger(conn, tmp_path)
    row = _rows(conn, "fact_handoff")[0]
    assert row["ack_state"] == "deferred"
    assert row["ack_ts"] is not None
    assert row["settled_ts"] is None


def test_the_latest_ack_wins_over_an_earlier_one(tmp_path, conn):
    """Mirrors ledger.py's own handoff_states(): latest[key] = entry['state'] -- last write
    wins, not first."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "handoff", issue=9)])
    _write_records(sdlc, "bob", [
        _rec("bob", 1, "2026-01-02T00:00:00Z", "ack", issue=9, state="accepted"),
        _rec("bob", 2, "2026-01-03T00:00:00Z", "ack", issue=9, state="resolved"),
    ])
    ingest_ledger(conn, tmp_path)
    rows = _rows(conn, "fact_handoff")
    assert len(rows) == 1
    assert rows[0]["ack_state"] == "resolved"
    assert rows[0]["settled_ts"] is not None


def test_a_new_handoff_for_the_same_issue_refreshes_the_open_side_only(tmp_path, conn):
    """A second `handoff` record for an issue already on file updates from_actor/to_actor/
    area/priority/opened_ts in place (last-write-wins) without touching ack_ts/ack_state/
    settled_ts -- only _apply_ack ever sets those."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:00:00Z", "handoff", to="bob", issue=5, priority="low"),
    ])
    _write_records(sdlc, "bob", [_rec("bob", 1, "2026-01-02T00:00:00Z", "ack", issue=5, state="resolved")])
    _write_records(sdlc, "alice", [
        _rec("alice", 2, "2026-01-03T00:00:00Z", "handoff", to="carol", issue=5, priority="urgent"),
    ])
    ingest_ledger(conn, tmp_path)
    rows = _rows(conn, "fact_handoff")
    assert len(rows) == 1
    row = rows[0]
    assert row["to_actor"] == "carol" and row["priority"] == "urgent"
    assert row["ack_state"] == "resolved"  # untouched by the second handoff record


def test_an_ack_with_no_matching_handoff_is_still_recorded_standalone(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "bob", [_rec("bob", 1, "2026-01-02T00:00:00Z", "ack", issue=99, state="resolved")])
    result = ingest_ledger(conn, tmp_path)
    assert result["handoffs"] == 1
    row = _rows(conn, "fact_handoff")[0]
    assert row["issue"] == 99
    assert row["ack_state"] == "resolved"
    assert row["from_actor"] is None and row["to_actor"] is None and row["opened_ts"] is None


def test_a_goal_only_handoff_with_no_issue_gets_its_own_standalone_row(tmp_path, conn):
    """Documented residue (module docstring): fact_handoff has no goal column, so two
    issue-less handoffs cannot be told apart across runs -- each gets its own row rather than
    risking a match against the wrong one."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:00:00Z", "handoff", goal="g1", to="bob"),
        _rec("alice", 2, "2026-01-02T00:00:00Z", "handoff", goal="g2", to="carol"),
    ])
    ingest_ledger(conn, tmp_path)
    rows = _rows(conn, "fact_handoff")
    assert len(rows) == 2
    assert all(r["issue"] is None for r in rows)


# --------------------------------------------------------------------------- reliability_class


def test_reliability_class_1_for_entries_2_for_events_stream(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "claimed", goal="from-entries")],
                    stream="entries")
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-02T00:00:00Z", "phase", goal="from-events")],
                    stream="events")
    ingest_ledger(conn, tmp_path)
    by_goal = {r["goal_id"]: r["reliability_class"] for r in _rows(conn, "fact_event")}
    assert by_goal == {"from-entries": 1, "from-events": 2}


def test_a_query_can_isolate_class_1_from_class_2(tmp_path, conn):
    """The load-bearing spec §3 property, exercised as a query, not just a stored value: a
    consumer asking ONLY for deterministic rows must never see a best-effort one leak in."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "claimed")], stream="entries")
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-02T00:00:00Z", "phase")], stream="events")
    ingest_ledger(conn, tmp_path)
    class_1_only = conn.execute("SELECT kind FROM fact_event WHERE reliability_class = 1").fetchall()
    assert class_1_only == [("claimed",)]


# --------------------------------------------------------------------------- idempotence (required)


def test_ingest_ledger_twice_writes_no_duplicate_ledger_record(tmp_path, conn):
    """The story's own done_when, stated exactly: a second run must not re-ingest a record it
    already stored -- NOT 'identical row counts' (fact_collector_pack is explicitly exempt from
    that framing; this table has no such exemption -- every row here traces to exactly one
    ledger record, so any duplicate here IS a real bug)."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:00:00Z", "claimed", goal="g1"),
        _rec("alice", 2, "2026-01-02T00:00:00Z", "done", goal="g1"),
    ])
    _write_records(sdlc, "bob", [_rec("bob", 1, "2026-01-01T00:00:00Z", "handoff", issue=1, to="alice")])

    first = ingest_ledger(conn, tmp_path)
    assert first == {"events": 2, "handoffs": 1, "skipped": 0}
    second = ingest_ledger(conn, tmp_path)
    assert second == {"events": 0, "handoffs": 0, "skipped": 0}  # nothing new to ingest

    assert conn.execute("SELECT count(*) FROM fact_event").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM fact_handoff").fetchone()[0] == 1


def test_ingesting_a_third_time_after_new_records_only_ingests_the_new_ones(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "claimed", goal="g1")])
    ingest_ledger(conn, tmp_path)
    ingest_ledger(conn, tmp_path)  # idempotent no-op
    _write_records(sdlc, "alice", [_rec("alice", 2, "2026-01-02T00:00:00Z", "done", goal="g1")])
    third = ingest_ledger(conn, tmp_path)
    assert third == {"events": 1, "handoffs": 0, "skipped": 0}
    assert conn.execute("SELECT count(*) FROM fact_event").fetchone()[0] == 2


def test_cursor_is_scoped_per_project_two_projects_do_not_interfere(tmp_path, conn):
    root_a = tmp_path / "repo-a"
    root_b = tmp_path / "repo-b"
    for root in (root_a, root_b):
        _write_records(root / ".sdlc", "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "claimed")])
    ingest_ledger(conn, root_a)
    result_b_first = ingest_ledger(conn, root_b)
    # root_b's actor "alice" seq=1 must NOT read as already-ingested just because root_a's did.
    assert result_b_first == {"events": 1, "handoffs": 0, "skipped": 0}
    assert conn.execute("SELECT count(*) FROM fact_event").fetchone()[0] == 2


# --------------------------------------------------------------------------- interrupt / resume (required)


def test_interrupted_run_resumes_without_gap_or_duplicate(tmp_path, conn, monkeypatch):
    """The story's own done_when: 'a partial run resumes without gaps or duplicates.' Simulates
    a real interruption by making the SQL write itself fail for one specific record (a
    RuntimeError from inside _write_event, not a monkeypatched cursor) -- everything already
    committed before the failure must stay committed; everything from the SAME actor after the
    failure must be deferred (never landed-but-uncursored, which the module's own docstring
    names as the exact gap an earlier revision of this fix had); a second call with the fault
    removed must land every remaining record exactly once."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:00:00Z", "claimed", goal="g1"),
        _rec("alice", 2, "2026-01-02T00:00:00Z", "done", goal="g1"),
        _rec("alice", 3, "2026-01-03T00:00:00Z", "claimed", goal="g2"),
    ])
    _write_records(sdlc, "carol", [
        _rec("carol", 1, "2025-12-01T00:00:00Z", "claimed", goal="g3"),
        _rec("carol", 2, "2025-12-02T00:00:00Z", "done", goal="g3"),
    ])

    real_write_event = ledger_writer._write_event

    def flaky(conn, project_id, record):
        if record.get("goal") == "g1" and record.get("kind") == "done":
            raise RuntimeError("simulated interruption")
        return real_write_event(conn, project_id, record)

    monkeypatch.setattr(ledger_writer, "_write_event", flaky)
    interrupted = ingest_ledger(conn, tmp_path)
    assert interrupted["skipped"] == 1
    # alice's g1/claimed landed; g1/done failed; g2/claimed (a LATER record from the SAME
    # actor) must have been deferred, not written -- otherwise the cursor would leapfrog the
    # failed record and it would silently never be retried (the exact bug this test pins).
    events_mid = {(r["goal_id"], r["kind"]) for r in _rows(conn, "fact_event")}
    assert events_mid == {("g1", "claimed"), ("g3", "claimed"), ("g3", "done")}
    cursor_mid = {r["actor_id"]: r["last_seq"] for r in _rows(conn, "ingest_ledger_cursor")}
    assert cursor_mid == {"alice": 1, "carol": 2}

    monkeypatch.setattr(ledger_writer, "_write_event", real_write_event)
    resumed = ingest_ledger(conn, tmp_path)
    assert resumed == {"events": 2, "handoffs": 0, "skipped": 0}  # exactly the two deferred rows

    counts = conn.execute(
        "SELECT goal_id, kind, count(*) FROM fact_event GROUP BY goal_id, kind ORDER BY 1, 2"
    ).fetchall()
    assert counts == [
        ("g1", "claimed", 1), ("g1", "done", 1), ("g2", "claimed", 1),
        ("g3", "claimed", 1), ("g3", "done", 1),
    ]  # no gap (every record present) and no duplicate (every count is exactly 1)

    assert ingest_ledger(conn, tmp_path) == {"events": 0, "handoffs": 0, "skipped": 0}


def test_a_blocked_actor_does_not_stall_a_different_actor(tmp_path, conn, monkeypatch):
    """The isolation half of the same fix: actor A's failure must not defer actor B's records
    at all, in either the interrupted run or afterward."""
    sdlc = tmp_path / ".sdlc"
    _write_records(sdlc, "alice", [_rec("alice", 1, "2026-01-01T00:00:00Z", "claimed", goal="ga")])
    _write_records(sdlc, "bob", [_rec("bob", 1, "2026-01-01T00:00:00Z", "claimed", goal="gb")])

    real_write_event = ledger_writer._write_event

    def flaky(conn, project_id, record):
        if record.get("actor") == "alice":
            raise RuntimeError("alice always fails")
        return real_write_event(conn, project_id, record)

    monkeypatch.setattr(ledger_writer, "_write_event", flaky)
    result = ingest_ledger(conn, tmp_path)
    assert result == {"events": 1, "handoffs": 0, "skipped": 1}
    rows = _rows(conn, "fact_event")
    assert [r["goal_id"] for r in rows] == ["gb"]  # bob landed; alice did not


# --------------------------------------------------------------------------- never raises


def test_never_raises_when_no_sdlc_or_ledger_directory_exists_at_all(tmp_path, conn):
    result = ingest_ledger(conn, tmp_path)  # no .sdlc/ at all
    assert result == {"events": 0, "handoffs": 0, "skipped": 0}


def test_a_record_missing_its_actor_is_ingested_at_most_once(tmp_path, conn):
    """No actor at all (a hand-corrupted/adversarial ledger line -- ledger_reader.py's own test
    suite keeps this shape, it is not filtered out by the reader). Written on first sight
    (there is no evidence it was already ingested), then never duplicated on a later run --
    see the module docstring's closing paragraph."""
    sdlc = tmp_path / ".sdlc"
    path = _entry_path(sdlc, "noactor")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"id": "x:1", "ts": "2026-01-01T00:00:00Z", "kind": "claimed", "goal": "g1"}) + "\n",
        encoding="utf-8",
    )
    first = ingest_ledger(conn, tmp_path)
    assert first == {"events": 1, "handoffs": 0, "skipped": 0}
    second = ingest_ledger(conn, tmp_path)
    assert second == {"events": 0, "handoffs": 0, "skipped": 0}
    assert conn.execute("SELECT count(*) FROM fact_event").fetchone()[0] == 1


def test_a_record_with_an_unparseable_id_tail_is_ingested_at_most_once(tmp_path, conn):
    sdlc = tmp_path / ".sdlc"
    path = _entry_path(sdlc, "alice")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"id": "alice:notanumber", "ts": "2026-01-01T00:00:00Z", "actor": "alice",
                     "kind": "claimed", "goal": "g1"}) + "\n",
        encoding="utf-8",
    )
    first = ingest_ledger(conn, tmp_path)
    assert first == {"events": 1, "handoffs": 0, "skipped": 0}
    second = ingest_ledger(conn, tmp_path)
    assert second == {"events": 0, "handoffs": 0, "skipped": 0}
    assert conn.execute("SELECT count(*) FROM fact_event").fetchone()[0] == 1
