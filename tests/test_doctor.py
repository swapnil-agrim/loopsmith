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


# --- telemetry: config block + doctor reporting (#138) ----------------------------------------

_ROW = "telemetry (agent-emitted events)"


def test_features_telemetry_absent_block_is_off():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {})
        rows = {name: state for name, state, _ in d.features(base)}
        assert rows[_ROW] == "off (nothing is recorded)"


def test_features_telemetry_enabled_false_matches_absent_byte_for_byte():
    """The headline behavior from the goal statement: an absent block and an explicit
    enabled:false must read identically — not just both 'look off', the literal same string."""
    d = _doc()
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
        absent = _sdlc(t1, {})
        explicit_off = _sdlc(t2, {"telemetry": {"enabled": False}})
        rows_absent = {name: state for name, state, _ in d.features(absent)}
        rows_off = {name: state for name, state, _ in d.features(explicit_off)}
        assert rows_absent[_ROW] == rows_off[_ROW] == "off (nothing is recorded)"


def test_features_telemetry_enabled_is_strict_true_not_truthy():
    """Guards the same is-True idiom as ledger.enabled() — a truthy string or int must not
    silently switch this on."""
    d = _doc()
    for bad in ("yes", 1):
        with tempfile.TemporaryDirectory() as t:
            base = _sdlc(t, {"telemetry": {"enabled": bad}})
            rows = {name: state for name, state, _ in d.features(base)}
            assert rows[_ROW] == "off (nothing is recorded)", f"enabled={bad!r} must not turn telemetry on"


def test_features_telemetry_on_share_true_reports_ops_branch_path():
    d = _doc()
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2, tempfile.TemporaryDirectory() as t3:
        explicit_share_true = _sdlc(t1, {"telemetry": {"enabled": True, "share": True}})
        default_share = _sdlc(t2, {"telemetry": {"enabled": True}})       # share omitted -> defaults to on
        malformed_share = _sdlc(t3, {"telemetry": {"enabled": True, "share": "sure"}})
        for base in (explicit_share_true, default_share, malformed_share):
            rows = {name: state for name, state, _ in d.features(base)}
            assert "ON, share:true" in rows[_ROW]
            assert ".sdlc/ledger/events/" in rows[_ROW]
        rows1 = {name: state for name, state, _ in d.features(explicit_share_true)}
        rows2 = {name: state for name, state, _ in d.features(default_share)}
        assert rows1[_ROW] == rows2[_ROW]          # omitted share == share:true, byte for byte


def test_features_telemetry_on_share_false_says_not_yet_honored():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"telemetry": {"enabled": True, "share": False}})
        rows = {name: state for name, state, _ in d.features(base)}
        assert "share:false" in rows[_ROW]
        assert "NOT YET HONORED" in rows[_ROW]
        assert ".sdlc/ledger/events/" in rows[_ROW]          # the real landing path, not .sdlc/events/
        assert ".sdlc/events/" not in rows[_ROW].replace(".sdlc/ledger/events/", "")


def test_features_telemetry_counts_existing_events():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"telemetry": {"enabled": True, "share": True}})
        events = pathlib.Path(base) / "ledger" / "events"; events.mkdir(parents=True)
        (events / "dana.jsonl").write_text('{"kind":"phase"}\n{"kind":"gate"}\n')
        rows = {name: state for name, state, _ in d.features(base)}
        assert "2 events" in rows[_ROW]


def test_features_telemetry_malformed_block_does_not_crash():
    """Fail-open against a non-dict `telemetry` value — the one shape a bare `or {}` idiom
    would not catch (a non-empty string/list is truthy)."""
    d = _doc()
    for bad in ("oops", ["a", "b"], None, True):
        with tempfile.TemporaryDirectory() as t:
            base = _sdlc(t, {"telemetry": bad})
            rows = {name: state for name, state, _ in d.features(base)}
            assert rows[_ROW] == "off (nothing is recorded)", f"telemetry={bad!r} must fail open, not crash"


def test_features_telemetry_no_events_dir_at_all():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"telemetry": {"enabled": True, "share": True}})
        # .sdlc/ledger/events/ never created
        rows = {name: state for name, state, _ in d.features(base)}
        assert "0 events" in rows[_ROW]


def test_features_telemetry_empty_events_dir():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"telemetry": {"enabled": True, "share": True}})
        (pathlib.Path(base) / "ledger" / "events").mkdir(parents=True)
        rows = {name: state for name, state, _ in d.features(base)}
        assert "0 events" in rows[_ROW]


