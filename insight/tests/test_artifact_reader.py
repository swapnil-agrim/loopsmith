# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.artifact_reader (issue #102). Module-level importorskip like
test_packs.py: most tests need a real conn fixture, and this file's small number of pure-read
tests are cheap enough that gating the whole file on duckdb (rather than splitting into a
duckdb-free companion file) matches the established #100 precedent for a read+write module."""
import pathlib

import pytest

duckdb = pytest.importorskip("duckdb")

from insight.ingest.artifact_reader import (  # noqa: E402
    discover_goal_files, goal_record, ingest_artifacts, parse_frontmatter, project_id_for,
    read_config_snapshot, read_goal_file, read_slices, write_goal, write_project_snapshot,
    write_slices,
)
from insight.ingest.store import ensure_schema  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    ensure_schema(c)
    yield c
    c.close()


def _goal(sdlc_dir, name, body):
    goals = pathlib.Path(sdlc_dir) / "goals"
    goals.mkdir(parents=True, exist_ok=True)
    (goals / name).write_text(body, encoding="utf-8")


# --------------------------------------------------------------------------- parse_frontmatter / read_goal_file


def test_parse_frontmatter_returns_only_keys_that_appeared():
    fm = parse_frontmatter('---\nid: 1\ntitle: "Quoted"\nlane: small\n---\nbody\n')
    assert fm == {"id": "1", "title": "Quoted", "lane": "small"}


def test_parse_frontmatter_no_fence_returns_empty_dict():
    assert parse_frontmatter("no frontmatter here\n") == {}


def test_read_goal_file_missing_returns_none_not_raise(tmp_path):
    assert read_goal_file(tmp_path / "nope.md") is None


def test_read_goal_file_bom_and_invalid_utf8_do_not_crash(tmp_path):
    p = tmp_path / "g.md"
    p.write_bytes(b"\xef\xbb\xbf---\nid: 1\ntitle: caf\xe9 (invalid utf8)\n---\nbody\n")
    fm = read_goal_file(p)  # must not raise
    assert fm["id"] == "1"


# --------------------------------------------------------------------------- discover_goal_files

def test_discover_goal_files_missing_dir_returns_empty(tmp_path):
    assert discover_goal_files(tmp_path) == []


def test_discover_goal_files_sorted(tmp_path):
    _goal(tmp_path, "0002.md", "---\nid: 2\n---\n")
    _goal(tmp_path, "0001.md", "---\nid: 1\n---\n")
    names = [p.name for p in discover_goal_files(tmp_path)]
    assert names == ["0001.md", "0002.md"]


# --------------------------------------------------------------------------- goal_record: the done_when test

def test_goal_missing_done_when_is_recorded_as_absent_not_defaulted(tmp_path):
    """The issue's own done_when, literally: a goal with no done_when line must record
    done_when_present = False, never True (a truthiness bug) and never silently omitted."""
    _goal(tmp_path, "g.md", "---\nid: g1\ntitle: No done_when here\nlane: small\n---\nbody\n")
    record = goal_record(tmp_path, tmp_path / "goals" / "g.md")
    assert record["done_when_present"] is False


def test_goal_with_empty_done_when_is_present_not_absent(tmp_path):
    """The other half of the ABSENCE GOTCHA: done_when: "" is PRESENT (empty), distinct from
    no done_when: line at all -- bool(fm.get(...)) would wrongly conflate the two."""
    _goal(tmp_path, "g.md", '---\nid: g1\ndone_when: ""\n---\nbody\n')
    record = goal_record(tmp_path, tmp_path / "goals" / "g.md")
    assert record["done_when_present"] is True


def test_goal_record_missing_id_falls_back_to_file_stem(tmp_path):
    _goal(tmp_path, "0007-no-id.md", "---\ntitle: No id line\n---\nbody\n")
    record = goal_record(tmp_path, tmp_path / "goals" / "0007-no-id.md")
    assert record["goal_id"] == "0007-no-id"


