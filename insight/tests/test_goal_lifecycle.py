# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.goal_lifecycle (issue #217): deriving fact_goal's
claimed_ts/first_done_ts/terminal_ts/outcome by replaying fact_event, gated on
discovery.source == "github", and purging stale github-mode fact_goal rows that no longer
have a matching fact_event. See .sdlc/plans/217.md Decisions 1-4.

Module-level importorskip("duckdb") like test_ledger_writer.py/test_artifact_reader.py:
this is a read+write module's own test file.
"""
import json
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.goal_lifecycle import (  # noqa: E402
    _discovery_source, derive_goals_from_events, ingest_goal_lifecycle,
)
from insight.ingest.artifact_reader import write_goal  # noqa: E402
from insight.ingest.packs import project_id_for  # noqa: E402
from insight.ingest.store import ensure_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _rows(conn, table, order_by=None):
    sql = f"SELECT * FROM {table}" + (f" ORDER BY {order_by}" if order_by else "")
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _event(conn, project_id, goal_id, ts, kind, reliability_class=1):
    conn.execute(
        "INSERT INTO fact_event (project_id, goal_id, ts, actor_id, kind, reliability_class) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [project_id, goal_id, ts, "alice", kind, reliability_class],
    )


def _write_config(sdlc_dir, config):
    sdlc_dir = pathlib.Path(sdlc_dir)
    sdlc_dir.mkdir(parents=True, exist_ok=True)
    (sdlc_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


# --------------------------------------------------------------------------- _discovery_source


def test_discovery_source_defaults_to_local_goals_on_missing_config(tmp_path):
    assert _discovery_source(tmp_path / ".sdlc") == "local-goals"


def test_discovery_source_reads_github(tmp_path):
    sdlc = tmp_path / ".sdlc"
    _write_config(sdlc, {"discovery": {"source": "github"}})
    assert _discovery_source(sdlc) == "github"


def test_discovery_source_malformed_json_degrades(tmp_path):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir(parents=True)
    (sdlc / "config.json").write_text("{not valid", encoding="utf-8")
    assert _discovery_source(sdlc) == "local-goals"


def test_discovery_source_non_dict_discovery_key_degrades(tmp_path):
    sdlc = tmp_path / ".sdlc"
    _write_config(sdlc, {"discovery": "oops"})
    assert _discovery_source(sdlc) == "local-goals"


# --------------------------------------------------------------------------- derive_goals_from_events: lifecycle mapping


def test_derive_done_goal(conn):
    pid = "p1"
    _event(conn, pid, "42", "2026-01-01T00:00:00", "claimed")
    _event(conn, pid, "42", "2026-01-01T01:00:00", "merged")
    _event(conn, pid, "42", "2026-01-01T02:00:00", "done")
    derive_goals_from_events(conn, pid)
    row = _rows(conn, "fact_goal")[0]
    assert row["goal_id"] == "42"
    assert str(row["claimed_ts"]) == "2026-01-01 00:00:00"
    assert str(row["first_done_ts"]) == "2026-01-01 02:00:00"
    assert str(row["terminal_ts"]) == "2026-01-01 02:00:00"
    assert row["outcome"] == "done"


def test_derive_failed_goal(conn):
    pid = "p1"
    _event(conn, pid, "42", "2026-01-01T00:00:00", "claimed")
    _event(conn, pid, "42", "2026-01-01T01:00:00", "failed")
    derive_goals_from_events(conn, pid)
    row = _rows(conn, "fact_goal")[0]
    assert row["outcome"] == "failed"
    assert str(row["terminal_ts"]) == "2026-01-01 01:00:00"


def test_derive_rework_uses_first_claim_not_last(conn):
    """Mirrors goal #105's real shape: six park/reclaim cycles -- claimed_ts must be the
    FIRST claimed event's ts, not the last."""
    pid = "p1"
    _event(conn, pid, "105", "2026-01-01T13:10:40", "claimed")
    _event(conn, pid, "105", "2026-01-01T13:20:18", "parked")
    _event(conn, pid, "105", "2026-01-01T13:23:53", "claimed")
    _event(conn, pid, "105", "2026-01-01T14:00:00", "parked")
    _event(conn, pid, "105", "2026-01-01T22:58:22", "claimed")
    _event(conn, pid, "105", "2026-01-02T00:01:03", "merged")
    _event(conn, pid, "105", "2026-01-02T00:01:21", "done")
    derive_goals_from_events(conn, pid)
    row = _rows(conn, "fact_goal")[0]
    assert str(row["claimed_ts"]) == "2026-01-01 13:10:40"


