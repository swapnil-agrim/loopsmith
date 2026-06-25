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


def test_config_knowledge_graph_off_by_default():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold(tmp)
        kg = json.loads((pathlib.Path(tmp) / ".sdlc" / "config.json").read_text())["knowledge_graph"]
        assert kg["enabled"] is False                # opt-in only
        assert kg["scope"] == "full" and kg["builder"] == "graphify"


def test_scaffold_github_creates_pm_files():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        created, skipped = mod.scaffold_github(tmp)
        gh = pathlib.Path(tmp) / ".github"
        for rel in ["ISSUE_TEMPLATE/epic.md", "ISSUE_TEMPLATE/task.md", "ISSUE_TEMPLATE/bug.md",
                    "ISSUE_TEMPLATE/config.yml", "workflows/add-to-project.yml",
                    "CRITICAL_INSIGHT_TEMPLATE.md", "LABELS.md"]:
            assert (gh / rel).exists(), f"missing {rel}"
        assert skipped == []


def test_scaffold_github_idempotent_preserves_edits():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold_github(tmp)
        epic = pathlib.Path(tmp) / ".github" / "ISSUE_TEMPLATE" / "epic.md"
        epic.write_text("MY EDITS")
        created, skipped = mod.scaffold_github(tmp)
        assert created == [] and "ISSUE_TEMPLATE/epic.md" in skipped
        assert epic.read_text() == "MY EDITS"          # never clobbered


def test_issue_templates_wellformed():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold_github(tmp)
        it = pathlib.Path(tmp) / ".github" / "ISSUE_TEMPLATE"
        epic = (it / "epic.md").read_text()
        assert epic.startswith("---") and "labels:" in epic and "Tasks" in epic and "Definition of done" in epic
        assert "Parent epic" in (it / "task.md").read_text()


def test_add_to_project_workflow_self_activates():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.scaffold_github(tmp)
        wf = (pathlib.Path(tmp) / ".github" / "workflows" / "add-to-project.yml").read_text()
        assert "add-to-project" in wf and "issues:" in wf
        assert "SDLC_PROJECT_URL" in wf and "ADD_TO_PROJECT_PAT" in wf   # guarded by repo var + secret


def test_main_github_flag_scaffolds_dotgithub_and_sdlc():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        assert mod.main(["sdlc_init.py", tmp, "--github"]) == 0
        assert (pathlib.Path(tmp) / ".github" / "workflows" / "add-to-project.yml").exists()
        assert (pathlib.Path(tmp) / ".sdlc" / "config.json").exists()   # still scaffolds .sdlc too


def test_main_without_github_flag_skips_dotgithub():
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        mod.main(["sdlc_init.py", tmp])
        assert not (pathlib.Path(tmp) / ".github").exists()             # opt-in only


def test_main_errors_on_missing_target():
    mod = _load()
    assert mod.main(["sdlc_init.py", "/no/such/dir/really"]) == 1
