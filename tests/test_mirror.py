"""Local board mirror (mirror.py, slice 0.9.20): a token-free, gitignored snapshot of the GitHub
backlog for the cross-check. Reaches GitHub only through an injectable runner, so every test here is
hermetic — no network, no `gh`. Deterministic, $0."""
import json, pathlib, importlib.util, tempfile

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _gh_runner(open_payload, closed_payload):
    """Fake `gh`: returns the closed payload for the `--state closed` list, else the open payload."""
    calls = []
    def run(args):
        calls.append(list(args))
        return json.dumps(closed_payload if "--state closed" in " ".join(args) else open_payload)
    run.calls = calls
    return run


def _github_cfg(**gh):
    return {"discovery": {"source": "github", "github": gh}}


# --- normalization is pure + secret-safe ---

def test_normalize_issue_shapes_and_scrubs():
    m = _mod("mirror")
    rec = m.normalize_issue({
        "number": 42, "title": "Fix the AKIAABCDEFGHIJKLMNOP leak", "state": "OPEN",
        "body": "steps: password: hunter2xyz then ship", "closedAt": None,
        "updatedAt": "2026-08-01T10:00:00Z",
        "labels": [{"name": "sdlc:goal"}, {"name": "area:ui"}, {"other": "x"}]})
    assert rec["number"] == 42 and rec["state"] == "open"
    assert "AKIAABCDEFGHIJKLMNOP" not in rec["title"] and "hunter2xyz" not in rec["body_excerpt"]
    assert rec["labels"] == ["sdlc:goal", "area:ui"]          # malformed label object dropped
    assert len(rec["content_hash"]) == 16 and int(rec["content_hash"], 16) >= 0   # 16 hex chars


def test_build_records_dedup_open_wins_and_sorted_and_skips_malformed():
    m = _mod("mirror")
    open_raw = [{"number": 5, "title": "open five", "state": "open"},
                {"number": 3, "title": "open three", "state": "open"}]
    closed_raw = [{"number": 5, "title": "closed five", "state": "closed"},   # clashes with open #5
                  {"title": "no number"}]                                     # malformed -> skipped
    recs = m.build_records(open_raw, closed_raw)
    assert [r["number"] for r in recs] == [3, 5]              # sorted, malformed dropped
    assert next(r for r in recs if r["number"] == 5)["state"] == "open"   # open wins the clash


# --- the fetch orchestrator ---

def test_fetch_writes_ndjson_and_meta_under_gitignored_state():
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        run = _gh_runner([{"number": 7, "title": "open goal", "state": "open",
                           "labels": [{"name": "sdlc:goal"}], "updatedAt": "2026-08-01T00:00:00Z"}],
                         [{"number": 4, "title": "done work", "state": "closed",
                           "closedAt": "2026-07-30T00:00:00Z", "updatedAt": "2026-07-30T00:00:00Z"}])
        n = m.fetch_and_write(str(base), config=_github_cfg(repo="acme/widget"), run=run, now=1000.0)
        assert n == 2
        recs = m.read_mirror(str(base))
        assert {r["number"] for r in recs} == {4, 7}
        # exactly two list calls: one open (goal-labelled), one closed (not goal-filtered)
        joined = [" ".join(c) for c in run.calls]
        assert any("--label sdlc:goal" in c and "--state open" in c for c in joined)
        assert any("--state closed" in c for c in joined)
        assert all("--repo acme/widget" in c for c in joined)
        meta = json.loads((base / m.META_REL).read_text())
        assert meta["count"] == 2 and meta["mirrored_at"] == 1000.0 and meta["schema"] == m.SCHEMA
        assert m.MIRROR_REL.startswith("state/") and m.META_REL.startswith("state/")   # gitignored dir


def test_assignee_scopes_the_open_query_only():
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        run = _gh_runner([], [])
        m.fetch_and_write(str(base), config=_github_cfg(assignee="@me"), run=run, now=1.0)
        joined = [" ".join(c) for c in run.calls]
        assert any("--state open" in c and "--assignee @me" in c for c in joined)
        assert not any("--state closed" in c and "--assignee" in c for c in joined)   # closed net is wide


def test_local_mode_writes_no_mirror():
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        called = _gh_runner([], [])
        assert m.fetch_and_write(str(base), config={"discovery": {"source": "local-goals"}},
                                 run=called, now=1.0) is None
        assert called.calls == [] and not (base / m.MIRROR_REL).exists()


def test_fetch_fail_open_on_gh_error():
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        def boom(args):
            raise RuntimeError("gh not authenticated")
        assert m.fetch_and_write(str(base), config=_github_cfg(), run=boom, now=1.0) is None
        assert not (base / m.MIRROR_REL).exists()            # nothing half-written


def test_fetch_fail_open_on_bad_closed_limit():
    # a hand-edited config typo must not crash the pick path: "closed_limit": "all" -> None, not ValueError
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        run = _gh_runner([{"number": 1, "title": "a", "state": "open"}], [])
        cfg = _github_cfg(); cfg["backlog_check"] = {"mirror": {"closed_limit": "all"}}
        assert m.fetch_and_write(str(base), config=cfg, run=run, now=1.0) is None
        assert not (base / m.MIRROR_REL).exists()


