"""Decision gate: the one guardrail in this kit that is not a prompt. An `invariant` violation is
DENIED before the edit happens, by a script that does not negotiate.

The value of a gate like this is entirely in its precision. A gate that cries wolf gets clicked
through, and then it protects nothing — so most of these test the cases where it must STAY SILENT,
not the ones where it fires."""
import json, pathlib, importlib.util, tempfile, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
G = ROOT / "hooks" / "decision_gate.py"


def _gate():
    spec = importlib.util.spec_from_file_location("decision_gate", G)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _reg(*decisions):
    return {"version": 1, "decisions": list(decisions)}


def _inv(**over):
    d = {"id": "INV-001", "title": "Bounded timeouts", "class": "invariant", "status": "active",
         "statement": "No call may set a timeout above 30s.", "rationale": "one slow dep = outage",
         "protected_paths": ["src/**/*.py"],
         "protected_params": [{"name": "timeout", "op": "le", "value": 30}]}
    d.update(over)
    return d


def _edit(path, new):
    return ("Edit", {"file_path": path, "new_string": new})


# --- it fires when it should -------------------------------------------------------------------

def test_denies_an_invariant_violation():
    m = _gate()
    tool, ti = _edit("/repo/src/api/client.py", "timeout = 120")
    decision, reason = m.evaluate(tool, ti, _reg(_inv()), "/repo")
    assert decision == "deny"
    assert "timeout=120" in reason and "one slow dep = outage" in reason   # statement + why


def test_asks_rather_than_denies_on_a_recipe():
    """A recipe is a proven default worth a second thought, not a law."""
    m = _gate()
    tool, ti = _edit("/repo/src/a.py", "timeout = 120")
    assert m.evaluate(tool, ti, _reg(_inv(**{"class": "recipe"})), "/repo")[0] == "ask"


def test_caution_on_touch_guards_a_path_with_no_params():
    """Without this, a decision guarding a dangerous PATH but declaring no params falls through to
    allow SILENTLY — so code that deploys or spends money could not be guarded at all."""
    m = _gate()
    d = _inv(id="INV-9", protected_params=[], caution_on_touch=True,
             protected_paths=["deploy/**"], statement="Deploy scripts spend money.")
    decision, reason = m.evaluate(*_edit("/repo/deploy/ship.py", "x = 1"), _reg(d), "/repo")
    assert decision == "ask" and "spend money" in reason


def test_editing_the_registry_always_asks():
    """Changing a recorded invariant is a supersession the user makes deliberately — never a silent
    edit by the agent that is about to be bound by it."""
    m = _gate()
    decision, reason = m.evaluate(*_edit("/repo/.sdlc/decisions.json", "{}"), _reg(), "/repo")
    assert decision == "ask" and "supersede" in reason.lower()


# --- it stays silent when it should — the tests that make it trustworthy ------------------------

def test_a_value_that_still_satisfies_is_allowed():
    m = _gate()
    assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 5"), _reg(_inv()), "/repo")[0] == "allow"


def test_the_param_is_scoped_to_its_own_decisions_paths():
    """A name as common as `timeout` appears everywhere. Checking it outside the paths its decision
    declares is what would make this gate unusable."""
    m = _gate()
    assert m.evaluate(*_edit("/repo/tests/t.py", "timeout = 999"), _reg(_inv()), "/repo")[0] == "allow"


def test_prose_and_comments_do_not_trip_it():
    m = _gate()
    r = _reg(_inv())
    assert m.evaluate(*_edit("/repo/src/a.py", "# timeout = 120 was the old default"), r, "/repo")[0] == "allow"
    assert m.evaluate(*_edit("/repo/src/a.py", 'doc = "set timeout: 120 here"'), r, "/repo")[0] == "allow"


def test_a_similar_name_is_not_the_protected_one():
    m = _gate()
    r = _reg(_inv())
    for line in ("read_timeout = 120", "self.timeout_ms = 120", "x.timeout = 120"):
        assert m.evaluate(*_edit("/repo/src/a.py", line), r, "/repo")[0] == "allow", line


def test_non_literal_values_are_left_alone():
    """The gate judges values it can actually read. Guessing at an expression would produce false
    denies, which cost more trust than the misses cost safety."""
    m = _gate()
    r = _reg(_inv())
    for line in ("timeout = CONFIG.default", "timeout = compute()", "timeout = a + b"):
        assert m.evaluate(*_edit("/repo/src/a.py", line), r, "/repo")[0] == "allow", line


def test_superseded_decisions_do_not_fire():
    m = _gate()
    r = _reg(_inv(status="superseded"))
    assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 120"), r, "/repo")[0] == "allow"


def test_non_edit_tools_and_empty_registries_allow():
    m = _gate()
    assert m.evaluate("Bash", {"command": "rm -rf /"}, _reg(_inv()), "/repo")[0] == "allow"
    assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 120"), _reg(), "/repo")[0] == "allow"


def test_float_comparison_uses_tolerance_not_equality():
    """`eq` on a float via naive == would deny a value that is correct to every digit that matters."""
    m = _gate()
    d = _inv(protected_params=[{"name": "ratio", "op": "eq", "value": 0.1}])
    assert m.evaluate(*_edit("/repo/src/a.py", "ratio = 0.1"), _reg(d), "/repo")[0] == "allow"


def test_in_op_and_booleans():
    m = _gate()
    d = _inv(protected_params=[{"name": "region", "op": "in", "value": ["eu", "us"]}])
    assert m.evaluate(*_edit("/repo/src/a.py", 'region = "eu"'), _reg(d), "/repo")[0] == "allow"
    assert m.evaluate(*_edit("/repo/src/a.py", 'region = "cn"'), _reg(d), "/repo")[0] == "deny"
    b = _inv(protected_params=[{"name": "verify_ssl", "op": "eq", "value": True}])
    assert m.evaluate(*_edit("/repo/src/a.py", "verify_ssl = False"), _reg(b), "/repo")[0] == "deny"


