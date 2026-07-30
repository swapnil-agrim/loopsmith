"""session_start.sh (Slice 5): the OPT-IN SessionStart hook. When enabled, it injects a short SDLC
policy brief (+ a doctor-lite install self-check) as additionalContext so LoopSmith's conventions are in
context before the first prompt. Guards: OFF by default (silent unless .sdlc/ AND opted in), valid JSON
output, the self-check warning, and fail-open."""
import json, os, subprocess, pathlib

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "session_start.sh"


def _run(project_dir, payload="{}"):
    p = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _enabled(tmp_path, north_star=True):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"session_start":{"enabled":true}}')
    if north_star:
        (tmp_path / ".sdlc" / "context").mkdir()
        (tmp_path / ".sdlc" / "context" / "north-star.md").write_text("# north star\n")
    return tmp_path


def _context(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


# --- OFF by default ---

def test_no_config_is_silent(tmp_path):
    assert _run(tmp_path) == ""


def test_sdlc_present_but_not_enabled_is_silent(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text("{}")
    assert _run(tmp_path) == ""


def test_enabled_must_be_strict_true(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"session_start":{"enabled":"true"}}')
    assert _run(tmp_path) == ""          # a string "true" must not enable


# --- enabled: injects valid JSON policy ---

def test_enabled_injects_session_start_context(tmp_path):
    out = _run(_enabled(tmp_path))
    d = json.loads(out)                                  # must be valid JSON
    assert d["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = d["hookSpecificOutput"]["additionalContext"]
    assert "/sdlc-loop" in ctx and "north-star" in ctx and "reviewer is never the author" in ctx


def test_missing_north_star_adds_a_warning(tmp_path):
    ctx = _context(_run(_enabled(tmp_path, north_star=False)))
    assert "setup notes" in ctx and "/sdlc-vision" in ctx   # doctor-lite self-check warns, never blocks


def test_present_north_star_no_warning(tmp_path):
    ctx = _context(_run(_enabled(tmp_path, north_star=True)))
    assert "setup notes" not in ctx


def test_output_is_valid_json_on_warning_path(tmp_path):
    # the warning prepends a paragraph break; the emitted JSON must still parse (control chars escaped)
    json.loads(_run(_enabled(tmp_path, north_star=False)))


# --- fail-open + wiring ---

def test_no_sdlc_dir_is_silent(tmp_path):
    # a repo that never adopted LoopSmith is completely untouched
    assert _run(tmp_path) == ""


def test_wired_into_hooks_json():
    hooks = json.loads((HOOK.parent / "hooks.json").read_text())
    ss = hooks["hooks"].get("SessionStart", [])
    assert any("session_start.sh" in json.dumps(h) for h in ss), "SessionStart hook not wired"