def test_derive_two_done_events_terminal_ts_is_the_later_one(conn):
    """Mirrors goal #124's real shape: two `done` events -- terminal_ts is the LATER one
    (arg_max), first_done_ts is the EARLIER one (independent columns, Decision 1)."""
    pid = "p1"
    _event(conn, pid, "124", "2026-01-01T00:00:00", "claimed")
    _event(conn, pid, "124", "2026-01-01T01:00:00", "merged")
    _event(conn, pid, "124", "2026-01-01T02:00:00", "done")
    _event(conn, pid, "124", "2026-01-01T03:00:00", "done")
    derive_goals_from_events(conn, pid)
    row = _rows(conn, "fact_goal")[0]
    assert str(row["terminal_ts"]) == "2026-01-01 03:00:00"
    assert str(row["first_done_ts"]) == "2026-01-01 02:00:00"
    assert row["outcome"] == "done"


def test_derive_still_open_goal_gets_null_outcome(conn):
    """Mirrors goal #134: claimed only, never terminal."""
    pid = "p1"
    _event(conn, pid, "134", "2026-01-01T00:00:00", "claimed")
    derive_goals_from_events(conn, pid)
    row = _rows(conn, "fact_goal")[0]
    assert row["claimed_ts"] is not None
    assert row["first_done_ts"] is None
    assert row["terminal_ts"] is None
    assert row["outcome"] is None


def test_derive_done_with_no_claimed_event_leaves_claimed_ts_null(conn):
    """Mirrors goals #165/#195: merged, done -- no claimed event at all. claimed_ts must be
    NULL (not fabricated), and metric_2's own claimed_ts IS NOT NULL guard must exclude this
    goal without raising."""
    pid = "p1"
    _event(conn, pid, "165", "2026-01-01T00:00:00", "merged")
    _event(conn, pid, "165", "2026-01-01T01:00:00", "done")
    derive_goals_from_events(conn, pid)
    row = _rows(conn, "fact_goal")[0]
    assert row["claimed_ts"] is None
    assert row["outcome"] == "done"
    assert row["terminal_ts"] is not None

    from insight.metrics.loader import load_metrics
    load_metrics(conn)
    metric_2_rows = conn.execute("SELECT * FROM metric_2").fetchall()
    goal_ids = [r[0] for r in metric_2_rows]
    assert "165" not in goal_ids


def test_derive_excludes_reliability_class_2_rows(conn):
    pid = "p1"
    _event(conn, pid, "99", "2026-01-01T00:00:00", "done", reliability_class=2)
    derive_goals_from_events(conn, pid)
    rows = _rows(conn, "fact_goal")
    assert rows == []


# --------------------------------------------------------------------------- stale-row purge / column ownership (Decision 4)


def test_derive_purges_stale_row_not_in_fact_event(tmp_path, conn):
    pid = project_id_for(tmp_path)
    write_goal(conn, pid, {
        "goal_id": "0001", "title": "A goal", "lane": None, "source": None,
        "status": None, "verify_command": None,
        "done_when_present": False, "plan_artifact_present": False,
    })
    _event(conn, pid, "42", "2026-01-01T00:00:00", "claimed")
    _event(conn, pid, "42", "2026-01-01T01:00:00", "done")
    _write_config(tmp_path / ".sdlc", {"discovery": {"source": "github"}})

    ingest_goal_lifecycle(conn, tmp_path)

    goal_ids = {r["goal_id"] for r in _rows(conn, "fact_goal")}
    assert "0001" not in goal_ids
    assert "42" in goal_ids