def test_features_telemetry_malformed_jsonl_line_does_not_crash():
    """A garbage line still counts as a non-blank line (doctor only counts lines, it never
    parses JSON — same convention as _ledger_entries) and must not raise."""
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"telemetry": {"enabled": True, "share": True}})
        events = pathlib.Path(base) / "ledger" / "events"; events.mkdir(parents=True)
        (events / "dana.jsonl").write_text('not valid json at all\n{"kind":"phase"}\n')
        rows = {name: state for name, state, _ in d.features(base)}
        assert "2 events" in rows[_ROW]


def test_features_survives_a_half_written_non_utf8_line_in_either_stream():
    """A process killed mid-append truncates a multi-byte UTF-8 sequence. The resulting
    UnicodeDecodeError is a ValueError, NOT an OSError, so it used to sail past the counter's
    catch and crash the WHOLE dashboard — every row, not just this one. Both streams share one
    counter now, so neither can regress alone."""
    d = _doc()
    for stream in ("entries", "events"):
        with tempfile.TemporaryDirectory() as t:
            base = _sdlc(t, {"telemetry": {"enabled": True, "share": True},
                             "ledger": {"enabled": True, "actor": "dana"}})
            files = pathlib.Path(base) / "ledger" / stream; files.mkdir(parents=True)
            (files / "dana.jsonl").write_bytes(b'{"kind":"note"}\n\xff\xfe truncated mid-sequence\n')
            rows = {name: state for name, state, _ in d.features(base)}   # must not raise
            assert "2" in rows[_ROW if stream == "events" else "team ledger"]


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


# --- worktree footgun: a relative interpreter path fails exit=127 once work.enabled -------------

def test_flags_a_relative_venv_in_verify_command_when_work_on():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"work": {"enabled": True},
                         "verify": {"command": "cd backend && .venv/bin/python3 -m pytest -q"}})
        c = _by_name(d.check(base, run=_runner()))["verify.command resolves in the goal worktree"]
        assert c["ok"] is False and "exit=127" in c["fix"]


def test_an_absolute_interpreter_path_is_not_flagged():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"work": {"enabled": True},
                         "verify": {"command": "/abs/proj/.venv/bin/python3 -m pytest -q"}})
        assert _by_name(d.check(base, run=_runner()))["verify.command resolves in the goal worktree"]["ok"]


def test_no_worktree_dep_check_when_work_is_off():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"verify": {"command": ".venv/bin/python3 -m pytest"}})   # work off -> no worktree
        assert "verify.command resolves in the goal worktree" not in _by_name(d.check(base, run=_runner()))


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


def test_features_reports_the_stop_gate(tmp_path):
    d = _doc()
    row = "Stop gate (refuse to end a session with unplanned source)"
    off = {n: s for n, s, _ in d.features(_sdlc(tmp_path / "a", {}))}
    assert off[row] == "off"
    on = {n: s for n, s, _ in d.features(_sdlc(tmp_path / "b", {"gates": {"stop_gate": {"enabled": True}}}))}
    assert "ON" in on[row]


def test_features_reports_session_start(tmp_path):
    d = _doc()
    row = "SessionStart policy brief"
    off = {n: s for n, s, _ in d.features(_sdlc(tmp_path / "a", {}))}
    assert off[row] == "off"
    on = {n: s for n, s, _ in d.features(_sdlc(tmp_path / "b", {"session_start": {"enabled": True}}))}
    assert on[row].startswith("ON")


def test_features_surfaces_skill_selection_advisory(tmp_path):
    # a plugin can't disable a built-in, so this row is a static advisory (not a toggle) pointing at
    # the user-side remedies — it must always be present so adopters learn the limitation
    d = _doc()
    rows = {n: (s, e) for n, s, e in d.features(_sdlc(tmp_path, {}))}
    state, enable = rows["skill selection vs platform built-ins"]
    assert "can't disable a built-in" in state
    assert "skillOverrides" in enable and "/plugin disable" in enable


# --- pre-work backlog cross-check (0.9.22) ---------------------------------------------------

def _bc_rows(d, tmp_path, cfg):
    return {n: s for n, s, _ in d.features(_sdlc(tmp_path, cfg))}


def test_features_backlog_check_off_by_default_and_on_when_enabled(tmp_path):
    d = _doc()
    assert _bc_rows(d, tmp_path, {})["pre-work backlog cross-check"].startswith("off")   # absent -> off
    on = _bc_rows(d, tmp_path / "on", {"backlog_check": {"enabled": True}})["pre-work backlog cross-check"]
    assert on.startswith("ON") and "parks a confident" in on
    flag = _bc_rows(d, tmp_path / "flag",
                    {"backlog_check": {"enabled": True, "action": "flag"}})["pre-work backlog cross-check"]
    assert "flag mode" in flag and "never parks" in flag


