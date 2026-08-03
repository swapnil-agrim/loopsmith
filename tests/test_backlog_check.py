"""Pre-work backlog cross-check engine (backlog_check.py, slice 0.9.21): LLM-free TF-IDF retrieval +
explicit `#N` graph + ledger signals over the board mirror / local goals. Hermetic, deterministic, $0."""
import json, pathlib, importlib.util, tempfile, calendar, time

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _epoch(iso):  # "2026-08-03T00:00:00Z" -> epoch seconds
    return calendar.timegm(time.strptime(iso.replace("Z", "GMT"), "%Y-%m-%dT%H:%M:%S%Z"))


def _rec(number, title, body="", state="open", closed_at=None, updated="2026-08-01T00:00:00Z"):
    return {"number": number, "title": title, "body_excerpt": body, "labels": [],
            "state": state, "closed_at": closed_at, "updated_at": updated, "content_hash": "x"}


def _gh_base(d, records, **bc):
    base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps({"discovery": {"source": "github"}, "backlog_check": bc}))
    (base / "state" / "board-mirror.ndjson").write_text("".join(json.dumps(r) + "\n" for r in records))
    return str(base)


# clearly-similar pair (shared rare terms) vs a disjoint issue — neutral fixture, no real stack names
_GOAL = "migrate the widget cache onto the acme storage backend"
_DUP = "move the widget cache to acme storage"
_DISTINCT = "restyle the frontend dashboard button colours"
_LOOSE = {"dup_threshold": 0.4, "obsolete_threshold": 0.4, "closed_window_days": 3650}


# --- tokenization + similarity primitives ---

def test_tokens_lowercase_drop_stopwords_and_short():
    bc = _mod("backlog_check")
    toks = bc._tokens("The Widget Cache is a BIG win, ok")
    assert "widget" in toks and "cache" in toks and "big" in toks
    assert "the" not in toks and "is" not in toks and "a" not in toks   # stopwords gone


def test_cosine_identical_high_disjoint_zero():
    bc = _mod("backlog_check")
    idf = bc._idf([{"tokens": bc._doc_tokens(_GOAL, "")}, {"tokens": bc._doc_tokens(_DISTINCT, "")}])
    a = bc._vector(bc._doc_tokens(_GOAL, ""), idf)
    assert bc._cosine(a, a) > 0.99
    b = bc._vector(bc._doc_tokens(_DISTINCT, ""), idf)
    assert bc._cosine(a, b) == 0.0


# --- duplicate detection (github corpus) ---

def test_duplicate_open_issue_flagged_distinct_not():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL), _rec(2, _DUP), _rec(3, _DISTINCT)], **_LOOSE)
        pack = bc.cross_check(base, "1")
        kinds = {(f["kind"], f["ref"]) for f in pack["findings"]}
        assert ("duplicate", "2") in kinds          # the paraphrase is caught
        assert ("duplicate", "3") not in kinds       # the unrelated issue is not
        dup = next(f for f in pack["findings"] if f["ref"] == "2")
        assert dup["score"] >= 0.4 and dup["evidence"]   # carries shared-term evidence
        assert pack["schema"] == "backlog-check/v1" and pack["goal"] == "1"


def test_duplicate_pair_only_the_later_goal_is_park_confident():
    # both #1 and #2 are open duplicates: the EARLIER (#1) survives + is worked; only the LATER (#2)
    # parks. Without this, #1 parks as dup-of-#2 AND #2 parks as dup-of-#1 -> neither ever gets worked.
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL), _rec(2, _GOAL)],
                        dup_threshold=0.4, park_threshold=0.8, closed_window_days=3650)
        f1 = next(f for f in bc.cross_check(base, "1")["findings"] if f["ref"] == "2")
        assert f1["kind"] == "duplicate" and f1["confident"] is False   # #1 is earliest -> survives
        f2 = next(f for f in bc.cross_check(base, "2")["findings"] if f["ref"] == "1")
        assert f2["confident"] is True                                  # #2 is later -> parks against #1


def test_earlier_orders_github_numbers_and_local_paths():
    bc = _mod("backlog_check")
    assert bc._earlier("2", "10") is True and bc._earlier("10", "2") is False   # numeric, not lexical
    assert bc._earlier("/a/goals/0001.md", "/b/goals/0002.md") is True          # local: by filename


