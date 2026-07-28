"""sdlc-doctor: a setup check-up. doctor.check() audits only what THIS project's config makes relevant
(github board -> gh auth+scope; KG -> builder; vision-first -> north-star) and returns each check with
the exact one-line fix. The command runner is injectable so these are hermetic (no real gh/graphify)."""
import json, pathlib, importlib.util, tempfile

D = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-doctor" / "scripts" / "doctor.py"


def _doc():
    spec = importlib.util.spec_from_file_location("doctor", D)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _sdlc(d, cfg):
    base = pathlib.Path(d) / ".sdlc"; base.mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(cfg))
    return str(base)


def _runner(gh_auth="", builder=""):
    """Fake command runner: canned stdout for the probes, '' = command unavailable/failed."""
    def run(args):
        if args[:3] == ["gh", "auth", "status"]:
            return gh_auth
        if len(args) >= 2 and args[1] == "--version":   # <builder> --version
            return builder
        return ""
    return run


def _by_name(checks):
    return {c["name"]: c for c in checks}


def test_flags_missing_gh_project_scope():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "github", "github": {"project": {"enabled": True}}}})
        c = _by_name(d.check(base, run=_runner(gh_auth="Logged in. Token scopes: 'repo', 'workflow'")))
        assert c["gh auth"]["ok"] is True
        assert c["gh project scope"]["ok"] is False
        assert "gh auth refresh -s project" in c["gh project scope"]["fix"]


def test_passes_when_scope_present():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "github", "github": {"project": {"enabled": True}}}})
        c = _by_name(d.check(base, run=_runner(gh_auth="scopes: 'repo', 'project'")))
        assert c["gh project scope"]["ok"] is True


def test_flags_missing_kg_builder():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"knowledge_graph": {"enabled": True, "builder": "graphify"}})
        c = _by_name(d.check(base, run=_runner(builder="")))         # graphify --version fails
        assert c["graphify installed"]["ok"] is False
        assert "pip install graphifyy" in c["graphify installed"]["fix"]


def test_skips_irrelevant_checks_for_local_zero_dep():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "local-goals"}})
        names = [c["name"] for c in d.check(base, run=_runner())]
        assert not any("gh" in n or "graphify" in n or "north-star" in n for n in names)
        assert "project layer" in names                              # always checked


def test_main_runs():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "local-goals"}})
        assert d.main(["doctor.py", "check", base]) == 0


def test_detects_companions_never_failing_on_absence():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "local-goals"}})
        def run(args):
            return "superpowers@claude-plugins-official" if args == ["claude", "plugin", "list"] else _runner()(args)
        checks = d.check(base, run=run)
        names = " | ".join(c["name"] for c in checks)
        assert "superpowers: present" in names            # detected installed
        assert "code-review: absent" in names             # detected missing
        assert all(c["ok"] for c in checks if "superpowers" in c["name"] or "code-review" in c["name"])


def test_features_dashboard_reports_states(tmp_path):
    import json, importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "doctor", _pl.Path(__file__).resolve().parent.parent / "skills" / "sdlc-doctor" / "scripts" / "doctor.py")
    d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
    base = tmp_path / ".sdlc"; base.mkdir()
    base.joinpath("config.json").write_text(json.dumps(
        {"model_selection": "auto", "verify": {"enforce": True}}))
    rows = {name: state for name, state, _ in d.features(str(base))}
    assert "AUTO" in rows["model+effort auto-selection"]
    assert rows["machine-checked done (verify.enforce)"].startswith("ON")
    assert "off" in rows["hard plan-gate (deny source edits w/o fresh plan)"]
    assert rows["backlog source"] == "local-goals"
    assert rows["team ledger"].startswith("off")           # an absent block reads as off


