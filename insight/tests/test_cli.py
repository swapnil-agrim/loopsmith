# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for the insight.__main__ argparse skeleton (issue #95).

Most tests exercise build_parser()/main() directly — fast, and they pin exit codes and
message content precisely rather than just "some output happened". One test shells out
to a real `python -m insight --help` subprocess, because that is the literal wording of
the done_when criterion and the in-process tests could all pass while the module was
still unimportable as `python -m insight` (e.g. a __main__ guard typo).

Neither requires `pip install -e insight/` first: insight/ is both the pyproject.toml
root and the "insight" import root (see pyproject.toml's package-dir comment), so
running with cwd=REPO_ROOT lets Python's own `-m` machinery — which prepends the
current directory to sys.path — resolve `import insight` against the source tree
directly. See https://docs.python.org/3/using/cmdline.html#cmdoption-m.
"""
import ast
import pathlib
import subprocess
import sys

import pytest

from insight.__main__ import build_parser, main

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: kept in lockstep with insight.__main__._TRACKING_ISSUE so a drift between the two
#: fails a test instead of silently shipping a stale issue number in a stub message.
_EXPECTED_TRACKING_ISSUE = {"ingest": 98, "gaps": 115, "dash": 123}


def test_tracking_issue_table_matches_expectations():
    from insight.__main__ import _TRACKING_ISSUE
    assert _TRACKING_ISSUE == _EXPECTED_TRACKING_ISSUE


def test_help_exits_zero_and_lists_all_three_subcommands(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ("ingest", "dash", "gaps"):
        assert name in out


#: ingest (issue #99, E1.S1), gaps (issue #122, E3.S7), and dash (issue #124, E4.S1) are all
#: real now, not stubs — each is exercised by its own tests below, individually guarded with
#: pytest.importorskip("duckdb") where it touches a store. Empty dict -> the parametrized test
#: below collects zero cases and simply stops running, exactly mirroring what happened when
#: gaps was removed from it in #122 and ingest before that.
_STILL_STUB_TRACKING_ISSUE = {}


@pytest.mark.parametrize("command, issue", sorted(_STILL_STUB_TRACKING_ISSUE.items()))
def test_stub_subcommand_reports_not_implemented_with_its_tracking_issue(command, issue, capsys):
    code = main([command])
    assert code == 1
    err = capsys.readouterr().err
    assert "not implemented" in err.lower()
    assert ("#%d" % issue) in err


def test_missing_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_unknown_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == 2


def test_python_dash_m_insight_help_lists_subcommands_and_exits_zero():
    """The literal done_when wording, run for real rather than only in-process."""
    result = subprocess.run(
        [sys.executable, "-m", "insight", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    for name in ("ingest", "dash", "gaps"):
        assert name in result.stdout


# --------------------------------------------------------------------------- ingest (issue #99)
#
# main() imports insight.ingest.store (and therefore duckdb) lazily, inside the
# `ingest` branch only — so each test below individually needs its own
# pytest.importorskip("duckdb") rather than relying on a module-level skip: a
# module-level importorskip would also skip the dash/gaps/--help tests above on a box
# without duckdb, which must keep running as pure stubs.


@pytest.fixture
def isolate_path_empty(tmp_path, monkeypatch):
    """PATH set to a freshly created, completely empty directory -- no external binary (gh, git,
    bash, python3-as-a-bare-name, ...) is resolvable. Every subprocess-based reader in `insight
    ingest` (gh_reader.py, git_reader.py, collectors.py) must degrade gracefully under this,
    never raise -- this fixture is how every pre-existing CLI ingest test in this file proves it
    still does, now that ingest_gh_reader is wired in unconditionally (issue #104) and would
    otherwise spawn the REAL system gh on any box that has one on PATH. See .sdlc/plans/104.md
    Design decision I."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))


@pytest.fixture
def isolate_path_no_gh(tmp_path, monkeypatch):
    """PATH set to a directory containing ONLY symlinks to the real, currently installed `git`
    and `bash` (each resolved via shutil.which, never a hardcoded path -- works on any
    machine/CI runner). Both keep working -- for the tests below that need a real git repo
    (git_reader.py's own output) or a real collector script (invoked via `bash <script>`) to
    keep running -- but `gh` does NOT resolve. Verified live this session: with only these two
    symlinks on PATH, `bash <script>` and `git --version` both succeed, `gh --version` raises
    FileNotFoundError. See .sdlc/plans/104.md Design decision I."""
    import shutil
    bin_dir = tmp_path / "bin-no-gh"
    bin_dir.mkdir()
    for tool in ("git", "bash"):
        real = shutil.which(tool)
        if real is None:
            pytest.skip(f"no {tool} on this machine's PATH -- nothing to symlink")
        (bin_dir / tool).symlink_to(real)
    monkeypatch.setenv("PATH", str(bin_dir))


def test_ingest_creates_default_db_and_exits_zero(tmp_path, monkeypatch, isolate_path_empty):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    assert (tmp_path / ".sdlc" / "insight.duckdb").exists()


def test_ingest_prints_success_to_stdout_not_stderr(
    tmp_path, monkeypatch, capsys, isolate_path_empty
):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert str(pathlib.Path(".sdlc") / "insight.duckdb") in out


def test_ingest_respects_db_flag(tmp_path, monkeypatch, isolate_path_empty):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "x.duckdb"
    code = main(["ingest", "--db", str(target),
                 "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    assert target.exists()
    assert not (tmp_path / ".sdlc" / "insight.duckdb").exists()


def test_ingest_is_idempotent_via_cli(tmp_path, isolate_path_empty):
    duckdb = pytest.importorskip("duckdb")
    target = tmp_path / "x.duckdb"
    collectors_root = str(tmp_path / "no-collectors-here")
    assert main(["ingest", "--db", str(target), "--collectors-root", collectors_root]) == 0
    assert main(["ingest", "--db", str(target), "--collectors-root", collectors_root]) == 0

    conn = duckdb.connect(str(target))
    rows = conn.execute("select table_name from duckdb_tables()").fetchall()
    names = [r[0] for r in rows]
    assert sorted(names) == sorted({
        "dim_project", "dim_actor", "fact_goal", "fact_event", "fact_handoff",
        "fact_collector_pack", "fact_slice", "fact_merge_lead_time",
        "fact_pr_review", "fact_pr_check", "ingest_ledger_cursor",
    })
    assert len(names) == len(set(names))
    conn.close()


def test_main_module_does_not_import_duckdb_at_top_level():
    """AST-based, mirrors tests/test_import_boundary.py's style. No duckdb needed to
    run this test: it parses source text and inspects only top-level Import/ImportFrom
    nodes, never descending into function bodies, so main()'s lazy import inside the
    `ingest` branch is correctly invisible to it."""
    source = (REPO_ROOT / "insight" / "__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="insight/__main__.py")
    banned = {"duckdb", "insight.ingest.store", "insight.ingest.collectors",
              "insight.ingest.packs", "insight.ingest.artifact_reader",
              "insight.ingest.git_reader", "insight.ingest.gh_reader",
              "insight.ingest.repo_scan", "insight.ingest.ledger_writer",
              "insight.gaps.report", "insight.gaps.compare",
              "insight.dash.render", "insight.dash.serve", "insight"}
    top_level_targets = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level_targets.add(node.module)
    assert not (top_level_targets & banned), (
        f"insight/__main__.py must not import duckdb (directly or via "
        f"insight.ingest.store) at module level: found {top_level_targets & banned}"
    )


def test_ingest_collectors_root_flag_runs_fake_collectors_end_to_end(
    tmp_path, monkeypatch, capsys, isolate_path_no_gh
):
    duckdb = pytest.importorskip("duckdb")
    import stat
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()
    root = tmp_path / "fake-skills"
    script = root / "sdlc-align" / "scripts" / "alignment-collect.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        "#!/bin/sh\necho '{\"schema\":\"alignment-collect/v1\",\"window\":{\"since_days\":1,"
        "\"oldest\":{\"sha\":\"\",\"date\":\"\"},\"newest\":{\"sha\":\"\",\"date\":\"\"},"
        "\"commit_count\":0},\"degraded\":[],\"commits\":[],\"dimensions\":{}}'\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    code = main(["ingest", "--collectors-root", str(root)])
    assert code == 0
    out = capsys.readouterr().out
    assert "alignment-collect/v1" in out
    assert "discovery-scan/v1" in out  # not found under fake-skills -> still printed, degraded
    assert "adapter_collector_not_found" in out
    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    # Scoped to the 3 collectors.py sources: `main()` now ALSO writes two more, separate
    # fact_collector_pack rows every run -- schema="git-facts/v1" (#103) and schema="gh-facts/v1"
    # (#104), both unconditionally wired -- a deliberate, unrelated addition, not a regression in
    # collectors.py's own count. isolate_path_no_gh guarantees gh-facts/v1 is always degraded here
    # (adapter_spawn_failed), never a real network call, while bash still resolves for the real
    # fake-collector script above.
    count = conn.execute(
        "select count(*) from fact_collector_pack "
        "where schema not in ('git-facts/v1', 'gh-facts/v1')"
    ).fetchone()[0]
    assert count == 3
    conn.close()


def test_ingest_never_fatal_when_collectors_root_absent(tmp_path, monkeypatch, isolate_path_empty):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "nope")])
    assert code == 0  # never fatal, per done_when


def test_ingest_wires_artifact_reader_and_populates_dim_project_and_fact_goal(
    tmp_path, monkeypatch, capsys, isolate_path_empty
):
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    goals = tmp_path / ".sdlc" / "goals"
    goals.mkdir(parents=True)
    (goals / "0001-x.md").write_text(
        "---\nid: 0001\ntitle: A goal\nlane: small\ndone_when: it works\n---\nbody\n",
        encoding="utf-8",
    )
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    out = capsys.readouterr().out
    assert "1 goal(s)" in out

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    goal_row = conn.execute("select goal_id, title from fact_goal").fetchone()
    assert goal_row == ("0001", "A goal")
    project_count = conn.execute("select count(*) from dim_project").fetchone()[0]
    assert project_count == 1
    conn.close()


def test_ingest_never_fatal_with_no_goals_dir_at_all(tmp_path, monkeypatch, isolate_path_empty):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0  # never fatal, per done_when — mirrors #100's own analogous test


# --------------------------------------------------------------------------- git facts reader (issue #103)


def test_ingest_wires_git_reader_and_populates_both_new_targets(
    tmp_path, monkeypatch, capsys, isolate_path_no_gh
):
    duckdb = pytest.importorskip("duckdb")
    import subprocess
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat: x (#1)"], cwd=tmp_path, check=True)

    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    out = capsys.readouterr().out
    assert "git-facts/v1" in out
    assert "merge lead-time event(s)" in out

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    pack_row = conn.execute(
        "select window_commit_count, window_merge_count from fact_collector_pack "
        "where schema = 'git-facts/v1'"
    ).fetchone()
    assert pack_row == (1, 0)
    lead_row = conn.execute(
        "select kind, pr_number, degraded from fact_merge_lead_time"
    ).fetchone()
    assert lead_row == ("squash_pr", 1, ["lead_time_requires_network"])
    conn.close()


def test_ingest_never_fatal_when_project_root_is_not_a_git_repo(
    tmp_path, monkeypatch, isolate_path_empty
):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)  # tmp_path is NOT a git repo
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0  # never fatal, per done_when — mirrors #100/#102's own analogous tests


def test_ingest_git_window_days_flag_is_respected(
    tmp_path, monkeypatch, capsys, isolate_path_no_gh
):
    duckdb = pytest.importorskip("duckdb")
    import subprocess
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    env = {**__import__("os").environ, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+00:00",
           "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00"}
    subprocess.run(["git", "commit", "-q", "-m", "old commit"], cwd=tmp_path, check=True, env=env)

    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here"),
                 "--git-window-days", "1"])
    assert code == 0
    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    row = conn.execute(
        "select window_commit_count from fact_collector_pack where schema = 'git-facts/v1'"
    ).fetchone()
    assert row == (0,)  # the 2020 commit is outside a 1-day window
    conn.close()