def test_derive_never_touches_columns_it_does_not_own(conn):
    pid = "p1"
    write_goal(conn, pid, {
        "goal_id": "42", "title": "Real title", "lane": "small", "source": None,
        "status": None, "verify_command": None,
        "done_when_present": False, "plan_artifact_present": False,
    })
    _event(conn, pid, "42", "2026-01-01T00:00:00", "claimed")
    _event(conn, pid, "42", "2026-01-01T01:00:00", "done")

    derive_goals_from_events(conn, pid)

    row = _rows(conn, "fact_goal")[0]
    assert row["title"] == "Real title"
    assert row["lane"] == "small"
    assert row["claimed_ts"] is not None
    assert row["outcome"] == "done"


def test_ingest_goal_lifecycle_is_a_noop_in_local_mode(tmp_path, conn):
    pid = project_id_for(tmp_path)
    write_goal(conn, pid, {
        "goal_id": "0001", "title": "A goal", "lane": None, "source": None,
        "status": None, "verify_command": None,
        "done_when_present": False, "plan_artifact_present": False,
    })
    _event(conn, pid, "42", "2026-01-01T00:00:00", "claimed")
    _event(conn, pid, "42", "2026-01-01T01:00:00", "done")
    # no config.json at all -- defaults to local-goals

    before = _rows(conn, "fact_goal", order_by="goal_id")
    result = ingest_goal_lifecycle(conn, tmp_path)
    after = _rows(conn, "fact_goal", order_by="goal_id")

    assert result == {"goals": 0, "purged": 0}
    assert before == after


# --------------------------------------------------------------------------- coverage gaps closed
# after PR review MUTATION-TESTED the two claims below and found both invisible: deleting the
# purge's own `reliability_class = 1` filter, and flipping arg_max to arg_min, each left the whole
# suite green. The shipped SQL was correct both times -- these pin it so a future edit cannot
# weaken it silently. (Issue #217 review, findings 1 and 2.)

def test_purge_ignores_a_reliability_class_2_only_goal(tmp_path, conn):
    """The purge's subquery carries its own `reliability_class = 1` filter, separate from the
    upsert's. A goal whose ONLY events are class-2 (agent-emitted, best-effort) must not count as
    "backed by fact_event" -- otherwise a stale local row survives on the strength of evidence the
    upsert itself refuses to derive from, and fact_goal keeps a row no metric can trust."""
    pid = project_id_for(tmp_path)
    write_goal(conn, pid, {
        "goal_id": "0001", "title": "stale local", "lane": None, "source": None,
        "status": None, "verify_command": None,
        "done_when_present": False, "plan_artifact_present": False,
    })
    _event(conn, pid, "0001", "2026-01-01T00:00:00", "claimed", reliability_class=2)
    _event(conn, pid, "0001", "2026-01-01T01:00:00", "done", reliability_class=2)
    _write_config(tmp_path / ".sdlc", {"discovery": {"source": "github"}})

    ingest_goal_lifecycle(conn, tmp_path)

    assert not [r for r in _rows(conn, "fact_goal") if r["goal_id"] == "0001"], (
        "a class-2-only goal must not shield a stale row from the purge"
    )


@pytest.mark.parametrize("first,second,expected", [
    ("done", "failed", "failed"),
    ("failed", "done", "done"),
])
def test_derive_terminal_outcome_follows_the_LATER_event_not_the_first(conn, first, second,
                                                                      expected):
    """arg_max(kind, ts) means the LATEST terminal event wins. The pre-existing two-`done` test
    cannot detect a wrong direction -- both values are "done" either way, which is exactly why
    flipping arg_max to arg_min passed it. Two DIFFERENT terminal kinds are the only shape that
    distinguishes them."""
    pid = "p1"
    _event(conn, pid, "900", "2026-01-01T00:00:00", "claimed")
    _event(conn, pid, "900", "2026-01-01T01:00:00", first)
    _event(conn, pid, "900", "2026-01-01T02:00:00", second)
    derive_goals_from_events(conn, pid)
    row = [r for r in _rows(conn, "fact_goal") if r["goal_id"] == "900"][0]
    assert row["outcome"] == expected
    assert str(row["terminal_ts"]) == "2026-01-01 02:00:00"
