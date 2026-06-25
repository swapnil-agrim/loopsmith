import json, subprocess, pathlib

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "sdlc_gate.sh"


def _run(prompt):
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # raises if invalid JSON → also tests the invariant


def test_output_is_valid_json_with_policy():
    out = _run("implement a new feature")
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "GOAL-BASED SDLC" in ctx


def test_code_intent_flagged():
    ctx = _run("implement the parser in parser.py")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


def test_question_intent_is_read_only():
    ctx = _run("what does this function do?")["hookSpecificOutput"]["additionalContext"]
    assert "READ-ONLY" in ctx


def test_no_onshot_specifics():
    ctx = _run("hello")["hookSpecificOutput"]["additionalContext"]
    for banned in ("R2", "Temporal", "GPU", "OnShot", "episode"):
        assert banned not in ctx


def _run_raw(stdin_text):
    # Send arbitrary raw stdin (not wrapped in {"prompt": ...}) to pin the
    # always-valid-JSON invariant on the malformed-input paths.
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=stdin_text, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # raises if invalid JSON


def test_empty_stdin_is_valid_json():
    out = _run_raw("")
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]


def test_garbage_stdin_is_valid_json():
    out = _run_raw("garbage{{{ not json")
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]
