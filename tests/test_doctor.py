"""sdlc-doctor: a setup check-up. doctor.check() audits only what THIS project's config makes relevant
(github board -> gh auth+scope; KG -> builder; vision-first -> north-star) and returns each check with
the exact one-line fix. The command runner is injectable so these are hermetic (no real gh/graphify)."""
import json, pathlib, importlib.util, tempfile

import pytest

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


# --------------------------------------------------------------- plugin update awareness (#378)
# Auto-update is off by default for a non-Anthropic marketplace, so a stale install can otherwise
# persist silently forever. Only reported when BOTH the installed and latest versions actually
# resolve -- an unreachable network or unrecognized output must never read as a false alarm OR a
# false all-clear.

_PLUGIN_LIST_JSON = json.dumps([{"id": "loopsmith@loopsmith", "version": "0.9.7"},
                                 {"id": "other-plugin@some-marketplace", "version": "1.2.3"}])
_MARKETPLACE_JSON = json.dumps({"plugins": [{"name": "loopsmith", "version": "0.9.23"},
                                             {"name": "other-plugin", "version": "1.2.3"}]})


def _version_runner(plugin_list=_PLUGIN_LIST_JSON, marketplace=_MARKETPLACE_JSON):
    def run(args):
        if args[:3] == ["claude", "plugin", "list"] and "--json" in args:
            return plugin_list
        if args[:1] == ["curl"]:
            return marketplace
        return _runner()(args)
    return run


def test_version_tuple_parses_plain_dotted_integers():
    d = _doc()
    assert d._version_tuple("0.9.23") == (0, 9, 23)
    assert d._version_tuple("1.0.0") == (1, 0, 0)


def test_version_tuple_is_none_for_anything_unparseable():
    d = _doc()
    assert d._version_tuple("not-a-version") is None
    assert d._version_tuple(None) is None
    assert d._version_tuple("") is None


def test_version_tuple_compares_numerically_not_lexicographically():
    """0.9.23 > 0.9.7 numerically, even though '2' < '7' as characters -- a naive string compare
    would get this exactly backwards."""
    d = _doc()
    assert d._version_tuple("0.9.23") > d._version_tuple("0.9.7")


def test_plugin_versions_resolves_both_sides():
    d = _doc()
    installed, latest = d._plugin_versions(_version_runner())
    assert installed == (0, 9, 7)
    assert latest == (0, 9, 23)


def test_plugin_versions_ignores_other_plugins_in_the_list():
    d = _doc()
    installed, _ = d._plugin_versions(_version_runner(
        plugin_list=json.dumps([{"id": "other-plugin@some-marketplace", "version": "9.9.9"}])))
    assert installed is None


def test_plugin_versions_is_none_when_claude_cli_is_unavailable():
    d = _doc()
    installed, latest = d._plugin_versions(_version_runner(plugin_list=""))
    assert installed is None
    assert latest == (0, 9, 23)          # the OTHER side still resolves independently


def test_plugin_versions_is_none_when_the_network_fetch_fails():
    d = _doc()
    installed, latest = d._plugin_versions(_version_runner(marketplace=""))
    assert installed == (0, 9, 7)
    assert latest is None


def test_plugin_versions_is_none_on_malformed_json_never_raises():
    d = _doc()
    installed, latest = d._plugin_versions(_version_runner(
        plugin_list="not json", marketplace="also not json"))
    assert (installed, latest) == (None, None)


def test_check_flags_an_out_of_date_install():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "local-goals"}})
        c = _by_name(d.check(base, run=_version_runner()))
        entry = c["loopsmith up to date (installed 0.9.7)"]
        assert entry["ok"] is False
        assert "0.9.23 is available" in entry["fix"]
        assert "claude plugin update loopsmith" in entry["fix"]


def test_check_passes_when_already_current():
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "local-goals"}})
        same = json.dumps([{"id": "loopsmith@loopsmith", "version": "0.9.23"}])
        c = _by_name(d.check(base, run=_version_runner(plugin_list=same)))
        assert c["loopsmith up to date (installed 0.9.23)"]["ok"] is True


