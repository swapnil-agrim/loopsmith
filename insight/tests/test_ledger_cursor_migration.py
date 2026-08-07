# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the one-shot ingest_ledger_cursor key widening (loopsmith#380): the
`(project_id, actor_id)` -> `(project_id, actor_id, writer_id, stream)` conversion in
insight.ingest.store.ensure_schema, and the lazy, project-scoped rebuild
insight.ingest.ledger_writer.ingest_ledger performs for a project the conversion marked.

ONE FILE, not two, because every test here needs the SAME `_old_shape_store` fixture and the
migration deliberately spans both modules -- ensure_schema does only the structural half
(non-destructive, store-wide) and ingest_ledger does the per-project rebuild. Splitting these
across test_store.py and test_ledger_writer.py would mean two hand-written copies of the
pre-#380 schema, and a migration test whose fixture has drifted from the real old shape tests
nothing at all.

REAL DUCKDB, real on-disk JSONL, no mocks -- same rule as test_ledger_writer.py's own header.
"""
import json
import pathlib
import shutil

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest import ledger_writer  # noqa: E402
from insight.ingest.ledger_writer import ingest_ledger  # noqa: E402
from insight.ingest.packs import project_id_for  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402

#: The pre-#380 cursor table, transcribed verbatim from store.py as it stood at the commit this
#: migration converts. Hand-written on purpose: today's ensure_schema can no longer produce an
#: old-shape store, so there is no way to obtain one except to build it, and a migration tested
#: only against the shape it already produces tests nothing.
_PRE_380_CURSOR_DDL = """
    CREATE TABLE ingest_ledger_cursor (
        project_id VARCHAR,
        actor_id VARCHAR,
        last_seq BIGINT,
        PRIMARY KEY (project_id, actor_id)
    )
"""


def _old_shape_store(path):
    """An open connection to a store exactly as a pre-#380 deployment left it: every other table
    at its current shape (they are unchanged by this story), `ingest_ledger_cursor` back at its
    3-column/2-column-PK shape, and no carrier table at all."""
    conn = duckdb.connect(str(path))
    ensure_schema(conn)
    conn.execute("DROP TABLE ingest_ledger_cursor")
    conn.execute("DROP TABLE ingest_ledger_cursor_legacy")
    conn.execute(_PRE_380_CURSOR_DDL)
    return conn


def _rec(actor, seq, ts, kind, goal="g1", **fields):
    out = {"id": "%s:%d" % (actor, seq), "ts": ts, "actor": actor, "kind": kind, "goal": goal}
    out.update(fields)
    return out


def _write_records(sdlc_dir, actor, records, stream="entries"):
    path = pathlib.Path(sdlc_dir) / "ledger" / stream / ("%s.jsonl" % actor)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _rows(conn, table, order_by=None):
    sql = "SELECT * FROM %s" % table + (" ORDER BY %s" % order_by if order_by else "")
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _seed_ledger(project_root):
    """A ledger carrying both fact tables' shapes: lifecycle records for fact_event, and a
    handoff answered by TWO acks for fact_handoff -- the merge-y table where a rebuild bug would
    actually hide, since one row absorbs the handoff and every later ack, last-write-wins."""
    sdlc = pathlib.Path(project_root) / ".sdlc"
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:00:00Z", "claimed", goal="g1"),
        _rec("alice", 2, "2026-01-01T00:01:00Z", "done", goal="g1"),
        _rec("alice", 3, "2026-01-01T00:02:00Z", "handoff", goal="g2", to="bob", area="ingest",
             issue=42, priority="P1"),
    ])
    _write_records(sdlc, "bob", [
        _rec("bob", 1, "2026-01-01T00:03:00Z", "ack", issue=42, state="accepted"),
        _rec("bob", 2, "2026-01-01T00:04:00Z", "ack", issue=42, state="resolved"),
    ])
    _write_records(sdlc, "alice", [
        _rec("alice", 1, "2026-01-01T00:05:00Z", "phase", goal="g2"),
    ], stream="events")
    return sdlc


def _seed_stale_facts(conn, project_id, actor="alice", last_seq=3):
    """What a pre-#380 run left behind: some fact rows, some cursor rows. Deliberately NOT a
    faithful subset of the ledger -- one bogus row is planted so "the stale rows are GONE, not
    doubled" is a real assertion rather than a row-count coincidence."""
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES (?, 'stale-goal', NULL, ?, 'claimed', 1)", [project_id, actor])
    conn.execute(
        "INSERT INTO fact_handoff (project_id, from_actor, issue) VALUES (?, ?, 999)",
        [project_id, actor])
    conn.execute("INSERT INTO ingest_ledger_cursor VALUES (?, ?, ?)",
                 [project_id, actor, last_seq])


