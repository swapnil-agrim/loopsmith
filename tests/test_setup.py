import importlib.util
import json
import pathlib
import subprocess

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-setup" / "scripts" / "setup.py"


def _mod():
    spec = importlib.util.spec_from_file_location("setup", S)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


setup = _mod()


def _sdlc(tmp_path, cfg=None):
    d = tmp_path / ".sdlc"; d.mkdir()
    (d / "config.json").write_text(json.dumps(cfg or {}))
    return str(d)


# ------------------------------------------------------------------ detect_repo


def test_detect_repo_handles_ssh_https_and_host_alias():
    urls = {
        "git@github.com:acme/widget.git": "acme/widget",
        "https://github.com/acme/app.git": "acme/app",
        "github.com-alias:acme/tool.git": "acme/tool",           # an ssh host-alias form
        "https://github.com/acme/plain": "acme/plain",           # no .git suffix
    }
    for url, want in urls.items():
        assert setup.detect_repo(".", run=lambda _r, _a, u=url: u) == want


def test_detect_repo_empty_without_a_remote():
    assert setup.detect_repo(".", run=lambda _r, _a: "") == ""


# ------------------------------------------------------------------ configure


def test_configure_sets_the_adoption_defaults(tmp_path):
    d = _sdlc(tmp_path)
    cfg, _ = setup.configure(d, repo="acme/app", verify_command="pytest -q")
    assert cfg["discovery"]["source"] == "github"
    assert cfg["discovery"]["github"]["repo"] == "acme/app"
    assert cfg["discovery"]["github"]["assignee"] == "@me"       # always scoped to me
    assert cfg["ledger"]["enabled"] is True
    assert cfg["work"]["enabled"] is True and cfg["work"]["auto_merge"] == "off"
    assert cfg["verify"] == {"command": "pytest -q", "enforce": True}


def test_configure_never_enforces_verify_without_a_command(tmp_path):
    d = _sdlc(tmp_path)
    cfg, notes = setup.configure(d)                              # no verify command supplied
    assert cfg["verify"].get("enforce") in (False, None)
    assert not cfg["verify"].get("command")
    assert any("enforce left OFF" in n for n in notes)


def test_configure_defuses_a_preexisting_enforce_without_command(tmp_path):
    d = _sdlc(tmp_path, {"verify": {"enforce": True, "command": ""}})   # the field-found trap
    cfg, notes = setup.configure(d)
    assert cfg["verify"]["enforce"] is False                    # turned back off so `done` isn't refused forever
    assert any("turned OFF" in n for n in notes)


def test_configure_preserves_existing_settings(tmp_path):
    d = _sdlc(tmp_path, {"budget": {"max_iterations": 7}, "work": {"auto_merge": "always"}})
    cfg, _ = setup.configure(d, repo="a/b")
    assert cfg["budget"]["max_iterations"] == 7                  # untouched
    assert cfg["work"]["auto_merge"] == "always"                # a human's explicit choice is kept


def test_configure_preserves_an_explicit_assignee_on_rerun(tmp_path):
    d = _sdlc(tmp_path, {"discovery": {"github": {"assignee": "specific-user"}}})
    cfg, notes = setup.configure(d, repo="a/b")
    assert cfg["discovery"]["github"]["assignee"] == "specific-user"   # a human's explicit choice is kept
    assert any("assignee=specific-user" in n for n in notes)


def test_configure_local_goals_source(tmp_path):
    d = _sdlc(tmp_path)
    cfg, _ = setup.configure(d, source="local-goals")
    assert cfg["discovery"]["source"] == "local-goals" and "github" not in cfg["discovery"]


# ------------------------------------------------------------------ ensure_ignore


def test_ensure_ignore_adds_missing_runtime_dirs_to_tracked(tmp_path):
    added, skipped = setup.ensure_ignore(str(tmp_path), scope="tracked")
    gi = (tmp_path / ".gitignore").read_text()
    assert set(added) == set(setup.RUNTIME_IGNORES) and skipped == []
    assert ".sdlc/ledger/" in gi and ".sdlc/state/" in gi


