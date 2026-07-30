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


@pytest.mark.parametrize("command, issue", sorted(_EXPECTED_TRACKING_ISSUE.items()))
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