def test_goal_record_status_source_verify_command_absent_are_none(tmp_path):
    _goal(tmp_path, "g.md", "---\nid: g1\ntitle: T\nlane: small\ndone_when: x\n---\n")
    record = goal_record(tmp_path, tmp_path / "goals" / "g.md")
    assert record["status"] is None
    assert record["source"] is None
    assert record["verify_command"] is None


def test_goal_record_status_source_verify_command_present_pass_through(tmp_path):
    _goal(tmp_path, "g.md",
          "---\nid: g1\nstatus: pending\nsource: discovery\n"
          "verify_command: pytest -q\n---\n")
    record = goal_record(tmp_path, tmp_path / "goals" / "g.md")
    assert record["status"] == "pending"
    assert record["source"] == "discovery"
    assert record["verify_command"] == "pytest -q"


def test_goal_record_plan_artifact_present_true_when_plan_md_exists(tmp_path):
    _goal(tmp_path, "0009-x.md", "---\nid: g1\n---\n")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "0009-x.md").write_text("# plan\n", encoding="utf-8")
    record = goal_record(tmp_path, tmp_path / "goals" / "0009-x.md")
    assert record["plan_artifact_present"] is True


def test_goal_record_plan_artifact_present_false_when_no_plan(tmp_path):
    _goal(tmp_path, "0009-x.md", "---\nid: g1\n---\n")
    record = goal_record(tmp_path, tmp_path / "goals" / "0009-x.md")
    assert record["plan_artifact_present"] is False


def test_goal_record_unreadable_file_returns_none(tmp_path):
    assert goal_record(tmp_path, tmp_path / "goals" / "nope.md") is None


# --------------------------------------------------------------------------- read_slices

def test_read_slices_absent_file_returns_empty_list(tmp_path):
    assert read_slices(tmp_path, "no-such-goal") == []


def test_read_slices_malformed_json_returns_empty_not_raise(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "g.slices.json").write_text("not json", encoding="utf-8")
    assert read_slices(tmp_path, "g") == []  # must not raise, unlike slices.py's load()


def test_read_slices_non_list_top_level_returns_empty_not_raise(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "g.slices.json").write_text('{"not": "a list"}', encoding="utf-8")
    assert read_slices(tmp_path, "g") == []


def test_read_slices_normalises_defaults(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "g.slices.json").write_text(
        '[{"id": "s1", "title": "T", "files": "a.py"}]', encoding="utf-8"
    )
    slices = read_slices(tmp_path, "g")
    assert slices == [{"id": "s1", "title": "T", "size": "small", "status": "pending",
                        "needs": [], "files": ["a.py"]}]


def test_read_slices_duplicate_ids_keep_last_occurrence(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "g.slices.json").write_text(
        '[{"id": "s1", "title": "first"}, {"id": "s1", "title": "second"}]', encoding="utf-8"
    )
    slices = read_slices(tmp_path, "g")
    assert len(slices) == 1
    assert slices[0]["title"] == "second"


def test_read_slices_missing_ids_all_land_with_distinct_synthetic_ids(tmp_path):
    """Round one of the bug a plan-reviewer caught: keying dedup on `id` unconditionally
    normalises every id-less slice to the same "" key and silently drops all but the last.
    Three declared slices with no `id` at all must all still land, AND (round two, caught on
    re-review) each needs a DISTINCT non-empty id -- storing them all as "" would pass THIS
    test's row count but still collide on fact_slice's PRIMARY KEY downstream, in write_slices.
    See .sdlc/plans/102.md §I; test_ingest_artifacts_id_less_slices_write_through_without_raising
    below is what actually pins the write-layer half of this."""
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "g.slices.json").write_text(
        '[{"title": "a"}, {"title": "b"}, {"title": "c"}]', encoding="utf-8"
    )
    slices = read_slices(tmp_path, "g")
    assert len(slices) == 3
    assert [s["title"] for s in slices] == ["a", "b", "c"]
    ids = [s["id"] for s in slices]
    assert all(sid for sid in ids), "every id-less slice must get a non-empty synthetic id"
    assert len(set(ids)) == 3, "synthetic ids must be distinct, not all collapsed to one value"