def test_fetch_fail_open_on_non_list_gh_payload_does_not_clobber():
    # a gh error object ({"message": "Not Found"}) is not a backlog: return None, leave any prior mirror intact
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        good = _gh_runner([{"number": 1, "title": "keep me", "state": "open"}], [])
        m.fetch_and_write(str(base), config=_github_cfg(), run=good, now=1.0, force=True)
        before = (base / m.MIRROR_REL).read_text()
        def err(args):
            return json.dumps({"message": "Not Found"})          # valid JSON, not a list
        assert m.fetch_and_write(str(base), config=_github_cfg(), run=err, now=2.0, force=True) is None
        assert (base / m.MIRROR_REL).read_text() == before        # prior good mirror untouched


def test_build_records_skips_non_dict_rows_without_raising():
    # a stray null / string row inside an otherwise-valid list is skipped, the good rows survive
    m = _mod("mirror")
    recs = m.build_records([{"number": 2, "title": "ok", "state": "open"}, None, "garbage"], [])
    assert [r["number"] for r in recs] == [2]


def test_fetch_fail_open_on_unreadable_config():
    # config=None + a garbage config.json on disk -> _load_config returns {} -> not github mode -> None
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        (base / "config.json").write_text("{ not json")
        assert m.fetch_and_write(str(base), now=1.0) is None


def test_mirror_reached_only_through_the_opt_in_precheck_hook():
    """Additive guarantee: the PICK itself (sources.next_pending) never runs the mirror. As of 0.9.22
    the loop reaches it in exactly one place — loop.py's `precheck` hook — and only AFTER the
    off-by-default `backlog_check.enabled is True` gate, so a default config never mirrors."""
    assert "fetch_and_write" not in (S / "sources.py").read_text()      # the pick path is untouched
    loop_txt = (S / "loop.py").read_text()
    assert "fetch_and_write" in loop_txt                                # the hook wires it now...
    gate, call = loop_txt.index('backlog_check") or {}).get("enabled") is not True'), loop_txt.index("fetch_and_write")
    assert gate < call                                                  # ...behind the enable gate (returns OFF first)


def test_ttl_skips_refetch_until_forced():
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        run = _gh_runner([{"number": 1, "title": "a", "state": "open"}], [])
        assert m.fetch_and_write(str(base), config=_github_cfg(), run=run, now=1000.0) == 1
        first = len(run.calls)
        # 30s later, TTL 60min default -> still fresh -> skip (no new gh calls)
        assert m.fetch_and_write(str(base), config=_github_cfg(), run=run, now=1030.0) is None
        assert len(run.calls) == first
        # force bypasses freshness
        assert m.fetch_and_write(str(base), config=_github_cfg(), run=run, now=1030.0, force=True) == 1
        assert len(run.calls) > first


def test_secret_never_lands_in_the_mirror_file():
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        run = _gh_runner([{"number": 9, "title": "rotate creds", "state": "open",
                           "body": "aws AKIAABCDEFGHIJKLMNOP and ghp_" + "z" * 30,
                           "updatedAt": "2026-08-01T00:00:00Z"}], [])
        m.fetch_and_write(str(base), config=_github_cfg(), run=run, now=1.0)
        raw = (base / m.MIRROR_REL).read_text()
        assert "AKIAABCDEFGHIJKLMNOP" not in raw and "ghp_zzz" not in raw and "REDACTED" in raw


def test_read_mirror_roundtrip_and_fail_open():
    m = _mod("mirror")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        assert m.read_mirror(str(base)) == []                # absent -> empty
        (base / m.MIRROR_REL).write_text(
            json.dumps({"number": 1, "title": "ok"}) + "\n" + "{not json}\n" + "\n")
        recs = m.read_mirror(str(base))
        assert [r["number"] for r in recs] == [1]            # garbage line skipped


def test_records_and_file_are_deterministic():
    m = _mod("mirror")
    raw = [{"number": 2, "title": "b", "state": "open", "updatedAt": "t"},
           {"number": 1, "title": "a", "state": "open", "updatedAt": "t"}]
    assert m.build_records(raw, []) == m.build_records(raw, [])
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        run = _gh_runner(raw, [])
        m.fetch_and_write(str(base), config=_github_cfg(), run=run, now=5.0, force=True)
        first = (base / m.MIRROR_REL).read_text()
        m.fetch_and_write(str(base), config=_github_cfg(), run=run, now=5.0, force=True)
        assert (base / m.MIRROR_REL).read_text() == first    # byte-identical for the same snapshot


def test_mirror_location_is_covered_by_runtime_ignores():
    m = _mod("mirror")
    setup_path = (pathlib.Path(__file__).resolve().parent.parent
                  / "skills" / "sdlc-setup" / "scripts" / "setup.py")
    spec = importlib.util.spec_from_file_location("setup_mod", setup_path)
    setup = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
    # RUNTIME_IGNORES entries are like ".sdlc/state/"; the mirror rel path (state/…) must fall under one
    rels = [ig.split(".sdlc/", 1)[-1] for ig in setup.RUNTIME_IGNORES]
    assert any(m.MIRROR_REL.startswith(r) for r in rels)


def test_mirror_cli_in_process_local_mode_is_noop(capsys):
    m = _mod("mirror")
    pl = _mod("pipeline")
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"; (base / "state").mkdir(parents=True)
        (base / "config.json").write_text(json.dumps({"discovery": {"source": "local-goals"}}))
        assert pl.main(["pipeline.py", "mirror", str(base)]) == 0
        assert "skipped" in capsys.readouterr().out
        assert m.main(["mirror.py", str(base)]) == 0
        assert "skipped" in capsys.readouterr().out
