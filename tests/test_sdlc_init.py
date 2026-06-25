import json, pathlib, importlib.util, tempfile

SCAFFOLDER = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sdlc-init" / "scripts" / "sdlc_init.py"


def _load():
    spec = importlib.util.spec_from_file_location("sdlc_init", SCAFFOLDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # safe: __name__ != "__main__", so main() does not run
    return mod


def test_scaffold_creates_full_tree():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        created, skipped = mod.scaffold(tmp)
        base = pathlib.Path(tmp) / ".sdlc"
        for rel in ["project.md", "config.json", "goals/README.md",
                    "goals/0001-example.md", "state/STATE.md", "state/review-queue.md"]:
            assert (base / rel).exists(), f"missing {rel}"
        assert skipped == []
        assert "config.json" in created


def test_config_is_valid_json_with_expected_keys():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold(tmp)
        cfg = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())
        for key in ["mode", "discovery", "budget", "gates", "verify"]:
            assert key in cfg
        assert cfg["gates"]["on_block"] == "park"
        assert cfg["verify"]["command"] == ""


def test_project_name_substituted():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        named = pathlib.Path(tmp) / "myrepo"; named.mkdir()
        mod.scaffold(str(named))
        assert "myrepo" in (named / ".sdlc" / "project.md").read_text()


def test_idempotent_skip_preserves_edits():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold(tmp)
        state = pathlib.Path(tmp) / ".sdlc" / "state" / "STATE.md"
        state.write_text("LIVE PROGRESS — do not clobber")
        created, skipped = mod.scaffold(tmp)            # second run
        assert created == []
        assert "state/STATE.md" in skipped
        assert state.read_text() == "LIVE PROGRESS — do not clobber"


def test_config_discovery_supports_local_and_github():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold(tmp)
        cfg = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())
        assert cfg["discovery"]["source"] == "local-goals"      # default stays local (zero-dep)
        assert cfg["discovery"]["github"]["goal_label"]          # github knobs present for opt-in


def test_config_discovery_has_github_project_board():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold(tmp)
        disc = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())["discovery"]
        assert disc["source"] == "local-goals"                       # default unchanged
        assert disc["github_project"]["columns"]["qc"] == "QC"       # board columns scaffolded


def test_config_knowledge_graph_off_by_default():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold(tmp)
        kg = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())["knowledge_graph"]
        assert kg["enabled"] is False                # opt-in only
        assert kg["scope"] == "full" and kg["builder"] == "graphify"


def test_main_errors_on_missing_target():
    mod = _load()
    assert mod.main(["sdlc_init.py", "/no/such/dir/really"]) == 1