def test_larger_duplicate_cluster_keeps_earliest_in_top_k_window():
    # a cluster BIGGER than top_k (8) with mixed 1-/2-digit numbers: a lexical candidate tiebreak would
    # sort 10..17 before 2 and crowd #2 out of #3's window, leaving #3 a wrong SECOND survivor. The
    # numeric-aware _ref_key keeps #2 in-window, so #3 parks against the true earliest.
    bc = _mod("backlog_check")
    nums = [2, 3, 10, 11, 12, 13, 14, 15, 16, 17]
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(n, _GOAL) for n in nums],
                        dup_threshold=0.4, park_threshold=0.8, closed_window_days=3650)
        f = [x for x in bc.cross_check(base, "3")["findings"] if x["ref"] == "2"]
        assert f and f[0]["confident"] is True          # #3 parks against the earlier #2, not survives


def test_confidence_gate_park_vs_annotate():
    bc = _mod("backlog_check")
    # cross-check from the LATER goal (#2) so the earlier dup (#1) is the park-confident match
    with tempfile.TemporaryDirectory() as d1:
        # identical -> score ~1.0 >= park_threshold 0.9 -> confident (the loop would park)
        base = _gh_base(d1, [_rec(1, _GOAL), _rec(2, _GOAL)],
                        dup_threshold=0.4, park_threshold=0.9, closed_window_days=3650)
        assert next(f for f in bc.cross_check(base, "2")["findings"] if f["ref"] == "1")["confident"] is True
    with tempfile.TemporaryDirectory() as d2:
        # same score, but an unreachable park_threshold -> a finding, NOT confident (annotate + proceed)
        base = _gh_base(d2, [_rec(1, _GOAL), _rec(2, _GOAL)],
                        dup_threshold=0.4, park_threshold=1.01, closed_window_days=3650)
        assert next(f for f in bc.cross_check(base, "2")["findings"] if f["ref"] == "1")["confident"] is False


# --- obsolescence (closed within the window) ---

def test_obsoleted_by_recent_close_but_not_stale_close():
    bc = _mod("backlog_check")
    now = _epoch("2026-08-03T00:00:00Z")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL),
                            _rec(2, _DUP, state="closed", closed_at="2026-08-01T00:00:00Z")],
                        dup_threshold=0.4, obsolete_threshold=0.4, closed_window_days=30)
        assert ("obsoleted-by", "2") in {(f["kind"], f["ref"]) for f in bc.cross_check(base, "1", now=now)["findings"]}
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL),
                            _rec(2, _DUP, state="closed", closed_at="2026-01-01T00:00:00Z")],
                        dup_threshold=0.4, obsolete_threshold=0.4, closed_window_days=30)
        assert ("obsoleted-by", "2") not in {(f["kind"], f["ref"]) for f in bc.cross_check(base, "1", now=now)["findings"]}


def test_within_window_helper():
    bc = _mod("backlog_check")
    now = _epoch("2026-08-03T00:00:00Z")
    assert bc._within_window({"closed_at": "2026-08-01T00:00:00Z"}, 30, now) is True
    assert bc._within_window({"closed_at": "2026-01-01T00:00:00Z"}, 30, now) is False
    assert bc._within_window({"closed_at": None}, 30, now) is True        # local done: no date -> keep
    assert bc._within_window({"closed_at": "garbage"}, 30, now) is True   # unparseable -> keep


# --- explicit blocker graph ---

def test_explicit_blocked_by_open_ref_only():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, "wire the surface", body="blocked by #7 until the contract lands"),
                            _rec(7, "freeze the contract"),
                            _rec(9, "closed dep", state="closed")], **_LOOSE)
        f = [x for x in bc.cross_check(base, "1")["findings"] if x["kind"] == "blocked-by"]
        assert any(x["ref"] == "7" and x["confident"] for x in f)    # open ref -> confident blocker
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, "x", body="depends on #9"), _rec(9, "done dep", state="closed")], **_LOOSE)
        assert not [x for x in bc.cross_check(base, "1")["findings"] if x["kind"] == "blocked-by"]  # closed ref: not a blocker


# --- ledger team-wide signals ---

def _write_claim(base, actor, goal, kind="claimed", ts="2026-08-02T00:00:00Z", **extra):
    led = pathlib.Path(base) / "ledger" / "entries"; led.mkdir(parents=True, exist_ok=True)
    row = {"id": f"{actor}:1", "actor": actor, "kind": kind, "goal": str(goal), "ts": ts, **extra}
    with (led / f"{actor}.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


def test_in_flight_elsewhere_from_ledger_claim():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL), _rec(2, _DUP)], **_LOOSE)
        _write_claim(base, "bob", "2")           # a teammate is already working the paraphrase #2
        kinds = {(f["kind"], f["ref"]) for f in bc.cross_check(base, "1")["findings"]}
        assert ("in-flight-elsewhere", "2") in kinds