def test_read_slices_real_duplicate_id_still_dedupes_alongside_id_less_ones(tmp_path):
    """Mixed manifest: a genuine duplicate id is still collapsed (last wins, in place), while
    id-less entries in the same manifest are each kept with distinct synthetic ids -- the two
    rules must not interfere."""
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "g.slices.json").write_text(
        '[{"id": "s1", "title": "A"}, {"title": "no-id-1"}, '
        '{"id": "s1", "title": "A-updated"}, {"title": "no-id-2"}]',
        encoding="utf-8",
    )
    slices = read_slices(tmp_path, "g")
    assert [(s["id"], s["title"]) for s in slices] == [
        ("s1", "A-updated"), ("_pos1", "no-id-1"), ("_pos3", "no-id-2"),
    ]


# --------------------------------------------------------------------------- read_config_snapshot

def test_read_config_snapshot_absent_returns_none(tmp_path):
    assert read_config_snapshot(tmp_path) is None


def test_read_config_snapshot_malformed_returns_none(tmp_path):
    (tmp_path / "config.json").write_text("{not valid", encoding="utf-8")
    assert read_config_snapshot(tmp_path) is None


def test_read_config_snapshot_valid_returns_raw_text_verbatim(tmp_path):
    raw = '{\n  "mode": {"default": "goal"}\n}\n'
    (tmp_path / "config.json").write_text(raw, encoding="utf-8")
    assert read_config_snapshot(tmp_path) == raw


# --------------------------------------------------------------------------- write layer

def test_write_project_snapshot_first_seen_set_once_last_seen_updates(conn):
    write_project_snapshot(conn, "p1", '{"a": 1}')
    first = conn.execute("select first_seen, last_seen, config_json from dim_project").fetchone()
    write_project_snapshot(conn, "p1", '{"a": 2}')
    second = conn.execute("select first_seen, last_seen, config_json from dim_project").fetchone()
    assert second[0] == first[0]          # first_seen untouched
    assert second[2] == '{"a": 2}'        # config_json updated
    count = conn.execute("select count(*) from dim_project").fetchone()[0]
    assert count == 1                     # upserted, not duplicated


def test_write_project_snapshot_defaults_match_pre_106_behaviour(conn):
    write_project_snapshot(conn, "p1", '{"x":1}')
    row = conn.execute(
        "select config_json, repo, remote_url_sha256, adopted, skip_reason "
        "from dim_project where project_id = 'p1'"
    ).fetchone()
    assert row == ('{"x":1}', None, None, True, None)


def test_write_project_snapshot_persists_repo_and_remote_url_sha256(conn):
    write_project_snapshot(conn, "p1", "{}", repo="github.com/o/r", remote_url_sha256="abc123")
    row = conn.execute(
        "select repo, remote_url_sha256 from dim_project where project_id = 'p1'"
    ).fetchone()
    assert row == ("github.com/o/r", "abc123")


def test_write_project_snapshot_records_a_skip(conn):
    write_project_snapshot(conn, "p1", None, repo="github.com/o/r",
                            remote_url_sha256="abc123", adopted=False, skip_reason="no_sdlc")
    row = conn.execute(
        "select config_json, adopted, skip_reason from dim_project where project_id = 'p1'"
    ).fetchone()
    assert row == (None, False, "no_sdlc")


def test_write_project_snapshot_heals_a_previously_skipped_project(conn):
    """A repo that was skipped, then later adopted, must show adopted=True/skip_reason=None on
    its NEXT ingest -- the upsert overwrites both, it does not merely add to them."""
    write_project_snapshot(conn, "p1", None, adopted=False, skip_reason="no_sdlc")
    write_project_snapshot(conn, "p1", "{}", adopted=True, skip_reason=None)
    row = conn.execute(
        "select config_json, adopted, skip_reason from dim_project where project_id = 'p1'"
    ).fetchone()
    assert row == ("{}", True, None)