# --------------------------------------------------------------------------- gh reader (issue #104)


def test_ingest_wires_gh_reader_and_records_a_degrade_code_without_gh(
    tmp_path, monkeypatch, capsys, isolate_path_empty
):
    """The issue's own explicitly-named test, at the CLI level: PATH without gh still completes
    `insight ingest` -- an entirely empty PATH means NOTHING external (gh, git, bash) can be
    spawned, and every reader in the same run must degrade, never raise. See .sdlc/plans/104.md
    Design decision I."""
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()

    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0  # never fatal, per done_when

    out = capsys.readouterr().out
    assert "gh-facts/v1" in out
    assert "PR review event(s)" in out

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    row = conn.execute(
        "select degraded_collector from fact_collector_pack where schema = 'gh-facts/v1'"
    ).fetchone()
    assert row == (["adapter_spawn_failed"],)
    assert conn.execute("select count(*) from fact_pr_review").fetchone()[0] == 0
    assert conn.execute("select count(*) from fact_pr_check").fetchone()[0] == 0
    conn.close()


def test_ingest_gh_window_days_flag_is_accepted(tmp_path, monkeypatch, isolate_path_empty):
    """Not a behavioural assertion about gh's own output (that's gh_reader.py's own test suite,
    Task 6) -- this pins that the flag exists, parses, and reaches ingest_gh_reader without
    raising, exactly mirroring --git-window-days's own CLI-level test."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here"),
                 "--gh-window-days", "30"])
    assert code == 0


def test_help_lists_the_new_gh_window_days_flag(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest", "--help"])
    out = capsys.readouterr().out
    assert "--gh-window-days" in out


# --------------------------------------------------------------------------- ledger writer (issue #105)


def test_ingest_wires_ledger_writer_and_populates_fact_event_and_fact_handoff(
    tmp_path, monkeypatch, capsys, isolate_path_empty
):
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    entries = tmp_path / ".sdlc" / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "alice.jsonl").write_text(
        '{"id":"alice:1","ts":"2026-01-01T00:00:00Z","actor":"alice","kind":"claimed","goal":"g1"}\n'
        '{"id":"alice:2","ts":"2026-01-02T00:00:00Z","actor":"alice","kind":"handoff","goal":"g1",'
        '"to":"bob","issue":7}\n',
        encoding="utf-8",
    )
    (entries / "bob.jsonl").write_text(
        '{"id":"bob:1","ts":"2026-01-03T00:00:00Z","actor":"bob","kind":"ack","goal":"g1",'
        '"issue":7,"state":"resolved"}\n',
        encoding="utf-8",
    )

    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    out = capsys.readouterr().out
    assert "ledger event(s)" in out
    assert "hand-off(s)" in out

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    event_row = conn.execute("select goal_id, kind, reliability_class from fact_event").fetchone()
    assert event_row == ("g1", "claimed", 1)
    handoff_row = conn.execute(
        "select from_actor, to_actor, issue, ack_state from fact_handoff"
    ).fetchone()
    assert handoff_row == ("alice", "bob", 7, "resolved")
    conn.close()


def test_ingest_ledger_writer_is_idempotent_via_cli(tmp_path, monkeypatch, isolate_path_empty):
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    entries = tmp_path / ".sdlc" / "ledger" / "entries"
    entries.mkdir(parents=True)
    (entries / "alice.jsonl").write_text(
        '{"id":"alice:1","ts":"2026-01-01T00:00:00Z","actor":"alice","kind":"claimed","goal":"g1"}\n',
        encoding="utf-8",
    )
    collectors_root = str(tmp_path / "no-collectors-here")
    assert main(["ingest", "--collectors-root", collectors_root]) == 0
    assert main(["ingest", "--collectors-root", collectors_root]) == 0

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    assert conn.execute("select count(*) from fact_event").fetchone()[0] == 1
    conn.close()


# --------------------------------------------------------------------------- --repos glob + adoption gate (issue #106)


def test_help_lists_the_new_repos_flag(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest", "--help"])
    out = capsys.readouterr().out
    assert "--repos" in out


def test_ingest_skips_a_repo_without_sdlc_and_records_the_reason(
    tmp_path, monkeypatch, capsys, isolate_path_empty
):
    """Default (no --repos) mode: the universal adoption gate, Design decision E. No .sdlc/ at
    all -- exit 0 (skip is never fatal), a skip line printed, NONE of the five readers
    (collectors, artifacts, git, gh, ledger -- issue #105 adds the fifth) ran."""
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)  # no .sdlc/ created

    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    out = capsys.readouterr().out
    assert "no_sdlc" in out

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    row = conn.execute("select adopted, skip_reason from dim_project").fetchone()
    assert row == (False, "no_sdlc")
    for table in ("fact_goal", "fact_collector_pack", "fact_merge_lead_time",
                   "fact_pr_review", "fact_pr_check", "fact_event", "fact_handoff"):
        assert conn.execute("select count(*) from %s" % table).fetchone()[0] == 0
    conn.close()