def test_check_adds_no_entry_at_all_when_either_side_is_undeterminable():
    """A can't-tell (offline, claude CLI missing, whatever) must never show as a false alarm --
    the default fake runner returns "" for everything, so both sides fail to resolve."""
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"discovery": {"source": "local-goals"}})
        names = [c["name"] for c in d.check(base, run=_runner())]
        assert not any("loopsmith up to date" in n for n in names)


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


# --- F17/#342 review: doctor.py reads the SAME verify.enforce as loop.py's own done-gate --------
# Independent review of the original F17 fix found the two were built together as a matched pair
# (loop.py's `record done` gate + doctor's own "permanent-refusal trap" check) and both used the
# same fragile `is True`. Fixing only loop.py would have turned a latent inconsistency (both sides
# silently NOT enforcing a non-bool truthy value) into an actively misleading one: loop.py now
# genuinely refuses every `done` for `enforce: 1`, while doctor stayed silent and its status row
# claimed "off". These pin doctor's own generous read AND keep it in lockstep with loop.py's.

def test_flags_verify_enforce_with_empty_command_for_non_bool_truthy_values():
    d = _doc()
    for enforce_value in (1, "true", "yes"):
        with tempfile.TemporaryDirectory() as t:
            base = _sdlc(t, {"verify": {"enforce": enforce_value, "command": ""}})
            c = _by_name(d.check(base, run=_runner()))["verify command present (enforce is on)"]
            assert c["ok"] is False and "every `done` is refused" in c["fix"], enforce_value


def test_features_dashboard_reports_enforce_on_for_non_bool_truthy_values():
    d = _doc()
    for enforce_value in (1, "true", "yes"):
        with tempfile.TemporaryDirectory() as t:
            base = _sdlc(t, {"verify": {"enforce": enforce_value}})
            rows = {name: state for name, state, _ in d.features(base)}
            assert rows["machine-checked done (verify.enforce)"].startswith("ON"), enforce_value


def test_doctor_and_loop_enforce_reads_agree_on_the_same_truth_table():
    """The parity check the duplication comment promises: doctor.py's _enforce_enabled and loop.py's
    own copy must never silently drift apart. Runs the SAME representative inputs (the full set from
    F17's own investigation, including the ones that only matter for a safety-gate's fail-direction)
    through both and asserts identical results."""
    d = _doc()
    L = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts" / "loop.py"
    spec = importlib.util.spec_from_file_location("loop", L)
    loop = importlib.util.module_from_spec(spec); spec.loader.exec_module(loop)

    for value in (True, False, 1, 0, -1, "true", "True", "FALSE", "false", "yes", "no", "off", "on",
                  "", None, [], {}, ["true"], 1.0, "disabled", "null", "  false  "):
        verify = {"enforce": value} if value is not None else {}
        assert d._enforce_enabled(verify) == loop._enforce_enabled(verify), value


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


def test_flags_a_dot_slash_prefixed_relative_dep_when_work_on():
    """F23/#351: `./node_modules/…` is still relative to the goal worktree — the lookbehind that
    exempts absolute paths must not also swallow an explicit `./` prefix."""
    d = _doc()
    with tempfile.TemporaryDirectory() as t:
        base = _sdlc(t, {"work": {"enabled": True},
                         "verify": {"command": "./node_modules/.bin/eslint ."}})
        c = _by_name(d.check(base, run=_runner()))["verify.command resolves in the goal worktree"]
        assert c["ok"] is False and "exit=127" in c["fix"]


def test_flags_a_dot_dot_slash_prefixed_relative_dep_when_work_on():
    """F23/#351: same footgun for `../venv/…` (and repeated `../../…`) — a parent-relative dep path
    is still relative to the goal worktree, not an absolute path, so it must be flagged too."""
    d = _doc()
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
        base = _sdlc(t1, {"work": {"enabled": True},
                          "verify": {"command": "../venv/bin/python -m pytest -q"}})
        c = _by_name(d.check(base, run=_runner()))["verify.command resolves in the goal worktree"]
        assert c["ok"] is False and "exit=127" in c["fix"]

        base2 = _sdlc(t2, {"work": {"enabled": True},
                           "verify": {"command": "../../node_modules/.bin/eslint ."}})
        c2 = _by_name(d.check(base2, run=_runner()))["verify.command resolves in the goal worktree"]
        assert c2["ok"] is False and "exit=127" in c2["fix"]


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


