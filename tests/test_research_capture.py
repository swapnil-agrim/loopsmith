"""The research-capture hook (PostToolUse) auto-collects WebSearch/WebFetch into the KG corpus —
but ONLY for a project that opted in (.sdlc/config.json -> knowledge_graph.enabled). It ships
globally, so the critical property is: no-op (and never error) for every project that didn't opt in."""
import json, os, subprocess, pathlib, importlib.util, tempfile

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "research_capture.py"


def _mod():
    spec = importlib.util.spec_from_file_location("research_capture", HOOK)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


# --- pure breadcrumb builder ---

def test_breadcrumb_for_websearch():
    out = _mod().build_breadcrumb("WebSearch", {"query": "rust async runtime"}, "some results")
    assert out is not None
    path, md = out
    assert path.startswith(".sdlc/knowledge/research/web/") and path.endswith(".md")
    assert "rust async runtime" in md and "source: websearch" in md and "some results" in md


def test_breadcrumb_for_webfetch_uses_url():
    path, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com/x"}, {"text": "body"})
    assert "https://example.com/x" in md and "source: webfetch" in md


def test_no_breadcrumb_for_non_web_tool():
    assert _mod().build_breadcrumb("Bash", {"command": "ls"}, "out") is None


def test_no_breadcrumb_for_empty_subject():
    rc = _mod()
    assert rc.build_breadcrumb("WebSearch", {}, "resp") is None          # failed/empty call -> no junk
    assert rc.build_breadcrumb("WebFetch", {"url": "   "}, "resp") is None


def test_heading_has_no_embedded_newline():
    _, md = _mod().build_breadcrumb("WebSearch", {"query": "line1\nline2"}, "r")
    heading = md.split("---\n\n", 1)[1].splitlines()[0]
    assert "\n" not in heading and "line1 line2" in heading


# --- security: never persist raw web bodies (secret-shaped substrings are redacted) ---

_SECRETS = {
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "gh": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "pw_plain": "password: hunter2superlong",
    "pw_json": '"api_key":"sk-livesecretvalue0123456789"',
    "bearer": "Bearer abc123def456ghi789",
}


def test_secret_shaped_body_is_redacted():
    body = "intro text " + " and ".join(_SECRETS.values()) + " trailer"
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com/x"}, body)
    for secret in ("AKIAIOSFODNN7EXAMPLE", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                   "hunter2superlong", "sk-livesecretvalue0123456789", "abc123def456ghi789"):
        assert secret not in md, "leaked secret substring: %s" % secret
    assert "[REDACTED" in md              # something was actually redacted, not just dropped by the cap


def test_secret_glued_to_preceding_word_char_is_redacted():
    # a secret with NO delimiter before it must still be caught — a leading \b would let it slip through
    for glued, secret in [("requestid=AKIAIOSFODNN7EXAMPLE&next", "AKIAIOSFODNN7EXAMPLE"),
                          ("prefix-ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
                          ("x" + _SECRETS["jwt"], _SECRETS["jwt"])]:
        _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"}, glued)
        assert secret not in md, "glued secret leaked: %s" % secret


def test_authorization_basic_header_is_redacted():
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"},
                                    "Authorization: Basic dXNlcjpwYXNzd29yZA== rest of page")
    assert "dXNlcjpwYXNzd29yZA==" not in md and "[REDACTED" in md


def test_secret_straddling_the_excerpt_boundary_is_redacted():
    # scrub runs on the 4000-char region BEFORE the 400-char cap, so a secret near char 400 is redacted
    # (not truncated into a non-matching partial) — this ordering is the load-bearing part
    body = "x" * 388 + " AKIAIOSFODNN7EXAMPLE and more"
    _, md = _mod().build_breadcrumb("WebSearch", {"query": "q"}, body)
    assert "AKIAIOSFODNN7EXAMPLE" not in md


def test_secret_inside_json_response_is_redacted():
    # WebFetch often returns a dict; it gets json.dumps'd, so redaction must survive that serialization
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"},
                                    {"text": "token is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ok"})
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in md and "[REDACTED" in md


def test_excerpt_is_capped_far_below_the_raw_body():
    _, md = _mod().build_breadcrumb("WebSearch", {"query": "q"}, "x" * 10000)
    excerpt = md.split("---\n\n", 1)[1].split("\n\n", 1)[1]   # everything after the "# heading" line
    assert len(excerpt) < 600 and "x" * 4001 not in md        # not the raw 4000-char dump


def test_enabled_must_be_strict_true():
    rc = _mod()
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp) / ".sdlc"; base.mkdir()
        (base / "config.json").write_text('{"knowledge_graph":{"enabled":"false"}}')
        assert rc._kg_enabled(tmp) is False             # a stringy value must not opt in
        (base / "config.json").write_text('{"knowledge_graph":{"enabled":true}}')
        assert rc._kg_enabled(tmp) is True


# --- end-to-end gating + fail-open (the hook ships globally) ---

def _run(project_dir, payload_bytes):
    return subprocess.run(["python3", str(HOOK)], input=payload_bytes,
                          capture_output=True, env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})


def _project(tmp, enabled):
    base = pathlib.Path(tmp) / ".sdlc"; base.mkdir(parents=True)
    (base / "config.json").write_text(json.dumps({"knowledge_graph": {"enabled": enabled}}))
    return tmp


def _web_payload():
    return json.dumps({"tool_name": "WebSearch", "tool_input": {"query": "kubernetes hpa"},
                       "tool_response": "results about hpa"}).encode()


def test_writes_breadcrumb_when_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, True)
        proc = _run(tmp, _web_payload())
        assert proc.returncode == 0
        webdir = pathlib.Path(tmp) / ".sdlc" / "knowledge" / "research" / "web"
        files = list(webdir.glob("*.md"))
        assert files and "kubernetes hpa" in files[0].read_text()


def test_planted_secret_never_reaches_disk():
    # the security guarantee, end to end: a secret in the raw web response must not land in the file
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, True)
        payload = json.dumps({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/leak"},
                              "tool_response": "here is a key %s in the page body" % SECRET}).encode()
        proc = _run(tmp, payload)
        assert proc.returncode == 0
        files = list((pathlib.Path(tmp) / ".sdlc" / "knowledge" / "research" / "web").glob("*.md"))
        assert files, "breadcrumb should still be written (KG enabled)"
        disk = files[0].read_text()
        assert SECRET not in disk and "[REDACTED" in disk


def test_noop_when_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, False)
        proc = _run(tmp, _web_payload())
        assert proc.returncode == 0
        assert not (pathlib.Path(tmp) / ".sdlc" / "knowledge").exists()   # nothing written


def test_noop_when_no_sdlc_project():
    # a project that never ran /sdlc-init must be completely untouched
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run(tmp, _web_payload())
        assert proc.returncode == 0
        assert not (pathlib.Path(tmp) / ".sdlc").exists()


def test_fail_open_on_garbage_stdin():
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, True)
        proc = _run(tmp, b"garbage{{ not json")
        assert proc.returncode == 0          # never errors the tool call


def test_hook_wired_in_hooks_json():
    hooks = json.loads((pathlib.Path(__file__).resolve().parent.parent / "hooks" / "hooks.json").read_text())
    post = hooks["hooks"].get("PostToolUse", [])
    assert any("WebSearch" in h.get("matcher", "") and "WebFetch" in h.get("matcher", "")
               and "research_capture.py" in json.dumps(h) for h in post)
