import pathlib, importlib.util, tempfile, os, subprocess, sys

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _rc():
    spec = importlib.util.spec_from_file_location("review_context", S / "review_context.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _repo(d):
    """A minimal adopted repo: .sdlc with a north-star + project.md, a CLAUDE.md, a frozen contract."""
    root = pathlib.Path(d)
    base = root / ".sdlc"; (base / "context").mkdir(parents=True); (base / "plans").mkdir()
    (base / "context" / "north-star.md").write_text(
        "## Vision\nShip acme/widget.\n## Strategy\nNon-goals: no multi-tenant.\n"
        "## Architecture\n1. UI holds no business logic.\n", encoding="utf-8")
    (base / "project.md").write_text("# acme/widget\nStack: python. verify: pytest.\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("Rule: no client strings.\n", encoding="utf-8")
    contracts = root / "docs" / "CONTRACTS"; contracts.mkdir(parents=True)
    (contracts / "api.md").write_text("Status: FROZEN\n", encoding="utf-8")
    return str(base), str(root)


def test_brief_is_project_informed_and_author_blind():
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        out = _rc().brief(base, "make the button blue", "plan-review", repo_root=root)
        # project-informed: the north-star and its rules are in the pack
        assert "north-star" in out and "UI holds no business logic" in out
        assert "no multi-tenant" in out          # non-goals the plan is judged against
        assert "CLAUDE.md" in out and "FROZEN" in out
        # author-blind: it tells the reviewer it did NOT write this and to work from code
        assert "did NOT write this" in out
        assert "blast radius" in out.lower() and "whole repository" in out
        # the goal is carried; the maker's reasoning is not (there is no field for it)
        assert "make the button blue" in out
        assert "reasoning" in out.lower()        # only ever "you have not seen the author's reasoning"
        assert out.lower().count("author") >= 1


def test_every_phase_names_its_own_artifact():
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        rc = _rc()
        assert "PLAN" in rc.brief(base, "g", "plan-review", repo_root=root).upper() or \
               ".sdlc/plans" in rc.brief(base, "g", "plan-review", repo_root=root)
        assert "diff" in rc.brief(base, "g", "code-review", repo_root=root).lower()
        pr = rc.brief(base, "g", "pr-review", artifact="42", repo_root=root)
        assert "#42" in pr and "gh pr diff 42" in pr
        assert "intent" in rc.brief(base, "g", "retro", repo_root=root).lower() or \
               "goal it claimed" in rc.brief(base, "g", "retro", repo_root=root)


def test_goal_given_as_a_file_path_is_read():
    """Local mode passes a goal FILE path — the brief must carry the file's intent, not the path."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        goal_file = pathlib.Path(d) / ".sdlc" / "goals" / "0007.md"
        goal_file.parent.mkdir(parents=True, exist_ok=True)
        goal_file.write_text("---\nid: 0007\n---\nAdd a dark-mode toggle to the settings page.\n",
                             encoding="utf-8")
        out = _rc().brief(base, str(goal_file), "plan-review", repo_root=root)
        assert "dark-mode toggle" in out


def test_github_bare_issue_number_points_to_the_issue():
    """Github mode passes a bare issue NUMBER; the reviewer must be told to read the issue for the
    acceptance criteria, not handed just the digits."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        out = _rc().brief(base, "42", "pr-review", artifact="42", repo_root=root)
        assert "issue #42" in out and "gh issue view 42" in out
        assert "acceptance criteria" in out


def test_unknown_phase_is_a_loud_error():
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        try:
            _rc().brief(base, "g", "sign-off", repo_root=root)
            assert False, "expected ValueError on unknown phase"
        except ValueError as exc:
            assert "unknown --for" in str(exc)


def test_fail_open_when_project_docs_absent():
    """A repo with no north-star/project.md/CLAUDE.md still yields a usable brief — the missing lines
    just drop, and the reviewer instruction + artifact pointer are always present."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; base.mkdir(parents=True)
        out = _rc().brief(str(base), "some goal", "code-review", repo_root=d)
        assert "did NOT write this" in out and "blast radius" in out.lower()
        assert "north-star" not in out           # absent -> its line dropped, no crash


def test_cli_ascii_safe_under_c_locale():
    """The printed brief must survive a non-utf8 locale (there is a C-locale test elsewhere)."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        env = dict(os.environ, LC_ALL="C", PYTHONIOENCODING="ascii")
        p = subprocess.run([sys.executable, str(S / "review_context.py"), "brief", base,
                            "make it blue", "--for", "plan-review", "--repo-root", root],
                           capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr
        assert "INDEPENDENT reviewer" in p.stdout


def test_cli_missing_for_flag_usages_out():
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        p = subprocess.run([sys.executable, str(S / "review_context.py"), "brief", base, "g"],
                           capture_output=True, text=True)
        assert p.returncode == 2 and "usage:" in p.stderr


def test_cli_parses_artifact_and_repo_root_flags():
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        p = subprocess.run([sys.executable, str(S / "review_context.py"), "brief", base, "g",
                            "--for", "pr-review", "--artifact", "42", "--repo-root", root],
                           capture_output=True, text=True)
        assert p.returncode == 0 and "#42" in p.stdout
        assert "FROZEN" in p.stdout               # --repo-root was honored (contracts found under it)


def test_cli_unknown_verb_and_bad_phase():
    p = subprocess.run([sys.executable, str(S / "review_context.py"), "explain"],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "usage:" in p.stderr
    with tempfile.TemporaryDirectory() as d:
        base, _ = _repo(d)
        p2 = subprocess.run([sys.executable, str(S / "review_context.py"), "brief", base, "g",
                            "--for", "sign-off"], capture_output=True, text=True)
        assert p2.returncode == 2 and "unknown --for" in p2.stderr