def test_features_backlog_check_enabled_is_strict_true_not_truthy(tmp_path):
    d = _doc()
    # a stringy "true" / 1 must NOT switch a pick-path behavior on
    assert _bc_rows(d, tmp_path, {"backlog_check": {"enabled": "true"}})["pre-work backlog cross-check"].startswith("off")
    assert _bc_rows(d, tmp_path / "one", {"backlog_check": {"enabled": 1}})["pre-work backlog cross-check"].startswith("off")


def test_features_backlog_check_malformed_block_does_not_crash(tmp_path):
    d = _doc()
    assert _bc_rows(d, tmp_path, {"backlog_check": "yes please"})["pre-work backlog cross-check"].startswith("off")


def test_check_flags_park_threshold_below_dup_threshold(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"backlog_check": {"enabled": True, "dup_threshold": 0.72, "park_threshold": 0.5}})
    checks = {c["name"]: c for c in d.check(base, run=_runner())}
    assert checks["backlog cross-check thresholds sane"]["ok"] is False
    assert "park_threshold" in checks["backlog cross-check thresholds sane"]["fix"]


def test_check_does_not_crash_on_a_non_dict_backlog_check_block(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"backlog_check": "yes please"})     # malformed -> reads as off, no crash
    names = {c["name"] for c in d.check(base, run=_runner())}
    assert "backlog cross-check thresholds sane" not in names


def test_check_backlog_thresholds_ok_when_sane_and_absent_when_disabled(tmp_path):
    d = _doc()
    sane = _sdlc(tmp_path, {"backlog_check": {"enabled": True, "dup_threshold": 0.72, "park_threshold": 0.8}})
    assert {c["name"]: c for c in d.check(sane, run=_runner())}["backlog cross-check thresholds sane"]["ok"] is True
    off = _sdlc(tmp_path / "off", {"backlog_check": {"enabled": False, "park_threshold": 0.1}})
    assert "backlog cross-check thresholds sane" not in {c["name"] for c in d.check(off, run=_runner())}


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


_BOARD_FIELDS = {"fields": [
    {"id": "F_status", "name": "Status", "options": [{"id": "s1", "name": "Todo"}]},
    {"id": "F_pri", "name": "Priority", "options": [{"id": "p1", "name": "High"}]},
    {"id": "F_sec", "name": "Section", "options": [{"id": "x1", "name": "Task"}]},
    {"id": "F_due", "name": "Due date", "type": "ProjectV2Field"},   # text/date -> no options -> not flaggable
]}


def test_unmapped_board_fields_flags_only_unmapped_single_selects():
    import json as _json
    d = _doc()
    run = (lambda a: _json.dumps(_BOARD_FIELDS) if a[:3] == ["gh", "project", "field-list"] else "")
    cfg = {"repo": "acme/widget", "project": {"enabled": True, "number": 8}}
    # Status (driven) is excluded; the two custom single-selects are flagged; the date field isn't
    assert d._unmapped_board_fields(cfg, run) == ["Priority", "Section"]
    # mapping one leaves only the other
    cfg["project"]["custom_fields"] = {"Priority": "High"}
    assert d._unmapped_board_fields(cfg, run) == ["Section"]
    # a custom status_field name is the one excluded instead of "Status"
    cfg2 = {"repo": "acme/widget", "project": {"enabled": True, "number": 8, "status_field": "Priority"}}
    assert "Priority" not in d._unmapped_board_fields(cfg2, run)