def test_ingest_repos_glob_zero_matches_is_a_usage_error(tmp_path, monkeypatch, isolate_path_empty):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--repos", "nope-*", "--collectors-root", str(tmp_path / "x")])
    assert code == 1


def test_ingest_repos_glob_ingests_two_repos_one_adopted_one_skipped(
    tmp_path, monkeypatch, capsys, isolate_path_no_gh
):
    """The issue's own explicit 4th task: a two-repo fixture, built with real local git init +
    fake remote URLs, NEVER a clone. Also the load-bearing 'project_id is stable across runs'
    check: the SAME two-repo ingest is run TWICE and must not duplicate dim_project rows."""
    duckdb = pytest.importorskip("duckdb")
    import subprocess
    monkeypatch.chdir(tmp_path)

    repos = tmp_path / "repos"
    adopted = repos / "adopted-repo"
    skipped = repos / "skipped-repo"
    for root, remote in ((adopted, "git@github.com:org/adopted.git"),
                          (skipped, "git@github.com:org/skipped.git")):
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "x (#1)"],
                        cwd=root, check=True)
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=root, check=True)
    (adopted / ".sdlc" / "goals").mkdir(parents=True)
    (adopted / ".sdlc" / "goals" / "0001-x.md").write_text(
        "---\nid: 0001\ntitle: A goal\n---\nbody\n", encoding="utf-8",
    )

    argv = ["ingest", "--repos", str(repos / "*"),
            "--collectors-root", str(tmp_path / "no-collectors-here")]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert str(adopted) in out
    assert str(skipped) in out
    assert "no_sdlc" in out

    db = tmp_path / ".sdlc" / "insight.duckdb"
    conn = duckdb.connect(str(db))
    adopted_row = conn.execute(
        "select adopted, skip_reason from dim_project where repo = 'github.com/org/adopted'"
    ).fetchone()
    skipped_row = conn.execute(
        "select adopted, skip_reason from dim_project where repo = 'github.com/org/skipped'"
    ).fetchone()
    assert adopted_row == (True, None)
    assert skipped_row == (False, "no_sdlc")
    assert conn.execute("select count(*) from fact_goal").fetchone()[0] == 1
    assert conn.execute(
        "select count(*) from fact_collector_pack where schema = 'git-facts/v1'"
    ).fetchone()[0] == 1  # only the ADOPTED repo's git-facts row -- the skipped repo never ran
    assert conn.execute("select count(*) from dim_project").fetchone()[0] == 2
    ids_first_run = set(r[0] for r in conn.execute("select project_id from dim_project").fetchall())
    conn.close()

    # Re-run: project_id must be STABLE across runs -- still exactly 2 dim_project rows, not 4.
    assert main(argv) == 0
    conn = duckdb.connect(str(db))
    assert conn.execute("select count(*) from dim_project").fetchone()[0] == 2
    ids_second_run = set(r[0] for r in conn.execute("select project_id from dim_project").fetchall())
    assert ids_first_run == ids_second_run
    conn.close()