def test_check_flags_embed_enabled_with_no_command(tmp_path):
    d = _doc()
    dead = _sdlc(tmp_path, {"backlog_check": {"enabled": True, "embed": {"enabled": True, "command": ""}}})
    assert {c["name"]: c for c in d.check(dead, run=_runner())}["backlog cross-check embedder configured"]["ok"] is False
    live = _sdlc(tmp_path / "live",
                 {"backlog_check": {"enabled": True, "embed": {"enabled": True, "command": "my-embedder"}}})
    assert {c["name"]: c for c in d.check(live, run=_runner())}["backlog cross-check embedder configured"]["ok"] is True


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


# --- #389: /sdlc-doctor dependency-marker check -- a comment matching backlog_check._BLOCK_RE with
# NO matching body marker is likely-intended-but-silently-ignored by precheck(). Cost-bounded (R6:
# default max_issues=10, ~6s added on a real repo, down from an initial 30/~18.5s draft) and the
# bound is always visibly reported in the check's own `name`, pass or fail -- never silently applied.
# NOT gated on backlog_check.enabled (same github-only gating as the existing gh auth/project checks).

def _dm_run(issues, comments=None, view_calls=None):
    """Fake doctor runner: `gh issue list` answers `issues` ([{"number", "body"}, ...]); every
    `gh issue view ... --json comments` answers the SAME canned `comments` list (tests that care which
    issue was asked don't need to here -- there is only ever one real candidate in play). Records every
    `issue view` call into `view_calls` when given, so a test can assert on cost (how many, not just
    whether)."""
    comments = comments if comments is not None else []

    def run(args):
        if args[:3] == ["gh", "auth", "status"]:
            return "Logged in."
        if args[:3] == ["gh", "issue", "list"]:
            return json.dumps(issues)
        if args[:3] == ["gh", "issue", "view"]:
            if view_calls is not None:
                view_calls.append(list(args))
            return json.dumps({"comments": comments})
        return ""
    return run


def _dm_check(checks):
    return next(c for n, c in checks.items() if n.startswith("dependency markers:"))


def test_dependency_marker_doctor_check_flags_comment_only_marker(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github", "github": {"repo": "acme/widget"}}})
    issues = [{"number": 42, "body": "no marker here"}]
    comment = {"id": "IC_1", "author": {"login": "bob"}, "body": "blocked by #9 until that lands",
               "createdAt": "2026-08-01T00:00:00Z"}
    checks = {c["name"]: c for c in d.check(base, run=_dm_run(issues, [comment]))}
    hit = _dm_check(checks)
    assert hit["ok"] is False
    assert "#42" in hit["fix"]


def test_dependency_marker_doctor_check_silent_when_body_already_has_marker(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github", "github": {"repo": "acme/widget"}}})
    issues = [{"number": 9, "body": "do the migration\n\n**Blocked by:** #3"}]
    view_calls = []
    checks = {c["name"]: c for c in d.check(base, run=_dm_run(issues, view_calls=view_calls))}
    hit = _dm_check(checks)
    assert hit["ok"] is True
    assert view_calls == []          # a body-marked issue is never charged a comment fetch (cost proof)


def test_dependency_marker_doctor_check_caps_at_max_issues_and_reports_the_bound(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github", "github": {"repo": "acme/widget"}},
                            "backlog_check": {"doctor_scan": {"max_issues": 5}}})
    issues = [{"number": n, "body": "no marker"} for n in range(1, 51)]     # 50 candidates, none pre-marked
    view_calls = []
    checks = {c["name"]: c for c in d.check(base, run=_dm_run(issues, view_calls=view_calls))}
    hit = _dm_check(checks)
    assert len(view_calls) == 5              # capped at max_issues, not charged for all 50
    assert "5/50" in hit["name"]             # the bound is visible on every run, pass or fail


def test_dependency_marker_doctor_check_skipped_in_local_mode(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "local-goals"}})
    names = [c["name"] for c in d.check(base, run=_runner())]
    assert not any(n.startswith("dependency markers:") for n in names)


