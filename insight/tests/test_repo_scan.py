# SPDX-License-Identifier: BUSL-1.1 - LoopSmith Insight. NOT MIT. See insight/LICENSE.
"""Tests for insight.ingest.repo_scan (issue #106): --repos glob expansion + the .sdlc
adoption gate. See .sdlc/plans/106.md Design decisions A and D."""
import os
import pathlib

from insight.ingest.repo_scan import SKIP_NO_SDLC, SKIP_UNREADABLE, expand_repos, is_adopted


def test_expand_repos_matches_directories_only(tmp_path):
    (tmp_path / "repos" / "a").mkdir(parents=True)
    (tmp_path / "repos" / "b").mkdir(parents=True)
    (tmp_path / "repos" / "not-a-dir.txt").write_text("x", encoding="utf-8")
    result = expand_repos("repos/*", tmp_path)
    assert result == sorted([tmp_path / "repos" / "a", tmp_path / "repos" / "b"])


def test_expand_repos_zero_matches_returns_empty_list(tmp_path):
    assert expand_repos("nope/*", tmp_path) == []


def test_expand_repos_accepts_an_absolute_pattern(tmp_path):
    """The load-bearing verification: pathlib.Path.glob() raises NotImplementedError on an
    absolute pattern (Python 3.9, this codebase's own floor) -- expand_repos must not."""
    (tmp_path / "repos" / "a").mkdir(parents=True)
    abs_pattern = str(tmp_path / "repos" / "*")
    assert expand_repos(abs_pattern, tmp_path) == [tmp_path / "repos" / "a"]


def test_expand_repos_matches_a_literal_single_directory_name(tmp_path):
    (tmp_path / "onerepo").mkdir()
    assert expand_repos("onerepo", tmp_path) == [tmp_path / "onerepo"]


def test_is_adopted_true_when_sdlc_dir_exists(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    assert is_adopted(tmp_path) is True


def test_is_adopted_false_when_sdlc_dir_absent(tmp_path):
    assert is_adopted(tmp_path) is False


def test_is_adopted_false_when_sdlc_is_a_file_not_a_directory(tmp_path):
    (tmp_path / ".sdlc").write_text("oops", encoding="utf-8")
    assert is_adopted(tmp_path) is False


def test_skip_no_sdlc_constant_matches_no_git_naming_shape():
    assert SKIP_NO_SDLC == "no_sdlc"


def test_expand_repos_never_raises_on_a_non_string_pattern(tmp_path):
    """Hits the except (TypeError, ValueError) guard directly -- args.repos is always a string
    by the time it reaches expand_repos in practice, so nothing else in this plan's own test
    suite exercises this branch; verified live that omitting this test leaves it uncovered
    (coverage flagged repo_scan.py at 83%, lines 12-13) and that adding it closes the gap to
    100%. See Task 6."""
    assert expand_repos(123, tmp_path) == []


def test_expand_repos_expands_a_leading_tilde(tmp_path, monkeypatch):
    """Code-review fold-in: --repos '~/repos/*' previously silently matched zero, because the
    flag's own --help tells users to single-quote the pattern (correctly, to stop the SHELL
    expanding the glob) -- but single quotes also block the shell's OWN '~' expansion, so an
    unexpanded literal '~' reached expand_repos and never matched anything under the user's
    real home directory. HOME is monkeypatched to tmp_path so this test doesn't depend on (or
    pollute) the real invoking user's home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "repos" / "a").mkdir(parents=True)
    assert expand_repos("~/repos/*", tmp_path / "unrelated-cwd") == [tmp_path / "repos" / "a"]


# --------------------------------------------------------------------------- is_adopted OSError guard (code review, issue #106)
#
# BLOCKING finding: Path.is_dir() propagates PermissionError (and any OSError besides the
# ENOENT/ENOTDIR/EBADF/ELOOP it swallows internally) for a directory the process cannot stat.
# is_adopted's original docstring claimed "never raises" on the strength of the ENOENT case
# alone -- reproduced live (PermissionError, uncaught) against the real __main__.py call site,
# which computes adoption for EVERY --repos match in one list comprehension BEFORE open_store()
# is even called, so one unreadable sibling directory crashed main() outright and took down
# every other repo in the same run. Fixed the same way insight/ingest/ledger_reader.py's
# _glob_records/_telemetry_share_is_off already guard this exact class of OSError: "guard the
# computation, degrade the record" -- except here the "record" must be an HONEST third state
# (None, "cannot confirm"), not a silent False, because False would misreport a repo that may
# well be adopted as SKIP_NO_SDLC.


def test_is_adopted_returns_none_when_the_directory_is_unreadable(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    os.chmod(blocked, 0o000)
    try:
        assert is_adopted(blocked) is None
    finally:
        os.chmod(blocked, 0o755)  # restore so pytest's own tmp_path cleanup can remove it


def test_is_adopted_returns_none_for_any_oserror_not_only_permission_denied(tmp_path, monkeypatch):
    """Cheap to produce without depending on filesystem permissions (which behave differently
    under e.g. a root-run CI container): patches Path.is_dir itself to raise a DIFFERENT OSError
    subclass than PermissionError -- proves the guard is a bare `except OSError`, not narrowly
    scoped to EACCES."""
    def boom(self):
        raise OSError(24, "Too many open files")  # EMFILE -- an unrelated OSError
    monkeypatch.setattr(pathlib.Path, "is_dir", boom)
    assert is_adopted(tmp_path) is None


def test_skip_unreadable_constant_is_distinct_from_no_sdlc():
    assert SKIP_UNREADABLE == "unreadable"
    assert SKIP_UNREADABLE != SKIP_NO_SDLC