def test_ingest_artifacts_populates_repo_and_remote_url_sha256(tmp_path, conn):
    """ingest_artifacts (the adopted path) now opportunistically populates dim_project.repo /
    .remote_url_sha256 via packs.remote_identity_for -- closes dossier BR-6."""
    import subprocess
    from insight.ingest.artifact_reader import ingest_artifacts
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:o/r.git"],
                    cwd=tmp_path, check=True)
    (tmp_path / ".sdlc").mkdir()
    ingest_artifacts(conn, tmp_path)
    row = conn.execute("select repo, remote_url_sha256 from dim_project").fetchone()
    # Non-blocking fix from PR review: the previous `row == (..., None) or row[0] == ...` had a
    # first disjunct that could never be true (the very next line requires row[1] non-None) --
    # dead drafting logic, simplified to the one assertion it was actually checking.
    assert row[0] == "github.com/o/r"
    assert row[1] is not None and len(row[1]) == 64  # sha is a real 64-hex value


def test_write_goal_upsert_preserves_other_story_owned_columns(conn):
    record = {"goal_id": "g1", "title": "T1", "lane": "small", "source": None,
              "status": "pending", "verify_command": None,
              "done_when_present": True, "plan_artifact_present": False}
    write_goal(conn, "p1", record)
    conn.execute("update fact_goal set outcome = 'done', pr = 42 where goal_id = 'g1'")

    record2 = dict(record, title="T1 renamed", status="done")
    write_goal(conn, "p1", record2)

    row = conn.execute(
        "select title, status, outcome, pr from fact_goal where goal_id = 'g1'"
    ).fetchone()
    assert row == ("T1 renamed", "done", "done", 42)  # outcome/pr survived the re-ingest
    count = conn.execute("select count(*) from fact_goal").fetchone()[0]
    assert count == 1


def test_write_slices_deletes_rows_removed_from_the_manifest(conn):
    write_slices(conn, "p1", "g1", [
        {"id": "s1", "title": "A", "size": "small", "status": "pending", "needs": [], "files": []},
        {"id": "s2", "title": "B", "size": "small", "status": "pending", "needs": [], "files": []},
    ])
    assert conn.execute("select count(*) from fact_slice where goal_id = 'g1'").fetchone()[0] == 2

    write_slices(conn, "p1", "g1", [
        {"id": "s1", "title": "A", "size": "small", "status": "done", "needs": [], "files": []},
    ])
    rows = conn.execute(
        "select slice_id, status from fact_slice where goal_id = 'g1'"
    ).fetchall()
    assert rows == [("s1", "done")]  # s2 is gone, s1's status updated


def test_write_slices_two_id_less_slices_do_not_collide_on_the_primary_key(conn):
    """The write-layer half of the bug a re-review caught: read_slices' synthetic _posN ids
    (not a shared "") are what make this insert two rows instead of raising
    duckdb.ConstraintException on fact_slice's PRIMARY KEY. Exercises write_slices directly with
    the exact shape read_slices now produces for an id-less manifest."""
    write_slices(conn, "p1", "g1", [
        {"id": "_pos0", "title": "a", "size": "small", "status": "pending", "needs": [], "files": []},
        {"id": "_pos1", "title": "b", "size": "small", "status": "pending", "needs": [], "files": []},
    ])
    rows = conn.execute(
        "select slice_id, title from fact_slice where goal_id = 'g1' order by slice_id"
    ).fetchall()
    assert rows == [("_pos0", "a"), ("_pos1", "b")]


# --------------------------------------------------------------------------- ingest_artifacts: end to end

def test_ingest_artifacts_end_to_end(conn, tmp_path):
    _goal(tmp_path, "0001-x.md",
          "---\nid: 0001\ntitle: First goal\nlane: small\ndone_when: it works\n"
          "status: pending\n---\nbody\n")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "0001-x.slices.json").write_text(
        '[{"id": "s1", "title": "Slice one", "files": ["a.py"]}]', encoding="utf-8"
    )
    (tmp_path / "config.json").write_text('{"mode": {"default": "goal"}}', encoding="utf-8")

    summary = ingest_artifacts(conn, tmp_path, sdlc_dir=tmp_path)
    assert summary == {"goals": 1, "slices": 1, "config_present": True}

    goal_row = conn.execute(
        "select goal_id, title, done_when_present, status from fact_goal"
    ).fetchone()
    assert goal_row == ("0001", "First goal", True, "pending")
    slice_row = conn.execute("select slice_id, files from fact_slice").fetchone()
    assert slice_row == ("s1", ["a.py"])
    project_row = conn.execute("select config_json from dim_project").fetchone()
    assert project_row == ('{"mode": {"default": "goal"}}',)