def test_ledger_outstanding_handoff_is_a_blocker():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL)], **_LOOSE)
        _write_claim(base, "amy", "1", kind="handoff", state="open", issue=1, to="amy")
        assert any(f["kind"] == "blocked-by" for f in bc.cross_check(base, "1")["findings"])


# --- secret-safety, fail-open, determinism ---

def test_secret_shaped_token_never_reaches_the_pack():
    bc = _mod("backlog_check")
    secret = "AKIAABCDEFGHIJKLMNOP"
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL, body="rotate " + secret),
                            _rec(2, _DUP, body="rotate " + secret)], **_LOOSE)
        pack = bc.cross_check(base, "1")
        blob = json.dumps(pack)
        # CASE-INSENSITIVE: the tokenizer lowercases, so a case-sensitive check would pass even on
        # reverted (leaky) code where the secret reaches evidence as `akiaabcdefghijklmnop`.
        assert secret.lower() not in blob.lower()
        assert any(f["ref"] == "2" for f in pack["findings"])   # the dup WAS found — the evidence path ran


def test_fail_open_empty_corpus_and_missing_goal():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [], **_LOOSE)                     # empty mirror
        pack = bc.cross_check(base, "1")
        assert pack["findings"] == [] and "no_mirror" in pack["degraded"]
        assert "goal_not_in_corpus" in pack["degraded"]


def test_fail_open_never_raises_on_garbage():
    bc = _mod("backlog_check")
    pack = bc.cross_check("/nonexistent/nope", "1")
    assert pack["schema"] == "backlog-check/v1" and pack["findings"] == []


def test_github_corpus_skips_a_non_dict_record_not_the_whole_check():
    # a garbage mirror line (valid JSON, not an object) must degrade ONE record, not zero the check
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        (base / "config.json").write_text(json.dumps({"discovery": {"source": "github"},
                                                       "backlog_check": _LOOSE}))
        (base / "state" / "board-mirror.ndjson").write_text(
            "\n".join([json.dumps(_rec(1, _GOAL)), "null", "42", json.dumps(_rec(2, _DUP))]) + "\n")
        pack = bc.cross_check(str(base), "1")
        assert "error" not in pack["degraded"]                       # one bad line didn't zero it
        assert ("duplicate", "2") in {(f["kind"], f["ref"]) for f in pack["findings"]}


def test_bad_config_numeric_falls_back_to_default_not_disabled():
    # a hand-edited config typo (top_k: "all", dup_threshold: "high") degrades to the DEFAULTS, not an
    # error. Identical titles (cosine ~1.0) clear the default 0.72 dup_threshold, proving it was applied.
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL), _rec(2, _GOAL)],
                        dup_threshold="high", top_k="all", closed_window_days=3650)
        pack = bc.cross_check(base, "1")
        assert "error" not in pack["degraded"]
        assert ("duplicate", "2") in {(f["kind"], f["ref"]) for f in pack["findings"]}   # default 0.72 applied


def test_candidate_gen_skips_zero_overlap_goal():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, "totally unique wombat telemetry"), _rec(2, _DUP)], **_LOOSE)
        assert bc.cross_check(base, "1")["findings"] == []   # nothing shares a term with the goal


def test_determinism():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL), _rec(2, _DUP), _rec(3, _GOAL + " variant")], **_LOOSE)
        assert bc.cross_check(base, "1") == bc.cross_check(base, "1")


# --- local-files mode ---

def _local_base(d, goals, **bc):
    base = pathlib.Path(d) / ".sdlc"; (base / "goals").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps({"backlog_check": bc}))     # source defaults to local
    for name, status, title, body in goals:
        (base / "goals" / name).write_text(
            f"---\nid: {name[:-3]}\nstatus: {status}\ntitle: {title}\n---\n{body}\n")
    return str(base)


def test_local_mode_duplicate_and_obsolete():
    bc = _mod("backlog_check")
    with tempfile.TemporaryDirectory() as d:
        base = _local_base(d, [("0001.md", "pending", _GOAL, "do it"),
                               ("0002.md", "pending", _DUP, "same thing"),
                               ("0003.md", "done", _GOAL + " earlier", "already shipped")], **_LOOSE)
        (pathlib.Path(base) / "goals" / "README.md").write_text("just notes, no frontmatter")  # not a goal
        goal = str(pathlib.Path(base) / "goals" / "0001.md")
        kinds = {(f["kind"], pathlib.Path(f["ref"]).name) for f in bc.cross_check(base, goal)["findings"]}
        assert ("duplicate", "0002.md") in kinds
        assert ("obsoleted-by", "0003.md") in kinds          # a `done` goal obsoletes
        assert not any("README" in f["ref"] for f in bc.cross_check(base, goal)["findings"])  # README skipped


