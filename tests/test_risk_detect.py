"""risk-detect.sh (Slice 3): the read-only, jq-free collector that names which conditional-risk
categories the current change touches, so the loop's Research/Review phases can surface the matching
/sdlc-<risk>-check. Guards its three principles: correct detection, FAIL-OPEN, and SECRET-SAFETY (it
scans diff bodies but must emit only {category,file,line,pattern_id} — never the matched value)."""
import json, os, subprocess, pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts" / "risk-detect.sh"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    return tmp_path


def _run(project_dir):
    p = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_schema_and_empty_on_clean_repo(tmp_path):
    out = _run(_repo(tmp_path))
    assert out["schema"] == "risk-detect/v1" and out["matched"] == [] and out["hits"] == []


def test_detects_each_category(tmp_path):
    repo = _repo(tmp_path)
    (repo / "db").mkdir(); (repo / "src").mkdir()
    (repo / "db" / "001_migration.sql").write_text("ALTER TABLE users ADD COLUMN age int;\n")
    (repo / "src" / "routes.js").write_text('app.post("/pay", handler)\n')
    (repo / "auth.py").write_text("api_key = configure()\n")
    out = _run(repo)
    assert set(out["matched"]) == {"migration", "contract", "sensitive"}
    # hits are location-only: exactly the four keys, nothing resembling a value
    for h in out["hits"]:
        assert set(h.keys()) == {"category", "file", "line", "pattern_id"}


def test_matched_is_sorted_and_deduped(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a_migration.sql").write_text("CREATE TABLE t (id int);\n")
    (repo / "b_migration.sql").write_text("DROP TABLE t;\n")   # second migration file
    out = _run(repo)
    assert out["matched"] == ["migration"]                     # deduped, single category


def test_planted_secret_value_never_in_output(tmp_path):
    repo = _repo(tmp_path)
    SECRET = "AKIAZZZZ0000EXAMPLE1"
    (repo / "config.env").write_text("AWS_SECRET=%s\npassword: hunter2plaintext\n" % SECRET)
    out = _run(repo)
    assert "sensitive" in out["matched"]
    blob = json.dumps(out)
    assert SECRET not in blob and "hunter2plaintext" not in blob   # location only, never the value


def test_fail_open_on_non_git_dir(tmp_path):
    out = _run(tmp_path)   # never ran git init
    assert out == {"schema": "risk-detect/v1", "matched": [], "hits": []}


def test_deterministic(tmp_path):
    repo = _repo(tmp_path)
    (repo / "schema.prisma").write_text("model User { id Int }\n")
    (repo / "openapi.yaml").write_text("paths: {}\n")
    assert _run(repo) == _run(repo)


def test_excludes_sdlc_and_docs(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc").mkdir(); (repo / "docs").mkdir()
    (repo / ".sdlc" / "001_migration.sql").write_text("ALTER TABLE x ADD COLUMN y int;\n")
    (repo / "docs" / "auth.md").write_text("Authorization: Bearer xyz\n")
    out = _run(repo)
    assert out["matched"] == []   # SDLC machinery + docs are not the engineer's source


def test_review_and_research_skills_wire_the_detector():
    # the collector is only useful if the phases invoke it — guard the SKILL prose reference
    skills = SCRIPT.parent.parent.parent
    review = (skills / "sdlc-review" / "SKILL.md").read_text(encoding="utf-8")
    research = (skills / "sdlc-research" / "SKILL.md").read_text(encoding="utf-8")
    assert "risk-detect.sh" in review, "sdlc-review must invoke the risk detector"
    for skill_ref in ("/sdlc-security-review", "/sdlc-contract-check", "/sdlc-migration-check"):
        assert skill_ref in review, "sdlc-review must surface %s" % skill_ref
    # research anticipates the same risks so the plan budgets for them
    assert "risk-detect" in research and "/sdlc-security-review" in research