def test_features_reports_the_ledger_and_counts_its_entries(tmp_path):
    import json, importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "doctor", _pl.Path(__file__).resolve().parent.parent / "skills" / "sdlc-doctor" / "scripts" / "doctor.py")
    d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
    base = tmp_path / ".sdlc"; base.mkdir()
    base.joinpath("config.json").write_text(json.dumps({"ledger": {"enabled": True}}))
    entries = base / "ledger" / "entries"; entries.mkdir(parents=True)
    (entries / "amy.jsonl").write_text('{"kind":"done"}\n')
    rows = {name: state for name, state, _ in d.features(str(base))}
    assert rows["team ledger"] == "ON — 1 entry in .sdlc/ledger/entries/"


# --- ledger setup: enabled-but-not-created is a real gap doctor can fix -----------------------

def test_flags_ledger_enabled_but_not_initialised():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"ledger": {"enabled": True}})               # on in config, never created
        c = _by_name(d.check(base, run=_runner()))
        assert c["team ledger initialized"]["ok"] is False
        assert "/sdlc-ledger" in c["team ledger initialized"]["fix"]


def test_ledger_check_passes_once_the_worktree_exists():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"ledger": {"enabled": True}})
        (pathlib.Path(base) / "ledger").mkdir()
        (pathlib.Path(base) / "ledger" / ".git").write_text("gitdir: elsewhere\n")   # worktree present
        c = _by_name(d.check(base, run=_runner()))
        assert c["team ledger initialized"]["ok"] is True


def test_no_ledger_check_when_the_ledger_is_off():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"ledger": {"enabled": False}})
        assert "team ledger initialized" not in _by_name(d.check(base, run=_runner()))


def test_features_flags_an_enabled_but_unset_up_ledger(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"ledger": {"enabled": True}})            # enabled, nothing created yet
    rows = {name: state for name, state, _ in d.features(base)}
    assert "NOT set up" in rows["team ledger"] and "/sdlc-ledger" in rows["team ledger"]


# --- the verify permanent-refusal trap: enforce on with no command ---------------------------

def test_flags_verify_enforce_with_empty_command():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"verify": {"enforce": True, "command": ""}})
        c = _by_name(d.check(base, run=_runner()))["verify command present (enforce is on)"]
        assert c["ok"] is False and "every `done` is refused" in c["fix"]


def test_verify_check_ok_with_a_command():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"verify": {"enforce": True, "command": "pytest -q"}})
        assert _by_name(d.check(base, run=_runner()))["verify command present (enforce is on)"]["ok"]


def test_verify_check_ok_when_a_goal_sets_verify_command():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"verify": {"enforce": True, "command": ""}})
        (pathlib.Path(base) / "goals").mkdir()
        (pathlib.Path(base) / "goals" / "0001.md").write_text(
            "---\nstatus: pending\nverify_command: pytest\n---\nx\n")
        assert _by_name(d.check(base, run=_runner()))["verify command present (enforce is on)"]["ok"]


def test_no_verify_check_when_enforce_is_off():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"verify": {"command": ""}})
        assert "verify command present (enforce is on)" not in _by_name(d.check(base, run=_runner()))


# --- the dashboard surfaces the two silent-adoption states --------------------------------------

def test_features_work_off_says_nothing_is_written_to_git(tmp_path):
    d = _doc()
    rows = {n: s for n, s, _ in d.features(_sdlc(tmp_path, {}))}
    assert "writes NOTHING to git" in rows["per-goal worktree + PR"]


def test_features_reports_which_mechanism_ignores_the_runtime_dirs(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {})                                       # repo root = tmp_path
    rows_none = {n: s for n, s, _ in d.features(base)}
    assert "NOT ignored" in rows_none["runtime dirs ignored via"]
    (tmp_path / ".gitignore").write_text(".sdlc/\n")
    rows_tracked = {n: s for n, s, _ in d.features(base)}
    assert "tracked .gitignore" in rows_tracked["runtime dirs ignored via"]