def test_dependency_marker_doctor_check_default_max_issues_is_ten(tmp_path):
    """R6: the plan-review measured ~0.62s/call for `gh issue view --json comments` on a real repo;
    at the ORIGINAL draft default of 30 that is ~18.5s added to a routine /sdlc-doctor run (4-7x
    regression), and since candidates are issues WITHOUT a body marker -- nearly all of them in
    practice -- that cap is hit on essentially any real backlog, so it was the TYPICAL cost, not a
    worst case. Lowered to 10 (~6s) by default, still configurable."""
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github", "github": {"repo": "acme/widget"}}})
    issues = [{"number": n, "body": "no marker"} for n in range(1, 31)]    # 30 candidates, no override
    view_calls = []
    checks = {c["name"]: c for c in d.check(base, run=_dm_run(issues, view_calls=view_calls))}
    assert len(view_calls) == 10
    assert "10/30" in _dm_check(checks)["name"]


def test_dependency_marker_doctor_check_survives_a_malformed_doctor_scan_block(tmp_path):
    # F6 class: a truthy non-dict backlog_check.doctor_scan (a hand-edited config typo) must degrade
    # to the defaults, never crash the one tool an adopter runs BECAUSE their config is wrong --
    # matches every other block reader in this file (_block()'s own documented contract).
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github", "github": {"repo": "acme/widget"}},
                            "backlog_check": {"doctor_scan": "oops"}})
    issues = [{"number": 1, "body": "no marker"}]
    d.check(base, run=_dm_run(issues))    # must not raise


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


# --- F6: a truthy non-dict config block must never crash check()/features() -------------------


def test_block_helper_degrades_a_non_dict_to_empty():
    d = _doc()
    assert d._block({"x": "oops"}, "x") == {}
    assert d._block({"x": ["a", "list"]}, "x") == {}
    assert d._block({"x": 42}, "x") == {}
    assert d._block({"x": True}, "x") == {}
    assert d._block({"x": None}, "x") == {}
    assert d._block({}, "x") == {}
    assert d._block({"x": {"y": 1}}, "x") == {"y": 1}          # a real dict passes through unchanged
    assert d._block("not a dict", "x") == {}                   # a non-dict cfg itself is also guarded
    assert d._block(["a", "list"], "x") == {}


def test_non_dict_top_level_config_json_reads_as_empty(tmp_path):
    # json.loads can succeed on a non-object top level ("[1,2]", "\"oops\"", "42") — _cfg must not
    # hand a list/str/int to every downstream cfg.get() call.
    d = _doc()
    base = pathlib.Path(tmp_path) / ".sdlc"
    base.mkdir(parents=True)
    (base / "config.json").write_text(json.dumps([1, 2, 3]))
    assert d._cfg(str(base)) == {}
    d.check(str(base), run=_runner())      # must not raise
    d.features(str(base))                  # must not raise


def test_the_issues_exact_repro_does_not_crash(tmp_path):
    # {"verify": "pytest"} — a common shape typo (config value where a block was meant), and the
    # exact repro from the finding this test guards against regressing.
    d = _doc()
    base = _sdlc(tmp_path, {"verify": "pytest"})
    d.features(base)                       # must not raise AttributeError
    d.check(base, run=_runner())           # must not raise AttributeError


_MALFORMED_BLOCKS = ["discovery", "knowledge_graph", "ledger", "verify", "work", "gates",
                     "review", "budget", "parallel", "session_start", "backlog_check", "telemetry"]


@pytest.mark.parametrize("bad_value", ["a string", ["a", "list"], 42, True], ids=["str", "list", "int", "bool"])
@pytest.mark.parametrize("block", _MALFORMED_BLOCKS)
def test_malformed_config_block_does_not_crash_check_or_features(tmp_path, block, bad_value):
    """A truthy non-dict value for ANY declared config block must degrade to reading as off, never
    raise — doctor is the one tool an adopter runs BECAUSE their config is wrong, so it must survive
    exactly the malformed input that brought them here."""
    d = _doc()
    base = _sdlc(tmp_path, {block: bad_value})
    checks = d.check(base, run=_runner())
    assert isinstance(checks, list) and all("ok" in c for c in checks)
    rows = d.features(base)
    assert isinstance(rows, list) and all(len(r) == 3 for r in rows)


def test_malformed_nested_gates_block_does_not_crash(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"gates": {"hard_plan_gate": "oops", "stop_gate": ["x"], "decision_gate": 1}})
    d.check(base, run=_runner())
    d.features(base)


