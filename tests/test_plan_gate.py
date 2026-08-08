"""Tests for the OPT-IN hard plan-gate (hooks/plan_gate.sh). Default off = allows
everything silently; on = denies source edits without a fresh .sdlc/plans/*.md."""
import json, os, pathlib, re, subprocess, time

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "plan_gate.sh"
COMPLETION_HOOK = HOOK.parent / "completion_gate.sh"


def _run(project_dir, file_path):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"tool_input": {"file_path": file_path}}),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    # Deny is the ONLY output this script may print (module docstring): a bash diagnostic reaching
    # stderr means an expansion died, and under `set -u` that silently changes the verdict. Same
    # pin the sibling Stop gate keeps (test_completion_gate.py's non-numeric-freshness case).
    assert proc.stderr == "", proc.stderr
    return proc.stdout.strip()


def _project(tmp_path, enabled=True, fresh_hours=24):
    base = tmp_path / ".sdlc"
    base.mkdir()
    base.joinpath("config.json").write_text(json.dumps(
        {"gates": {"hard_plan_gate": {"enabled": enabled,
                                      "plan_freshness_hours": fresh_hours}}}))
    return tmp_path


def _deny_reason(out):
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_default_off_allows_everything(tmp_path):
    # no .sdlc at all → silent allow
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""
    # .sdlc present but flag off → silent allow
    _project(tmp_path, enabled=False)
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_enabled_denies_source_edit_without_plan(tmp_path):
    _project(tmp_path)
    out = _run(tmp_path, str(tmp_path / "app.py"))
    assert "deny" in out and "no fresh plan" in _deny_reason(out)


def test_enabled_allows_docs_config_and_sdlc_layer(tmp_path):
    _project(tmp_path)
    for path in ("README.md", "config.json", "notes.txt",
                 str(tmp_path / ".sdlc" / "goals" / "g.md"),
                 str(tmp_path / "docs" / "spec.md")):
        assert _run(tmp_path, path) == ""


def test_fresh_plan_unblocks_source_edits(tmp_path):
    _project(tmp_path)
    plans = tmp_path / ".sdlc" / "plans"
    plans.mkdir()
    (plans / "0001-plan.md").write_text("# plan")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_stale_plan_still_denies(tmp_path):
    _project(tmp_path, fresh_hours=1)
    plans = tmp_path / ".sdlc" / "plans"
    plans.mkdir()
    plan = plans / "0001-plan.md"
    plan.write_text("# plan")
    two_hours_ago = time.time() - 7200
    os.utime(plan, (two_hours_ago, two_hours_ago))
    out = _run(tmp_path, str(tmp_path / "app.py"))
    assert "deny" in out


def test_override_sentinel_allows(tmp_path):
    _project(tmp_path)
    (tmp_path / ".sdlc" / ".allow-direct-edits").write_text("")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_malformed_stdin_fails_open(tmp_path):
    _project(tmp_path)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    proc = subprocess.run(["bash", str(HOOK)], input="garbage{{{",
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_deny_output_is_valid_hook_json(tmp_path):
    _project(tmp_path)
    out = _run(tmp_path, str(tmp_path / "core.go"))
    payload = json.loads(out)["hookSpecificOutput"]
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"


# --- #536: a bad freshness value must never LOCK THE GATE ON ------------------------------------
# Both cases below are silent lockouts on the unfixed script: a fresh plan is sitting there and the
# edit is denied anyway. They are distinct failures, and the second is why the numeric case guard
# is load-bearing rather than redundant with the inner try.

def test_non_numeric_freshness_does_not_lock_out_a_fresh_plan(tmp_path):
    # The mode line was printed BEFORE int() could raise inside the same try, so an unparseable
    # window emitted three lines and the by-position read handed "off" to $(( )). Under `set -u`
    # the arithmetic died, the find never ran, and every source edit was denied -- with an
    # unbound-variable diagnostic leaking to stderr (caught by _run's own pin).
    _project(tmp_path, fresh_hours="24h")
    plans = tmp_path / ".sdlc" / "plans"; plans.mkdir()
    (plans / "0001-plan.md").write_text("# plan")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_negative_freshness_does_not_lock_out_a_fresh_plan(tmp_path):
    # The SECOND lockout, and a FULLY SILENT one: int(-5) parses, so the inner try never fires and
    # nothing reaches stderr. Only the numeric case guard rejects the leading '-'. Without it,
    # `-$((-5 * 60))` expands to `--300`, find refuses the argument (its stderr is suppressed),
    # and a fresh plan is denied with no diagnostic anywhere. This is what isolates the guard.
    _project(tmp_path, fresh_hours=-5)
    plans = tmp_path / ".sdlc" / "plans"; plans.mkdir()
    (plans / "0001-plan.md").write_text("# plan")
    assert _run(tmp_path, str(tmp_path / "app.py")) == ""


def test_non_numeric_freshness_deny_reason_names_a_real_window(tmp_path):
    # Denying is correct here (the plan IS stale) -- what must never ship is the reason telling an
    # engineer the freshness window is "offh". Epoch mtime is the sibling's own staleness recipe:
    # with an unparseable value the post-fix window is the 24h default, which two-hours-ago would
    # still satisfy.
    _project(tmp_path, fresh_hours="24h")
    plans = tmp_path / ".sdlc" / "plans"; plans.mkdir()
    old = plans / "0001-plan.md"; old.write_text("# plan")
    os.utime(old, (0, 0))
    reason = _deny_reason(_run(tmp_path, str(tmp_path / "app.py")))
    assert "offh" not in reason and "24h" in reason


# --- #536: the source-extension list had drifted from the sibling gate --------------------------

def test_scala_and_elixir_source_edits_are_gated(tmp_path):
    # An unrecognized extension exits 0 (silent allow), so a missing entry does not fail loudly --
    # it makes the edit gate inert for that language while the Stop gate still fires on it.
    _project(tmp_path)
    for name in ("Main.scala", "app.ex", "script.exs"):
        assert "deny" in _run(tmp_path, str(tmp_path / name)), name


def _source_extensions(text, what):
    # The source-extension `case` alternation. Anchored on the literal `*.py|`, NOT a bare `*.`
    # scan: plan_gate.sh has an earlier docs-exclusion case block that a loose pattern also hits.
    m = re.search(r"(\*\.py\|[^)]*)\)", text)
    assert m, "could not locate the source-extension case pattern in %s" % what
    return {p.strip().removeprefix("*.") for p in m.group(1).split("|")}


def test_source_extension_lists_stay_in_sync_with_the_completion_gate():
    """The two gates deliberately keep inlined copies of this list -- a hook cannot reliably source
    another file across the plugin layout, and a failed `source` under `set -u` would break the
    fail-open promise both gates document (the same failure class as the freshness lockout above).
    So the shared constant is enforced by test, the way scrub.py/research_capture.py and
    risk-detect/alignment-collect already are. EXACT equality, not a superset: a superset assert
    would let the Stop gate quietly LOSE an extension while this one kept it."""
    plan_exts = _source_extensions(HOOK.read_text(encoding="utf-8"), "plan_gate.sh")
    comp_exts = _source_extensions(COMPLETION_HOOK.read_text(encoding="utf-8"), "completion_gate.sh")
    assert plan_exts == comp_exts, "plan_gate.sh and completion_gate.sh source extensions drifted"
    # and the shared set really did grow to the sibling's, rather than the two meeting in the middle
    for ext in ("scala", "ex", "exs"):
        assert ext in plan_exts and ext in comp_exts
