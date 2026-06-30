import json, subprocess, pathlib, pytest

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


# --- intent classification accuracy ---

def test_multiline_code_intent_not_downgraded_to_ask():
    # a real code request whose LATER line starts with a question word must stay CODE:
    # the `asky` prefix test is line-oriented, so it must judge only the first line.
    ctx = _run("rename the column\nwhat else")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


def test_bare_add_imperative_is_code_change():
    ctx = _run("add logging to the auth flow")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


def test_write_imperative_is_code_change():
    ctx = _run("write a function that sorts the list")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


# --- always-valid-JSON invariant: hostile inputs must never break the emit ---

@pytest.mark.parametrize("prompt", [
    'implement "a\\b"',            # embedded quote + backslash
    'refactor\nthen explain it',  # newline
    'fix 100% of %s in the printf',  # percent / format tokens
    '--help',                     # leading dashes (option-injection bait)
    'build the thing $(whoami)',  # shell-ish
    'x' * 200000,                 # very long
    'naïve café 日本語 🚀 prompt', # unicode
])
def test_prompt_always_emits_valid_json(prompt):
    out = _run(prompt)  # _run already asserts rc==0 and json.loads (raises on invalid JSON)
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]


def _run_bytes(stdin_bytes):
    proc = subprocess.run(["bash", str(HOOK)], input=stdin_bytes, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # raises if invalid JSON (json.loads accepts bytes)


@pytest.mark.parametrize("raw", [
    b'{"prompt":null}',
    b'{"prompt":123}',
    b'[1,2,3]',
    b'{"no":"prompt"}',
    b'not json at all',
    b'\xff\xfe\x80',                # invalid UTF-8
    b'{"prompt":"x\xed\xa0\x80y"}', # surrogate bytes inside a JSON string
])
def test_malformed_stdin_always_emits_valid_json(raw):
    out = _run_bytes(raw)
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]


# --- behavioral spec: a labeled prompt corpus pins the intent classifier ---
# The hook IS the deterministic enforcement of "run the spine", so this is the closest test of
# behavior (not just plumbing). "code" -> CODE CHANGE banner; "ask" -> READ-ONLY banner;
# "standard" -> policy only (neither banner).
_CORPUS = [
    # clear code-change (imperative, no leading question)
    ("implement the retry in client.py", "code"),
    ("refactor the auth module", "code"),
    ("add a cache to the fetch helper", "code"),
    ("fix the off-by-one in pagination", "code"),
    ("migrate the config to TOML", "code"),
    ("rewrite this to be async", "code"),
    ("delete the dead helper and update its callers", "code"),
    ("wire up the webhook handler", "code"),
    ("rename the column and adjust the schema", "code"),
    # clear read-only / conversational (leading interrogative, no strong code signal)
    ("what does the loop do when it parks?", "ask"),
    ("how does the plan-review gate work?", "ask"),
    ("why is the hook fail-open?", "ask"),
    ("explain the self-improving knowledge graph", "ask"),
    ("list the skills this plugin ships", "ask"),
    ("show me where the budget is configured", "ask"),
    ("which backlog sources are supported?", "ask"),
    # ambiguous: a question that is really a code request (interrogative + strong signal) -> code
    ("how should I implement the parser in parser.py?", "code"),
    ("can you refactor this function?", "code"),
    # standard: neither interrogative nor codey
    ("hello", "standard"),
    ("good morning", "standard"),
    ("", "standard"),
]


@pytest.mark.parametrize("prompt,expected", _CORPUS)
def test_intent_corpus(prompt, expected):
    ctx = _run(prompt)["hookSpecificOutput"]["additionalContext"]
    assert "GOAL-BASED SDLC" in ctx                       # the policy is always present
    if expected == "code":
        assert "CODE CHANGE" in ctx and "READ-ONLY" not in ctx
    elif expected == "ask":
        assert "READ-ONLY" in ctx and "CODE CHANGE" not in ctx
    else:                                                 # standard: neither banner
        assert "CODE CHANGE" not in ctx and "READ-ONLY" not in ctx


def test_classifier_no_false_code_flag_on_readonly_corpus():
    # aggregate bound: NONE of the read-only prompts may be flagged as a code change (false positives
    # are the costlier error here — they'd force the full spine onto a plain question).
    asks = [p for p, e in _CORPUS if e == "ask"]
    flagged = [p for p in asks if "CODE CHANGE" in _run(p)["hookSpecificOutput"]["additionalContext"]]
    assert flagged == [], f"read-only prompts mis-flagged as code: {flagged}"