def test_ingest_default_single_repo_output_has_no_repo_prefix(
    tmp_path, monkeypatch, capsys, isolate_path_empty
):
    """Design decision E: bare `insight ingest` (no --repos) prints byte-for-byte what it did
    before this story -- no repo-path prefix on any summary line."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    out = capsys.readouterr().out
    assert str(tmp_path) not in out


def test_ingest_repos_glob_survives_an_unreadable_sibling_directory(
    tmp_path, monkeypatch, capsys, isolate_path_empty
):
    """BLOCKING finding from independent code review, reproduced live then fixed:
    repo_scan.is_adopted's Path.is_dir() call propagated PermissionError (and any other OSError
    besides the ENOENT-shaped ones is_dir() itself swallows) for a directory the process cannot
    stat. The call site computed adoption for EVERY --repos match in ONE list comprehension
    BEFORE open_store() was ever called (Design decision E's own ordering fix), so this crashed
    main() outright and took down every OTHER repo in the same run -- including ones sorted
    ahead of the bad one, and the good one below sorts after it alphabetically ('blocked' <
    'good'), so this is the maximally damaging ordering. Also proves the fix does not conflate
    'cannot confirm adoption' with 'confirmed no .sdlc' -- SKIP_UNREADABLE ('unreadable') must
    appear, SKIP_NO_SDLC ('no_sdlc') must NOT, for the blocked repo."""
    duckdb = pytest.importorskip("duckdb")
    import os
    monkeypatch.chdir(tmp_path)

    repos = tmp_path / "repos"
    good = repos / "good-repo"
    good.mkdir(parents=True)
    (good / ".sdlc").mkdir()
    blocked = repos / "blocked-repo"
    blocked.mkdir()
    os.chmod(blocked, 0o000)
    try:
        code = main(["ingest", "--repos", str(repos / "*"),
                     "--collectors-root", str(tmp_path / "no-collectors-here")])
    finally:
        os.chmod(blocked, 0o755)  # restore so pytest's own tmp_path cleanup can remove it

    assert code == 0  # never fatal -- one unreadable sibling must not abort the run
    out = capsys.readouterr().out
    assert str(good) in out          # the healthy repo was NOT taken down
    assert "unreadable" in out

    conn = duckdb.connect(str(tmp_path / ".sdlc" / "insight.duckdb"))
    reasons = {r[0] for r in conn.execute("select skip_reason from dim_project").fetchall()}
    assert "unreadable" in reasons
    assert "no_sdlc" not in reasons  # must NOT be conflated with confirmed non-adoption
    goal_count = conn.execute("select count(*) from fact_goal").fetchone()[0]
    assert goal_count == 0  # sanity: the good repo just has no goals, ingest still ran for it
    conn.close()


# --------------------------------------------------------------------------- gaps (issue #122)


def test_help_lists_the_new_gaps_flags(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["gaps", "--help"])
    out = capsys.readouterr().out
    for flag in ("--db", "--json", "--compare"):
        assert flag in out


def test_gaps_against_an_empty_store_exits_zero(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "x.duckdb"
    code = main(["gaps", "--db", str(target)])
    assert code == 0


def test_gaps_prints_to_stdout_not_stderr_on_a_clean_run(tmp_path, monkeypatch, capsys):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["gaps", "--db", str(tmp_path / "x.duckdb")])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "Gap findings report" in out


def test_gaps_exits_one_on_a_real_fail_finding(tmp_path, monkeypatch):
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "x.duckdb"
    conn = duckdb.connect(str(target))
    from insight.ingest.store import ensure_schema
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO dim_project (project_id, config_json) VALUES "
        "('p1', '{\"work\":{\"require_review\":\"approval\"}}')"
    )
    conn.execute(
        "INSERT INTO fact_merge_lead_time (project_id, merge_sha, pr_number, kind) "
        "VALUES ('p1', 's1', 101, 'squash_pr')"
    )
    conn.execute(
        "INSERT INTO fact_pr_check (project_id, pr_number, check_name, conclusion) "
        "VALUES ('p1', 101, 'ci', 'success')"
    )
    conn.close()
    code = main(["gaps", "--db", str(target)])
    assert code == 1


def test_gaps_compare_against_a_missing_prior_returns_3(tmp_path, monkeypatch, capsys):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["gaps", "--db", str(tmp_path / "x.duckdb"),
                 "--compare", str(tmp_path / "nope.json")])
    assert code == 3
    assert "not found" in capsys.readouterr().err


def test_gaps_json_flag_writes_a_report_that_round_trips(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "report.json"
    code = main(["gaps", "--db", str(tmp_path / "x.duckdb"), "--json", str(out)])
    assert code == 0
    import json
    data = json.loads(out.read_text())
    assert data["schema"] == "insight-gaps-report/v1"
    # Derived from the registry, not a literal: this test is about the JSON round-tripping every
    # rule, and a hardcoded count broke the moment #121 added an eleventh. The property is
    # "nothing is lost through serialisation", not "the catalog has ten entries".
    from insight.gaps.loader import load_gap_rules
    assert len(data["findings"]) == len(load_gap_rules())


def test_gaps_compare_degrades_on_a_truncated_prior_file(tmp_path, monkeypatch, capsys):
    """A prior report is an artifact of an earlier `--json` run, so it can be half-written
    (disk full, SIGKILL). That must take the same SKIP/exit-3 path as a missing file, never a
    raw traceback -- pipeline.py's own --compare would traceback here."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prior.json").write_text('{"schema": "insight-gaps-report/v1", "findi')
    from insight.ingest.store import ensure_schema
    import duckdb
    ensure_schema(duckdb.connect(str(tmp_path / "s.duckdb")))
    assert main(["gaps", "--db", "s.duckdb", "--compare", "prior.json"]) == 3
    assert "not valid JSON" in capsys.readouterr().err


def test_gaps_compare_degrades_on_a_prior_file_of_the_wrong_shape(tmp_path, monkeypatch, capsys):
    """Valid JSON that is not a report object at all -- a list, say -- is rejected by shape
    rather than exploding somewhere inside compare_reports."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prior.json").write_text("[1, 2, 3]")
    from insight.ingest.store import ensure_schema
    import duckdb
    ensure_schema(duckdb.connect(str(tmp_path / "s.duckdb")))
    assert main(["gaps", "--db", "s.duckdb", "--compare", "prior.json"]) == 3
    assert "not a findings report" in capsys.readouterr().err


# --------------------------------------------------------------------------- dash (issue #124)


def test_help_lists_the_new_dash_flags(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["dash", "--help"])
    out = capsys.readouterr().out
    for flag in ("--db", "--out", "--serve", "--port"):
        assert flag in out


def test_dash_builds_index_html_and_exits_zero(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "index.html").exists()
    assert (tmp_path / "out" / "manager.html").exists()
    assert (tmp_path / "out" / "leadership.html").exists()
    assert (tmp_path / "out" / "cross-functional.html").exists()


def test_dash_reports_coverage_denominator_missing_as_a_clean_error_not_a_traceback(
    tmp_path, monkeypatch, capsys, isolate_path_empty,
):
    """CoverageDenominatorMissing can only fire against the real catalog if #114's own static
    guard (test_class_2_metrics_expose_a_coverage_denominator.py) has already regressed --
    belt-and-suspenders, per issue #129 D8 -- so this is exercised via monkeypatch, the same shape
    every other CLI-level error-path test in this file already uses (see isolate_path_empty/
    isolate_path_no_gh's own precedent), not by constructing a real broken catalog."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    import insight.dash.render as render_mod

    def _boom(conn, db_path_label, metrics_dir=None):
        raise render_mod.CoverageDenominatorMissing("metric 900: missing coverage-denominator column(s)")

    monkeypatch.setattr(render_mod, "render_dashboard", _boom)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 1
    captured = capsys.readouterr()
    assert "insight dash: metric catalog failed to load" in captured.err
    assert "Traceback" not in captured.err


def test_dash_reports_coverage_denominator_missing_from_manager_view_as_a_clean_error(
    tmp_path, monkeypatch, capsys,
):
    """Same shape as the render_dashboard test above, but for the manager-view call site (issue
    #129 review, fix 1): that call previously had NO except clause at all, relying on the
    implicit, undocumented invariant that render_dashboard's own registry-wide sweep over the
    same catalog would always raise first. Monkeypatching render_manager_view directly (not
    render_dashboard) proves this call site now catches CoverageDenominatorMissing on its own,
    independent of that ordering."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    import insight.dash.manager as manager_mod
    import insight.dash.render as render_mod

    def _boom(conn, now=None, metrics_dir=None):
        raise render_mod.CoverageDenominatorMissing(
            "metric 900: missing coverage-denominator column(s)"
        )

    monkeypatch.setattr(manager_mod, "render_manager_view", _boom)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 1
    captured = capsys.readouterr()
    assert "insight dash: metric catalog failed to load" in captured.err
    assert "Traceback" not in captured.err


def test_dash_reports_coverage_denominator_missing_from_leadership_view_as_a_clean_error(
    tmp_path, monkeypatch, capsys,
):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    import insight.dash.leadership as leadership_mod
    import insight.dash.render as render_mod

    def _boom(conn, now=None, metrics_dir=None):
        raise render_mod.CoverageDenominatorMissing(
            "metric 900: missing coverage-denominator column(s)"
        )

    monkeypatch.setattr(leadership_mod, "render_leadership_view", _boom)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 1
    captured = capsys.readouterr()
    assert "insight dash: metric catalog failed to load" in captured.err
    assert "Traceback" not in captured.err


def test_dash_reports_coverage_denominator_missing_from_cross_functional_view_as_a_clean_error(
    tmp_path, monkeypatch, capsys,
):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    import insight.dash.cross_functional as cf_mod
    import insight.dash.render as render_mod

    def _boom(conn, now=None, metrics_dir=None):
        raise render_mod.CoverageDenominatorMissing(
            "metric 900: missing coverage-denominator column(s)"
        )

    monkeypatch.setattr(cf_mod, "render_cross_functional_view", _boom)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 1
    captured = capsys.readouterr()
    assert "insight dash: metric catalog failed to load" in captured.err
    assert "Traceback" not in captured.err


def test_dash_prints_to_stdout_not_stderr_on_success(tmp_path, monkeypatch, capsys):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "wrote" in out
    assert (tmp_path / "out" / "manager.html").exists()
    assert (tmp_path / "out" / "leadership.html").exists()
    assert (tmp_path / "out" / "cross-functional.html").exists()


def test_dash_default_out_dir_is_under_dot_sdlc(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb")])
    assert code == 0
    assert (tmp_path / ".sdlc" / "insight-dash" / "index.html").exists()
    assert (tmp_path / ".sdlc" / "insight-dash" / "manager.html").exists()
    assert (tmp_path / ".sdlc" / "insight-dash" / "leadership.html").exists()
    assert (tmp_path / ".sdlc" / "insight-dash" / "cross-functional.html").exists()


def test_dash_against_a_never_ingested_store_still_exits_zero_but_warns(tmp_path, monkeypatch, capsys):
    """Store file doesn't exist yet -- open_store() creates it, but nothing has ever ingested
    into it. Distinct from the onboarding-week test below (Blocking 2, .sdlc/plans/124.md
    section L) -- the two must render DIFFERENT banners, not the same generic "cold" one."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "never ingested" in out
    html_text = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "this store has never been ingested" in html_text
    assert "Ingested, nothing measurable yet" not in html_text
    assert (tmp_path / "out" / "manager.html").exists()
    assert (tmp_path / "out" / "leadership.html").exists()
    assert (tmp_path / "out" / "cross-functional.html").exists()


def test_dash_against_an_onboarding_week_store_warns_differently_and_never_says_never_ingested(
    tmp_path, monkeypatch, capsys
):
    """A real insight ingest ran (a dim_project row exists) but nothing measurable has landed
    yet -- spec's own 'cold start' state. Must NOT tell this user to re-run ingest; they already
    did. Regression test for Blocking 2."""
    duckdb = pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "x.duckdb"
    from insight.ingest.store import ensure_schema
    conn = duckdb.connect(str(target))
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO dim_project (project_id, repo, adopted, first_seen, last_seen) "
        "VALUES ('p1', 'github.com/org/fresh-repo', true, now(), now())"
    )
    conn.close()

    code = main(["dash", "--db", str(target), "--out", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "nothing measurable yet" in out
    html_text = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "Ingested, nothing measurable yet" in html_text
    assert "this store has never been ingested" not in html_text
    assert (tmp_path / "out" / "manager.html").exists()
    assert (tmp_path / "out" / "leadership.html").exists()
    assert (tmp_path / "out" / "cross-functional.html").exists()


def test_dash_index_html_has_no_external_reference(tmp_path, monkeypatch):
    """Task 3's page-level network assertion, exercised at the full CLI level too -- not just
    inside test_dash_render.py's more targeted unit tests."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    from insight.dash.render import assert_self_contained
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 0
    html_text = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert_self_contained(html_text)  # must not raise
    assert (tmp_path / "out" / "manager.html").exists()
    assert (tmp_path / "out" / "leadership.html").exists()
    assert (tmp_path / "out" / "cross-functional.html").exists()


# --------------------------------------------------------------------------- dash IC view (issue #126, E4.S3)


def test_help_lists_the_new_actor_flag(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["dash", "--help"])
    out = capsys.readouterr().out
    for flag in ("--db", "--out", "--serve", "--port", "--actor"):
        assert flag in out


def test_dash_writes_ic_html_when_actor_flag_given(tmp_path, monkeypatch):
    """tmp_path has no .sdlc/config.json at all -- the --actor flag alone must be enough."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main([
        "dash", "--actor", "swapnil-agrim",
        "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out"),
    ])
    assert code == 0
    ic_path = tmp_path / "out" / "ic.html"
    assert ic_path.exists()
    assert "swapnil-agrim" in ic_path.read_text(encoding="utf-8")


def test_dash_writes_ic_html_from_config_actor_when_no_flag(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text(
        '{"ledger": {"actor": "swapnil-agrim"}}', encoding="utf-8",
    )
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 0
    ic_path = tmp_path / "out" / "ic.html"
    assert ic_path.exists()
    assert "swapnil-agrim" in ic_path.read_text(encoding="utf-8")


def test_dash_skips_ic_html_and_warns_when_actor_unresolved(tmp_path, monkeypatch, capsys):
    """The regression proof for Decision 2: tmp_path, no config, no --actor flag -- the exact
    setup every pre-existing `dash` CLI test above already uses -- must still exit 0 with an
    empty stderr, must NOT write ic.html, and must warn on stdout."""
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out")])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "WARNING" in out and "no actor resolved" in out
    assert not (tmp_path / "out" / "ic.html").exists()
    assert (tmp_path / "out" / "index.html").exists()  # the S1 shell must still be written


def test_dash_ic_html_has_no_external_reference(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    from insight.dash.render import assert_self_contained
    code = main([
        "dash", "--actor", "swapnil-agrim",
        "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out"),
    ])
    assert code == 0
    html_text = (tmp_path / "out" / "ic.html").read_text(encoding="utf-8")
    assert_self_contained(html_text)  # must not raise


def test_dash_serve_actually_serves_over_loopback_end_to_end(tmp_path, monkeypatch):
    """The literal 'builds and serves locally' done_when clause, exercised through the real CLI
    wiring (not just insight/dash/serve.py's own unit tests) -- runs main(["dash", "--serve", ...])
    in a background thread (main() blocks inside --serve until interrupted), fetches the real page
    over http://127.0.0.1:<port>/, then shuts the server down.

    Synchronization without a sleep-based race: insight.dash.serve.build_server is spied on (the
    real function is still called through) so the test learns the ACTUAL bound port (--port 0
    means the OS assigns one) the moment the server object is constructed, via a threading.Event
    -- before serve_forever() ever blocks. The test then calls the captured server's own
    .shutdown() directly, which makes serve_forever() return normally (not via KeyboardInterrupt),
    letting main()'s dash branch fall through to `return 0` the same way `python3 -m http.server`
    exiting cleanly would."""
    pytest.importorskip("duckdb")
    import threading
    import urllib.request
    from insight.dash import serve as serve_mod

    monkeypatch.chdir(tmp_path)

    created = {}
    ready = threading.Event()
    real_build_server = serve_mod.build_server

    def spying_build_server(directory, host=serve_mod.DEFAULT_HOST, port=serve_mod.DEFAULT_PORT):
        httpd = real_build_server(directory, host=host, port=port)
        created["httpd"] = httpd
        ready.set()
        return httpd

    monkeypatch.setattr(serve_mod, "build_server", spying_build_server)

    result = {}

    def run():
        result["code"] = main([
            "dash", "--db", str(tmp_path / "x.duckdb"), "--out", str(tmp_path / "out"),
            "--serve", "--port", "0",  # port 0 -> OS-assigned ephemeral port, no CI collisions
        ])

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(timeout=5), "server was never constructed"
    port = created["httpd"].server_address[1]

    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3)
    assert resp.status == 200
    assert b"insight-dash-data" in resp.read()

    created["httpd"].shutdown()
    t.join(timeout=5)
    assert not t.is_alive()
    assert result["code"] == 0