def test_log_dir_inherits_the_existing_state_ignore_coverage(tmp_path):
    """Extends this test's own pattern for the new local-only action log (#463): after
    `setup.ensure_ignore()` runs, write a REAL file under `.sdlc/state/log/` — the new action-log
    directory `actionlog.py` writes to — and assert `git check-ignore` reports it ignored. Proves
    the plan's "`.sdlc/state/log/` inherits `.sdlc/state/`'s existing coverage for free — no new
    RUNTIME_IGNORES entry, no new setup.py code" claim BEHAVIORALLY, against real git, not by
    reading the tuple (which would pass even if `.gitignore`'s actual matching rules didn't agree)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    setup.ensure_ignore(str(tmp_path), scope="tracked")
    log_dir = tmp_path / ".sdlc" / "state" / "log"
    log_dir.mkdir(parents=True)
    probe = log_dir / "158.jsonl"
    probe.write_text('{"kind": "note"}\n', encoding="utf-8")
    result = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "--quiet", str(probe)])
    assert result.returncode == 0, (
        "a file under .sdlc/state/log/ must be git-ignored via the existing .sdlc/state/ coverage")


def test_ensure_ignore_targets_local_exclude_when_asked(tmp_path):
    (tmp_path / ".git" / "info").mkdir(parents=True)
    setup.ensure_ignore(str(tmp_path), scope="local")
    assert not (tmp_path / ".gitignore").exists()               # the tracked file is untouched
    assert ".sdlc/ledger/" in (tmp_path / ".git" / "info" / "exclude").read_text()


def test_ensure_ignore_never_narrows_an_existing_blanket_exclude(tmp_path):
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".git" / "info" / "exclude").write_text(".sdlc/\n")     # a human's blanket choice
    added, skipped = setup.ensure_ignore(str(tmp_path), scope="tracked")
    assert added == []                                          # the blanket already covers everything
    assert set(skipped) == set(setup.RUNTIME_IGNORES)
    assert (tmp_path / ".git" / "info" / "exclude").read_text() == ".sdlc/\n"   # left exactly as-is
    assert not (tmp_path / ".gitignore").exists()              # nothing added to the tracked file


def test_ensure_ignore_skips_a_dir_already_covered_and_adds_the_rest(tmp_path):
    (tmp_path / ".gitignore").write_text(".sdlc/ledger/\n")
    added, skipped = setup.ensure_ignore(str(tmp_path), scope="tracked")
    assert ".sdlc/ledger/" in skipped
    assert ".sdlc/state/" in added and ".sdlc/work/" in added   # only the missing ones


# ------------------------------------------------------------------ ignore_status


def test_ignore_status_reports_the_mechanism(tmp_path):
    (tmp_path / ".gitignore").write_text(".sdlc/state/\n")
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".git" / "info" / "exclude").write_text(".sdlc/ledger/\n")
    st = setup.ignore_status(str(tmp_path))
    assert st[".sdlc/state/"] == "tracked"
    assert st[".sdlc/ledger/"] == "local"
    assert st[".sdlc/work/"] is None


# ------------------------------------------------------------------ #541: flag parser


def test_flag_parser_handles_a_bare_switch():
    assert setup._flags(["--scope", "local"]) == {"scope": "local"}
    assert setup._flags(["--auto-merge"]) == {"auto-merge": "true"}


def test_flags_consumes_verify_value_that_starts_with_a_double_dash():
    """#541: setup.py's own `_flags` copy gets the same fix -- `verify` (a free-form command
    string) unconditionally consumes the next token, and `--name=value` works for any flag."""
    assert setup._flags(["--verify", "--strict pytest"]) == {"verify": "--strict pytest"}
    assert setup._flags(["--repo=acme/--weird-repo-name"]) == {"repo": "acme/--weird-repo-name"}


def test_flags_never_keeps_a_whitespace_bearing_leaked_key():
    assert setup._flags(["--this looks like leaked prose, not a flag"]) == {}


def test_flags_drops_a_whitespace_bearing_key_in_the_eq_form_too():
    """#541 cycle 2: the `--name=value` branch bypassed the never-a-real-flag rule its
    space-separated sibling applies, so leaked prose that happened to contain '=' still landed
    as a whitespace-bearing key. Same shape in all four `_flags` copies, pinned in each."""
    assert setup._flags(["--zzunknown", "--a b=c d"]) == {"zzunknown": "true"}
