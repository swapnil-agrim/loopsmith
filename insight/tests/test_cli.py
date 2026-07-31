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


#: ingest (issue #99, E1.S1) is real now, not a stub — it's exercised by its own
#: tests below, each individually guarded with pytest.importorskip("duckdb").
_STILL_STUB_TRACKING_ISSUE = {"gaps": 115, "dash": 123}


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
        "fact_pr_review", "fact_pr_check",
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
              "insight.ingest.git_reader", "insight.ingest.gh_reader", "insight"}
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