def test_ingest_artifacts_no_goals_dir_still_writes_project_snapshot(conn, tmp_path):
    (tmp_path / "config.json").write_text('{}', encoding="utf-8")
    summary = ingest_artifacts(conn, tmp_path, sdlc_dir=tmp_path)
    assert summary == {"goals": 0, "slices": 0, "config_present": True}
    assert conn.execute("select count(*) from dim_project").fetchone()[0] == 1


def test_ingest_artifacts_is_idempotent_across_reruns(conn, tmp_path):
    _goal(tmp_path, "g.md", "---\nid: g1\ntitle: T\n---\n")
    ingest_artifacts(conn, tmp_path, sdlc_dir=tmp_path)
    ingest_artifacts(conn, tmp_path, sdlc_dir=tmp_path)
    assert conn.execute("select count(*) from fact_goal").fetchone()[0] == 1
    assert conn.execute("select count(*) from dim_project").fetchone()[0] == 1


def test_ingest_artifacts_id_less_slices_write_through_without_raising(conn, tmp_path):
    """THE end-to-end regression test for the bug a re-review caught: read_slices alone
    returning 2+ distinct dicts is not sufficient proof -- this runs an id-less-slices manifest
    all the way through write_slices AND ingest_artifacts, and checks every observable the
    reviewer named: no exception, the correct fact_slice row count, fact_goal's row for that
    goal present (not rolled back), and goal_count/slice_count in the summary both correct (not
    silently 0, which is what an unrolled-back partial write plus a swallowed exception used to
    produce)."""
    _goal(tmp_path, "0001-x.md", "---\nid: 0001\ntitle: Has id-less slices\n---\nbody\n")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "0001-x.slices.json").write_text(
        '[{"title": "first, no id"}, {"title": "second, no id"}]', encoding="utf-8"
    )

    summary = ingest_artifacts(conn, tmp_path, sdlc_dir=tmp_path)  # must not raise

    assert summary == {"goals": 1, "slices": 2, "config_present": False}
    goal_row = conn.execute("select goal_id, title from fact_goal").fetchone()
    assert goal_row == ("0001", "Has id-less slices")
    slice_rows = conn.execute(
        "select slice_id, title from fact_slice where goal_id = '0001' order by slice_id"
    ).fetchall()
    assert slice_rows == [("_pos0", "first, no id"), ("_pos1", "second, no id")]


def test_ingest_artifacts_rolls_back_a_goal_whose_write_fails_partway(conn, tmp_path, monkeypatch):
    """Proves Design decision L's transaction wrapper, independent of the id-less-slice bug it
    was written to generalise past: force write_slices to raise AFTER write_goal has already
    succeeded for the same goal, and assert the goal is cleanly ABSENT afterward (rolled back),
    not left as a half-landed row -- and that it is correctly excluded from goal_count/
    slice_count, not miscounted as landed."""
    import insight.ingest.artifact_reader as artifact_reader_module

    _goal(tmp_path, "g.md", "---\nid: g1\ntitle: T\n---\n")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure after write_goal already committed")

    monkeypatch.setattr(artifact_reader_module, "write_slices", _boom)
    summary = ingest_artifacts(conn, tmp_path, sdlc_dir=tmp_path)  # must not raise

    assert summary == {"goals": 0, "slices": 0, "config_present": False}
    assert conn.execute("select count(*) from fact_goal").fetchone()[0] == 0, (
        "write_goal's effect must be rolled back, not left committed, when a LATER "
        "statement for the same goal fails"
    )


def test_project_id_is_shared_with_packs_project_id_for(tmp_path):
    """The cross-module consistency Design decision D exists for: the SAME project_root must
    hash to the SAME project_id whether packs.py or artifact_reader.py computes it."""
    from insight.ingest.packs import project_id_for as packs_project_id_for
    assert project_id_for is packs_project_id_for