def test_malformed_decision_gate_block_does_not_crash_with_a_registry_present(tmp_path):
    # `_decision_gate_state` short-circuits BEFORE reaching the malformed-block read when
    # decisions.json is absent (`if not reg.exists(): return "off..."`) — so a malformed
    # gates.decision_gate only actually reaches the vulnerable line when a registry exists (the
    # realistic state for any adopter who has run /sdlc-decide). Without the registry present,
    # this case would pass even on the unfixed code — reaching the line is the whole test.
    d = _doc()
    base = _sdlc(tmp_path, {"gates": {"decision_gate": "oops"}})
    (pathlib.Path(base) / "decisions.json").write_text(json.dumps({"decisions": []}))
    d.check(base, run=_runner())
    d.features(base)


def test_malformed_nested_github_project_block_does_not_crash(tmp_path):
    d = _doc()
    base = _sdlc(tmp_path, {"discovery": {"source": "github", "github": {"project": "oops"}}})
    d.check(base, run=_runner(gh_auth="Logged in. Token scopes: 'repo', 'project'"))


# --- F33/#358: "north-star filled" must clear every tier, not just Vision ----------------------
# The check used to test the file for ONLY the Vision-tier placeholder, so a north-star with Vision
# written up but Strategy/Design/Architecture still on the scaffolded placeholder text read as
# "filled" anyway.

_NS_VISION_ONLY = """# demo - North Star

## Vision (why this exists, for whom)
We help QA teams ship browser tests fast, for engineers who hate writing selectors by hand.

## Strategy (what we're building now)
- Priorities: <the few things that matter this cycle>
- Non-goals: <what we are deliberately NOT doing - the alignment gate uses these>

## Design (how the product should feel)
<the experience + the principles a change must respect>

## Architecture (how it's built + the rules we develop by)
<the shape of the system - the stack itself lives in project.md.>
1. <e.g. the UI layer holds no business logic>
"""

_NS_FULLY_FILLED = """# demo - North Star

## Vision (why this exists, for whom)
We help QA teams ship browser tests fast.

## Strategy (what we're building now)
- Priorities: ship the parser this cycle
- Non-goals: no mobile support yet

## Design (how the product should feel)
Fast, forgiving, terse output.

## Architecture (how it's built + the rules we develop by)
A LangGraph engine with Postgres state.
1. The UI layer holds no business logic
"""


def _ns(d, content):
    base = _sdlc(d, {})
    (pathlib.Path(base) / "context").mkdir(parents=True, exist_ok=True)
    (pathlib.Path(base) / "context" / "north-star.md").write_text(content)
    return base


def test_flags_not_filled_when_only_vision_tier_is_written(tmp_path):
    # The issue's own acceptance case: Vision filled, the other three tiers still on the scaffolded
    # placeholder text. Must NOT read as "filled".
    d = _doc()
    base = _ns(tmp_path, _NS_VISION_ONLY)
    c = _by_name(d.check(base, run=_runner()))
    assert c["north-star filled"]["ok"] is False
    assert "Strategy" in c["north-star filled"]["fix"]        # names the first unfilled tier


def test_north_star_filled_when_every_tier_is_written(tmp_path):
    d = _doc()
    base = _ns(tmp_path, _NS_FULLY_FILLED)
    c = _by_name(d.check(base, run=_runner()))
    assert c["north-star filled"]["ok"] is True
    assert c["north-star filled"]["fix"] == ""


def test_flags_not_filled_when_only_the_last_tier_is_a_placeholder(tmp_path):
    # Every tier but Architecture is written - proves the check walks ALL FOUR tiers (not just
    # Vision, the bug) and correctly names a LATER tier, not only ever the first one.
    content = _NS_FULLY_FILLED.replace(
        "A LangGraph engine with Postgres state.\n1. The UI layer holds no business logic\n",
        "<the shape of the system - the stack itself lives in project.md.>\n"
        "1. <e.g. the UI layer holds no business logic>\n",
    )
    d = _doc()
    base = _ns(tmp_path, content)
    c = _by_name(d.check(base, run=_runner()))
    assert c["north-star filled"]["ok"] is False
    assert "Architecture" in c["north-star filled"]["fix"]