# --- velocity-scaled window ---

def test_closed_window_days_pinned_and_auto_and_fallback():
    bc = _mod("backlog_check")
    assert bc._closed_window_days({"backlog_check": {"closed_window_days": 45}}) == 45
    assert bc._closed_window_days({"backlog_check": {"closed_window_days": "60"}}) == 60
    # auto: invert an injected velocity (50 target / 5 prs_per_day = 10 days)
    fast = lambda days=30, run=None: {"prs_per_day": 5.0, "commits_per_day": 9.0}
    assert bc._closed_window_days({"backlog_check": {"closed_window_days": "auto"}}, velocity_measure=fast) == 10
    # rate 0 (fresh/non-git) -> fallback 90
    dead = lambda days=30, run=None: {"prs_per_day": 0, "commits_per_day": 0}
    assert bc._closed_window_days({"backlog_check": {"closed_window_days": "auto"}}, velocity_measure=dead) == 90
    # bool is an int subclass: True must be treated as unset ("auto"), NOT a 1-day window
    assert bc._closed_window_days({"backlog_check": {"closed_window_days": True}}, velocity_measure=dead) == 90


def test_closed_window_days_loads_the_real_velocity_module():
    # exercise the cross-skill _load_velocity() path (no velocity_measure injected) with a fake git
    # runner so it stays hermetic: 5 merges / 30 days = 0.17 prs/day -> 50/0.17 ≈ 294 -> clamped to 180
    bc = _mod("backlog_check")
    fake_git = lambda args: "\n".join(["h"] * (5 if "--merges" in args else 60))
    assert bc._closed_window_days({"backlog_check": {"closed_window_days": "auto"}}, run=fake_git) == 180


# --- CLI verb ---

# --- decide(): pure pack -> loop-hook action ---

def _mkpack(findings):
    return {"schema": "backlog-check/v1", "goal": "1", "findings": findings, "degraded": []}


def _f(kind, ref, score, confident, ev=("widget", "cache")):
    return {"kind": kind, "ref": ref, "score": score, "source": "mirror",
            "evidence": list(ev), "confident": confident}


def test_decide_parks_a_confident_finding_by_default():
    bc = _mod("backlog_check")
    d = bc.decide(_mkpack([_f("duplicate", "42", 0.85, True)]), {})
    assert d["action"] == "park" and "duplicate of #42" in d["reason"] and "widget" in d["reason"]
    assert d["note"] == ""


def test_decide_annotates_a_weak_finding():
    bc = _mod("backlog_check")
    d = bc.decide(_mkpack([_f("duplicate", "42", 0.6, False)]), {})
    assert d["action"] == "proceed" and "advisory" in d["note"] and "duplicate of #42" in d["note"]


def test_decide_proceeds_on_no_findings():
    bc = _mod("backlog_check")
    assert bc.decide(_mkpack([]), {}) == {"action": "proceed", "reason": "", "note": ""}


def test_decide_flag_mode_never_parks_even_a_confident_hit():
    bc = _mod("backlog_check")
    d = bc.decide(_mkpack([_f("duplicate", "42", 0.95, True)]), {"backlog_check": {"action": "flag"}})
    assert d["action"] == "proceed" and "advisory" in d["note"]


def test_decide_summary_covers_multiple_kinds_secret_safe():
    bc = _mod("backlog_check")
    d = bc.decide(_mkpack([_f("obsoleted-by", "5", 0.8, True), _f("blocked-by", "7", 1.0, True, ev=[])]), {})
    assert "obsoleted by #5" in d["reason"] and "blocked by #7" in d["reason"]
    # a local-mode ref is a path; summary shows the stem, not the whole path
    d2 = bc.decide(_mkpack([_f("duplicate", "/tmp/x/.sdlc/goals/0002.md", 0.9, True)]), {})
    assert "duplicate of #0002.md" in d2["reason"] and "/tmp/x" not in d2["reason"]


def test_crosscheck_cli_in_process(capsys):
    bc, pl = _mod("backlog_check"), _mod("pipeline")
    with tempfile.TemporaryDirectory() as d:
        base = _gh_base(d, [_rec(1, _GOAL), _rec(2, _DUP)], **_LOOSE)
        assert pl.main(["pipeline.py", "crosscheck", base, "1"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["schema"] == "backlog-check/v1" and out["goal"] == "1"
        assert bc.main(["backlog_check.py", base, "1"]) == 0
        assert bc.main(["backlog_check.py", base]) == 2       # missing goal -> usage error
        capsys.readouterr()
