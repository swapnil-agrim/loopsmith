"""Cross-check: insight.ingest.git_reader.measure_window() against skills/sdlc-velocity/scripts/
velocity.py's measure() -- issue #103's own done_when ('matches velocity.py's measurement for the
same window'), proven by running BOTH real implementations against the SAME real, disposable git
fixture repos (never a shared fake `run` injected into both -- git_reader.py's own git invocation
is not injectable by design, so parity is proven end-to-end against real git, not just
algorithmically).

Lives under tests/, not insight/tests/: velocity.py is loaded via
importlib.util.spec_from_file_location, the SAME pattern tests/test_velocity.py already uses --
legal here because tests/test_import_boundary.py only scans skills/, hooks/, and insight/ (a file
under tests/ is exempt from either direction of the plugin/product boundary, confirmed by reading
that file's own _PLUGIN_DIRS / direction-2 test). insight.ingest.git_reader is imported normally
-- also legal: the boundary is about skills/hooks/insight not importing each other, never about
something OUTSIDE insight/ importing it. See .sdlc/plans/103.md §I.
"""
import importlib.util
import os
import pathlib
import subprocess

from insight.ingest.git_reader import measure_window

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_VELOCITY_PATH = REPO_ROOT / "skills" / "sdlc-velocity" / "scripts" / "velocity.py"


def _velocity():
    spec = importlib.util.spec_from_file_location("velocity", _VELOCITY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(root, *args, env=None):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, env=env)


def _repo(root):
    # "-b main": pinned explicitly -- a plan-review confirmed "main" is NOT git's compiled-in
    # default (with no system/global gitconfig, git 2.39.5 produces refs/heads/master; "main"
    # here is only Apple's Xcode-bundled system gitconfig). Every later `git checkout -q main`
    # in these tests would otherwise die on a runner without that config. See
    # insight/tests/test_git_reader.py's _repo() for the identical fix and its full reasoning.
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    return root


def _commit(root, name, message, when=None):
    (root / name).write_text("x\n", encoding="utf-8")
    _git(root, "add", ".")
    env = None
    if when:
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    _git(root, "commit", "-q", "-m", message, env=env)


def _both(root, days=365):
    velocity = _velocity()
    v = velocity.measure(days=days, run=lambda a: velocity._git(a, str(root)))
    g = measure_window(root, days=days)
    return v, g


def test_parity_zero_commits(tmp_path):
    _repo(tmp_path)
    v, g = _both(tmp_path)
    assert (v["commits"], v["merges"]) == (g["commits"], g["merges"]) == (0, 0)


def test_parity_commits_only_no_merges(tmp_path):
    _repo(tmp_path)
    for i in range(4):
        _commit(tmp_path, f"f{i}.txt", f"commit {i}")
    v, g = _both(tmp_path)
    assert (v["commits"], v["merges"]) == (g["commits"], g["merges"]) == (4, 0)


def test_parity_with_a_real_two_parent_merge_commit(tmp_path):
    _repo(tmp_path)
    _commit(tmp_path, "a.txt", "c1")
    _commit(tmp_path, "b.txt", "c2")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _commit(tmp_path, "c.txt", "c3 on feature")
    _git(tmp_path, "checkout", "-q", "main")
    _git(tmp_path, "merge", "--no-ff", "-q", "-m", "Merge pull request #1 from x/feature", "feature")
    v, g = _both(tmp_path)
    assert (v["commits"], v["merges"]) == (g["commits"], g["merges"]) == (4, 1)


def test_parity_respects_the_since_window_boundary_identically(tmp_path):
    """A backdated commit outside the window must be excluded by BOTH implementations
    identically -- pins that the two share the exact same --since semantics, not just the same
    counting logic."""
    _repo(tmp_path)
    _commit(tmp_path, "old.txt", "old commit", when="2020-01-01T00:00:00+00:00")
    _commit(tmp_path, "new.txt", "recent commit")
    v, g = _both(tmp_path, days=30)
    assert (v["commits"], v["merges"]) == (g["commits"], g["merges"]) == (1, 0)


def test_parity_non_git_directory_is_a_deliberate_intentional_divergence(tmp_path):
    """The ONE deliberate divergence in DEGRADED-path behaviour (see git_reader.py's module
    docstring and .sdlc/plans/103.md §B): velocity.py has no no_git guard -- a failing git
    subprocess yields empty stdout -> 0 commits, silently indistinguishable from a real
    empty-window repo. measure_window degrades explicitly instead. This test pins that the two
    DISAGREE ON PURPOSE here, not that they should ever agree on this one case."""
    velocity = _velocity()
    v = velocity.measure(days=14, run=lambda a: velocity._git(a, str(tmp_path)))
    g = measure_window(tmp_path, days=14)
    assert v["commits"] == 0 and v["merges"] == 0
    assert g["commits"] is None and g["merges"] is None and g["degraded"] == ["no_git"]
