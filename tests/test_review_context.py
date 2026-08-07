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
    """A repo with no north-star/project.md/CLAUDE.md still yields a usable brief — the missing
    section drops (never fabricated, never a crash) and the reviewer instruction + artifact pointer
    are always present.

    The absence is now DECLARED rather than silent: fail-open keeps the review running, but a
    reviewer that doesn't know what it was denied returns a confident verdict on partial inputs,
    which reads exactly like a real pass."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; base.mkdir(parents=True)
        out = _rc().brief(str(base), "some goal", "code-review", repo_root=d)
        assert "did NOT write this" in out and "blast radius" in out.lower()
        assert "## north-star" not in out              # no fabricated section, no crash
        assert "alignment cannot be judged" in out     # ...but the gap is stated, not hidden


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


# --- the dossier: closing the blast-radius gap the module docstring names ------------------------
# A fresh reviewer "cannot see BLAST RADIUS". Research already measured it and stored the sweep
# commands verbatim — handing those over turns "trace the callers yourself" into a checkable start.

def _dossier_at(base, name, body="| BR-1 | src/pay.py:42 | charge() | caller | in-scope |"):
    research = pathlib.Path(base) / "research"; research.mkdir(parents=True, exist_ok=True)
    (research / (name + ".md")).write_text(
        "# Research\n**Queries (re-run these at Review):**\n- `grep -rn charge src/`\n" + body,
        encoding="utf-8")


def test_dossier_is_included_and_its_queries_are_flagged_for_rerun():
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        goals = pathlib.Path(base) / "goals"; goals.mkdir(exist_ok=True)
        goal = goals / "0007-fix-retry.md"; goal.write_text("---\nstatus: pending\n---\nfix it\n")
        _dossier_at(base, "0007-fix-retry")
        out = _rc().brief(base, str(goal), "plan-review", repo_root=root)
        assert "src/pay.py:42" in out and "grep -rn charge src/" in out
        assert "landed" in out and "AFTER research" in out       # told WHY to re-run them


def test_dossier_found_under_the_bare_slug_too():
    """Goals are `NNNN-slug.md`; Research may file the dossier under either form."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        goals = pathlib.Path(base) / "goals"; goals.mkdir(exist_ok=True)
        goal = goals / "0007-fix-retry.md"; goal.write_text("---\nstatus: pending\n---\nfix it\n")
        _dossier_at(base, "fix-retry")
        assert "src/pay.py:42" in _rc().brief(base, str(goal), "plan-review", repo_root=root)


def test_dossier_is_project_evidence_not_the_makers_reasoning():
    """It records what the code IS, never why the author chose what they chose — so including it
    cannot reintroduce the anchoring this module exists to remove."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        out = _rc().brief(base, "a goal", "plan-review", repo_root=root)
        assert "author's reasoning" in out and "did NOT write this" in out


# --- ABSENT never PASS: fail-open must not mean fail-silent --------------------------------------

def test_a_missing_dossier_is_stated_not_hidden():
    """An under-briefed reviewer returning a confident 'no issues' is worse than a biased one: the
    verdict is indistinguishable from a real pass."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        out = _rc().brief(base, "a goal", "plan-review", repo_root=root)
        assert "Inputs NOT available" in out
        assert "blast radius was never measured" in out
        assert "ABSENT, never PASS" in out


def test_a_nonexistent_artifact_tells_the_reviewer_to_stop():
    """The exact failure the plan-persistence gap produced: an independent reviewer pointed at a plan
    file the Plan phase never wrote, reviewing from the goal text and calling it clean."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        out = _rc().brief(base, "a goal", "plan-review",
                          artifact=str(pathlib.Path(d) / ".sdlc" / "plans" / "gone.md"), repo_root=root)
        assert "does not exist" in out and "nothing to review" in out


def test_a_complete_brief_carries_no_gap_notice():
    """The notice must stay rare, or it stops being read."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        goals = pathlib.Path(base) / "goals"; goals.mkdir(exist_ok=True)
        goal = goals / "0001-g.md"; goal.write_text("---\nstatus: pending\n---\ndo it\n")
        _dossier_at(base, "0001-g")
        plan = pathlib.Path(base) / "plans" / "0001-g.md"; plan.write_text("the plan\n")
        out = _rc().brief(base, str(goal), "plan-review", artifact=str(plan), repo_root=root)
        assert "Inputs NOT available" not in out


def test_pr_review_artifact_is_a_number_not_a_path():
    """A PR number must never be probed as a filesystem path and reported missing."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        out = _rc().brief(base, "a goal", "pr-review", artifact="42", repo_root=root)
        assert "does not exist" not in out


def test_a_drop_in_repo_is_told_what_it_cannot_judge():
    """No north-star is legitimate — but the reviewer must know alignment is out of scope rather
    than silently reporting a clean strategic review it never performed."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; base.mkdir()
        out = _rc().brief(str(base), "a goal", "plan-review", repo_root=d)
        assert "alignment cannot be judged" in out


def test_plan_phase_persists_the_plan_the_reviewer_is_pointed_at():
    """review_context points plan-review at `.sdlc/plans/`; the Plan phase must actually write there
    or the independent reviewer arrives with nothing, and hard_plan_gate denies every edit."""
    t = (pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-plan" / "SKILL.md").read_text()
    assert ".sdlc/plans/" in t
    assert "hard_plan_gate" in t and "review.independent" in t


# --- #500: pinning safe-by-construction Path(goal).stem sites (no work.stem() reduction needed) ---

def test_dossier_safe_from_traversal_via_stem_extraction():
    """#500: _dossier() uses Path(goal).stem to extract only the filename, preventing any `../`-bearing
    goal from escaping .sdlc/research/. Path(goal).stem removes both directory components and file
    extensions, so even if goal is `../../../etc/passwd`, stem is just `passwd`, and the lookup stays
    confined to the research directory."""
    with tempfile.TemporaryDirectory() as d:
        base, root = _repo(d)
        research = pathlib.Path(base) / "research"; research.mkdir(parents=True, exist_ok=True)
        # create a safe research dossier
        (research / "passwd.md").write_text("# Research\n**Queries:**\n- safe query\n", encoding="utf-8")
        rc = _rc()
        # a traversal attempt should read from .sdlc/research/passwd.md, not escape
        result = rc._dossier(base, "../../../etc/passwd")
        assert "safe query" in result, "goal with ../ should read from research directory, not escape it"
        # confirm the escape directory DOES NOT exist (proof the traversal failed)
        assert not (pathlib.Path(d) / "etc" / "passwd.md").exists(), "traversal must not access files outside .sdlc/research/"
