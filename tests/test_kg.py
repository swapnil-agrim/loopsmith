"""The knowledge-graph helper is the deterministic side of the optional KG feature: read config,
locate the corpus, compute the build plan per scope. Disabled by default; the builder (graphify) is
a soft dep driven by the /sdlc-kg skill, not by this helper."""
import json, pathlib, importlib.util, tempfile

KG = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-kg" / "scripts" / "kg.py"


def _kg():
    spec = importlib.util.spec_from_file_location("kg", KG)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _sdlc(d, kg_block):
    base = pathlib.Path(d) / ".sdlc"; base.mkdir(parents=True)
    cfg = {"budget": {}}
    if kg_block is not None:
        cfg["knowledge_graph"] = kg_block
    (base / "config.json").write_text(json.dumps(cfg))
    return str(base)


def test_disabled_by_default_when_no_block():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.load_config(base)["enabled"] is False
        assert kg.build_plan(base) is None              # disabled -> no plan


def test_disabled_explicitly():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": False, "scope": "full"})
        assert kg.build_plan(base) is None


def test_enabled_full_scope_includes_code():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "scope": "full"})
        plan = kg.build_plan(base, repo_root=d)
        assert plan["include_code"] is True and plan["code_root"] is not None
        assert plan["builder"] == "graphify" and plan["corpus"].endswith("knowledge")


def test_enabled_research_scope_skips_code():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "scope": "research"})
        plan = kg.build_plan(base, repo_root=d)
        assert plan["include_code"] is False and plan["code_root"] is None


def test_builder_and_auto_refresh_configurable():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "builder": "mygraph", "auto_refresh": True})
        plan = kg.build_plan(base)
        assert plan["builder"] == "mygraph" and plan["auto_refresh"] is True


def test_enabled_must_be_strict_true():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": "true"})        # stringy -> treated as disabled
        assert kg.load_config(base)["enabled"] is False and kg.build_plan(base) is None


def test_status_graph_built_follows_builder_output_dir():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "builder": "mygraph"})
        out = pathlib.Path(d) / "mygraph-out"; out.mkdir(); (out / "graph.json").write_text("{}")
        assert kg.status(base)["graph_built"] is True   # not hardcoded to graphify-out


def test_gap_log_appends_and_lists():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.gap_list(base) == []                       # nothing logged yet
        assert kg.gap_log(base, "how does auth refresh work?") is True
        assert kg.gap_log(base, "where is the retry budget set?") is True
        assert kg.gap_list(base) == ["how does auth refresh work?",
                                     "where is the retry budget set?"]


def test_gap_log_dedups_so_it_cannot_bloat():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.gap_log(base, "same question") is True
        assert kg.gap_log(base, "same question") is False         # exact dup skipped
        assert kg.gap_log(base, "  same question  ") is False     # whitespace-normalized dup
        assert kg.gap_list(base) == ["same question"]


def test_gap_log_ignores_empty():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.gap_log(base, "   ") is False
        assert kg.gap_list(base) == []


def test_main_gap_log_and_list():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.main(["kg.py", "gap", "log", "what calls _set_board_status?", base]) == 0
        assert kg.main(["kg.py", "gap", "list", base]) == 0
        assert kg.gap_list(base) == ["what calls _set_board_status?"]   # logged + listable via CLI


def test_gap_resolve_closes_an_open_gap():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "q-one"); kg.gap_log(base, "q-two")
        assert kg.gap_resolve(base, "q-one") is True
        assert kg.gap_list(base) == ["q-two"]              # resolved drops out of the open list


def test_gap_resolve_unknown_is_noop():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "real")
        assert kg.gap_resolve(base, "never logged") is False
        assert kg.gap_list(base) == ["real"]               # untouched


def test_resolved_gap_reopens_if_missed_again():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "flaky"); kg.gap_resolve(base, "flaky")
        assert kg.gap_list(base) == []                     # closed
        assert kg.gap_log(base, "flaky") is True           # missed again -> re-opens
        assert kg.gap_list(base) == ["flaky"]


def test_main_gap_resolve():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "via cli")
        assert kg.main(["kg.py", "gap", "resolve", "via cli", base]) == 0
        assert kg.gap_list(base) == []


def test_maintain_flags_stale_source_notes():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        (pathlib.Path(d) / "src").mkdir()
        (pathlib.Path(d) / "src" / "live.py").write_text("x")     # exists under repo_root
        an = pathlib.Path(base) / "knowledge" / "analysis"; an.mkdir(parents=True)
        (an / "fresh.md").write_text("documents `src/live.py` which still exists")
        (an / "stale.md").write_text("documents `backend/gone.py` long deleted")
        stale = kg.maintain_report(base, repo_root=d)["stale"]
        assert [s["note"] for s in stale] == ["analysis/stale.md"]   # only the dead-path note
        assert stale[0]["missing"] == ["backend/gone.py"]


def test_maintain_finds_exact_dups():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        an = pathlib.Path(base) / "knowledge" / "analysis"; an.mkdir(parents=True)
        (an / "a.md").write_text("identical body")
        (an / "b.md").write_text("identical body")
        (an / "c.md").write_text("different")
        assert kg.maintain_report(base, repo_root=d)["dups"] == [["analysis/a.md", "analysis/b.md"]]


def test_maintain_clean_corpus_reports_nothing():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        rep = kg.maintain_report(base, repo_root=d)               # empty corpus
        assert rep["stale"] == [] and rep["dups"] == []
        assert rep["counts"] == {"research": 0, "analysis": 0, "gaps": 0}
        assert rep["over_threshold"] is False


def test_main_maintain_runs():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.main(["kg.py", "maintain", base, d]) == 0       # report-only, exits clean


def test_load_config_tolerates_missing_or_garbage_config():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        # no .sdlc/config.json at all -> safe defaults, disabled
        assert kg.load_config(str(pathlib.Path(d) / ".sdlc"))["enabled"] is False
