"""completion_gate.sh (Slice 4): the OPT-IN interactive Stop gate. When enabled, it refuses to let the
agent stop with unplanned SOURCE changes in the working tree — the Stop-time counterpart to the
PreToolUse plan_gate. Guards the safety-critical properties: OFF by default, fail-open, a working loop
guard (so a block never loops), and the deliberate-override sentinel."""
import json, os, subprocess, pathlib

GATE = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "completion_gate.sh"


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _run(project_dir, payload="{}"):
    p = subprocess.run(["bash", str(GATE)], input=payload, capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stderr          # the gate always exits 0 (block is via stdout JSON)
    return p.stdout.strip()


def _enabled_repo(tmp_path, extra_cfg=""):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    cfg = '{"gates":{"stop_gate":{"enabled":true%s}}}' % extra_cfg
    (tmp_path / ".sdlc" / "config.json").write_text(cfg)
    (tmp_path / "a.py").write_text("x = 1\n")   # an untracked source change
    return tmp_path


def _is_block(out):
    if not out:
        return False
    return json.loads(out).get("decision") == "block"


# --- OFF by default: the whole point — installing it changes nothing until opted in ---

def test_no_config_allows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""                 # no .sdlc/config.json -> silent allow


def test_config_without_stop_gate_allows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"gates":{}}')
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""                 # stop_gate absent -> off


def test_enabled_false_allows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"gates":{"stop_gate":{"enabled":false}}}')
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""


# --- enabled behavior ---

def test_enabled_source_change_no_plan_blocks(tmp_path):
    assert _is_block(_run(_enabled_repo(tmp_path)))


def test_enabled_source_change_with_fresh_plan_allows(tmp_path):
    repo = _enabled_repo(tmp_path)
    (repo / ".sdlc" / "plans").mkdir()
    (repo / ".sdlc" / "plans" / "p.md").write_text("# plan\n")
    assert _run(repo) == ""


def test_only_docs_or_sdlc_change_allows(tmp_path):
    repo = _enabled_repo(tmp_path)
    (repo / "a.py").unlink()                    # remove the source change
    (repo / "docs").mkdir(); (repo / "docs" / "x.md").write_text("doc\n")
    assert _run(repo) == ""                     # docs/.sdlc are the harness, not source


# --- safety: loop guard (BOTH schemas) + override + fail-open ---

def test_loop_guard_stop_hook_active_allows(tmp_path):
    assert _run(_enabled_repo(tmp_path), payload='{"stop_hook_active":true}') == ""


def test_loop_guard_recursive_state_allows(tmp_path):
    payload = '{"recursive_state":{"is_recursive":true,"blocked_by_hook":true}}'
    assert _run(_enabled_repo(tmp_path), payload=payload) == ""


def test_override_sentinel_allows(tmp_path):
    repo = _enabled_repo(tmp_path)
    (repo / ".sdlc" / ".allow-direct-edits").touch()
    assert _run(repo) == ""


def test_non_git_dir_allows(tmp_path):
    # enabled config but not a git repo -> fail-open
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text('{"gates":{"stop_gate":{"enabled":true}}}')
    (tmp_path / "a.py").write_text("x = 1\n")
    assert _run(tmp_path) == ""


def test_wired_into_hooks_json():
    hooks = json.loads((GATE.parent / "hooks.json").read_text())
    stop = hooks["hooks"].get("Stop", [])
    assert any("completion_gate.sh" in json.dumps(h) for h in stop), "Stop hook not wired"