def _pk_columns(conn, table):
    row = conn.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'", [table]).fetchone()
    return list(row[0]) if row else []


def _columns(conn, table):
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
        "ORDER BY ordinal_position", [table]).fetchall()]


# --------------------------------------------------------------------------- the structural half


def test_ensure_schema_converts_an_old_shape_cursor_table_and_preserves_the_old_watermarks(tmp_path):
    """T8. The conversion itself: the cursor table comes out at the new shape and EMPTY (its old
    values cannot be decomposed into per-(writer, stream) watermarks, so they are not carried
    forward as data), and every old row lands verbatim in the carrier -- which is simultaneously
    the per-project "this project's facts predate the new key" marker and the only audit trail
    anyone will want if a rebuild ever needs debugging."""
    conn = _old_shape_store(tmp_path / "s.duckdb")
    conn.execute("INSERT INTO ingest_ledger_cursor VALUES ('p1', 'alice', 5), ('p1', 'bob', 2), "
                 "('p2', 'alice', 9)")

    ensure_schema(conn)

    assert _columns(conn, "ingest_ledger_cursor") == [
        "project_id", "actor_id", "writer_id", "stream", "last_seq"]
    assert _pk_columns(conn, "ingest_ledger_cursor") == [
        "project_id", "actor_id", "writer_id", "stream"]
    assert conn.execute("SELECT count(*) FROM ingest_ledger_cursor").fetchone()[0] == 0
    assert sorted(conn.execute(
        "SELECT project_id, actor_id, last_seq FROM ingest_ledger_cursor_legacy").fetchall()) == [
        ("p1", "alice", 5), ("p1", "bob", 2), ("p2", "alice", 9)]
    conn.close()


def test_the_migration_fires_at_most_once(tmp_path):
    """T9. `writer_id NOT IN columns(ingest_ledger_cursor)` is self-extinguishing: once the
    conversion has run the column exists, so a re-run cannot re-copy the carrier (which would
    re-arm a marker a completed rebuild had already consumed) or empty a live cursor."""
    conn = _old_shape_store(tmp_path / "s.duckdb")
    conn.execute("INSERT INTO ingest_ledger_cursor VALUES ('p1', 'alice', 5)")
    ensure_schema(conn)
    conn.execute("INSERT INTO ingest_ledger_cursor VALUES ('p1', 'alice', 'alice', 'entries', 7)")

    for _ in range(3):
        ensure_schema(conn)

    assert conn.execute("SELECT count(*) FROM ingest_ledger_cursor_legacy").fetchone()[0] == 1
    assert conn.execute(
        "SELECT writer_id, stream, last_seq FROM ingest_ledger_cursor").fetchall() == [
        ("alice", "entries", 7)]
    conn.close()


def test_ensure_schema_alone_never_touches_fact_rows(tmp_path):
    """T15. The whole reason the wipe lives in ingest_ledger and NOT in ensure_schema: every
    read-intent command (`insight gaps`, `dash`, `ic`, `manager`, `panel`, `leadership`,
    `cross-functional`) opens the store through open_store -> ensure_schema, so a destructive
    ensure_schema would delete a multi-repo store's ledger facts on a command that only meant to
    render them. Nothing asserted this before; it is the claim a future edit is likeliest to break."""
    conn = _old_shape_store(tmp_path / "s.duckdb")
    _seed_stale_facts(conn, "p1")
    _seed_stale_facts(conn, "p2", actor="bob", last_seq=4)
    before_events = _rows(conn, "fact_event", order_by="project_id")
    before_handoffs = _rows(conn, "fact_handoff", order_by="project_id")

    ensure_schema(conn)

    assert _rows(conn, "fact_event", order_by="project_id") == before_events
    assert _rows(conn, "fact_handoff", order_by="project_id") == before_handoffs
    conn.close()


