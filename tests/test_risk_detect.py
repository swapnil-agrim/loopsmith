"""risk-detect.sh (Slice 3): the read-only, jq-free collector that names which conditional-risk
categories the current change touches, so the loop's Research/Review phases can surface the matching
/sdlc-<risk>-check. Guards its three principles: correct detection, FAIL-OPEN, and SECRET-SAFETY (it
scans diff bodies but must emit only {category,file,line,pattern_id} — never the matched value)."""
import json, os, re, subprocess, pathlib

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


def test_content_line_that_renders_as_a_diff_header_never_leaks_tracked(tmp_path):
    # a line beginning "++ " renders as "+++ " in the outer unified diff; the scanner must treat it as
    # CONTENT (location-only), never misparse it as a "+++ b/path" header that captures the secret value
    # into `file`. Mirrors alignment-collect.sh's already-fixed test_content_line_that_renders_as_a_diff_
    # header_never_leaks (slice #89) — this was the unfixed twin (risk-detect.sh never got that fix).
    # NOTE the second line must ALSO trip a hard-stop: on the buggy awk, the poisoned `file` is only ever
    # EMITTED when a later line matches a pattern, so an assertion that only plants line 1 would pass
    # vacuously even against the bug (nothing gets emitted at all). Line 2 forces the poisoned `file` to
    # surface, so this test actually fails on the pre-fix code.
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text("placeholder\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t.co", "-c", "user.name=t", "commit", "-q", "-m", "init")
    (repo / "notes.txt").write_text(
        '++ aws_key = "AKIALEAK0000000000FAKE"\napi_key = "TRIGGER9SECRETVALUE"\n'
    )
    out = _run(repo)
    blob = json.dumps(out)
    assert "AKIALEAK0000000000FAKE" not in blob             # value never in output (fails on buggy awk)
    assert "sensitive" in out["matched"], "the second line must still hard-stop (guard must not be vacuous)"
    for h in out["hits"]:
        assert h["file"] == "notes.txt"                     # real filename, never the "+++ …" line text


def test_content_line_that_renders_as_a_diff_header_never_leaks_untracked(tmp_path):
    # same shape, but for an UNTRACKED file — risk-detect.sh synthesizes its own diff for these
    # (emit_combined_diff), a code path alignment-collect.sh (committed-history only) doesn't have; the
    # synthesized block must also carry a "diff --git" line so the same inhunk reset fires.
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(
        '++ aws_key = "AKIALEAK0000000000FAKE"\napi_key = "TRIGGER9SECRETVALUE"\n'
    )
    out = _run(repo)
    blob = json.dumps(out)
    assert "AKIALEAK0000000000FAKE" not in blob
    assert "sensitive" in out["matched"], "the second line must still hard-stop (guard must not be vacuous)"
    for h in out["hits"]:
        assert h["file"] == "notes.txt"


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


ALIGN_SCRIPT = SCRIPT.parent.parent.parent / "sdlc-align" / "scripts" / "alignment-collect.sh"


def _secret_kv_pattern(text):
    # the key:value trigger words (api_key=, password:, ...) — the alternation between the
    # opening "line ~ /(" and the closing ")[ \t]*[:=]/" that both scripts share verbatim.
    m = re.search(r"line ~ /\(([^)]+)\)\[ \\t\]\*\[:=\]/", text)
    assert m, "could not locate the secret key:value pattern"
    return m.group(1)


def _secret_token_pattern(text):
    # the token-SHAPE alternation (AKIA.../ghp_.../xox.../glpat-.../AIza.../-----BEGIN...KEY-----)
    m = re.search(r"line ~ /\((AKIA[^)]+)\)/\)", text)
    assert m, "could not locate the secret token-shape pattern"
    return m.group(1)


def test_secret_patterns_stay_in_sync_with_alignment_collect():
    # F29: risk-detect.sh and alignment-collect.sh each hard-stop on a secret-shaped line, but the
    # two pattern sets had drifted (alignment-collect alone caught Slack tokens; both missed GitLab
    # and Google). Both scan diff bodies to CLASSIFY, never to redact, so drift here is a detection
    # gap, not a leak — but the two location-only collectors should carry the same set.
    risk_text = SCRIPT.read_text(encoding="utf-8")
    align_text = ALIGN_SCRIPT.read_text(encoding="utf-8")
    assert _secret_kv_pattern(risk_text) == _secret_kv_pattern(align_text)
    assert _secret_token_pattern(risk_text) == _secret_token_pattern(align_text)
    # and the union actually grew to include the previously-missing shapes, not just stayed equal
    for shape in ("xox[baprs]-", "glpat-", "AIza", "AWS_SECRET_ACCESS_KEY"):
        assert shape in risk_text and shape in align_text


