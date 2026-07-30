"""alignment-collect.sh (Slice 7): the read-only, jq-free, deterministic collector that gathers FACTS
from git history + .sdlc/ artifacts over an N-day window into one evidence pack (schema
alignment-collect/v1). It renders NO verdicts — sdlc-align judges the pack. Guards its three
principles: correct facts, FAIL-OPEN (missing dep/non-git → minimal JSON + degraded[] code, exit 0),
and SECRET-SAFETY (the hard-stop scan reads diff bodies but emits ONLY {commit,file,line,pattern_id} —
never the matched substring)."""
import json, os, subprocess, pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-align" / "scripts" / "alignment-collect.sh"


def _git(repo, *a, **kw):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, **kw)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _commit(repo, name, content, msg):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _run(project_dir, since=3650):
    p = subprocess.run(["bash", str(SCRIPT), "--since-days", str(since)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)          # ALWAYS valid JSON


def test_minimal_pack_on_non_git_is_valid_and_degraded(tmp_path):
    out = _run(tmp_path)                 # never ran git init
    assert out["schema"] == "alignment-collect/v1"
    assert out["degraded"] == ["no_git"] and out["window"]["commit_count"] == 0
    for d in ("d1", "d2", "d3", "d4", "d5", "d6", "d7"):
        assert d in out["dimensions"]    # minimal pack still carries the full shape


def test_gathers_window_facts_and_classifies_paths(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "def f():\n    return 1\n", "feat: add a.py")
    _commit(repo, "test_a.py", "def test_f(): assert True\n", "test: cover a")
    out = _run(repo)
    assert out["window"]["commit_count"] == 2
    d = out["dimensions"]
    assert d["d1"]["commits_with_source"] == 2
    assert d["d2"]["tests_touched_with_source_pct"] == 50   # 1 of 2 source commits touched a test
    assert d["d4"]["net_lines_added_window"] > 0


def test_secret_in_a_committed_diff_is_location_only(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "cfg.py", 'password = "hunter2SUPERSECRET"\napi_key = "AKIA00001111EXAMPLE"\n', "add cfg")
    out = _run(repo)
    hits = out["dimensions"]["d6"]["hits"]
    assert hits, "the hard-stop scan should flag the secret assignment"
    for h in hits:
        assert set(h.keys()) == {"commit", "file", "line", "pattern_id", "category"}
    blob = json.dumps(out)
    assert "hunter2SUPERSECRET" not in blob and "AKIA00001111EXAMPLE" not in blob   # value never emitted


def test_content_line_that_renders_as_a_diff_header_never_leaks(tmp_path):
    # a committed line beginning "++ " renders as "+++ " in the outer diff; the scanner must treat it
    # as CONTENT (location-only), NOT misparse it as a "+++ b/path" header that captures the value
    repo = _repo(tmp_path)
    _commit(repo, "NOTES.md", '++ token = "ghp_REALSECRETTOKENLEAK01"\nplain line\n', "add notes")
    out = _run(repo)
    blob = json.dumps(out)
    assert "ghp_REALSECRETTOKENLEAK01" not in blob            # value never in the pack
    for h in out["dimensions"]["d6"]["hits"]:
        assert "SECRET" not in h["file"].upper() and h["file"] == "NOTES.md"   # real filename, not the line
    # and a committed .patch whose +lines become ++ in the outer diff must not leak either
    _commit(repo, "fix.patch", '--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+password = "PATCHSECRETLEAK42"\n', "patch")
    assert "PATCHSECRETLEAK42" not in json.dumps(_run(repo))


def test_test_command_known_reads_verify_command(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc").mkdir()
    (repo / ".sdlc" / "config.json").write_text('{"verify":{"command":"pytest -q"}}')
    _commit(repo, "a.py", "x = 1\n", "add")
    assert _run(repo)["dimensions"]["d2"]["test_command_known"] is True
    # empty command -> not known + degraded code
    (repo / ".sdlc" / "config.json").write_text('{"verify":{"command":""}}')
    _commit(repo, "b.py", "y = 2\n", "add b")
    out = _run(repo)
    assert out["dimensions"]["d2"]["test_command_known"] is False
    assert "no_test_command" in out["degraded"]
    # an unrelated "command" key with NO verify block must not false-positive
    (repo / ".sdlc" / "config.json").write_text('{"hooks":{"command":"echo hi"}}')
    _commit(repo, "c.py", "z = 3\n", "add c")
    assert _run(repo)["dimensions"]["d2"]["test_command_known"] is False


def test_reviews_dir_and_decisions_are_retargeted_to_sdlc(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "reviews").mkdir(parents=True)
    _commit(repo, ".sdlc/decisions.json", '{"decisions":[]}', "chore: seed decisions")
    _commit(repo, "a.py", "x = 1\n", "add a")
    out = _run(repo)
    assert out["dimensions"]["d5"]["reviews_dir_present"] is True
    assert ".sdlc/decisions.json" in out["dimensions"]["d7"]["decisions_added"]


def test_deterministic(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "x = 1\n", "add a")
    _commit(repo, "b.py", "y = 2\n", "add b")
    assert _run(repo) == _run(repo)


def test_sdlc_align_skill_wires_the_collector():
    # the collector only helps if the skill runs it — guard the SKILL prose reference + allowed-tools
    skill = (SCRIPT.parent.parent / "SKILL.md").read_text(encoding="utf-8")
    assert "alignment-collect.sh" in skill, "sdlc-align must invoke the evidence collector"
    assert "Bash(bash *)" in skill, "allowed-tools must permit the bash collector invocation"
    assert "renders no verdict" in skill.lower() or "no verdict" in skill.lower()