def test_features_reports_the_pr_review_gate(tmp_path):
    d = _doc()
    row = "PR review gate (independent of branch protection)"
    off = {n: s for n, s, _ in d.features(_sdlc(tmp_path / "a", {}))}
    assert off[row].startswith("off")
    on = {n: s for n, s, _ in d.features(_sdlc(tmp_path / "b", {"work": {"require_review": "approval"}}))}
    assert "ON (approval)" in on[row]


# --- standing-doc hygiene: the mechanical half of context maintenance -------------------------
# Rot that a script can settle (a reference that no longer resolves), NOT the judgment half
# (demoting a rule CI now enforces) — that's sdlc-retro's, because it changes files.

def _hyg(d, project_md=None, north_star=None):
    base = pathlib.Path(d) / ".sdlc"
    (base / "context").mkdir(parents=True)
    (base / "config.json").write_text("{}")
    if project_md is not None:
        (base / "project.md").write_text(project_md)
    if north_star is not None:
        (base / "context" / "north-star.md").write_text(north_star)
    return str(base)


def test_hygiene_is_silent_without_standing_docs():
    """A drop-in project has nothing to rot — it must not gain a new nag."""
    with tempfile.TemporaryDirectory() as d:
        assert _doc().hygiene(_hyg(d), d) == []


def test_hygiene_flags_a_cited_path_that_no_longer_exists():
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / "src").mkdir()
        (pathlib.Path(d) / "src" / "live.py").write_text("x = 1")
        sdlc = _hyg(d, project_md="Entry point is `src/live.py`; config in `src/gone.py`.")
        rows = {c["name"]: c for c in _doc().hygiene(sdlc, d)}
        paths = rows["standing docs: cited paths resolve"]
        assert not paths["ok"]
        assert "src/gone.py" in paths["fix"] and "src/live.py" not in paths["fix"]


def test_hygiene_ignores_patterns_and_urls():
    """Globs and <placeholders> are patterns, not references. A check that cries wolf gets
    ignored along with its true positives."""
    with tempfile.TemporaryDirectory() as d:
        sdlc = _hyg(d, project_md=(
            "Goals live in `.sdlc/goals/NNNN-*.md`, docs at `https://example.com/a/b`, "
            "research in `.sdlc/research/<slug>.md`."))
        assert all(c["ok"] for c in _doc().hygiene(sdlc, d))


def test_hygiene_flags_a_dangling_relative_link_but_not_external_ones():
    with tempfile.TemporaryDirectory() as d:
        sdlc = _hyg(d, north_star=(
            "See [gone](./missing.md), [site](https://example.com), [here](#anchor)."))
        rows = {c["name"]: c for c in _doc().hygiene(sdlc, d)}
        links = rows["standing docs: links resolve"]
        assert not links["ok"]
        assert "./missing.md" in links["fix"]
        assert "example.com" not in links["fix"] and "#anchor" not in links["fix"]


def test_hygiene_caps_the_offender_list():
    """A wall of paths is a report nobody reads."""
    with tempfile.TemporaryDirectory() as d:
        cited = " ".join(f"`src/gone{i}.py`" for i in range(7))
        rows = {c["name"]: c for c in _doc().hygiene(_hyg(d, project_md=cited), d)}
        fix = rows["standing docs: cited paths resolve"]["fix"]
        assert "+4 more" in fix and fix.count("src/gone") == 3


def test_check_surfaces_rot_without_scoring_it_as_setup(capsys):
    """Setup readiness and content rot are different questions: the rot must show up in a `check`
    run (or nobody runs it) but must NOT move the N/M ready score."""
    with tempfile.TemporaryDirectory() as d:
        sdlc = _hyg(d, project_md="Broken ref to `src/gone.py`.")
        m = _doc()
        m._real_run = lambda args: ""                 # hermetic: no real gh / claude probes
        n = len(m.check(sdlc, run=_runner()))
        m.main(["doctor.py", "check", sdlc])
        out = capsys.readouterr().out
        assert "standing-doc hygiene" in out and "src/gone.py" in out
        assert f"{n}/{n} ready" in out                # rot did NOT become a failed setup check