# --- #537: dotenv globs were the only PATH-ANCHORED patterns in any of the three lists ----------
# risk-detect.sh's own convention (the comment above the glob lists) is that every pattern is a
# substring glob matched at any depth. `.env*` broke it: `match_globs` matches the WHOLE path, so
# `.env*` only ever hit a repo-root dotenv and `*.env` only a path ENDING in ".env" — a nested
# `backend/.env.local` produced zero sensitive signal while the identical root file flagged.
#
# Every fixture below is `git add -A`'d on purpose: the scanner reaches untracked files through
# `git ls-files --others --exclude-standard`, so a contributor whose GLOBAL gitignore excludes
# .env files would otherwise see these tests flake for a reason unrelated to what they pin.

def test_nested_dotenv_signals_as_a_sensitive_path(tmp_path):
    # Asserts the NAME-scan hit specifically. "sensitive" in matched would be vacuous here: any
    # listed keyword in the fixture body makes it pass through the CONTENT scan even on the
    # unfixed script, so the fixture is deliberately keyword-free and the pattern_id is pinned.
    repo = _repo(tmp_path)
    (repo / "backend").mkdir()
    (repo / "backend" / ".env.local").write_text("PLACEHOLDER=1\n")
    _git(repo, "add", "-A")
    out = _run(repo)
    assert any(h["file"] == "backend/.env.local" and h["pattern_id"] == "path" for h in out["hits"])


def test_root_dotenv_still_signals_as_a_sensitive_path(tmp_path):
    # the half that already worked — pinned so the fix is proven to ADD depth, not move the anchor
    repo = _repo(tmp_path)
    (repo / ".env").write_text("PLACEHOLDER=1\n")
    _git(repo, "add", "-A")
    out = _run(repo)
    assert any(h["file"] == ".env" and h["pattern_id"] == "path" for h in out["hits"])


def test_nested_dotenv_example_signals_as_a_sensitive_path(tmp_path):
    # a checked-in template lives beside the real thing and is worth the same surfacing
    repo = _repo(tmp_path)
    (repo / "config").mkdir()
    (repo / "config" / ".env.example").write_text("PLACEHOLDER=1\n")
    _git(repo, "add", "-A")
    out = _run(repo)
    assert any(h["file"] == "config/.env.example" and h["pattern_id"] == "path" for h in out["hits"])


def test_dotenv_lookalikes_do_not_signal(tmp_path):
    # the over-match guard: widening to any depth must not start flagging files that merely have
    # "env" in the name.
    repo = _repo(tmp_path)
    (repo / "backend").mkdir()
    (repo / "backend" / "env.md").write_text("how to set up your environment\n")
    (repo / "foo.envrc").write_text("layout python\n")
    _git(repo, "add", "-A")
    out = _run(repo)
    assert out["matched"] == [], out["hits"]


# --- #537: content-scan keyword gaps (connection strings + one key shape) -----------------------

def test_connection_string_keys_are_sensitive_content(tmp_path):
    repo = _repo(tmp_path)
    (repo / "settings.py").write_text('DATABASE_URL = "postgres://u@h/db"\n')
    (repo / "cache.py").write_text('REDIS_URL="redis://h:6379/0"\n')
    _git(repo, "add", "-A")
    out = _run(repo)
    files = {h["file"] for h in out["hits"] if h["category"] == "sensitive"}
    assert {"settings.py", "cache.py"} <= files, out["hits"]


def test_stripe_live_key_shape_is_a_sensitive_token(tmp_path):
    # pattern_id is pinned to "token", so the fixture carries NO key:value trigger word — the kv
    # branch wins the else-if chain and would report "secret" instead.
    repo = _repo(tmp_path)
    (repo / "pay.js").write_text('const K = "sk_live_abcd1234efgh";\n')
    _git(repo, "add", "-A")
    out = _run(repo)
    assert any(h["file"] == "pay.js" and h["pattern_id"] == "token" for h in out["hits"])


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
