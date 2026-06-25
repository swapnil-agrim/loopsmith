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