# --------------------------------------------------------------------------- the rebuild half


def test_a_migrated_project_is_wiped_and_fully_reingested_with_no_duplicate_rows(tmp_path):
    """T10 + T17. The issue's own acceptance criterion -- "existing cursor rows survive the
    migration without ... causing duplicate rows" -- proved the strongest way available: the
    migrated store's rebuilt rows must equal, exactly, what a FRESH store ingesting the same
    ledger produces. That covers fact_handoff too, where the handoff and both of its acks merge
    into one last-write-wins row and a rebuild bug would be invisible in a row count."""
    root = tmp_path / "repo"
    _seed_ledger(root)
    project_id = project_id_for(root)

    reference = duckdb.connect(str(tmp_path / "reference.duckdb"))
    ensure_schema(reference)
    ingest_ledger(reference, root)
    expected_events = _rows(reference, "fact_event", order_by="ts, goal_id, kind")
    expected_handoffs = _rows(reference, "fact_handoff", order_by="issue")
    # 3 lifecycle records -> 3 fact_event rows; 1 handoff + 2 acks -> ONE merged fact_handoff row.
    assert len(expected_events) == 3 and len(expected_handoffs) == 1
    assert expected_handoffs[0]["ack_state"] == "resolved"        # the LATEST ack won
    assert expected_handoffs[0]["settled_ts"] is not None         # ...and it is a settling state
    reference.close()

    conn = _old_shape_store(tmp_path / "migrated.duckdb")
    _seed_stale_facts(conn, project_id)
    ensure_schema(conn)

    ingest_ledger(conn, root)

    assert _rows(conn, "fact_event", order_by="ts, goal_id, kind") == expected_events
    assert _rows(conn, "fact_handoff", order_by="issue") == expected_handoffs
    assert conn.execute("SELECT count(*) FROM ingest_ledger_cursor_legacy").fetchone()[0] == 0

    assert ingest_ledger(conn, root) == {"events": 0, "handoffs": 0, "skipped": 0}
    assert _rows(conn, "fact_event", order_by="ts, goal_id, kind") == expected_events
    assert _rows(conn, "fact_handoff", order_by="issue") == expected_handoffs
    conn.close()


def test_the_rebuild_is_scoped_to_the_project_being_ingested(tmp_path):
    """T11. ensure_schema's conversion is store-WIDE (it moves every project's rows into the
    carrier), so a `--repos <glob>` store can hold markers for projects this run's glob never
    covers. Their rows must survive untouched and their marker must stay armed until they are
    themselves ingested -- which is what makes a global `DELETE FROM fact_event` unacceptable."""
    root_a = tmp_path / "repo-a"
    _seed_ledger(root_a)
    pid_a, pid_b = project_id_for(root_a), project_id_for(tmp_path / "repo-b")

    conn = _old_shape_store(tmp_path / "s.duckdb")
    _seed_stale_facts(conn, pid_a)
    _seed_stale_facts(conn, pid_b, actor="bob", last_seq=4)
    ensure_schema(conn)
    b_events_before = [r for r in _rows(conn, "fact_event") if r["project_id"] == pid_b]

    ingest_ledger(conn, root_a)

    assert [r for r in _rows(conn, "fact_event") if r["project_id"] == pid_b] == b_events_before
    assert conn.execute(
        "SELECT project_id FROM ingest_ledger_cursor_legacy").fetchall() == [(pid_b,)]
    assert "stale-goal" not in {r["goal_id"] for r in _rows(conn, "fact_event")
                                if r["project_id"] == pid_a}
    conn.close()


def test_a_fresh_store_never_rebuilds_anything(tmp_path):
    """T12. The trigger is the carrier row, never "the cursor is empty" -- a fresh store's cursor
    is empty too, and treating that as a rebuild signal would delete rows on the very first
    ingest of a store some other writer had already put fact_event rows into."""
    root = tmp_path / "repo"
    _seed_ledger(root)
    project_id = project_id_for(root)

    conn = duckdb.connect(str(tmp_path / "fresh.duckdb"))
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, kind) VALUES (?, 'not-from-the-ledger', 'note')",
        [project_id])

    ingest_ledger(conn, root)

    assert "not-from-the-ledger" in {r["goal_id"] for r in _rows(conn, "fact_event")}
    conn.close()