def test_doctor_output_survives_a_non_utf8_locale(tmp_path):
    """Windows cp1252 / C locale: the em-dash-heavy dashboard must not crash with UnicodeEncodeError
    or garble to '?'. The UTF-8 output guard reconfigures the streams so non-ASCII is emitted cleanly."""
    import subprocess, os, sys, json, pathlib as _pl
    base = tmp_path / ".sdlc"; base.mkdir()
    base.joinpath("config.json").write_text(json.dumps({}))   # default features incl. an em-dash row
    D = _pl.Path(__file__).resolve().parent.parent / "skills" / "sdlc-doctor" / "scripts" / "doctor.py"
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONIOENCODING="ascii")
    p = subprocess.run([sys.executable, str(D), "features", str(base)],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr                        # no UnicodeEncodeError crash
    assert "per-goal worktree + PR" in p.stdout               # output actually came through


def test_board_dup_risk_flags_unpinned_number_with_existing_boards():
    import json as _json
    d = _doc()
    boards = {"projects": [{"number": 7, "title": "Acme Delivery Board"}]}
    run = (lambda a: _json.dumps(boards) if a[:3] == ["gh", "project", "list"] else "")
    # no number pinned + owner already has a board => duplicate-board risk
    cfg = {"repo": "acme/widget", "project": {"enabled": True, "owner": "acme"}}
    fix = d._board_dup_risk(cfg, run)
    assert fix and "project.number" in fix and "Acme Delivery Board" in fix
    # pinning a number removes the risk (resolves directly, no create path)
    cfg["project"]["number"] = 7
    assert d._board_dup_risk(cfg, run) is None
    # owner with ZERO boards => a fresh create is safe, not flagged
    run0 = (lambda a: _json.dumps({"projects": []}) if a[:3] == ["gh", "project", "list"] else "")
    assert d._board_dup_risk({"repo": "acme/widget", "project": {"enabled": True}}, run0) is None
    # a board already titled loopsmith's default `<repo> — SDLC` resolves by title => NO false alarm
    match = (lambda a: _json.dumps({"projects": [{"number": 3, "title": "widget — SDLC"}]})
             if a[:3] == ["gh", "project", "list"] else "")
    assert d._board_dup_risk({"repo": "acme/widget", "project": {"enabled": True}}, match) is None
    # can't read the board list => None (no false alarm)
    assert d._board_dup_risk({"repo": "acme/widget", "project": {"enabled": True}}, lambda a: "") is None


def test_check_surfaces_board_dup_risk(tmp_path):
    import json as _json
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github",
                 "github": {"repo": "acme/widget", "project": {"enabled": True, "owner": "acme"}}}})

    def run(a):
        if a[:3] == ["gh", "auth", "status"]:
            return "Logged in ... token scopes: project"
        if a[:3] == ["gh", "project", "list"]:
            return _json.dumps({"projects": [{"number": 7, "title": "Acme Delivery Board"}]})
        return ""
    checks = {c["name"]: c for c in d.check(base, run=run)}
    assert checks["project.number pinned (no duplicate-board risk)"]["ok"] is False


def test_unmapped_board_fields_survives_a_malformed_custom_fields():
    """A non-dict custom_fields (e.g. a list) must NOT crash the doctor run — the helper treats it as
    'nothing mapped' and still reports the fields, staying fail-open like the rest of doctor."""
    import json as _json
    d = _doc()
    run = (lambda a: _json.dumps(_BOARD_FIELDS) if a[:3] == ["gh", "project", "field-list"] else "")
    cfg = {"repo": "acme/widget", "project": {"enabled": True, "number": 8, "custom_fields": ["Priority"]}}
    assert d._unmapped_board_fields(cfg, run) == ["Priority", "Section"]      # no crash; list => nothing mapped


def test_unmapped_board_fields_none_when_board_unreadable():
    d = _doc()
    # no project number yet -> can't enumerate -> None (never a false all-clear)
    assert d._unmapped_board_fields({"project": {"enabled": True, "owner": "acme"}}, lambda a: "") is None
    # number present but the call returns nothing (e.g. missing `project` scope) -> None
    cfg = {"repo": "acme/widget", "project": {"enabled": True, "number": 8}}
    assert d._unmapped_board_fields(cfg, lambda a: "") is None


def test_check_surfaces_unmapped_board_fields(tmp_path):
    import json as _json
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github",
                 "github": {"repo": "acme/widget", "project": {"enabled": True, "number": 8}}}})

    def run(a):
        if a[:3] == ["gh", "auth", "status"]:
            return "Logged in to github.com ... token scopes: project"
        if a[:3] == ["gh", "project", "field-list"]:
            return _json.dumps(_BOARD_FIELDS)
        return ""
    checks = {c["name"]: c for c in d.check(base, run=run)}
    assert checks["board custom fields mapped"]["ok"] is False
    assert "Priority" in checks["board custom fields mapped"]["fix"] and "Section" in checks["board custom fields mapped"]["fix"]


def test_features_reports_independent_review_states(tmp_path):
    d = _doc()
    row = "independent review (maker is never the checker)"
    # default (no block) reads as ON — separation is the default
    assert d._review_independence_state({}).startswith("ON")
    # explicit off is called out as the maker reviewing its own work
    assert "INLINE" in d._review_independence_state({"review": {"independent": False}})
    # and it appears in the live dashboard
    base = _sdlc(tmp_path, {"review": {"independent": True}})
    rows = {name: state for name, state, _ in d.features(base)}
    assert rows[row].startswith("ON")
