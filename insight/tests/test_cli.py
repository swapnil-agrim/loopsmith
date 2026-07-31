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


def test_ingest_creates_default_db_and_exits_zero(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    assert (tmp_path / ".sdlc" / "insight.duckdb").exists()


def test_ingest_prints_success_to_stdout_not_stderr(tmp_path, monkeypatch, capsys):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert str(pathlib.Path(".sdlc") / "insight.duckdb") in out


def test_ingest_respects_db_flag(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "x.duckdb"
    code = main(["ingest", "--db", str(target),
                 "--collectors-root", str(tmp_path / "no-collectors-here")])
    assert code == 0
    assert target.exists()
    assert not (tmp_path / ".sdlc" / "insight.duckdb").exists()


def test_ingest_is_idempotent_via_cli(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    target = tmp_path / "x.duckdb"
    collectors_root = str(tmp_path / "no-collectors-here")
    assert main(["ingest", "--db", str(target), "--collectors-root", collectors_root]) == 0
    assert main(["ingest", "--db", str(target), "--collectors-root", collectors_root]) == 0

    conn = duckdb.connect(str(target))
    rows = conn.execute("select table_name from duckdb_tables()").fetchall()
    names = [r[0] for r in rows]
    assert sorted(names) == sorted(
        {"dim_project", "dim_actor", "fact_goal", "fact_event", "fact_handoff", "fact_collector_pack"}
    )
    assert len(names) == len(set(names))
    conn.close()


def test_main_module_does_not_import_duckdb_at_top_level():
    """AST-based, mirrors tests/test_import_boundary.py's style. No duckdb needed to
    run this test: it parses source text and inspects only top-level Import/ImportFrom
    nodes, never descending into function bodies, so main()'s lazy import inside the
    `ingest` branch is correctly invisible to it."""
    source = (REPO_ROOT / "insight" / "__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="insight/__main__.py")
    banned = {"duckdb", "insight.ingest.store", "insight.ingest.collectors", "insight.ingest.packs", "insight"}
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


def test_ingest_collectors_root_flag_runs_fake_collectors_end_to_end(tmp_path, monkeypatch, capsys):
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
    count = conn.execute("select count(*) from fact_collector_pack").fetchone()[0]
    assert count == 3
    conn.close()


def test_ingest_never_fatal_when_collectors_root_absent(tmp_path, monkeypatch):
    pytest.importorskip("duckdb")
    monkeypatch.chdir(tmp_path)
    code = main(["ingest", "--collectors-root", str(tmp_path / "nope")])
    assert code == 0  # never fatal, per done_when