def test_a_failed_rebuild_does_not_ingest_and_leaves_the_marker_for_the_next_run(tmp_path):
    """T13. The single most dangerous line in the change: falling through to ingest after a wipe
    that did not commit is exactly how the duplicate rows this whole migration exists to prevent
    would be created. On failure the run returns all-zeros and the marker survives, so the next
    run retries the rebuild from an untouched store."""
    root = tmp_path / "repo"
    _seed_ledger(root)
    project_id = project_id_for(root)

    conn = _old_shape_store(tmp_path / "s.duckdb")
    _seed_stale_facts(conn, project_id)
    ensure_schema(conn)
    before = _rows(conn, "fact_event")

    with pytest.MonkeyPatch.context() as mp:
        def boom(*_args, **_kwargs):
            raise RuntimeError("the wipe transaction could not commit")
        mp.setattr(ledger_writer, "_rebuild_migrated_project", boom)
        assert ingest_ledger(conn, root) == {"events": 0, "handoffs": 0, "skipped": 0}

    assert _rows(conn, "fact_event") == before
    assert conn.execute(
        "SELECT project_id FROM ingest_ledger_cursor_legacy").fetchall() == [(project_id,)]
    conn.close()


def test_a_migrated_project_whose_ledger_is_gone_is_not_wiped(tmp_path):
    """T14, and the most important test in this file. `.sdlc/ledger/` is a WORKTREE of an ops
    branch, so it is routinely absent -- never bootstrapped, pruned, re-cloned, or a
    `--repos <glob>`/`--db <central-path>` run over repos that never had one. When it is missing
    while `.sdlc/` is present, read_all_with_reliability returns [] and the repo still counts as
    adopted, so ingest_ledger still runs.

    Without the `records` guard the marked project is wiped, the marker is consumed, the refill
    finds nothing, and NO future run ever retries -- wipe-and-reingest silently degrades to wipe.
    Every other guard in the design is intact in that scenario (the wipe is transactional,
    project-scoped and lazy) and the data is gone anyway, because "recomputable from the ledger
    JSONL source of truth" assumes the source is PRESENT.

    A project holding a marker had cursor rows by definition, so an empty read means the source
    is missing, never that there is nothing to rebuild. Leaving the marker armed is correct and
    self-healing: the moment the ledger reappears the rebuild fires normally."""
    root = tmp_path / "repo"
    _seed_ledger(root)
    project_id = project_id_for(root)

    conn = _old_shape_store(tmp_path / "s.duckdb")
    _seed_stale_facts(conn, project_id)
    ensure_schema(conn)
    before_events = _rows(conn, "fact_event")
    before_handoffs = _rows(conn, "fact_handoff")

    ledger_dir = root / ".sdlc" / "ledger"
    stashed = tmp_path / "detached-ledger-worktree"
    shutil.move(str(ledger_dir), str(stashed))     # the worktree goes away, exactly as it does
    assert (root / ".sdlc").is_dir()               # still adopted; only the ledger is gone

    assert ingest_ledger(conn, root) == {"events": 0, "handoffs": 0, "skipped": 0}

    assert _rows(conn, "fact_event") == before_events
    assert _rows(conn, "fact_handoff") == before_handoffs
    assert conn.execute(
        "SELECT project_id FROM ingest_ledger_cursor_legacy").fetchall() == [(project_id,)]

    # ...and the moment the ledger is back, the rebuild it deferred fires normally.
    shutil.move(str(stashed), str(ledger_dir))
    assert ingest_ledger(conn, root) == {"events": 3, "handoffs": 3, "skipped": 0}
    assert "stale-goal" not in {r["goal_id"] for r in _rows(conn, "fact_event")}
    assert [r["issue"] for r in _rows(conn, "fact_handoff")] == [42]   # the stale 999 row is gone
    assert conn.execute("SELECT count(*) FROM ingest_ledger_cursor_legacy").fetchone()[0] == 0
    conn.close()
