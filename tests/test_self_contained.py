import json, pathlib, subprocess, sys, tempfile, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_example_has_valid_sdlc():
    ex = ROOT / "examples" / "hello-sdlc"
    cfg = json.loads((ex / ".sdlc" / "config.json").read_text())   # valid JSON
    assert "budget" in cfg
    goals = list((ex / ".sdlc" / "goals").glob("[0-9]*.md"))
    assert goals, "example needs at least one numbered goal"


def test_example_loop_mechanics_run():
    """The example's loop runs end-to-end with a stub run_goal (mechanics, not the agent).
    Runs on a COPY so the committed example state isn't mutated."""
    ex = ROOT / "examples" / "hello-sdlc"
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(ex / ".sdlc", pathlib.Path(d) / ".sdlc")
        code = (
            "import importlib.util;"
            f"spec=importlib.util.spec_from_file_location('loop', r'{ROOT}/skills/sdlc-loop/scripts/loop.py');"
            "lp=importlib.util.module_from_spec(spec);spec.loader.exec_module(lp);"
            f"r=lp.run_loop(r'{d}/.sdlc', lambda g:('done',''));"
            "print(r);assert r['done']==1 and r['stopped']=='backlog-empty'"   # consumes the goal, not vacuous
        )
        subprocess.run([sys.executable, "-c", code], check=True)


def test_no_onshot_specifics_in_shipped_files():
    banned = ("media-orch", "OnShot", "Temporal", "RunPod", "/services/", "onshot")
    # include .tmpl: templates materialize into every user's repo, so they're shipped surface too
    scan_suffixes = (".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".tmpl")
    # Exclude tests/ + caches: test files legitimately NAME the banned words as leakage guards
    # (test_hook.py, test_packaging_slice4.py) — that's not host-project coupling in shipped logic.
    skip_dirs = {"tests", "__pycache__", ".pytest_cache"}
    offenders = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in scan_suffixes:
            continue
        if skip_dirs & set(p.relative_to(ROOT).parts):
            continue
        text = p.read_text(errors="ignore")
        offenders += [f"{p.relative_to(ROOT)}: {b}" for b in banned if b in text]
    assert not offenders, "host-project leakage in shipped files:\n" + "\n".join(offenders)