# --- fail-open and opt-in ------------------------------------------------------------------------

def test_a_malformed_registry_never_blocks():
    """Fail-open is the contract: a gate that wedges you on its own bug is worse than a missed
    check. `check` is the backstop."""
    m = _gate()
    for bad in ({"decisions": "not-a-list"}, {"decisions": [{"id": "X"}]}, {}):
        assert m.evaluate(*_edit("/repo/src/a.py", "timeout = 120"), bad, "/repo")[0] == "allow"


def test_hook_allows_when_no_registry_exists(tmp_path=None):
    """Installing the plugin must change nothing until a repo authors a registry."""
    with tempfile.TemporaryDirectory() as d:
        out = subprocess.run([sys.executable, str(G)], cwd=d,
                             input=json.dumps({"tool_name": "Edit",
                                               "tool_input": {"file_path": "src/a.py",
                                                              "new_string": "timeout = 120"}}),
                             capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert out.returncode == 0 and out.stdout.strip() == ""     # silence = allow


def test_enabled_false_disables_without_deleting_the_registry():
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; base.mkdir()
        (base / "decisions.json").write_text(json.dumps(_reg(_inv())))
        assert m.enabled(d) is True                                  # registry alone = on
        (base / "config.json").write_text(json.dumps({"gates": {"decision_gate": {"enabled": False}}}))
        assert m.enabled(d) is False


# --- the backstop: check + validate ---------------------------------------------------------------

def test_check_finds_violations_already_on_disk():
    """The hook only guards NEW edits; code predating the registry is invisible to it. This is the
    first question anyone asks after authoring one."""
    m = _gate()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / ".sdlc").mkdir(); (root / "src").mkdir()
        (root / ".sdlc" / "decisions.json").write_text(json.dumps(_reg(_inv(protected_paths=["src/*.py"]))))
        (root / "src" / "bad.py").write_text("timeout = 300\n")
        (root / "src" / "ok.py").write_text("timeout = 10\n")
        found = m.check(d)
        assert len(found) == 1 and found[0][0] == "src/bad.py" and found[0][3] == 300


def test_validate_catches_entries_that_can_never_fire():
    """A registry's failure mode is being quietly unenforceable, not loudly broken."""
    m = _gate()
    problems = m.validate(_reg(
        {"id": "A", "class": "invariant", "protected_paths": [], "protected_params": []},
        {"id": "A", "class": "nonsense", "protected_paths": ["x"], "protected_params": []},
        {"id": "C", "class": "recipe", "protected_paths": ["x"],
         "protected_params": [{"name": "n", "op": "≈", "value": 1}]},
    ))
    joined = " ".join(problems)
    assert "duplicate" in joined and "never match" in joined
    assert "never fire" in joined and "expected one of" in joined


def test_cli_reports_cleanly_with_no_registry():
    out = subprocess.run([sys.executable, str(G), "check", tempfile.gettempdir()],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "no registry" in out.stdout


def test_registered_as_a_pretooluse_hook():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for entry in hooks["hooks"]["PreToolUse"] for h in entry["hooks"]]
    assert any("decision_gate.py" in c for c in cmds)


def test_no_source_repo_leakage():
    banned = ("media-orch", "OnShot", "onshot", "Temporal", "RunPod", "decisions.yaml", "yaml")
    src = G.read_text() + (ROOT / "skills" / "sdlc-decide" / "SKILL.md").read_text()
    for b in banned:
        assert b not in src, f"decision gate leaked '{b}'"


def test_double_star_matches_zero_directories_like_every_other_glob_dialect():
    """`fnmatch` has no path-segment concept, so a bare fnmatch of `src/**/*.py` misses `src/a.py`.
    A rule written that way would silently guard the nested files and not the top-level ones — the
    worst kind of failure, because the registry looks like it is protecting something."""
    m = _gate()
    pats = ["src/**/*.py"]
    assert m.matches("src/a.py", pats)              # zero directories
    assert m.matches("src/api/client.py", pats)     # one directory
    assert m.matches("src/a/b/c.py", pats)          # several
    assert not m.matches("tests/a.py", pats)
    assert not m.matches("src/a.ts", pats)


def test_leading_double_star_matches_the_repo_root():
    m = _gate()
    assert m.matches("Makefile", ["**/Makefile"])
    assert m.matches("build/Makefile", ["**/Makefile"])


def test_non_string_patterns_are_skipped_not_raised():
    m = _gate()
    assert m.matches("src/a.py", [None, 42, "src/a.py"]) is True
    assert m.matches("src/a.py", [None, 42]) is False


def test_a_violating_value_quoted_inside_prose_is_not_a_false_deny():
    """The single worst failure mode. A false deny teaches people to click through the gate, and a
    gate that gets clicked through protects nothing — so a quoted mention must stay silent while a
    real string-valued assignment still gets judged."""
    m = _gate()
    r = _reg(_inv())
    for line in ('doc = "set timeout: 120 here"',
                 "msg = 'timeout = 999 is wrong'",
                 'log.warn("timeout = 500 exceeded")'):
        assert m.evaluate(*_edit("/repo/src/a.py", line), r, "/repo")[0] == "allow", line
    # ...while a genuine string-valued assignment is still checked
    d = _inv(protected_params=[{"name": "region", "op": "in", "value": ["eu"]}])
    assert m.evaluate(*_edit("/repo/src/a.py", 'region = "cn"'), _reg(d), "/repo")[0] == "deny"
